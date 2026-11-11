"""Catálogo de procedimentos Python: funções puras sobre MusicIR, com semente. Nenhum código vem do modelo."""

import math
import random
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from muschat import musicir as ir
from muschat.musicir import localizar
from muschat.plano import Plano


@dataclass(frozen=True)
class Faixa:
    minimo: float
    maximo: float
    padrao: float
    descricao: str = ""
    inteiro: bool = True
    unidade: str = ""  # vai junto do valor no resumo da operação ("3600 midicents", nunca "3600" solto)


Funcao = Callable[[ir.MusicIR | None, Plano, dict[str, float], random.Random], ir.MusicIR]


@dataclass(frozen=True)
class Procedimento:
    nome: str
    descricao: str
    parametros: dict[str, Faixa]
    funcao: Funcao
    requer_composicao: bool = False


# instrumento -> (clave, tessitura em midicents); altura real (sem transposição)
INSTRUMENTOS: dict[str, tuple[ir.Clave, tuple[int, int]]] = {
    "violino": ("sol", (5500, 10300)),
    "viola": ("do3", (4800, 9100)),
    "violoncelo": ("fa", (3600, 7600)),
    "contrabaixo": ("fa", (2800, 6700)),
    "flauta": ("sol", (6000, 9600)),
    "oboe": ("sol", (5800, 9100)),
    "clarinete": ("sol", (5000, 9100)),
    "fagote": ("fa", (3400, 7500)),
}
QUARTETO = ["violino", "violino", "viola", "violoncelo"]
_FIGURAS = [Fraction(1, 2), Fraction(3, 8), Fraction(1, 4), Fraction(1, 8)]


# shortcut: nome casado por substring ("violino I" -> violino); vira escolha por id quando o Registro existir
def _instrumento(nome: str) -> str:
    chave = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    for conhecido in sorted(INSTRUMENTOS, key=len, reverse=True):
        if conhecido in chave:
            return conhecido
    raise ValueError(f"instrumento sem tessitura conhecida: {nome!r} (conhecidos: {', '.join(INSTRUMENTOS)})")


def _no_registro(altura: int, grave: int, agudo: int) -> int:
    """Parcial fora da tessitura sobe/desce oitavas até caber: o espectro fica, o registro se ajusta."""
    while altura < grave:
        altura += 1200
    while altura > agudo:
        altura -= 1200
    return altura  # toda tessitura da tabela tem mais de uma oitava


def serie_harmonica(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """Parciais 1..n da fundamental, aproximados, distribuídos por tessitura; ritmo sorteado em 4/4."""
    passo = 100 // 2 ** int(p["microtom"])
    parciais = [round((p["fundamental"] + 1200 * math.log2(n)) / passo) * passo for n in range(1, int(p["parciais"]) + 1)]
    prov = ir.Proveniencia(origem="algoritmo")
    nomes = plano.instrumentos or QUARTETO
    partes = []
    for k, nome in enumerate(nomes, 1):
        clave, (grave, agudo) = INSTRUMENTOS[_instrumento(nome)]
        alturas = sorted({_no_registro(a, grave, agudo) for a in parciais})
        eventos: list[ir.Evento] = []
        inicio = Fraction(0)
        for _ in range(int(p["compassos"])):
            resta = Fraction(1)
            while resta:
                duracao = rng.choice([f for f in _FIGURAS if f <= resta])
                eid = f"P{k}V1E{len(eventos) + 1}"
                if rng.random() < 0.15:  # shortcut: 15% de pausas fixo; vira parâmetro com o catálogo do ticket 06
                    eventos.append(ir.Pausa(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov))
                else:
                    eventos.append(ir.Nota(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov,
                                           alturas=[ir.Altura(midicents=rng.choice(alturas))]))
                inicio, resta = inicio + duracao, resta - duracao
        partes.append(ir.Parte(id=f"P{k}", instrumento=nome.strip().capitalize(), clave=clave,
                               tessitura=(grave, agudo), vozes=[ir.Voz(id=f"P{k}V1", eventos=eventos)]))
    # shortcut: sempre 4/4 a 60; métrica e andamento viram parâmetros quando um pedido precisar
    compassos = [ir.Compasso(numerador=4, denominador=4, andamento=60 if n == 0 else None)
                 for n in range(int(p["compassos"]))]
    return ir.MusicIR(titulo=f"Série harmônica sobre {p['fundamental']:.0f} mc", compassos=compassos, partes=partes)


def permutar_duracoes(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """Embaralha as durações dentro de cada compasso das vozes selecionadas; ids e alturas ficam."""
    if atual is None:
        raise ValueError("permutar_duracoes precisa de uma composição")
    locais = localizar(atual)
    partes = []
    for parte in atual.partes:
        vozes = []
        for voz in parte.vozes:
            em_quialtera = {i for q in voz.quialteras for i in q.eventos}
            grupos: dict[int, list[ir.Evento]] = {}
            for e in voz.eventos:
                grupos.setdefault(locais[e.id][2] or 0, []).append(e)
            eventos: list[ir.Evento] = []
            for compasso, grupo in grupos.items():
                if plano.selecao.contem(parte.id, voz.id, compasso) and not em_quialtera & {e.id for e in grupo}:
                    duracoes = [e.duracao for e in grupo]
                    rng.shuffle(duracoes)
                    inicio = grupo[0].inicio
                    for e, d in zip(grupo, duracoes):
                        eventos.append(e.model_copy(update={"inicio": inicio, "duracao": d}))
                        inicio += d
                else:
                    eventos += grupo  # fora da seleção, ou com quiálteras (permutar quebraria os grupos)
            vozes.append(voz.model_copy(update={"eventos": eventos}))
        partes.append(parte.model_copy(update={"vozes": vozes}))
    return atual.model_copy(update={"partes": partes})


CATALOGO: dict[str, Procedimento] = {p.nome: p for p in [
    Procedimento(
        "serie_harmonica",
        "Cria uma peça nova: alturas dos parciais harmônicos de uma fundamental, distribuídas entre os instrumentos "
        "do plano (padrão: quarteto de cordas) respeitando a tessitura, com ritmo sorteado pela semente.",
        {
            "fundamental": Faixa(2400, 6000, 3600, "fundamental", unidade="midicents (3600 = Dó 2, 6000 = Dó 4)"),
            "parciais": Faixa(2, 32, 16, "quantos parciais (1..n)"),
            "compassos": Faixa(1, 32, 8, "número de compassos 4/4"),
            "microtom": Faixa(0, 2, 1, "aproximação: 0 semitom, 1 quarto de tom, 2 oitavo de tom"),
        },
        serie_harmonica,
    ),
    Procedimento(
        "permutar_duracoes",
        "Processo rítmico sobre a composição atual: permuta as durações dentro de cada compasso da seleção, "
        "mantendo as alturas e a ordem dos eventos.",
        {},
        permutar_duracoes,
        requer_composicao=True,
    ),
]}

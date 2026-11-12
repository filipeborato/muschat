"""Procedimentos que criam material: série harmônica e distribuição entre instrumentos."""

import math
import random
from fractions import Fraction

from muschat import musicir as ir
from muschat.plano import Plano
from muschat.instrumentos import instrumento, no_registro
from muschat.procedimentos.comum import QUARTETO, Grade, Ids, exigir

_DURACOES_SORTEADAS = [Fraction(1, 2), Fraction(3, 8), Fraction(1, 4), Fraction(1, 8)]


def serie_harmonica(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """Parciais 1..n da fundamental, aproximados, distribuídos por tessitura; ritmo sorteado em 4/4."""
    passo = 100 // 2 ** int(p["microtom"])
    parciais = [round((p["fundamental"] + 1200 * math.log2(n)) / passo) * passo for n in range(1, int(p["parciais"]) + 1)]
    prov = ir.Proveniencia(origem="algoritmo")
    partes = []
    for k, nome in enumerate(plano.instrumentos or QUARTETO, 1):
        inst = instrumento(nome)
        clave, tessitura = inst.clave, inst.tessitura
        alturas = sorted({no_registro(a, tessitura) for a in parciais})
        eventos: list[ir.Evento] = []
        inicio = Fraction(0)
        for _ in range(int(p["compassos"])):
            resta = Fraction(1)
            while resta:
                duracao = rng.choice([f for f in _DURACOES_SORTEADAS if f <= resta])
                eid = f"P{k}V1E{len(eventos) + 1}"
                if rng.random() < p["pausas"]:
                    eventos.append(ir.Pausa(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov))
                else:
                    eventos.append(ir.Nota(id=eid, inicio=inicio, duracao=duracao, proveniencia=prov,
                                           alturas=[ir.Altura(midicents=rng.choice(alturas))]))
                inicio, resta = inicio + duracao, resta - duracao
        partes.append(ir.Parte(id=f"P{k}", instrumento=nome.strip().capitalize(), clave=clave,
                               tessitura=tessitura, vozes=[ir.Voz(id=f"P{k}V1", eventos=eventos)]))
    # shortcut: sempre 4/4 a 60; métrica e andamento viram parâmetros quando um pedido precisar
    compassos = [ir.Compasso(numerador=4, denominador=4, andamento=60 if n == 0 else None)
                 for n in range(int(p["compassos"]))]
    return ir.MusicIR(titulo=f"Série harmônica sobre {p['fundamental']:.0f} mc", compassos=compassos, partes=partes)


def distribuir(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """As notas de uma parte de origem, no trecho, vão cada uma para uma parte selecionada cuja tessitura as
    contém (sorteada); as primeiras vozes das partes de destino são reescritas no trecho, com pausas no resto."""
    atual = exigir(atual)
    n = int(p["parte_origem"])
    if n > len(atual.partes):
        raise ValueError(f"parte_origem {n}: a composição tem {len(atual.partes)} partes")
    grade, ids, sel = Grade(atual), Ids(atual), plano.selecao
    primeiro, ultimo = grade.trecho(sel)
    ini, fim = grade.inicios[primeiro - 1], grade.fins[ultimo - 1]
    origem = atual.partes[n - 1].vozes[0]
    if origem.quialteras:
        raise ValueError(f"distribuir ainda não copia quiálteras ({origem.id})")
    material = [e for e in origem.eventos if isinstance(e, ir.Nota) and ini <= e.inicio and e.fim <= fim]
    destinos = [parte for parte in atual.partes if not sel.partes or parte.id in sel.partes]
    for parte in destinos:
        if parte.vozes[0].quialteras:
            raise ValueError(f"distribuir ainda não reescreve vozes com quiálteras ({parte.vozes[0].id})")

    recebidas: dict[str, list[ir.Nota]] = {parte.id: [] for parte in destinos}
    for e in material:
        cabe = [d for d in destinos if d.tessitura is None
                or all(d.tessitura[0] <= a.midicents <= d.tessitura[1] for a in e.alturas)]
        d = rng.choice(cabe or destinos)
        alturas = [ir.Altura(midicents=no_registro(a.midicents, d.tessitura)) for a in e.alturas]
        recebidas[d.id].append(e.model_copy(update={"id": ids.novo(f"{d.vozes[0].id}D"), "alturas": alturas,
                                                    "glissando": False}))

    def reescrever(parte: ir.Parte) -> ir.Parte:
        if parte.id not in recebidas:
            return parte
        voz = parte.vozes[0]
        eventos: list[ir.Evento] = [e for e in voz.eventos if not ini <= e.inicio < fim]
        cursor = ini
        for e in [*recebidas[parte.id], None]:
            ate = e.inicio if e else fim
            for inicio, duracao in grade.pedacos(cursor, ate - cursor) if ate > cursor else []:
                eventos.append(ir.Pausa(id=ids.novo(f"{voz.id}D"), inicio=inicio, duracao=duracao,
                                        proveniencia=ir.Proveniencia(origem="algoritmo")))
            if e:
                eventos.append(e)
                cursor = e.fim
        voz = voz.model_copy(update={"eventos": sorted(eventos, key=lambda x: x.inicio)})
        return parte.model_copy(update={"vozes": [voz, *parte.vozes[1:]]})

    return atual.model_copy(update={"partes": [reescrever(parte) for parte in atual.partes]})

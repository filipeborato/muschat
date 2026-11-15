"""Material espectral: parciais de uma fundamental, dado espectral (ex.: análise do OM-SuperVP) -> eventos
instrumentais, e alturas de uma composição trocadas por parciais preservando o contorno."""

import math
import random
from fractions import Fraction

from muschat import musicir as ir
from muschat.instrumentos import instrumento, no_registro, tessitura_da_parte
from muschat.plano import Plano
from muschat.procedimentos.comum import QUARTETO, Grade, Ids, com_alturas, exigir, por_voz, repartir


def parciais(fundamental: float, quantos: int, microtom: int) -> list[int]:
    """Parciais 1..n da fundamental (midicents), aproximados ao semitom, quarto ou oitavo de tom (microtom 0, 1, 2)."""
    passo = 100 // 2 ** microtom
    return [round((fundamental + 1200 * math.log2(n)) / passo) * passo for n in range(1, quantos + 1)]


def espectralizar(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """As alturas da seleção viram parciais da fundamental: cada parte recebe uma faixa do espectro (a de cima, os
    parciais altos; a de baixo, os graves) dentro da sua tessitura, e cada voz mantém o contorno (sobe onde subia,
    desce onde descia, repete onde repetia), com o salto limitado. Ritmo, pausas, dinâmicas e técnicas ficam."""
    atual = exigir(atual)
    reservatorio = sorted(set(parciais(p["fundamental"], int(p["parciais"]), int(p["microtom"]))))
    salto = int(p["salto_maximo"])
    sel = plano.selecao  # só as partes que a seleção alcança recebem faixa (e precisam de parciais)
    com_altura = [parte for parte in atual.partes if (not sel.partes or parte.id in sel.partes)
                  and len(set(tessitura_da_parte(parte) or (0, 1))) == 2]
    faixas = {parte.id: _faixa(reservatorio, parte, k, len(com_altura)) for k, parte in enumerate(com_altura)}

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        notas = [e for e in alvo if isinstance(e, ir.Nota)]
        if parte.id not in faixas or not notas:
            return alvo
        faixa, cabem = faixas[parte.id]
        linha = _contorno([n.alturas[0].midicents for n in notas], faixa, cabem, salto)
        novas = {n.id: com_alturas(n, _acorde(n, alto, cabem)) for n, alto in zip(notas, linha)}
        return [novas.get(e.id, e) for e in alvo]

    return por_voz(atual, plano.selecao, trecho)


def _faixa(reservatorio: list[int], parte: ir.Parte, k: int, n: int) -> tuple[list[int], list[int]]:
    """(faixa planejada da parte k de n, do agudo ao grave; parciais que cabem na tessitura). As faixas se
    sobrepõem pela metade, para as vozes vizinhas conversarem."""
    tessitura = tessitura_da_parte(parte)
    cabem = [a for a in reservatorio if tessitura is None or tessitura[0] <= a <= tessitura[1]]
    if not cabem:
        raise ValueError(f"nenhum parcial cabe na tessitura de {parte.instrumento} ({parte.id}); mude a fundamental "
                         "ou o número de parciais")
    ultimo = max(len(reservatorio) - 1, 1)
    baixo, alto = (n - 1 - k) / (n + 1), (n - k + 1) / (n + 1)
    faixa = [a for a in cabem if baixo <= reservatorio.index(a) / ultimo <= alto]
    # shortcut: faixa mínima fixa de 3 parciais; virar parâmetro se o compositor quiser linhas mais estreitas
    if len(faixa) < 3:  # faixa estreita demais: os parciais que cabem mais perto do centro dela
        centro = (baixo + alto) / 2 * ultimo
        faixa = sorted(sorted(cabem, key=lambda a: abs(reservatorio.index(a) - centro))[:3])
    return faixa, cabem


def _contorno(original: list[int], faixa: list[int], cabem: list[int], salto: int) -> list[int]:
    """Nova linha na faixa com as mesmas direções da original; o tamanho de cada passo segue o original até o
    salto máximo. Sem parcial na direção dentro do salto, a nota repete (nunca inverte a direção)."""
    menor, maior = min(original), max(original)
    posicao = (original[0] - menor) / (maior - menor) if maior > menor else 0.5
    linha = [faixa[round(posicao * (len(faixa) - 1))]]
    for antes, depois in zip(original, original[1:]):
        atual, passo = linha[-1], depois - antes
        if passo == 0:
            linha.append(atual)
            continue
        alvo = min(abs(passo), salto)
        for pool in (faixa, cabem):  # primeiro dentro da faixa planejada, depois em toda a tessitura
            opcoes = [a for a in pool if (a - atual) * passo > 0 and abs(a - atual) <= salto]
            if opcoes:
                linha.append(min(opcoes, key=lambda a: (abs(abs(a - atual) - alvo), abs(a - atual))))
                break
        else:
            linha.append(atual)
    return linha


def _acorde(nota: ir.Nota, alto: int, cabem: list[int]) -> list[int]:
    """A primeira altura vira `alto`; as outras do acorde, os parciais mais perto dos mesmos intervalos."""
    base = nota.alturas[0].midicents
    alturas = [alto]
    for a in nota.alturas[1:]:
        livres = [c for c in cabem if c not in alturas] or cabem
        alturas.append(min(livres, key=lambda c: abs(c - (alto + a.midicents - base))))
    return alturas


def eventos_do_espectro(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float],
                        rng: random.Random) -> ir.MusicIR:
    """Em cada quadro espectral, os parciais mais fortes viram alturas (quantizadas) distribuídas do agudo ao grave
    entre os instrumentos do plano, na tessitura de cada um; o dado espectral fica intacto na sua parte."""
    atual = exigir(atual)
    quadros = sorted((e for _, _, e in atual.eventos() if isinstance(e, ir.Espectral)), key=lambda e: e.inicio)
    if not quadros:
        raise ValueError("eventos_do_espectro precisa de dado espectral na composição (ex.: análise do OM-SuperVP)")
    passo = 100 // 2 ** int(p["microtom"])
    nomes = plano.instrumentos or QUARTETO
    grade, ids = Grade(atual), Ids(atual)
    fim = grade.fins[-1]
    usados = {parte.id for parte in atual.partes}
    novas = []
    for k, nome in enumerate(nomes, 1):
        inst = instrumento(nome)
        pid = f"E{k}"
        while pid in usados:
            pid += "b"
        usados.add(pid)
        eventos: list[ir.Evento] = []
        cursor = Fraction(0)
        for q in quadros:
            if q.inicio < cursor:
                continue  # quadros sobrepostos (de vozes diferentes): fica o primeiro
            if q.inicio > cursor:
                eventos += _pausas(ids.novo(f"{pid}V1E"), cursor, q.inicio - cursor, grade, ids)
            fortes = sorted(q.parciais, key=lambda x: -x.amplitude)[:int(p["alturas_por_quadro"])]
            alturas = sorted((round((6900 + 1200 * math.log2(x.frequencia / 440)) / passo) * passo for x in fortes),
                             reverse=True)
            fim_q = min(q.fim, fim)
            if k - 1 < len(alturas) and 0 <= alturas[k - 1] <= 12700:
                nota = ir.Nota(id=ids.novo(f"{pid}V1E"), inicio=q.inicio, duracao=fim_q - q.inicio,
                               proveniencia=ir.Proveniencia(origem="algoritmo"),
                               alturas=[ir.Altura(midicents=no_registro(alturas[k - 1], inst.tessitura))])
                eventos += repartir(nota, q.inicio, fim_q - q.inicio, grade, ids)
            else:
                eventos += _pausas(ids.novo(f"{pid}V1E"), q.inicio, fim_q - q.inicio, grade, ids)
            cursor = fim_q
        if cursor < fim:
            eventos += _pausas(ids.novo(f"{pid}V1E"), cursor, fim - cursor, grade, ids)
        novas.append(ir.Parte(id=pid, instrumento=nome.strip().capitalize(), clave=inst.clave,
                              tessitura=inst.tessitura, vozes=[ir.Voz(id=f"{pid}V1", eventos=eventos)]))
    return atual.model_copy(update={"partes": [*atual.partes, *novas]})


def _pausas(eid: str, inicio: Fraction, duracao: Fraction, grade: Grade, ids: Ids) -> list[ir.Evento]:
    pausa = ir.Pausa(id=eid, inicio=inicio, duracao=duracao, proveniencia=ir.Proveniencia(origem="algoritmo"))
    return repartir(pausa, inicio, duracao, grade, ids)

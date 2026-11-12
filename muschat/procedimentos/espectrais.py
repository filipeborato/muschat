"""Dado espectral (ex.: análise do OM-SuperVP) -> eventos instrumentais."""

import math
import random
from fractions import Fraction

from muschat import musicir as ir
from muschat.instrumentos import instrumento, no_registro
from muschat.plano import Plano
from muschat.procedimentos.comum import QUARTETO, Grade, Ids, exigir, repartir


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

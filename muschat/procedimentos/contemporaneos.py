"""Desvios de altura, ruído idiomático e rarefação do material existente."""

import math
import random
from fractions import Fraction as F

from muschat import musicir as ir
from muschat.instrumentos import identificar, tessitura_da_parte
from muschat.notacao.tecnicas import problema
from muschat.plano import Plano
from muschat.procedimentos.comum import DINAMICAS, Grade, Ids, exigir, pausa, por_voz, repartir, trocar_vozes


RUIDOS = {
    1: ("scratch", "col legno tratto", "dietro il ponticello", "sulla cordiera"),
    2: ("col legno battuto", "slap", "tongue ram", "key click"),
    3: ("aria",),
}
CORDAS_SOLTAS = {
    "violino": (5500, 6200, 6900, 7600), "viola": (4800, 5500, 6200, 6900),
    "violoncelo": (3600, 4300, 5000, 5700), "contrabaixo": (2800, 3300, 3800, 4300),
}


def desafinar(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    atual = exigir(atual)
    passo = 50 if p["resolucao"] == 1 else 25
    amplitude = math.floor(p["desvio"] / passo) * passo

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        notas = [e for e in alvo if isinstance(e, ir.Nota)]
        if not notas:
            return alvo
        inicio, fim = notas[0].inicio, notas[-1].inicio
        selecionadas = {n.id for n in notas}
        ataques, fixas = {}, set()
        cadeia: list[ir.Nota] = []
        for e, proximo in zip(voz.eventos, [*voz.eventos[1:], None]):
            if not isinstance(e, ir.Nota):
                continue
            cadeia.append(e)
            if ir.ligam(e, proximo):
                continue
            for n in cadeia:
                ataques[n.id] = cadeia[0].inicio
            if any(n.id not in selecionadas for n in cadeia):
                fixas.update(n.id for n in cadeia)
            cadeia = []
        glissandos = {a.id for gesto in ir.gestos(alvo) for a, b in zip(gesto, gesto[1:])
                     if a.fim == b.inicio and not ir.ligam(a, b) and rng.random() < p["glissandos"]}
        faixa = tessitura_da_parte(parte) or (0, 12700)
        novas = {}
        for nota in notas:
            if nota.id in fixas:
                continue
            t = float((ataques[nota.id] - inicio) / (fim - inicio)) if fim > inicio else 0
            curva = t if p["curva"] == 0 else math.sin(2 * math.pi * t)
            desvio = round(amplitude * curva / passo) * passo
            minimo = max(-amplitude, math.ceil((faixa[0] - min(a.midicents for a in nota.alturas)) / passo) * passo)
            maximo = min(amplitude, math.floor((faixa[1] - max(a.midicents for a in nota.alturas)) / passo) * passo)
            if minimo > maximo:
                raise ValueError(f"acorde não cabe na tessitura de {parte.instrumento}")
            desvio = min(max(desvio, minimo), maximo)
            novas[nota.id] = nota.model_copy(update={
                "alturas": [ir.Altura(midicents=a.midicents + desvio) for a in nota.alturas],
                "glissando": nota.id in glissandos})
        return [novas.get(e.id, e) for e in alvo]

    return por_voz(atual, plano.selecao, trecho)


def substituir_timbre(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float],
                      rng: random.Random) -> ir.MusicIR:
    atual = exigir(atual)
    permitidas = RUIDOS[int(p["familia_ruido"])] if p["familia_ruido"] else sum(RUIDOS.values(), ())

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        notas = [e for e in alvo if isinstance(e, ir.Nota)]
        novas = {}
        for k, nota in enumerate(notas):
            proporcao = p["inicial"] + (p["final"] - p["inicial"]) * k / max(len(notas) - 1, 1)
            if rng.random() >= proporcao:
                continue
            # Altura de referência temperada: ruído não recebe precisão microtonal fictícia.
            candidata = nota.model_copy(update={"alturas": [ir.Altura(midicents=round(a.midicents / 100) * 100)
                                                            for a in nota.alturas]})
            opcoes = [t for t in permitidas if problema(t, parte, candidata) is None]
            if opcoes:
                novas[nota.id] = candidata.model_copy(update={"tecnicas": [rng.choice(opcoes)],
                                                               "glissando": False, "ligada": False})
        return [novas.get(e.id, e) for e in alvo]

    return por_voz(atual, plano.selecao, trecho)


def rarefazer(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    atual = exigir(atual)
    grade, ids = Grade(atual), Ids(atual)
    grupos: dict[str, list[ir.Quialtera]] = {}

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        novas: dict[str, list[ir.Evento]] = {}
        em_quialtera = {i: q.id for q in voz.quialteras for i in q.eventos}
        por_id = {q.id: q for q in voz.quialteras}
        for gesto in ir.gestos(alvo):
            mantidas = [n for n in gesto if rng.random() < p["manter"]]
            for n in gesto:
                novas[n.id] = [pausa(n)]
            for n in mantidas:
                seguintes = gesto[gesto.index(n) + 1:]
                absorvidas = []
                fim = n.fim
                for seguinte in seguintes:
                    if (seguinte in mantidas or seguinte.inicio != fim
                            or em_quialtera.get(n.id) != em_quialtera.get(seguinte.id)):
                        break
                    absorvidas.append(seguinte)
                    fim = seguinte.fim
                copia = n.model_copy(update={"duracao": fim - n.inicio, "glissando": False,
                    "ligada": False, "dinamica": DINAMICAS[int(p["dinamica"])]})
                if rng.random() < p["harmonicos"]:
                    try:
                        cordas = CORDAS_SOLTAS.get(identificar(parte.instrumento), ())
                    except ValueError:
                        cordas = ()
                    if cordas:
                        # shortcut: afinação padrão, parciais 2–8; ampliar para scordatura quando houver modelo de cordas.
                        naturais = sorted({round((c + 1200 * math.log2(h)) / 25) * 25 for c in cordas for h in range(2, 9)})
                        candidatos = [copia.model_copy(update={"alturas": [ir.Altura(midicents=a)], "tecnicas": ["harmonico"]})
                                      for a in naturais]
                        candidatos = [c for c in candidatos if problema("harmonico", parte, c) is None]
                        if candidatos:
                            copia = min(candidatos, key=lambda c: abs(c.alturas[0].midicents - n.alturas[0].midicents))
                    elif problema("whistle tone", parte, copia) is None:
                        copia = copia.model_copy(update={"tecnicas": ["whistle tone"]})
                if rng.random() < p["acentos"]:
                    copia = copia.model_copy(update={"tecnicas": [*copia.tecnicas, "accento"]})
                partes: list[ir.Evento] = []
                if n.id in em_quialtera:
                    fator, grupo = F(1), em_quialtera[n.id]
                    while grupo is not None:
                        q = por_id[grupo]
                        fator *= F(q.denominador, q.numerador)
                        grupo = q.pai
                    cursor = copia.inicio
                    for duracao in ir.figuras(copia.duracao / fator):
                        partes.append(copia.model_copy(update={"id": n.id if not partes else ids.novo(n.id),
                            "inicio": cursor, "duracao": duracao * fator, "ligada": True}))
                        cursor += duracao * fator
                    partes[-1] = partes[-1].model_copy(update={"ligada": False})
                else:
                    partes = repartir(copia, copia.inicio, copia.duracao, grade, ids, sustentar=True)
                novas[n.id] = partes
                for removida in absorvidas:
                    novas[removida.id] = []
        grupos[voz.id] = [q.model_copy(update={"eventos": [n.id for i in q.eventos
            for n in novas.get(i, [next(e for e in voz.eventos if e.id == i)])]}) for q in voz.quialteras]
        return [n for e in alvo for n in novas.get(e.id, [e])]

    nova = por_voz(atual, plano.selecao, trecho)
    return trocar_vozes(nova, lambda parte, v: v.model_copy(update={"quialteras": grupos.get(v.id, v.quialteras)}))

"""Transformações melódicas: só as alturas das notas selecionadas mudam; ritmo, pausas e ids ficam."""

import random
from collections.abc import Callable

from muschat import musicir as ir
from muschat.plano import Plano
from muschat.procedimentos.comum import com_alturas, exigir, por_voz

Sequencia = list[list[int]]  # alturas (midicents) de cada nota da voz, na ordem; acorde = várias


def _sobre_sequencia(atual: ir.MusicIR | None, plano: Plano, f: Callable[[Sequencia], Sequencia]) -> ir.MusicIR:
    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        notas = [e for e in alvo if isinstance(e, ir.Nota)]
        novas = f([[a.midicents for a in n.alturas] for n in notas]) if notas else []
        trocadas = {n.id: com_alturas(n, alturas) for n, alturas in zip(notas, novas)}
        return [trocadas.get(e.id, e) for e in alvo]

    return por_voz(exigir(atual), plano.selecao, trecho)


def transpor(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    n = int(p["intervalo"])
    return _sobre_sequencia(atual, plano, lambda seq: [[a + n for a in acorde] for acorde in seq])


def inverter(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    def espelhar(seq: Sequencia) -> Sequencia:
        eixo = int(p["eixo"]) or seq[0][0]
        return [[2 * eixo - a for a in acorde] for acorde in seq]

    return _sobre_sequencia(atual, plano, espelhar)


def retrogradar(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    return _sobre_sequencia(atual, plano, lambda seq: seq[::-1])


def rotacionar(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    def girar(seq: Sequencia) -> Sequencia:
        k = int(p["passos"]) % len(seq)
        return seq[k:] + seq[:k]

    return _sobre_sequencia(atual, plano, girar)


def intervalos(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    def escalar(seq: Sequencia) -> Sequencia:
        base = seq[0][0]
        return [[base + round((a - base) * p["fator"] / 50) * 50 for a in acorde] for acorde in seq]  # quarto de tom

    return _sobre_sequencia(atual, plano, escalar)

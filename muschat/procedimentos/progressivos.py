"""Densidade e transformações progressivas: rampas lineares ao longo dos compassos do trecho."""

import random
from fractions import Fraction

from muschat import musicir as ir
from muschat.plano import Plano
from muschat.procedimentos.comum import (
    DINAMICAS, Grade, Ids, com_alturas, exigir, interpolar, pausa, por_compasso, por_voz,
)


def _metades(d: Fraction) -> tuple[Fraction, Fraction] | None:
    """Duas figuras que somam d (até a fusa): 1/4 -> 1/8 + 1/8; 3/8 -> 1/4 + 1/8."""
    if d.numerator == 1 and d >= Fraction(1, 16):
        return d / 2, d / 2
    if d.numerator == 3 and d >= Fraction(3, 32):
        return d * 2 / 3, d / 3
    return None


def densidade(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """Ataques por semínima em rampa do primeiro ao último compasso: remove ataques (viram pausas) ou cria
    (pausa vira nota com altura já usada na voz; nota se divide em duas)."""
    atual = exigir(atual)
    ids = Ids(atual)
    primeiro, ultimo = Grade(atual).trecho(plano.selecao)

    def compasso(parte: ir.Parte, voz: ir.Voz, grupo: list[ir.Evento], n: int) -> list[ir.Evento]:
        alvo = round(interpolar(p["inicial"], p["final"], n, primeiro, ultimo) * 4 * atual.compassos[n - 1].comprimento)
        reservatorio = sorted({a.midicents for e in voz.eventos if isinstance(e, ir.Nota) for a in e.alturas})
        eventos = list(grupo)

        def notas() -> list[int]:
            return [k for k, e in enumerate(eventos) if isinstance(e, ir.Nota)]

        while len(notas()) > alvo:
            k = rng.choice(notas())
            eventos[k] = pausa(eventos[k])
        while len(notas()) < alvo:
            pausas = [k for k, e in enumerate(eventos) if isinstance(e, ir.Pausa)]
            if pausas and reservatorio:
                k = rng.choice(pausas)
                e = eventos[k]
                eventos[k] = ir.Nota(id=e.id, inicio=e.inicio, duracao=e.duracao, proveniencia=e.proveniencia,
                                     alturas=[ir.Altura(midicents=rng.choice(reservatorio))])
                continue
            divisiveis = [k for k in notas() if _metades(eventos[k].duracao)]
            if not divisiveis:
                break  # alvo acima do que as figuras permitem: fica o máximo possível
            k = rng.choice(divisiveis)
            e = eventos[k]
            a, b = _metades(e.duracao)  # type: ignore[misc]
            eventos[k:k + 1] = [e.model_copy(update={"duracao": a, "glissando": False}),
                                e.model_copy(update={"id": ids.novo(e.id), "inicio": e.inicio + a, "duracao": b,
                                                     "dinamica": None, "texto": None})]
        return eventos

    return por_compasso(atual, plano.selecao, compasso)


def registro_progressivo(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float],
                         rng: random.Random) -> ir.MusicIR:
    """Desloca as alturas por uma rampa de cents do início ao fim do trecho (em quartos de tom)."""
    atual = exigir(atual)
    grade = Grade(atual)
    primeiro, ultimo = grade.trecho(plano.selecao)
    ini, fim = grade.inicios[primeiro - 1], grade.fins[ultimo - 1]

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        ultimo_ataque = max((e.inicio for e in alvo if isinstance(e, ir.Nota)), default=ini)

        def deslocar(e: ir.Nota) -> ir.Nota:  # a primeira nota recebe o inicial, a última o final
            t = float((e.inicio - ini) / (ultimo_ataque - ini)) if ultimo_ataque > ini else 0.0
            n = round((p["inicial"] + (p["final"] - p["inicial"]) * t) / 50) * 50
            return com_alturas(e, [a.midicents + n for a in e.alturas])

        return [deslocar(e) if isinstance(e, ir.Nota) else e for e in alvo]

    return por_voz(atual, plano.selecao, trecho)


def dinamica_progressiva(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float],
                         rng: random.Random) -> ir.MusicIR:
    """Uma dinâmica por compasso, no primeiro ataque, em rampa entre dois níveis; as outras marcas saem."""
    atual = exigir(atual)
    primeiro, ultimo = Grade(atual).trecho(plano.selecao)

    def compasso(parte: ir.Parte, voz: ir.Voz, grupo: list[ir.Evento], n: int) -> list[ir.Evento]:
        nivel = DINAMICAS[round(interpolar(p["inicial"], p["final"], n, primeiro, ultimo))]
        primeira = next((e.id for e in grupo if isinstance(e, ir.Nota)), None)
        return [e.model_copy(update={"dinamica": nivel if e.id == primeira else None})
                if isinstance(e, (ir.Nota, ir.Pausa)) else e for e in grupo]

    return por_compasso(atual, plano.selecao, compasso)


def aplicar_tecnicas(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """As técnicas do plano (nomes do grimório) em uma proporção sorteada das notas selecionadas."""
    if not plano.tecnicas:
        raise ValueError("aplicar_tecnicas precisa de técnicas no plano (campo tecnicas)")

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        return [e.model_copy(update={"tecnicas": list(dict.fromkeys([*e.tecnicas, *plano.tecnicas]))})
                if isinstance(e, ir.Nota) and rng.random() < p["proporcao"] else e for e in alvo]

    return por_voz(exigir(atual), plano.selecao, trecho)

"""Transformações rítmicas sobre o trecho selecionado, sempre dentro dos compassos que ele ocupa."""

import math
import random
from fractions import Fraction

from muschat import musicir as ir
from muschat.musicir import localizar
from muschat.plano import Plano
from muschat.procedimentos.comum import Grade, Ids, exigir, por_compasso, por_voz, repartir, trocar_vozes


def permutar_duracoes(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    def compasso(parte: ir.Parte, voz: ir.Voz, grupo: list[ir.Evento], n: int) -> list[ir.Evento]:
        duracoes = [e.duracao for e in grupo]
        rng.shuffle(duracoes)
        inicio, saida = grupo[0].inicio, []
        for e, d in zip(grupo, duracoes):
            saida.append(e.model_copy(update={"inicio": inicio, "duracao": d}))
            inicio += d
        return saida

    return por_compasso(exigir(atual), plano.selecao, compasso)


def aumentacao_diminuicao(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float],
                          rng: random.Random) -> ir.MusicIR:
    """Fator 2^expoente a partir do início do trecho: a aumentação corta o que passa do fim, a diminuição repete."""
    atual = exigir(atual)
    fator = Fraction(2) ** int(p["expoente"])
    grade, ids, locais = Grade(atual), Ids(atual), localizar(atual)

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        if {i for q in voz.quialteras for i in q.eventos} & {e.id for e in alvo}:
            raise ValueError(f"aumentacao_diminuicao ainda não reescala quiálteras ({voz.id}); selecione outra voz")
        if fator == 1:
            return alvo
        ini = grade.inicios[(locais[alvo[0].id][2] or 1) - 1]
        fim = grade.fins[(locais[alvo[-1].id][2] or 1) - 1]
        periodo = (fim - ini) * fator if fator < 1 else fim - ini
        saida: list[ir.Evento] = []
        for k in range(math.ceil((fim - ini) / periodo)):
            for e in alvo:
                inicio = ini + k * periodo + (e.inicio - ini) * fator
                if inicio >= fim:
                    break
                copia = e if k == 0 else e.model_copy(update={"id": ids.novo(e.id)})
                saida += repartir(copia, inicio, min(e.duracao * fator, fim - inicio), grade, ids)
        return saida

    return por_voz(atual, plano.selecao, trecho)


def subdividir(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    """Cada nota sorteada vira n notas iguais com a mesma altura: quiáltera n:2^k quando n não é potência de 2."""
    atual = exigir(atual)
    n, ids = int(p["divisao"]), Ids(atual)
    base = 2 ** int(math.log2(n))
    grupos: dict[str, list[ir.Quialtera]] = {}

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        em_quialtera = {i for q in voz.quialteras for i in q.eventos}
        saida: list[ir.Evento] = []
        for e in alvo:
            divisivel = e.id not in em_quialtera and e.duracao.numerator == 1 and e.duracao / base >= Fraction(1, 32)
            if not (isinstance(e, ir.Nota) and divisivel and rng.random() < p["proporcao"]):
                saida.append(e)
                continue
            d = e.duracao / n
            partes = [e.model_copy(update={
                "id": e.id if k == 0 else ids.novo(e.id), "inicio": e.inicio + k * d, "duracao": d,
                "glissando": e.glissando if k == n - 1 else False,
                **({} if k == 0 else {"dinamica": None, "texto": None}),
            }) for k in range(n)]
            saida += partes
            if base != n:
                grupos.setdefault(voz.id, []).append(ir.Quialtera(
                    id=ids.novo(f"{voz.id}Q"), numerador=n, denominador=base, eventos=[x.id for x in partes]))
        return saida

    subdividida = por_voz(atual, plano.selecao, trecho)
    return trocar_vozes(subdividida, lambda parte, v: v.model_copy(
        update={"quialteras": v.quialteras + grupos.get(v.id, [])}))

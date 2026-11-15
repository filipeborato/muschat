"""Cânones próximos e deformação de gestos, com escrita rítmica explícita."""

import random
from fractions import Fraction as F

from muschat import musicir as ir
from muschat.instrumentos import no_registro, tessitura_da_parte
from muschat.plano import Plano
from muschat.procedimentos.comum import Grade, Ids, exigir, por_voz, repartir, trocar_vozes

RAZOES = ((5, 4), (7, 4), (6, 4), (5, 6))


def micropolifonia(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    atual = exigir(atual)
    origem = int(p["parte_origem"]) - 1
    if origem >= len(atual.partes):
        raise ValueError("parte de origem não existe")
    grade, ids = Grade(atual), Ids(atual)
    primeiro, ultimo = grade.trecho(plano.selecao)
    inicio, fim = grade.inicios[primeiro - 1], grade.fins[ultimo - 1]
    fonte = atual.partes[origem].vozes[0]
    eventos = [e for e in fonte.eventos if inicio <= e.inicio < fim]
    if any(e.fim > fim for e in eventos) or any(e.inicio < inicio < e.fim for e in fonte.eventos):
        raise ValueError("evento de origem atravessa a seleção")
    destinos = [(parte.id, v.id) for parte in atual.partes for v in parte.vozes
                if plano.selecao.contem(parte.id, v.id, primeiro)]
    grupos: dict[str, list[ir.Quialtera]] = {}

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        selecionados = {e.id for e in alvo}
        if any(set(q.eventos) & selecionados and not set(q.eventos) <= selecionados for q in voz.quialteras):
            raise ValueError("quiáltera de destino atravessa a seleção")
        grupos[voz.id] = [q for q in voz.quialteras if not set(q.eventos) & selecionados]
        k = destinos.index((parte.id, voz.id))
        atraso = F(int(p["defasagem"]) * k, 16)
        fator = F(16 + rng.randint(-int(p["variacao"]), int(p["variacao"])), 16)
        desvio = round((k / max(len(destinos) - 1, 1) - 0.5) * p["espalhamento"] / 25) * 25
        saida: list[ir.Evento] = []
        cursor = inicio
        for e in eventos:
            a = inicio + atraso + F(round((e.inicio - inicio) * fator * 64), 64)
            b = inicio + atraso + F(round((e.fim - inicio) * fator * 64), 64)
            a, b = max(a, cursor), min(b, fim)
            if a >= fim:
                continue
            if b <= a:
                raise ValueError("micropolifonia: evento curto demais para a grade de 1/64; nenhuma nota foi descartada")
            if a > cursor:
                vazio = ir.Pausa(id=ids.novo(voz.id), inicio=cursor, duracao=a - cursor, proveniencia=e.proveniencia)
                saida += repartir(vazio, cursor, a - cursor, grade, ids)
            campos: dict = {"id": ids.novo(voz.id)}
            if isinstance(e, ir.Nota):
                campos |= {"alturas": [ir.Altura(midicents=no_registro(h.midicents + desvio, tessitura_da_parte(parte)))
                                       for h in e.alturas], "glissando": False,
                           "tecnicas": []}
            copia = e.model_copy(update=campos)
            saida += repartir(copia, a, b - a, grade, ids, sustentar=True)
            cursor = b
        if cursor < fim:
            vazio = ir.Pausa(id=ids.novo(voz.id), inicio=cursor, duracao=fim - cursor,
                            proveniencia=ir.Proveniencia(origem="algoritmo"))
            saida += repartir(vazio, cursor, fim - cursor, grade, ids)
        return saida

    def reescrever(parte: ir.Parte, voz: ir.Voz) -> ir.Voz:
        if (parte.id, voz.id) not in destinos:
            return voz
        alvo = [e for e in voz.eventos if inicio <= e.inicio < fim]
        if any(e.fim > fim for e in alvo) or any(e.inicio < inicio < e.fim for e in voz.eventos):
            raise ValueError("evento de destino atravessa a seleção")
        fora = [e for e in voz.eventos if not inicio <= e.inicio < fim]
        return voz.model_copy(update={"eventos": sorted(fora + trecho(parte, voz, alvo), key=lambda e: e.inicio)})

    nova = trocar_vozes(atual, reescrever)
    return trocar_vozes(nova, lambda parte, v: v.model_copy(update={"quialteras": grupos.get(v.id, v.quialteras)}))


def deformar_ritmo(atual: ir.MusicIR | None, plano: Plano, p: dict[str, float], rng: random.Random) -> ir.MusicIR:
    atual = exigir(atual)
    if p["minimo"] > p["maximo"]:
        raise ValueError("aumentação mínima maior que a máxima")
    grade, ids = Grade(atual), Ids(atual)
    razoes = [r for k, r in enumerate(RAZOES) if int(p["quialteras"]) & (1 << k)]
    grupos: dict[str, list[ir.Quialtera]] = {}

    def trecho(parte: ir.Parte, voz: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        membros = {e for q in voz.quialteras for e in q.eventos}
        novas: dict[str, list[ir.Evento]] = {}
        for gesto in ir.gestos(alvo):
            if rng.random() >= p["proporcao"]:
                continue
            if any(n.id in membros for n in gesto):
                raise ValueError("deformar_ritmo: gesto já contém quiáltera; selecione outro trecho")
            # Divide na barra antes de deformar: nenhuma quiáltera atravessa compassos.
            compassos: dict[int, list[ir.Nota]] = {}
            for nota in gesto:
                compasso = next(k for k, fim in enumerate(grade.fins) if nota.inicio < fim)
                if nota.fim > grade.fins[compasso]:
                    raise ValueError("nota atravessa a barra; divida com ligadura antes de deformar")
                compassos.setdefault(compasso, []).append(nota)
            for notas in compassos.values():
                inicio, fim = notas[0].inicio, notas[-1].fim
                span = fim - inicio
                limite = next(b for b in grade.fins if inicio < b)
                respiracoes = []
                for e in alvo[alvo.index(notas[-1]) + 1:]:
                    if not isinstance(e, ir.Pausa) or e.inicio != fim or e.fim > limite or e.id in membros:
                        break
                    respiracoes.append(e)
                    fim = e.fim
                numerador, denominador = rng.choice(razoes)
                fator = F(denominador, numerador)
                desejado = float(span) * rng.uniform(p["minimo"], p["maximo"])
                # O bloco ocupa denominador unidades binárias; o resto vira respiração fora da quiáltera.
                unidades = [F(1, 2**k) for k in range(7) if F(denominador, 2**k) <= fim - inicio]
                if not unidades:
                    raise ValueError("gesto curto demais para a quiáltera solicitada")
                unidade = min(unidades, key=lambda u: abs(float(u * denominador) - desejado))
                duracao = unidade * denominador
                total_escrito = unidade * numerador
                escala = F(1)
                while span * escala * 2 <= total_escrito:
                    escala *= 2
                while span * escala > total_escrito:
                    escala /= 2
                escritas = [n.duracao * escala for n in notas]
                if any(not ir.figura(d) for d in escritas):
                    raise ValueError("a deformação exigiria figura menor que semifusa")
                cursor = inicio
                saida: list[ir.Evento] = []
                for n, escrita in zip(notas, escritas):
                    saida.append(n.model_copy(update={"inicio": cursor, "duracao": escrita * fator,
                                                     "glissando": False, "ligada": False}))
                    cursor += escrita * fator
                    novas[n.id] = []
                resto = total_escrito - sum(escritas)
                for escrita in ir.figuras(resto) if resto else []:
                    saida.append(ir.Pausa(id=ids.novo(voz.id), inicio=cursor, duracao=escrita * fator,
                                         proveniencia=notas[-1].proveniencia))
                    cursor += escrita * fator
                grupos.setdefault(voz.id, []).append(ir.Quialtera(id=ids.novo(voz.id), numerador=numerador,
                    denominador=denominador, eventos=[e.id for e in saida]))
                if inicio + duracao < fim:
                    pausa = ir.Pausa(id=ids.novo(voz.id), inicio=inicio + duracao, duracao=fim - inicio - duracao,
                                     proveniencia=notas[-1].proveniencia)
                    saida += repartir(pausa, pausa.inicio, pausa.duracao, grade, ids)
                novas[notas[0].id] = saida
                for pausa in respiracoes:
                    novas[pausa.id] = []
        return [n for e in alvo for n in novas.get(e.id, [e])]

    nova = por_voz(atual, plano.selecao, trecho)
    return trocar_vozes(nova, lambda parte, v: v.model_copy(update={"quialteras": v.quialteras + grupos.get(v.id, [])}))

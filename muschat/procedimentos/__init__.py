"""Catálogo de procedimentos Python: funções puras sobre MusicIR, com semente. Nenhum código vem do modelo.

Todo procedimento recebe (composição atual ou None, plano, parâmetros já validados contra as faixas, gerador
aleatório com a semente) e devolve uma MusicIR nova; o orquestrador revalida, confere a seleção e carimba a
proveniência.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass

from muschat import musicir as ir
from muschat.plano import Plano
from muschat.procedimentos.geracao import distribuir, serie_harmonica
from muschat.procedimentos.melodicos import intervalos, inverter, retrogradar, rotacionar, transpor
from muschat.procedimentos.progressivos import (
    aplicar_tecnicas, densidade, dinamica_progressiva, registro_progressivo,
)
from muschat.procedimentos.ritmicos import aumentacao_diminuicao, permutar_duracoes, subdividir


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
    requer_composicao: bool = True


_NIVEIS = "0 pppp, 1 ppp, 2 pp, 3 p, 4 mp, 5 mf, 6 f, 7 ff, 8 fff, 9 ffff"
_RAMPA = "em rampa linear do primeiro ao último compasso da seleção"

CATALOGO: dict[str, Procedimento] = {p.nome: p for p in [
    Procedimento(
        "serie_harmonica",
        "Cria uma peça nova (substitui a atual): alturas dos parciais harmônicos de uma fundamental, distribuídas "
        "entre os instrumentos do plano (padrão: quarteto de cordas) respeitando a tessitura, ritmo sorteado.",
        {
            "fundamental": Faixa(2400, 6000, 3600, "fundamental", unidade="midicents (3600 = Dó 2, 6000 = Dó 4)"),
            "parciais": Faixa(2, 32, 16, "quantos parciais (1..n)"),
            "compassos": Faixa(1, 32, 8, "número de compassos 4/4"),
            "microtom": Faixa(0, 2, 1, "aproximação: 0 semitom, 1 quarto de tom, 2 oitavo de tom"),
            "pausas": Faixa(0, 0.9, 0.15, "proporção de pausas", inteiro=False),
        },
        serie_harmonica,
        requer_composicao=False,
    ),
    Procedimento("transpor", "Transpõe as notas da seleção; o ritmo fica.",
                 {"intervalo": Faixa(-2400, 2400, 0, "intervalo (100 = semitom)", unidade="cents")}, transpor),
    Procedimento("inverter", "Inversão melódica em torno de um eixo, voz por voz; o ritmo fica.",
                 {"eixo": Faixa(0, 12700, 0, "eixo; 0 = primeira altura da seleção em cada voz",
                                unidade="midicents")}, inverter),
    Procedimento("retrogradar", "Retrogradação das alturas da seleção, voz por voz; o ritmo fica.", {}, retrogradar),
    Procedimento("rotacionar", "Rotação da sequência de alturas da seleção, voz por voz; o ritmo fica.",
                 {"passos": Faixa(-16, 16, 1, "quantas notas girar")}, rotacionar),
    Procedimento("intervalos", "Compressão (<1) ou expansão (>1) dos intervalos em relação à primeira altura de "
                 "cada voz, em quartos de tom.",
                 {"fator": Faixa(0.25, 4, 1, "fator intervalar", inteiro=False)}, intervalos),
    Procedimento("aumentacao_diminuicao", "Aumentação (expoente > 0: durações × 2^n, o que passa do trecho é "
                 "cortado) ou diminuição (expoente < 0: o material encolhe e se repete até preencher o trecho).",
                 {"expoente": Faixa(-2, 2, 1, "potência de 2 do fator")}, aumentacao_diminuicao),
    Procedimento("permutar_duracoes", "Permuta as durações dentro de cada compasso da seleção, mantendo as alturas "
                 "e a ordem dos eventos.", {}, permutar_duracoes),
    Procedimento("subdividir", "Subdivide notas da seleção em quiálteras (3, 5, 6, 7) ou em 4 notas iguais, "
                 "repetindo a altura.",
                 {"divisao": Faixa(3, 7, 3, "notas por subdivisão"),
                  "proporcao": Faixa(0, 1, 1, "proporção das notas subdivididas (sorteadas)", inteiro=False)},
                 subdividir),
    Procedimento("densidade", f"Densidade de ataques {_RAMPA}: tira ataques (viram pausas) ou cria ataques "
                 "(pausas viram notas, notas se dividem). Fragmentar = densidade maior e mais pausas.",
                 {"inicial": Faixa(0, 4, 1, "ataques por semínima no primeiro compasso", inteiro=False),
                  "final": Faixa(0, 4, 1, "ataques por semínima no último compasso", inteiro=False)}, densidade),
    Procedimento("registro_progressivo", f"Desloca o registro {_RAMPA}.",
                 {"inicial": Faixa(-2400, 2400, 0, "deslocamento no início", unidade="cents"),
                  "final": Faixa(-2400, 2400, 1200, "deslocamento no fim", unidade="cents")},
                 registro_progressivo),
    Procedimento("dinamica_progressiva", f"Dinâmica {_RAMPA} (uma marca por compasso).",
                 {"inicial": Faixa(0, 9, 2, _NIVEIS), "final": Faixa(0, 9, 7, _NIVEIS)}, dinamica_progressiva),
    Procedimento("aplicar_tecnicas", "Aplica as técnicas listadas em tecnicas no plano (nomes do grimório, "
                 "ex.: harmonico, sul ponticello, pizzicato, staccato) a uma proporção sorteada das notas da seleção.",
                 {"proporcao": Faixa(0, 1, 1, "proporção das notas que recebem as técnicas", inteiro=False)},
                 aplicar_tecnicas),
    Procedimento("distribuir", "Distribui as notas de uma parte de origem entre as partes selecionadas, cada nota "
                 "numa parte cuja tessitura a contém; as partes de destino são reescritas no trecho.",
                 {"parte_origem": Faixa(1, 16, 1, "número da parte de origem (1 = primeira)")}, distribuir),
]}

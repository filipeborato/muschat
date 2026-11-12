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
from muschat.procedimentos.espectrais import eventos_do_espectro
from muschat.procedimentos.geracao import distribuir, orquestrar_material, serie_harmonica
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
    funcao: Funcao | None  # None: só existe como patch aprovado do OpenMusic (muschat.patches_om)
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
    Procedimento(
        "orquestrar_material",
        "Cria uma peça nova a partir das alturas da composição atual (ex.: material espectral importado do "
        "OpenMusic): elas viram o reservatório dos instrumentos do plano, respeitando a tessitura, ritmo sorteado.",
        {
            "compassos": Faixa(1, 32, 8, "número de compassos 4/4"),
            "pausas": Faixa(0, 0.9, 0.15, "proporção de pausas", inteiro=False),
        },
        orquestrar_material,
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
    Procedimento("eventos_do_espectro", "Cria partes instrumentais a partir do dado espectral da composição (ex.: "
                 "análise do OM-SuperVP): em cada quadro, os parciais mais fortes viram alturas distribuídas do agudo "
                 "ao grave entre os instrumentos do plano, na tessitura de cada um; o dado espectral fica.",
                 {"alturas_por_quadro": Faixa(1, 8, 4, "quantos parciais mais fortes por quadro"),
                  "microtom": Faixa(0, 2, 1, "aproximação: 0 semitom, 1 quarto de tom, 2 oitavo de tom")},
                 eventos_do_espectro),
    # --- só no OpenMusic (patches aprovados; o Python não executa) ---
    Procedimento("densidade_caotica", "Só no OpenMusic (Chaos): o mapa logístico de Verhulst dá um valor por "
                 "compasso da seleção, normalizado e usado como densidade de ataques entre mínimo e máximo.",
                 {"x0": Faixa(0.01, 0.99, 0.1, "valor inicial da série (população inicial)", inteiro=False),
                  "turbulencia": Faixa(0, 3, 2.8, "lambda de Verhulst: acima de ~2.57 o processo é caótico",
                                       inteiro=False),
                  "minimo": Faixa(0, 4, 0.5, "ataques por semínima no menor valor da série", inteiro=False),
                  "maximo": Faixa(0, 4, 3, "ataques por semínima no maior valor da série", inteiro=False)},
                 None),
    Procedimento("distribuir_restricoes", "Só no OpenMusic (Situation): cria uma peça nova em que N alturas "
                 "(base + k*passo) são distribuídas entre os instrumentos do plano por um solver de restrições, "
                 "cada uma na tessitura do instrumento, todas diferentes (sem uníssonos). Um acorde por compasso.",
                 {"alturas": Faixa(4, 24, 12, "quantas alturas"),
                  "base": Faixa(2400, 8400, 5500, "altura mais grave", unidade="midicents (5500 = Sol 3)"),
                  "passo": Faixa(50, 1200, 100, "distância entre alturas vizinhas", unidade="cents")},
                 None, requer_composicao=False),
    Procedimento("sintese_aditiva", "Só no OpenMusic (OMChroma + Csound): as notas da seleção controlam uma "
                 "síntese aditiva; cada nota vira parciais harmônicos cuja quantidade cresce do início ao fim. Gera "
                 ".orc/.sco (e áudio, com Csound) e acrescenta uma parte eletrônica (cues e curva, não notas).",
                 {"parciais_inicial": Faixa(1, 32, 2, "parciais por nota no início"),
                  "parciais_final": Faixa(1, 32, 16, "parciais por nota no fim"),
                  "amplitude": Faixa(-40, 0, -18, "amplitude do fundamental", inteiro=False, unidade="dB")},
                 None),
    Procedimento("analise_supervp", "Só no OpenMusic (OM-SuperVP, executável SuperVP): analisa um som e cria uma "
                 "peça nova com o resultado como dado espectral (nunca como notas); use eventos_do_espectro depois.",
                 {"parciais": Faixa(1, 64, 16, "parciais por quadro"),
                  "quadros": Faixa(1, 64, 8, "quantos quadros de análise")},
                 None, requer_composicao=False),
]}

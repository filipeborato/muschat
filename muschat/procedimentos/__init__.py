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
from muschat.procedimentos.espectrais import espectralizar, eventos_do_espectro
from muschat.procedimentos.geracao import distribuir, orquestrar_material, serie_harmonica
from muschat.procedimentos.melodicos import intervalos, inverter, retrogradar, rotacionar, transpor
from muschat.procedimentos.progressivos import (
    aplicar_tecnicas, densidade, dinamica_progressiva, registro_progressivo, tecnicas_por_gesto,
)
from muschat.procedimentos.ritmicos import aumentacao_diminuicao, permutar_duracoes, subdividir
from muschat.procedimentos.contemporaneos import desafinar, substituir_timbre, rarefazer
from muschat.procedimentos.texturas import micropolifonia, deformar_ritmo


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
    funcao: Funcao | None  # None: só existe em motor externo (patch aprovado do OpenMusic, ou o NotaGen)
    requer_composicao: bool = True


_NIVEIS = "0 pppp, 1 ppp, 2 pp, 3 p, 4 mp, 5 mf, 6 f, 7 ff, 8 fff, 9 ffff"
_RAMPA = "em rampa linear do primeiro ao último compasso da seleção"
_FIGURAS = "figura 1/2^n: 0 semibreve, 1 mínima, 2 semínima, 3 colcheia, 4 semicolcheia"
_VERHULST = {"x0": Faixa(0.01, 0.99, 0.1, "valor inicial da série (população inicial)", inteiro=False),
             "turbulencia": Faixa(0, 3, 2.8, "lambda de Verhulst: acima de ~2.57 o processo é caótico", inteiro=False)}

CATALOGO: dict[str, Procedimento] = {p.nome: p for p in [
    Procedimento("desafinar", "Desvio microtonal em rampa ou oscilação; glissandos dentro do gesto, sem mudar ritmo.", {
        "desvio": Faixa(0, 200, 100, "desvio máximo", unidade="cents"),
        "resolucao": Faixa(1, 2, 1, "1 = quarto de tom, 2 = oitavo de tom"),
        "curva": Faixa(0, 1, 0, "0 = rampa, 1 = oscilação completa"),
        "glissandos": Faixa(0, 1, 0.5, "proporção de ligações dentro do gesto", inteiro=False)}, desafinar),
    Procedimento("substituir_timbre", "Troca notas por ruído idiomático, mantendo ritmo e dinâmica. "
                 "tecnicas no plano restringe as opções de ruído.", {
        "inicial": Faixa(0, 1, 0.2, "proporção inicial", inteiro=False),
        "final": Faixa(0, 1, 0.8, "proporção final", inteiro=False),
        "familia_ruido": Faixa(0, 3, 0, "0 = todas, 1 = fricção, 2 = percussivo, 3 = ar")}, substituir_timbre),
    Procedimento("rarefazer", "Remove ataques, prolonga notas com ligaduras, harmônicos naturais ou whistle tones "
                 "tocáveis e dinâmica extrema; prolongamentos respeitam os grupos de quiálteras.", {
        "manter": Faixa(0, 1, 0.3, "proporção de notas mantidas", inteiro=False),
        "harmonicos": Faixa(0, 1, 0.5, "proporção de conversão", inteiro=False),
        "dinamica": Faixa(0, 1, 0, "0 = pppp, 1 = ppp"),
        "acentos": Faixa(0, 0.2, 0.03, "proporção de acentos raros", inteiro=False)}, rarefazer),
    Procedimento("micropolifonia", "Cânone próximo da primeira voz da origem nas vozes selecionadas; "
                 "defasagem, variação de duração e espalhamento microtonal. A cauda é limitada ao trecho.", {
        "parte_origem": Faixa(1, 16, 1, "número da parte de origem"),
        "defasagem": Faixa(0, 8, 1, "atraso por voz", unidade="semicolcheias"),
        "variacao": Faixa(0, 2, 1, "variação máxima da escala temporal em passos de 1/16"),
        "espalhamento": Faixa(0, 1200, 150, "largura total do cluster entre vozes", unidade="cents")}, micropolifonia),
    Procedimento("deformar_ritmo", "Deforma gestos em quiálteras dentro da barra, conservando notas e ordem; "
                 "o espaço restante vira respiração. Fatores são aproximados às figuras que cabem, sem cortar notas.", {
        "proporcao": Faixa(0, 1, 0.6, "proporção de gestos", inteiro=False),
        "quialteras": Faixa(1, 15, 15, "máscara somada: 1 = 5:4, 2 = 7:4, 4 = 6:4, 8 = 5:6"),
        "minimo": Faixa(0.25, 2, 0.5, "fator mínimo desejado", inteiro=False),
        "maximo": Faixa(0.25, 2, 1, "fator máximo desejado", inteiro=False)}, deformar_ritmo),
    Procedimento(
        "serie_harmonica",
        "Cria uma peça nova (substitui a atual): alturas dos parciais harmônicos de uma fundamental, distribuídas "
        "entre os instrumentos do plano (padrão: quarteto de cordas) respeitando a tessitura, ritmo sorteado. "
        "Registros iniciais: violino Sol3–Dó6, viola Dó3–Dó5, cello Dó2–Dó4; saltos de até uma oitava. "
        "Para outro registro, encadeie transpor.",
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
        "OpenMusic): elas viram o reservatório dos instrumentos do plano, respeitando a tessitura, ritmo sorteado. "
        "Registros iniciais: violino Sol3–Dó6, viola Dó3–Dó5, cello Dó2–Dó4; saltos de até uma oitava. "
        "Para outro registro, encadeie transpor.",
        {
            "compassos": Faixa(1, 32, 8, "número de compassos 4/4"),
            "pausas": Faixa(0, 0.9, 0.15, "proporção de pausas", inteiro=False),
        },
        orquestrar_material,
    ),
    Procedimento(
        "espectralizar",
        "Troca as alturas da seleção (de qualquer material: NotaGen, partitura aberta, série harmônica) pelos "
        "parciais de uma fundamental, preservando o contorno de cada voz (sobe onde subia, desce onde descia) e "
        "dando a cada parte uma faixa do espectro, do agudo ao grave, na tessitura dela; o ritmo, as dinâmicas e "
        "as técnicas ficam.",
        {
            "fundamental": Faixa(2400, 6000, 3600, "fundamental", unidade="midicents (3600 = Dó 2, 6000 = Dó 4)"),
            "parciais": Faixa(2, 32, 16, "quantos parciais (1..n)"),
            "microtom": Faixa(0, 2, 1, "aproximação: 0 semitom, 1 quarto de tom, 2 oitavo de tom"),
            "salto_maximo": Faixa(100, 2400, 1200, "maior intervalo entre notas seguidas de uma voz",
                                  unidade="cents"),
        },
        espectralizar,
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
    Procedimento("tecnicas_por_gesto", "Aplica as técnicas listadas em tecnicas no plano (nomes do grimório) por "
                 "gesto inteiro (notas entre pausas): cada gesto sorteado recebe uma técnica que se toca em todas as "
                 "notas dele (instrumento, registro e duração); um rótulo no começo, ord. só na volta. Prefira este "
                 "a aplicar_tecnicas.",
                 {"proporcao": Faixa(0, 1, 0.5, "proporção dos gestos que recebem técnica", inteiro=False)},
                 tecnicas_por_gesto),
    Procedimento("aplicar_tecnicas", "Aplica as técnicas listadas em tecnicas no plano (nomes do grimório, "
                 "ex.: harmonico, sul ponticello, pizzicato, staccato) a uma proporção sorteada das notas da seleção, "
                 "nota a nota (sem olhar o gesto nem o registro).",
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
    # --- só no NotaGen (modelo local opcional; o Python não executa) ---
    Procedimento("gerar_notagen", "Só no NotaGen (modelo local): cria uma peça nova com frases, ritmo e "
                 "articulação de música de câmara, a partir de uma condição período-compositor-instrumentação "
                 "(campo condicao da operação: o texto exato de uma das listadas, a mais próxima do pedido, formação e "
                 "período primeiro). Estilo barroco a romântico, sem técnicas estendidas: para outra estética, "
                 "encadeie depois as transformações do catálogo e diga em hipoteses o que cada uma faz.",
                 {"compassos": Faixa(1, 16, 8, "número de compassos (cada um leva alguns segundos na GPU)")},
                 None, requer_composicao=False),
    # --- só no OpenMusic (patches aprovados; o Python não executa) ---
    Procedimento("densidade_caotica", "Só no OpenMusic (Chaos): o mapa logístico de Verhulst dá um valor por "
                 "compasso da seleção, normalizado e usado como densidade de ataques entre mínimo e máximo.",
                 {**_VERHULST,
                  "minimo": Faixa(0, 4, 0.5, "ataques por semínima no menor valor da série", inteiro=False),
                  "maximo": Faixa(0, 4, 3, "ataques por semínima no maior valor da série", inteiro=False)},
                 None),
    Procedimento("registro_caotico", "Só no OpenMusic (Chaos): a série de Verhulst, um valor por compasso, desloca o "
                 "registro de todas as notas do compasso entre o mínimo e o máximo.",
                 {**_VERHULST,
                  "minimo": Faixa(-2400, 2400, 0, "deslocamento no menor valor da série", unidade="cents"),
                  "maximo": Faixa(-2400, 2400, 1200, "deslocamento no maior valor da série", unidade="cents")}, None),
    Procedimento("dinamica_caotica", "Só no OpenMusic (Chaos): a série de Verhulst, um valor por compasso, escolhe a "
                 "dinâmica do primeiro ataque de cada compasso entre o mínimo e o máximo.",
                 {**_VERHULST, "minimo": Faixa(0, 9, 1, _NIVEIS), "maximo": Faixa(0, 9, 8, _NIVEIS)}, None),
    Procedimento("duracoes_caoticas", "Só no OpenMusic (Chaos): a série de Verhulst, um valor por compasso, escolhe a "
                 "figura com que o compasso é reescrito (mantendo as alturas que ele tinha).",
                 {**_VERHULST, "minimo": Faixa(0, 4, 1, _FIGURAS), "maximo": Faixa(0, 4, 4, _FIGURAS)}, None),
    Procedimento("distribuir_restricoes", "Só no OpenMusic (Situation): cria uma peça nova em que N alturas "
                 "(base + k*passo) são distribuídas entre os instrumentos do plano por um solver de restrições, "
                 "cada uma na tessitura do instrumento, todas diferentes (sem uníssonos). Um acorde por compasso.",
                 {"alturas": Faixa(4, 24, 12, "quantas alturas"),
                  "base": Faixa(2400, 8400, 5500, "altura mais grave; cada instrumento precisa alcançar alturas "
                                "para as suas vagas, então comece onde todos tocam (cordas com violino: Sol 3 = 5500)",
                                unidade="midicents (5500 = Sol 3)"),
                  "passo": Faixa(50, 1200, 100, "distância entre alturas vizinhas", unidade="cents")},
                 None, requer_composicao=False),
    Procedimento("alturas_por_restricoes", "Só no OpenMusic (Cluster Engine): mantém o ritmo da seleção e "
                 "escolhe as alturas com um solver de restrições entre os parciais de uma fundamental, na tessitura "
                 "de cada parte (bom depois do NotaGen: o ritmo dele, alturas espectrais com regras).",
                 {"fundamental": Faixa(2400, 6000, 3600, "fundamental", unidade="midicents (3600 = Dó 2)"),
                  "parciais": Faixa(2, 32, 16, "quantos parciais (1..n)"),
                  "microtom": Faixa(0, 2, 1, "aproximação: 0 semitom, 1 quarto de tom, 2 oitavo de tom"),
                  "salto_maximo": Faixa(100, 2400, 700, "maior intervalo entre notas seguidas de uma voz",
                                        unidade="cents"),
                  "sem_unissono": Faixa(0, 2, 1, "alturas simultâneas diferentes: 0 desligada, 1 preferência, "
                                        "2 obrigatória (pode não ter solução)"),
                  "sem_cruzamento": Faixa(0, 2, 1, "a parte de cima acima da de baixo: 0 desligada, 1 preferência, "
                                          "2 obrigatória (pode não ter solução)"),
                  "notas_comuns": Faixa(0, 1, 0, "1 = preferir repetir a altura anterior (no OM real, ligada congela "
                                        "cada voz numa altura só; use só em trechos curtos)")},
                 None),
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

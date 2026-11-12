# ADR 0002: regras explícitas do roteador de motores

Status: **aceito**.

## Contexto

O compositor escolhe um modo (Automático, Python/Abjad, OpenMusic, Híbrido) e o plano lista operações do catálogo.
A escolha do motor precisa ser descritível num trabalho acadêmico. Não pode haver pontuação de "qualidade" nem troca
silenciosa de motor. O Registro de capacidades (`muschat/registro.py`) diz o que roda nesta máquina e em que estado:
`automatico`, `intercambio_arquivos`, `manual` ou `indisponivel`.

## Decisão

O roteador (`muschat/roteador.py`) aplica, a cada operação do plano, as regras abaixo, nesta ordem. A justificativa
de cada operação é guardada no registro do experimento e mostrada ao compositor.

| # | Regra | O que faz | Exemplo de justificativa |
|---|---|---|---|
| R1 | Adequação | Só motores que declaram o procedimento: o catálogo Python, e os patches aprovados do OpenMusic. | "densidade não existe no OpenMusic" |
| R2 | Disponibilidade | Sai o motor `indisponivel` nesta máquina (OpenMusic não instalado, biblioteca ausente, executável externo faltando). | "OpenMusic indisponível: não encontrado..." |
| R3 | Escolha manual | Modo Python ou OpenMusic é respeitado. Se não der, recusa com o motivo e alternativas, **sem executar outro motor**. Um pedido que nomeia um motor ("use o OpenMusic") vale como escolha manual. | "o motor OpenMusic não executa X... Alternativas: Python/Abjad (automático)" |
| R4 | Híbrido | Cada operação vai para o OpenMusic quando ele a executa aqui; o resto vai para o Python. Sem nenhuma etapa OpenMusic possível, recusa. | "serie_harmonica: OpenMusic, etapa espectral do modo Híbrido" |
| R5 | Intervenção manual | No automático, menos intervenção primeiro: `automatico` < `intercambio_arquivos` < `manual`. É também o mais reprodutível: roda de novo, com a mesma semente, sem ninguém. | "O OpenMusic também faz, mas aqui é assistido; o automático prefere menos intervenção manual (R5)" |
| R6 | Desempate fixo | Python: formato nativo da MusicIR, sem conversão com perda, menos etapas. | — |

Fidelidade do formato de intercâmbio entra em R6: a MusicIR nativa não perde nada, e o texto com midicents do
OpenMusic também não. Preservação de restrições **não** desempata, porque o verificador único roda depois de
qualquer motor. Complexidade e reprodutibilidade entram em R5.

## Consequências

- Com o OpenMusic só em modo assistido (ADR 0001), o Automático sempre escolhe Python para a série harmônica e diz
  isso na justificativa. O OpenMusic entra quando o compositor o escolhe (manual ou Híbrido), ou num procedimento
  que só exista nele.
- A bateria do Experimento D está em `tests/test_motores.py::test_experimento_d_decisao_e_justificativa_registradas`.
- Uma regra nova entra nesta tabela antes de entrar no código.

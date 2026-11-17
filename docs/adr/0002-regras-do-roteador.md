# ADR 0002: regras explícitas do roteador de motores

Status: **aceito**.

Atualização 2026-11-11: a interface habilita OM somente com `MUSCHAT_OPENMUSIC=1`. Instalação existente ou
pedido do modelo não habilitam a integração. O roteador consulta apenas o Registro (`muschat/registry.py`), sem importar `PATCHES`.
Seleção automática exige motor com estado `automatic`; uma rota exclusivamente assistida é recusada com
orientação para escolha explícita. Pedidos que nomeiam OM continuam escolhas explícitas, sujeitos à ativação.

Atualização 2026-11-15: motor opcional **NotaGen** (`MUSCHAT_NOTAGEN=1`). Ele declara só `generate_notagen`, então R1 o escolhe sozinho; roda em estado `automatic` (processo local, sem intervenção). Modo manual Python/OpenMusic com `generate_notagen` no plano recusa com a alternativa (R3); no Híbrido (`hybrid`), a etapa que o OpenMusic não faz vai ao motor automático que a declara (Python ou NotaGen).

## Contexto

Atualização de fluxo: selecionar OpenMusic e pedir uma cadeia que combine um patch aprovado com operações
exclusivas do Python passa explicitamente a Híbrido, com justificativa registrada. O OpenMusic continua
obrigatório e assistido; não se aceita execução inteiramente Python como se fosse OpenMusic. O planejador
recebe o catálogo combinado e corrige uma vez um plano que omita todas as etapas OpenMusic disponíveis.
Esta regra substitui a recusa de R3 apenas para essa cadeia mista.

O compositor escolhe um modo (Automático/`auto`, Python/Abjad/`python`, OpenMusic/`openmusic`, Híbrido/`hybrid`) e o plano lista operações do catálogo.
A escolha do motor precisa ser descritível num trabalho acadêmico. Não pode haver pontuação de "qualidade" nem troca
silenciosa de motor. O Registro de capacidades (`muschat/registry.py`) diz o que roda nesta máquina e em que estado:
`automatic`, `file_exchange`, `manual` ou `unavailable`.

## Decisão

O roteador (`muschat/router.py`) aplica, a cada operação do plano, as regras abaixo, nesta ordem. A justificativa
de cada operação é guardada no registro do experimento e mostrada ao compositor.

| # | Regra | O que faz | Exemplo de justificativa |
|---|---|---|---|
| R1 | Adequação | Só motores que declaram o procedimento: o catálogo Python, NotaGen e os patches aprovados do OpenMusic. | `"density does not exist in OpenMusic"` |
| R2 | Disponibilidade | Sai o motor `unavailable` nesta máquina (OpenMusic não instalado, biblioteca ausente, executável externo faltando). | `"OpenMusic unavailable: not found..."` |
| R3 | Escolha manual | Modo `python` ou `openmusic` é respeitado. Se não der, recusa com o motivo e alternativas, **sem executar outro motor**. Um pedido que nomeia um motor ("use o OpenMusic") vale como escolha manual. | `"engine OpenMusic does not execute X... Alternatives: Python/Abjad (automatic)"` |
| R4 | Híbrido | Cada operação vai para o OpenMusic quando ele a executa aqui; o resto vai para o Python/NotaGen. Sem nenhuma etapa OpenMusic possível, recusa. | `"harmonic_series: OpenMusic, spectral stage of hybrid mode"` |
| R5 | Intervenção manual | No automático, menos intervenção primeiro: `automatic` < `file_exchange` < `manual`. É também o mais reprodutível: roda de novo, com a mesma semente, sem ninguém. | `"OpenMusic also implements it, but is assisted here; automatic prefers less manual intervention (R5)"` |
| R6 | Desempate fixo | Python: formato nativo da `ScoreIR`, sem conversão com perda, menos etapas. | — |

Fidelidade do formato de intercâmbio entra em R6: a `ScoreIR` nativa não perde nada, e o texto com midicents do
OpenMusic também não. Preservação de restrições **não** desempata, porque o verificador único roda depois de
qualquer motor. Complexidade e reprodutibilidade entram em R5.

## Consequências

- Com o OpenMusic só em modo assistido (ADR 0001), o Automático sempre escolhe Python para a série harmônica e diz
  isso na justificativa. O OpenMusic entra quando o compositor o escolhe (manual ou Híbrido), ou num procedimento
  que só exista nele.
- A bateria de testes das regras de roteamento está em `tests/test_engines.py` (`test_routing_decision_and_rationale_recorded`, `test_auto_with_openmusic_prefers_automatic_and_records_r5`).
- Uma regra nova entra nesta tabela antes de entrar no código.

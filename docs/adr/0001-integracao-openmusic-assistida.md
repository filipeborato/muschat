# ADR 0001: integração com o OpenMusic por execução assistida com intercâmbio de arquivos

Status: **aceito em 2026-11-11**. Ida e volta executada no OpenMusic 8.0 com Esquisse 1.2 no Windows,
usando o workspace `.muschat/workspace`. Os 16 midicents coincidiram com o cálculo Python. Experimentos B e C
concluídos com planos fixos, sem LLM; detalhes em `docs/pesquisa/openmusic.md`.

## Contexto

O Muschat precisa usar procedimentos do OpenMusic (começando por `harm-series`, da Esquisse) a partir do chat. As
estratégias consideradas são: **A**, automática (o Muschat dispara a execução); **B**, intercâmbio de arquivos com
execução automática; **C**, assistida (o Muschat prepara a tarefa, o compositor roda o patch no OM e devolve o
resultado).

Evidência (detalhe e fontes em `docs/pesquisa/openmusic.md`):

- Não há execução headless nem por linha de comando documentada. Os patches rodam na GUI.
- O Listener Lisp é interativo, e carregar arquivos é feito pelo menu.
- A pasta `init` do app instalado contém `info.txt` documentando o carregamento na inicialização. O carregamento
  efetivo ainda não foi testado; não fundamenta uma estratégia automática.
- Há código OSC no repositório, mas o comportamento não está documentado. Disparar a avaliação de patches por OSC
  exigiria um patch receptor sempre aberto, e isso não está provado.
- Os formatos de arquivo estão documentados: MusicXML (import e export de voice/poly), MIDI, SDIF e TextFile ligada a
  arquivo externo. As alturas usam midicents, como a MusicIR.

## Decisão

**Estratégia C (assistida), com intercâmbio de arquivos.** A execução é **assistida, não automática.**

- O Muschat gera uma **Tarefa OpenMusic** num diretório isolado e ignorado pelo git. A tarefa contém um arquivo de
  entrada só com dados (números serializados pelo projeto a partir de parâmetros validados, nunca texto livre nem
  Lisp), uma cópia de um **patch aprovado** versionado no projeto e as instruções passo a passo.
- O compositor carrega `serie_harmonica.lisp` pelo menu `Lisp / Load File` do Listener e devolve a saída pela
  interface. Esse script fixo do projeto lê `entrada.txt` ao lado do próprio arquivo e grava `saida.txt` ali.
- O Muschat valida (formato, faixa de valores, número de eventos) e converte para MusicIR com proveniência
  `openmusic`.
- Formato de retorno comprovado: **texto com midicents inteiros**, arredondados pela função `round` do Lisp antes
  da quantização microtonal no Muschat. MusicXML direto do OM continua sem validação experimental.

## Consequências

- O Registro de capacidades declara o OpenMusic como `manual` quando a instalação for detectada, e como
  `indisponivel` quando não for. Nunca como `automatico`.
- O Roteador, no modo Automático, prefere Python para procedimentos que também existem em Python (ex.: série
  harmônica) e diz isso na justificativa.
- A composição precisa pausar e retomar uma etapa pendente (Tarefa sobrevive a rerun e vai no JSON exportado).
- Nenhum código das bibliotecas GPL entra no repositório. Só patches de autoria própria.

## Para evoluir

1. **Semiautomático pela pasta `init`**: se o protótipo provar que o app instalado carrega `init/` na
   inicialização, um arquivo aprovado do projeto poderia avaliar a tarefa ao abrir o OM.
2. **OSC com o OM aberto**: um patch receptor aprovado que, ao receber `/muschat/tarefa <caminho>`, lê a entrada,
   avalia e grava a saída. Isso viraria a estratégia B, com timeout e limite de tamanho no lado do Muschat.
3. Reavaliar a om# só se o OpenMusic deixar de ser o alvo.

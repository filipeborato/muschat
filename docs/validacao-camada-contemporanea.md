# Validação da camada contemporânea

Implementação dos tickets 12–17: desafinar, substituir timbre, rarefazer, micropolifonia,
deformar ritmo e receitas estéticas. Os parâmetros constam no catálogo e aparecem no plano editável.

As seis receitas foram executadas nas quatro peças de referência: 24 composições exportadas
em MusicXML, JSON e LilyPond, com 24 PDFs compilados pelo LilyPond 2.26.0.
Arquivos locais em `.muschat/references/contemporary-layer/` (gerados por `uv run python -m muschat.references contemporary-layer --recipes`).

| Referência | Ataques/semínima antes | Após rarefazer |
| --- | ---: | ---: |
| Quarteto espectral | 2,969 | 1,094 |
| Flauta e clarone | 1,531 | 0,625 |
| Misto adensando | 8,000 | 3,688 |
| Trio subindo | 3,750 | 1,594 |

As métricas completas incluem densidade por frase e cruzamentos; não são notas de qualidade estética.
A receita Lachenmann passou sem `unplayable_techniques` nas quatro referências.

Uma saída real do NotaGen salva localmente (gitignored em `.scratch/`) foi transformada
pela receita Scelsi e exportada como `notagen-scelsi.musicxml`, JSON e PDF.
Não houve nova inferência GPU nessa validação. A escuta do compositor permanece pendente,
assim como a avaliação visual da interface ao vivo.

Os testes cobrem determinismo, seleção, reaplicação, notação, ligaduras, tessitura de instrumentos
importados, destinos vazios, absorção em quiálteras, alongamento e recusa de eventos que sumiriam
na quantização. A revisão separada de especificação e padrões teve seus achados corrigidos.
Os limites de escrita estão em [Receitas estéticas](receitas.md).

## Diagnóstico do arquivo Sem_título.musicxml

O arquivo fornecido contém oito compassos para dois violinos, viola e violoncelo. Apenas o primeiro
violino tem notas (26); as demais partes contêm pausas. Há sul ponticello, sem cabeças de ruído.
A descrição de trio com flauta e multifônicos não corresponde ao arquivo.

O prompt incentivava explicações didáticas e exemplos ilustrativos. A síntese recebia o resumo
do plano, sem contagem de notas ou técnicas efetivamente presentes. Agora recebe esse inventário,
e o resumo indica partes sem notas. A ordem de operações também importa: `gestural_techniques`
substitui timbres anteriores; após `timbral_substitution`, pode apagar o ruído. O planejamento agora
explicita essa precedência. Ambas conservam o ritmo e não preenchem partes silenciosas.

O XML final não comprova se as partes vazias vieram da geração ou da conversão. A bridge do NotaGen
recebe condição período/compositor/formação, quantidade de compassos e semente; ela não recebe
o pedido textual de técnicas estendidas. Essa transformação cabe à cadeia posterior.

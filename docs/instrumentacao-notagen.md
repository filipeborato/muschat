# Instrumentação solicitada e saída do NotaGen

O arquivo relatado `Sem_título (1).musicxml` tinha oito compassos e 41 notas em uma única parte
`Violoncello`. A flauta solicitada não estava presente. O aviso de divergência antes permitia
continuar as transformações e gravar uma versão, tratada pela síntese como sucesso.

A etapa do NotaGen agora recusa uma quantidade de partes diferente da lista de instrumentos
do plano, antes das transformações, inserção em seção e gravação. A versão anterior permanece
intacta; o chat devolve o erro diretamente, sem síntese que possa inventar a parte ausente.

Outro defeito permitia `frullato` no violoncelo: `Violoncello` não era reconhecido, e instrumentos
desconhecidos passavam pela seleção de técnicas. Foram adicionados os nomes importados
Violoncello, Cello, Violin, Flute, Flauto e Double Bass. Instrumentos não identificados agora
não autorizam técnicas pela tabela de idiomatismo.

A condição período/compositor/formação do NotaGen continua sem garantir o conjunto exato.
Não há reorquestração automática de um solo para duo: uma saída incompatível exige outra geração
ou um motor que construa diretamente o conjunto solicitado. Quando a quantidade coincide,
permanece o comportamento existente de atribuir as partes aos instrumentos do plano.

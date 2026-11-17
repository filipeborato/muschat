# Instrumentação solicitada e saída do NotaGen

O arquivo relatado `Sem_título (1).musicxml` tinha oito compassos e 41 notas em uma única parte
`Violoncello`. A flauta solicitada não estava presente. O aviso de divergência antes permitia
continuar as transformações e gravar uma versão, tratada pela síntese como sucesso.

A etapa do NotaGen agora adapta uma quantidade de partes diferente da lista de instrumentos
do plano, antes das transformações, inserção em seção e gravação. Seleciona linhas ativas,
prioriza o instrumento correspondente e a proximidade de registro, reutilizando linhas quando
há menos fontes que destinos. Mantém uma linha monofônica por intérprete (voz superior dos acordes),
ajusta alturas por oitavas ao registro inicial do destino e usa sua clave e tessitura.
Ritmo, pausas e quiálteras são preservados; técnicas incompatíveis e textos instrumentais da
linha são retirados. O resumo documenta o mapeamento e o descarte das vozes não usadas.
Uma fonte sem notas continua recusada, preservando a versão anterior.

Outro defeito permitia `frullato` no violoncelo: `Violoncello` não era reconhecido, e instrumentos
desconhecidos passavam pela seleção de técnicas. Foram adicionados os nomes importados
Violoncello, Cello, Violin, Flute, Flauto e Double Bass. Instrumentos não identificados agora
não autorizam técnicas pela tabela de idiomatismo.

A condição período/compositor/formação do NotaGen continua sem garantir o conjunto exato.
O arranjo automático resolve a formação, mas não cria contraponto independente quando precisa
reutilizar uma linha: a camada contemporânea pode transformar esse material. Quando a quantidade coincide,
permanece o comportamento existente de atribuir as partes aos instrumentos do plano.

Validação local: uma saída real salva do NotaGen foi adaptada de quarteto para flauta e cello,
seguida de técnicas por gesto e substituição tímbrica. O arranjo tem oito compassos, passou pelo
verificador e foi exportado em MusicXML, JSON e PDF compilado pelo LilyPond em
`.muschat/references/flute-cello-duo/`. Não foi feita nova inferência GPU nem avaliação por escuta.

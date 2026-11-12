# Pesquisa: integração com o OpenMusic (Fase 0)

Data da consulta: 2026-11-11. Fontes primárias: repositório e releases do OpenMusic, manual do usuário, repositórios das
bibliotecas, páginas de licença. Marcação usada abaixo:

- **[fonte]**: afirmação tirada da página citada.
- **[máquina]**: verificado nesta máquina (Windows 11 Pro).
- **[hipótese]**: não comprovado; precisa do protótipo ou de mais leitura.

## Estado nesta máquina

### Execução real em 2026-11-11

**Ida e volta comprovada.** O workspace `.muschat/workspace` abriu pela opção `Open previous workspace`.
No Listener: `Lisp / Load File` → `.muschat/om-prototipo/rodar.lisp` → confirmação do carregamento da Esquisse.
O Listener registrou Esquisse v. 1.2 e o carregamento do arquivo, e `saida.txt` foi criado pelo OM.
As ferramentas de interface inicialmente indisponíveis foram substituídas por outra ferramenta de controle.

Entrada: `3600 1 1 1 16`. Saída real:

```text
3600 4800 5502 6000 6386 6702 6969 7200 7404 7586 7751 7902 8041 8169 8288 8400
```

Os 16 valores coincidiram exatamente com `round(3600 + 1200 * log2(n))`, para `n = 1..16`.
O script `patches/serie_harmonica.lisp` foi aprovado no catálogo; as tarefas agora recebem uma cópia local,
também incluída no ZIP. Carregar essa cópia executa a tarefa, resolvendo a entrada a partir do próprio arquivo.

**Experimento B:** tarefa `om-20261111-220208-e043c2`, executada no OM pelo menu Load File.
**Experimento C:** tarefa `om-20261111-220208-663088`, executada da mesma forma. Ambas produziram a saída acima.
Composições pendentes foram serializadas em JSON, restauradas e retomadas com as saídas reais.
B terminou com proveniência OpenMusic e material espectral; C encadeou OpenMusic → Python (orquestração para
dois violinos, viola e violoncelo) → Abjad/LilyPond. B gerou dois PDFs (grade e parte do material); C gerou
cinco PDFs (grade e quatro partes), além dos SVGs da grade. Todos os PDFs tiveram cabeçalho `%PDF` verificado.
Os sete PDFs foram abertos e renderizados (uma página cada); grade do quarteto e parte do violoncelo
inspecionadas visualmente, com notação legível e sem cortes.

Artefatos locais: `.muschat/experimentos-reais/{B,C}/`, incluindo entrada, patch, saída e partituras;
JSONs pendentes e finais em `.muschat/experimentos-reais/`. Entrada e saída de referência também estão em
`docs/pesquisa/evidencias-openmusic/`. Planos e semente 7 foram fixados para isolar a integração: o planejamento
pelo Gemini e o fluxo de upload do Streamlit não foram exercitados nesses experimentos.

ADR 0001 aceito. Estratégia comprovada: **assistida por arquivos**. A pasta `init` foi avaliada por sua
documentação instalada, mas sem teste de execução; permanece uma possibilidade de evolução, não um modo
automático disponível. MusicXML exportado diretamente do OM também não foi testado.

### Bibliotecas Chaos, Situation e OMChroma: execução real em 2026-11-12

Instaladas em `~/OM/Libraries`: Chaos 1.5, Situation 3.1, Clouds 1.1, OM2Csound 2.4, OMChroma 5.2 (releases oficiais
do `openmusic-project`). Csound 7.0 instalado no Windows. Cada patch do projeto (`patches/*.lisp`) foi carregado pelo
Listener (`Lisp › Load File`), com plano fixo e semente 7, sem o Gemini; as saídas foram importadas pelo `retomar`.

- **Chaos (`densidade_caotica.lisp`, `alea::verhulst`)**: entrada `0.1 2.8 3`; saída
  `0.1000000000 0.3520000000 0.9906688000 1.0165523604`, igual à fórmula documentada em `orbitals.lisp`. Importada
  sobre um quarteto de 4 compassos (mínimo 0,5, máximo 3 ataques por semínima): 2, 5, 12 e 12 ataques por compasso em
  cada parte, eventos alterados com proveniência `openmusic`.
- **Situation (`distribuir_restricoes.lisp`, `csolver` + `variable-domains` + `generic-cnstr`)**: primeira tentativa
  falhou com `Arithmetic error in >= ...`, gravada em `erro.txt` pelo próprio patch: `generic-cnstr` já devolve a
  lista de restrições e não deve ser embrulhada em outra lista. Corrigida, a saída foi
  `6500 6400 5800 6600 6300 5500 5700 6100 5600 5900 6000 6200`: 12 alturas diferentes, cada uma na tessitura do seu
  instrumento, aceitas pelo verificador. **Um problema sem solução (violinos com 6 vagas e 5 alturas ao alcance)
  travou o OpenMusic** na busca exaustiva; o OM foi fechado à mão. Por isso o Muschat decide a existência antes pela
  condição de Hall (necessária e suficiente para vagas -> alturas diferentes) e não envia problema impossível.
- **OMChroma (`sintese_aditiva.lisp`, `om-make-array 'add-1` + `synthesize :run nil`)**: 159 parciais de um quarteto
  de 2 compassos; o OMChroma escreveu `muschat.orc` (instrumento `add-1`: `poscil` com envelope por tabela) e
  `muschat.sco` (linhas `i1` com início, duração, amplitude em dB e frequência enviadas). O Muschat renderizou com o
  Csound 7.0: `muschat.wav`, mono 44,1 kHz, 8,0 s, pico 0,30. A composição ganhou a parte eletrônica (cues e curva de
  parciais; nenhuma nota).
- **Clouds**: instalada, sem patch (restrições definidas por caixas visuais, sem chamada por script).
- **OM-SuperVP**: não instalada; o executável SuperVP depende de assinatura do IRCAM Forum. Detecção, importação de
  dado espectral e `eventos_do_espectro` testados com fakes; nenhum patch validado.

Artefatos locais: `.muschat/experimentos-reais/libs/`.

### Registro da retomada antes da execução

- **[máquina]** OpenMusic 8.0 encontrado em `C:/Program Files/OpenMusic 8.0`; Esquisse 1.2 em
  `C:/Users/filipe/OM/Libraries/Esquisse 1.2`. O registro de ausência abaixo é anterior à instalação.
- **[máquina]** O código instalado confirma os menus `Midics = 1` e `Chord = 1` de `harm-series`.
- **[máquina]** `init/info.txt` do aplicativo instalado declara o carregamento automático de arquivos Lisp.
  O funcionamento real ainda não foi testado; nenhum arquivo foi colocado nessa pasta nesta retomada.
- O controle da interface retornou `Computer Use native pipe is unavailable`, também pelo fallback CUA.
  Nenhum clique foi enviado e o estado do workspace não pôde ser observado.
- Carregador local preparado em `.muschat/om-prototipo/rodar.lisp` (ignorado pelo git), com entrada
  `3600 1 1 1 16`. Os caminhos são resolvidos a partir do próprio carregador, sem o temporário do Claude.
  Ele carrega `patches/serie_harmonica.lisp` e deve gravar `saida.txt` ao lado de `entrada.txt`.
- **Execução real pendente.** Comparar a saída com `round(3600 + 1200 * log2(n))`, para `n` de 1 a 16,
  antes da quantização microtonal. Os valores esperados em Python não comprovam execução no OpenMusic.
  O ADR permanece proposto; o patch ainda não foi liberado no catálogo. Experimentos B e C pendentes.

### Registro anterior à instalação

- **[máquina]** O OpenMusic **não está instalado** (nada em `C:\Program Files`, `C:\Program Files (x86)` nem em
  `Documents`). Também não há LilyPond, Csound nem SuperVP no PATH. O protótipo de ida e volta depende de o usuário
  instalar o OpenMusic e a Esquisse (ver "Protótipo").

## Os 10 pontos

### 1. Versões e plataformas

- **[fonte]** A última release é a **OM 8.0** (tag `v8.0`, marcada "Latest", publicada em 30 de março; a página lida
  não mostra o ano), com binários para "MacOS: 64bits - ARM and Intel", "WINDOWS: 64 bits", "LINUX: 64 bits RPM and
  DEB packages, tar-ball". A estável anterior é a 7.7. — <https://github.com/openmusic-project/openmusic/releases>
- **[fonte]** Desde a 7.4 o Windows é só 64 bits, e "Since january 2017 OM is a 64-bit application. The previous
  32-bit version is no longer maintained." — <https://openmusic-project.github.io/openmusic/download>
- **[fonte]** As páginas do projeto têm links velhos para as releases (`openmusic-project/releases`, `OM6/releases`).
  O endereço que vale é `openmusic-project/openmusic/releases`.
- Alvo: **OM 8.0 para Windows 64 bits**, nativo. Rodar via WSL/Linux não traz vantagem de automação documentada
  (ver ponto 3) e acrescentaria uma camada gráfica (WSLg). **[hipótese]**

### 2. Como os patches rodam

- **[fonte]** Os patches são avaliados na interface gráfica (avaliar uma caixa, ou menus Import/Export no editor).
  O manual descreve a avaliação sempre a partir da GUI. — <https://openmusic-project.github.io/openmusic/doc/om-manual/OM-User-Manual>

### 3. Automação possível

- **Headless / linha de comando: não documentado.** Nenhuma página do projeto, das releases ou do manual menciona
  execução sem GUI ou por linha de comando. A única pista é a nota da 7.7, "Added methods for pathname and string for
  sound processing methods (for batch usage)", que fala de processamento em lote **dentro** do OM. **[fonte]**
- **Listener Lisp: existe, mas é interativo.** "The Listener window is the main interface with the Lisp under OM.
  It can be used for evaluating Lisp expressions". Para carregar código: menu `Lisp/Load File`, à mão. —
  <https://openmusic-project.github.io/openmusic/doc/om-manual/LispListener> **[fonte]**
- **Arquivo carregado na inicialização: não comprovado para o aplicativo distribuído.** A página de desenvolvimento
  diz que, na árvore de código-fonte, "init: Any file in this folder is loaded at OM startup" —
  <https://openmusic-project.github.io/openmusic/dev>. A pasta `OPENMUSIC/init` existe no repositório, mas o código
  que a carrega não foi encontrado, e o manual não cita esse mecanismo para o app instalado. **[hipótese]**
- **Bibliotecas na inicialização: sim.** As Preferências têm a aba "Libraries: libraries to be loaded automatically
  when starting a session", e as preferências ficam em `preferences.lisp`, no Workspace. —
  <https://openmusic-project.github.io/openmusic/doc/om-manual/Preferences> **[fonte]**
- **OSC: o código existe, mas o comportamento não está documentado.** Existe
  `OPENMUSIC/code/api/externals/OSC/` (`om-osc.lisp`, `cl-osc/`) no repositório; o manual só cita a aba de
  Preferências "MIDI / OSC". Relatos do fórum do IRCAM usam caixas `osc-send`/`osc-receive` com o OM aberto
  (<https://discussion.forum.ircam.fr/t/accumulate-multiple-messages-from-osc/1348>). Uma via "OSC dispara a
  avaliação de um patch" com o OM aberto é plausível, mas exigiria um patch receptor sempre aberto e não está provada.
  **[hipótese]**

### 4. Comunicação externa

- O que está documentado são **arquivos**: MusicXML, MIDI, SDIF, TextFile (pontos 5 e 6). Pela rede, só OSC
  (ponto 3, sem prova). Não há API HTTP nem Python. **[fonte]**
- **[fonte]** A caixa **TextFile** pode estar "attached to an external file of the computer" e fica em
  `Classes / Basic Tools / Text / TEXTFILE`. — <https://openmusic-project.github.io/openmusic/doc/om-manual/textfile>

### 5. MusicXML

- **Exportação: sim**, para `voice` e `poly` (caixa `export-musicxml` ou `File / Export` no editor). "the privileged
  and recommended format in OM to import/export voice and poly objects". —
  <https://openmusic-project.github.io/openmusic/doc/om-manual/ImportExport>,
  <https://openmusic-project.github.io/openmusic/doc/om-manual/Export> **[fonte]**
- **Importação: sim.** "MusicXML files can be imported and loaded into voice or poly objects ... using the
  import-musicxml box"; a saída é sempre um POLY. — <https://openmusic-project.github.io/openmusic/doc/om-manual/Import>
  **[fonte]**
- Não está documentado como microtons, técnicas e quiálteras aninhadas sobrevivem à ida e volta. **[hipótese]**

### 6. MIDI e SDIF

- **MIDI:** a caixa `MIDIFile` lê arquivos; `save-as-midi` grava "All Musical and MIDI objects (including maquettes)".
  "Voice or poly objects can also be stored as MIDI, but most rhythmic information will be lost." —
  <https://openmusic-project.github.io/openmusic/doc/om-manual/MIDIFile>,
  <https://openmusic-project.github.io/openmusic/doc/om-manual/ImportExport> **[fonte]**
- **SDIF:** ferramentas nos menus `Classes`/`Functions` → `SDIF`, e um projeto `sdif` dentro do código do OM; no
  Linux, "the SDIF ... package [is] no longer needed, as these are now installed together with the rest of the
  application". — <https://openmusic-project.github.io/openmusic/doc/om-manual/SDIF> **[fonte]**
- **Midicents:** confirmado no código da Esquisse ("<fund> is entered in midicents"; uso de `mc->f`/`f->mc`;
  `make-instance 'chord :lmidic`). A página de midicents do manual não foi encontrada (404). **[fonte]** A MusicIR usa
  midicents, então as alturas passam sem conversão.

### 7. Execução de Common Lisp

- **[fonte]** O aplicativo distribuído inclui um interpretador Lisp ("an up-to-date released version on OM (which
  includes a Lisp interpreter)") e o Listener avalia expressões. — <https://github.com/openmusic-project/openmusic>
- Regra do projeto (spec): o Muschat **nunca** gera Lisp para o OM executar; só patches aprovados, de autoria do projeto.

### 8. Dependência de LispWorks

- **[fonte]** Para compilar a partir do código, é preciso LispWorks: "Using OM sources therefore requires owning a
  Lisp compiler"; a OM 7 é desenvolvida com LispWorks 8.0.1 e não há edição gratuita da LW 8. —
  <https://openmusic-project.github.io/openmusic/dev>
- **[fonte]** Para quem só usa o binário, o runtime LispWorks não cobra licença do usuário final: "You do not pay
  runtime license fees" (a licença de desenvolvimento é de quem entrega o app, o IRCAM). —
  <https://www.lispworks.com/products/runtimes.html>
- Consequência: o usuário instala o **binário** e não precisa de LispWorks.

### 9. GPL e distribuição

- **[fonte]** "OpenMusic is a free software distributed under the GPLv3 license." —
  <https://github.com/openmusic-project/openmusic>
- O Muschat **não** inclui nem modifica código do OpenMusic. Fala com ele por arquivos de dados e por patches de
  autoria própria, que só *chamam* funções instaladas pelo usuário. Ver o relatório de licenças abaixo.

### 10. Dependências das bibliotecas

Todas ficam em <https://github.com/openmusic-project> e são **baixadas à parte** (a página de bibliotecas não diz
que alguma vem junto com o OM): "Most OpenMusic libraries can be found and downloaded from
https://github.com/openmusic-project/"; a pasta padrão é `~/OM/Libraries/` (OM 6.13+). —
<https://openmusic-project.github.io/libraries> **[fonte]**

| Biblioteca | O que é (repositório) | Depende de | Estado esperado |
|---|---|---|---|
| Esquisse | "Spectral music / intervallic manipulation tools" | só o OM | candidata ao protótipo |
| Chaos | "Dynamic systems library" | só o OM | `indisponivel` até ser instalada |
| Situation | "Constraint programming in OpenMusic" | só o OM | idem |
| Clouds | "Library for Musical Constraints" | só o OM | idem |
| OM2Csound | geração de partituras/orquestras Csound | **Csound** (executável) | `indisponivel` sem Csound |
| OMChroma | controle de síntese em alto nível | **OM2Csound + Csound** | `indisponivel` sem os dois |
| OM-SuperVP | controle do vocoder de fase SuperVP | **executável SuperVP** (IRCAM Forum) | `indisponivel` sem SuperVP |

- **[fonte]** OM-SuperVP: "This library does NOT include SuperVP sources or binaries, which must be installed
  separately", com o download em <https://forum.ircam.fr/projects/detail/analysissynthesis-command-line-tools/>.
- **[fonte]** Procedimentos espectrais da Esquisse (`sources/esquisse.lisp`): `harm-series (fund numer denom begin
  end unit type)`, `nth-harm (fund nth unit type)`, `freq-mod (carrier modul index unit type)`,
  `ring-mod (ch1 ch2 unit type)`, `fshift`, `virt-fund`. O argumento `unit` escolhe "midicents, ('midic'), or in hertz
  ('freq')". **`harm-series` é o procedimento do protótipo.**

### om# (só como referência)

- Também é GPL e roda sobre LispWorks. Não foi encontrada documentação de execução headless. Um relato do fórum diz
  que o OSC funcionou na OM 7.0 e falhou na om# 1.7
  (<https://discussion.forum.ircam.fr/t/om-sharp-and-osc/35792>). A binding `o.OM` (odot/OSC) avalia expressões
  odot recebidas por OSC (<https://github.com/cac-t-u-s/o.om>), mas é da om#, não do OpenMusic. **Não muda o
  quadro**: o alvo continua sendo o OpenMusic.

## Relatório de licenças

| Componente | Licença | Fonte | Pode entrar no repositório? |
|---|---|---|---|
| OpenMusic | GPL-3.0 | README do repositório | **Não.** O usuário instala. |
| Esquisse, Chaos, Situation, Clouds, OMChroma, OM2Csound, OM-SuperVP (wrapper) | GPL-3.0 (LICENSE da Esquisse lido; as demais pela listagem da organização no GitHub) | github.com/openmusic-project | **Não.** O usuário instala. |
| LispWorks (runtime embutido no OM) | comercial; runtime sem taxa para o usuário final | lispworks.com/products/runtimes.html | Não se aplica (vem dentro do OM). |
| SuperVP (executável) | proprietário do IRCAM, contrato do Forum: uso não comercial, cópia proibida | forum.ircam.fr/legal/contrat-de-licence-forum-ircam/ | **Nunca.** Download pessoal pelo Forum. Assinatura exigida: não confirmado. |
| Csound | LGPL-2.1 | README de csound/csound | Não. Executável externo instalado pelo usuário. |
| LilyPond | GPL-3.0 | lilypond.org/gpl.html | Não. Executável externo chamado como processo. |
| **Abjad** | **GPL-3.0** (LICENSE do repositório; README "Abjad 3.31", exige Python 3.12+ e **LilyPond 2.25.26+**) | github.com/Abjad/abjad | **Aprovado pelo usuário (2026-11-11).** Entra como dependência (`uv add abjad`), nunca como código copiado. |
| Patches `.omp` do Muschat | autoria do projeto | — | **Sim.** Só chamam funções das bibliotecas; nada de código GPL copiado. |

**Divergência com a spec:** a spec supunha que o Abjad fosse MIT. **Ele é GPL-3.0.** Importar o Abjad em Python
torna o Muschat, se for distribuído, um trabalho combinado sujeito à GPL-3.0. Para o projeto da disciplina isso é
compatível se o Muschat for publicado sob GPL-3.0 (ou não for redistribuído). **Decisão (2026-11-11): o usuário
aceitou a GPL-3.0 e o Abjad entrou no ticket 05.** Em testes, o LilyPond 2.26.0 (estável) atende ao mínimo de 2.25.26.

## Roteiro original do protótipo (registro anterior à instalação)

**Não executado.** O OpenMusic não está instalado nesta máquina, e o aceite pede execução real. Nenhum resultado
abaixo é inventado. Roteiro proposto, para o usuário rodar e registrar:

1. Instalar a **OM 8.0 para Windows 64 bits** (<https://github.com/openmusic-project/openmusic/releases>) e a
   **Esquisse** (<https://github.com/openmusic-project/Esquisse/releases/latest>) em `~/OM/Libraries/`. Anotar caminho
   e versão.
2. Muschat → **entrada**: um arquivo de texto só com números (ex.: `entrada.txt` com `4800 1 1 1 16`, que são
   fundamental em midicents, numer, denom, begin, end), gerado pelo projeto a partir de parâmetros validados.
3. **Patch do projeto** (montado à mão no OM e salvo como `.omp` versionado): `TextFile` ligado a `entrada.txt` →
   leitura dos números → `harm-series` com `unit = midic` → `chord`/`chord-seq` → duas saídas:
   (a) `TextFile` gravando a lista de midicents em `saida.txt`; (b) `export-musicxml` de um `poly`/`voice`.
4. Avaliar o patch (registrar o que foi clicado) e trazer `saida.txt` e `saida.musicxml` para o Muschat.
5. Muschat lê a saída, valida (formato, faixa 0–12700, número de eventos = `end - begin + 1`) e compara com a série
   harmônica calculada em Python. Registrar o resultado aqui.

Perguntas que o protótipo responde: (a) a TextFile lê/grava números simples sem sintaxe Lisp livre?
(b) O MusicXML exportado preserva quartos de tom? (c) A pasta `init` do app instalado carrega arquivos na
inicialização (para uma evolução semiautomática)?

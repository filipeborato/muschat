# Muschat

### Receitas estéticas

As [receitas documentadas](docs/receitas.md) são cadeias de transformações ajustáveis para material novo
do NotaGen ou composições existentes. São pontos de partida por referência estética, não imitações de
compositores. O plano registra a receita e sua hipótese; os parâmetros continuam editáveis na interface.
A tabela em `muschat/recipes.py` alimenta o prompt e gera a documentação com `uv run python -m muschat.recipes`.
Scelsi explora desvios e glissandos; Lachenmann, ruído instrumental; Sciarrino, silêncio e harmônicos;
Ligeti, cânones próximos; espectral, parciais; ritmo elástico, quiálteras e respirações.

**Alturas temperadas por padrão.** Alturas novas ou transformadas ficam em semitons; microtons só entram quando o
pedido os menciona explicitamente (microtons, quartos/oitavos de tom, ou desvio em cents fora de múltiplos de 100).
Pedir música espectral, técnicas estendidas ou uma receita não basta. Material microtonal já existente fora da edição
é preservado, e experimentos salvos antes dessa regra são reaplicados com o comportamento registrado.
Gere as 24 comparações sobre as quatro peças de referência com
`uv run python -m muschat.references contemporary-layer --recipes` (MusicXML, JSON, métricas e PDF com LilyPond).

A proof-of-concept chat assistant for composers and performers working with **contemporary concert music, electroacoustic music and extended techniques**.

Generic chatbots tend to answer shallowly on this niche: they invent references and are vague about how to actually *write* a multiphonic, a *sul ponticello* passage or a live-electronics setup. Muschat is a focused alternative: it stays on its domain, answers with practical performance and notation detail, admits uncertainty instead of making things up, and politely declines anything off-topic.

Built with [Streamlit](https://streamlit.io) and Google Gemini (`google-genai`).

## MVP status

| Feature | Status |
|---|---|
| Chat UI with conversation memory (follow-ups like "and on the cello?") | ✅ |
| Restricted scope (off-topic questions are declined) | ✅ |
| Friendly errors: missing/invalid key, rate limit (429), server and network failures | ✅ |
| Automatic retry with backoff on transient API errors, and a timeout on every request | ✅ |
| "Nova conversa" button to reset the screen and the model's context | ✅ |
| Chain-of-Thought: hidden analysis step (scope, plan, multilingual search queries) | ✅ |
| Hybrid RAG (Gemini embeddings + BM25, RRF) over a local corpus, with `[source, location]` citations | ✅ |
| Reasoning panel under each answer: the analysis plus every retrieved passage (source, location, score, dense/lexical) | ✅ |
| Authorship from the corpus metadata: works and examples are never credited to another composer | ✅ |
| Sheet music: real excerpts from the corpus, or an illustrative example generated on demand (JSON → MusicXML → Verovio SVG, offline) with `.musicxml` download | ✅ |
| Prompt-injection defence: user text, analysis and corpus passages are delimited and treated as data | ✅ |
| Session composition: typed plan → Python procedures on MusicIR → versions with undo, compare and re-apply | ✅ |
| Engraving with Abjad + LilyPond (score and parts as PDF), Verovio fallback | ✅ (LilyPond optional) |
| Exports: MusicXML, MIDI, LilyPond/PDF/SVG, composition JSON (re-importable), experiment JSON, one `.zip` | ✅ |
| Descriptive metrics and technical inspection panel per version | ✅ |
| OpenMusic integration (assisted, file exchange, opt-in) | 🧪 experimental, validated on Windows only |

The UI is in Portuguese.

## How it works

Each question goes through a short chain, all in `muschat/reasoning.py`:

1. **Analysis**: a separate, hidden Gemini call returns schema-validated JSON: in/out of scope,
   instruments, techniques, a step-by-step plan, search queries (Italian term + score abbreviation +
   Portuguese term) and whether notation is needed. Off-topic questions stop here, costing one call.
2. **Retrieval**: each query runs a dense search (Gemini embeddings) and a lexical BM25 search
   (which catches abbreviations such as `s.p.e.` or `c.l.t.`), merged with reciprocal rank fusion and
   boosted for the instrument family. Bar cards bring their work card along.
3. **Notation (only when asked)**: real MusicXML from the retrieved passages, or a short example the model
   writes as JSON, converted to MusicIR (the internal score model: midicents, exact fractions, per-event
   provenance) and then by a deterministic builder into MusicXML (grimório techniques: `x`/diamond
   noteheads, Italian indications, quarter tones, tremolo, glissando). Verovio renders it to SVG locally.
   **Composition requests** ("crie oito compassos para quarteto...", "agora só o violoncelo") take a different
   path (`muschat/orchestrator.py`): one more Gemini call returns a typed composition plan that may only name
   procedures from the Python catalog (`muschat/procedures/`) with parameters inside their ranges, plus a
   selection (parts, voices, bars). Invalid plans are refused before anything runs; the model never writes code.
   The procedure runs on `ScoreIR` (`muschat/score_ir.py`) with a seed, and the orchestrator rejects the result if
   anything outside the selection changed. A single constraint checker (`muschat/constraints.py`: range, forbidden
   interval, no unison, maximum density) runs after every operation and its violations are reported, not hidden. Subjective requests ("mais fragmentado") come
   back as explicit operational hypotheses with numbers, which the sidebar lets you adjust and re-apply without
   calling the model. The result becomes a new version of the session composition, engraved with Abjad +
   LilyPond (score and parts as PDF, `.ly` download) when LilyPond is installed.
4. **Export**: every version can be downloaded as MusicXML, MIDI, or a `.zip` with what was really produced
   for it (see [Exports](#exports)); the composition JSON loads back into a new session.
5. **Answer**: the chat receives only the question; the analysis, passages and score list go into that
   turn's system instruction, so the conversation history stays small (questions and answers only).

## Quick start

Requirements: Python 3.13+, [uv](https://docs.astral.sh/uv/) and a Gemini API key ([get one here](https://aistudio.google.com/apikey)).
The standard Python composition workflow does **not** require OpenMusic, its libraries, LispWorks, Csound,
SuperVP or an OpenMusic workspace. `uv sync` does not install these external applications.

| What | Installed by | Needed for |
|---|---|---|
| Streamlit, google-genai, Abjad, Verovio, numpy, pypdf, rank-bm25, pydantic | `uv sync` | everything: chat, composition, MusicXML/MIDI/JSON export, Verovio scores |
| [LilyPond](https://lilypond.org) ≥ 2.25.26 | you, optional | engraved PDF/SVG of score and parts |
| [OpenMusic 8.0](https://github.com/openmusic-project/openmusic/releases) + Esquisse | you, optional | only the experimental OpenMusic/Hybrid modes (appendix) |

```bash
git clone <repo-url> muschat
cd muschat
uv sync                      # creates .venv and installs dependencies
cp .env.example .env         # then put your key in .env
uv run python -m muschat.rag.ingest   # builds index/ from corpus/ (optional, see below)
uv run streamlit run app.py  # opens http://localhost:8501
```

Optional, for engraved scores (grade + one PDF per instrument): install [LilyPond](https://lilypond.org) 2.25.26 or
newer (2.26 works) and put it on `PATH`, or set `LILYPOND=<path to lilypond.exe>` in `.env`. Without it, composition
scores are rendered by Verovio from MusicXML and the app says what is lost. On Windows the app also finds an
unzipped release in `%LOCALAPPDATA%\Programs\lilypond-*`.

### Experimental OpenMusic integration (optional)

Step-by-step setup is in [Appendix: OpenMusic experiments B and C](#appendix-openmusic-experiments-b-and-c).

This is a research integration with manual desktop steps, outside the standard installation workflow.
Set `MUSCHAT_OPENMUSIC=1` in `.env` or Streamlit secrets to enable discovery and the OpenMusic/Hybrid modes.
By default, the app does not scan OpenMusic or its libraries and only offers Automatic/Python modes.
Python composition works without it. No GUI automation is provided by Muschat.

To reproduce the optional OpenMusic experiments, install [OpenMusic 8.0](https://github.com/openmusic-project/openmusic/releases)
and the libraries you use in `~/OM/Libraries/` (the spectral procedure needs
[Esquisse](https://github.com/openmusic-project/Esquisse/releases)). The app finds `C:\Program Files\OpenMusic*`
or the path in `OPENMUSIC`. OpenMusic runs **assisted** (see `docs/adr/0001-integracao-openmusic-assistida.md`):
Muschat prepares a task folder under `.muschat/openmusic/` with the input numbers and step-by-step instructions, you
run the approved patch in OpenMusic and upload `saida.txt`, and the composition resumes from there.

The [official OM 8.0 release](https://github.com/openmusic-project/openmusic/releases/tag/v8.0) advertises Linux
RPM/DEB/tarball packages as well as Windows and macOS builds. **Muschat's OpenMusic integration has only been
validated on Windows**; Linux/macOS adapter behavior and headless execution have not been validated.

### NotaGen: local composition model (optional)

[NotaGen](https://github.com/ElectricAlexis/NotaGen) (MIT) is a symbolic music model trained on classical scores.
When enabled, the plan can start a piece with **`generate_notagen`**: NotaGen-X writes phrases, rhythm and articulation
for chamber music from a *period-composer-instrumentation* condition (112 combinations, e.g. `Romantic_Ravel,
Maurice_Chamber`), the result comes back as MusicXML and becomes a Version (provenance `external`, author
`NotaGen-X`). Gemini only plans: it picks the condition and the number of bars. No tokens are spent on the notes.

**What it does not do.** Its style is Baroque to Romantic: no extended techniques, no microtones. That gestural
language is still useful for a lot of contemporary music, and the plan can chain the catalog transformations after
it (transpose, density, register, dynamics, techniques...) to move the material elsewhere.

The sidebar **NotaGen — modelo local** lets you enable it and configure the Python executable and repository
directory for the current session. Installation status updates immediately. Select **NotaGen + Python** to require
local model generation followed by Python transformations; a plan that omits NotaGen is rejected, not silently
replaced with harmonic-series generation. CUDA is checked when generation runs. These UI settings are not saved
to `.env`; use the environment configuration below for defaults across sessions.

**Off by default.** Without it Muschat works exactly as before; with `MUSCHAT_NOTAGEN=1` but something missing, the
sidebar (*Motores e bibliotecas*) says what is missing and the plan never offers it.

Requirements: an NVIDIA GPU with **8 GB** or more (tested on an RTX 4060 8 GB, Windows 11), ~10 GB of disk.
NotaGen needs its own Python environment (Python 3.10, torch 2.3, numpy 1.x), incompatible with Muschat's, so it
runs as a separate process and is never a dependency of this project. Install it under `.muschat/notagen/`
(gitignored), with `uv`:

```powershell
git clone --depth 1 https://github.com/ElectricAlexis/NotaGen.git .muschat/notagen/NotaGen
uv venv --python 3.10 .muschat/notagen/venv
uv pip install --python .muschat/notagen/venv/Scripts/python.exe torch==2.3.0 --index-url https://download.pytorch.org/whl/cu121
uv pip install --python .muschat/notagen/venv/Scripts/python.exe transformers==4.40.0 numpy==1.26.4 abctoolkit==0.0.6 samplings==0.1.7 pyparsing==3.2.1 accelerate music21
curl -L -o .muschat/notagen/NotaGen/gradio/weights_notagenx_p_size_16_p_length_1024_p_layers_20_h_size_1280.pth https://huggingface.co/ElectricAlexis/NotaGen/resolve/main/weights_notagenx_p_size_16_p_length_1024_p_layers_20_h_size_1280.pth
```

Then in `.env`:

```dotenv
MUSCHAT_NOTAGEN=1
# only if you installed elsewhere (defaults shown; on Linux/macOS the python is venv/bin/python)
# NOTAGEN_PYTHON=.muschat/notagen/venv/Scripts/python.exe
# NOTAGEN_DIR=.muschat/notagen/NotaGen
```

Set `MUSCHAT_NOTAGEN=0` (or remove it) to turn it off. Each generation loads the model (~6 GB of weights) and
stops after the requested complete measures (1 to 16), instead of generating a whole piece before trimming it.
An eight-bar local test completed in about 33 seconds; time varies with instrumentation and hardware. Reapplying a
Version reuses the stored MusicXML unless you change the number of bars. Ask, for example: *"oito compassos de
quarteto de cordas à maneira de Fauré, depois desloque o registro uma oitava acima"*.

Pure composition turns skip corpus retrieval. Analyzer and planner context is limited to the most recent
12 messages by default; `MUSCHAT_HISTORY_MESSAGES` changes this count (0 sends no previous messages).
Stored conversation and composition history are retained.

Optional: set `MUSCHAT_NOTAGEN_WORKER=1` to reuse loaded NotaGen weights across adjacent section calls.
`MUSCHAT_NOTAGEN_IDLE_TIMEOUT=60` controls how many idle seconds the worker stays alive before releasing
GPU memory. A stopped or unavailable worker falls back to ordinary per-call generation. It is disabled by default.
The sidebar caches detected tools for the current configuration; use **Redetectar ferramentas** after installing
or updating a tool. Export artifacts are cached per session by score content and transposition mode.

Without an index the chat still works and says it is answering from general knowledge.

`.env`:

```dotenv
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.1-flash-lite          # optional, this is the default
GEMINI_EMBED_MODEL=gemini-embedding-2      # optional, this is the default
```

The default embedding model is now `gemini-embedding-2` (previously `gemini-embedding-001`). It has its own
free-tier quota and gives one vector per passage. An index is tied to the model that built it: after changing
`GEMINI_EMBED_MODEL`, re-run the ingestion (the app warns in the sidebar if the index and the model differ).

The key can also come from an exported environment variable or from `.streamlit/secrets.toml` (`GEMINI_API_KEY = "your-key"`). Never commit any of these files; they are gitignored.

## Corpus

Put your material in `corpus/` (gitignored, never redistributed): Markdown (split by heading), PDF/TXT with
extractable text (split by page) and MusicXML scores (turned into a work card plus 3-bar cards that can be
rendered back as notation). Subfolders named after an instrument family (`cordas/`, `sopros/`, ...) tag the
chunks. A `_acervo.toml` in a folder declares who wrote that material, so the chat never credits it to anyone else:

```toml
compositor = "Your Name"
```

Re-run `uv run python -m muschat.rag.ingest` whenever the corpus changes. On the free tier, embeddings are
rate-limited; the ingestion waits out the per-minute limit (and says so on screen) and stops with a clear
message if the daily quota is exhausted (switch `GEMINI_EMBED_MODEL` or try later). The previous index is kept.

Notation is rendered offline: the project never fetches scores or images from the internet, and published
scores are never reproduced. Asking for "a score by composer X" who is not in the corpus gets an explanation,
not someone else's music.

## Try it

Paste these into the chat to exercise the current features:

1. **Domain answer**: `Como escrevo um multifônico para clarinete em Si♭ e como o intérprete lê isso?`
2. **Memory**: follow up with `E um equivalente no saxofone?` The model should resolve "equivalente" from the previous turn.
3. **Scope**: `Qual a receita de bolo de cenoura?` The request should be politely declined.
4. **Reset**: say `Minha peça se chama "Vidro Partido".`, click **Nova conversa** in the sidebar, then ask `Qual o nome da minha peça?` It should no longer know the name.
5. **Notation from the corpus**: `Como notar col legno battuto no violino?` shows the real notated example
   from the corpus, with its author in the caption.
6. **Generated notation**: `Escreva 4 compassos para violino alternando sul ponticello e col legno battuto.`
   shows an illustrative score (clearly labelled as generated) and a `.musicxml` download.
7. **A bar of a work in the corpus**: `O que acontece no compasso 8 do Quarteto delle Pressioni?` (if that
   score is in your corpus) cites `[Quarteto delle Pressioni, m.7–9]` and shows those real bars.
8. **No misattribution**: `Me dê um exemplo de partitura da Nina Šenk.` explains there is no published score
   to show and credits nothing from the corpus to her.
9. **Composition**: `Crie oito compassos para quarteto de cordas utilizando uma sequência derivada de parciais
   harmônicos.` shows a four-part score (version 1 in the sidebar's **Composição** section, with its **Exportar**
   menu). Then `Aplique um processo às durações do violoncelo, preservando os outros instrumentos.` creates
   version 2; comparing 1 → 2 in the sidebar lists changes only in the cello part. **Desfazer** goes back to 1.
10. **Engraving and procedures**: `Crie oito compassos para quarteto de cordas utilizando quiálteras, harmônicos e
    transformação progressiva de densidade.` (with LilyPond installed) shows the engraved score with download
    buttons for the `.ly`, the score PDF and one PDF per instrument. Then `Deixe os compassos finais mais
    fragmentados.` shows the operational hypothesis in the answer and in the sidebar, and changes only the final
    bars; **Ajustar parâmetros e reaplicar** changes a number and re-runs it.
11. **Engines** (OpenMusic/Híbrido only with `MUSCHAT_OPENMUSIC=1`): in the sidebar pick **Motor**. *Automático* explains its choice in the answer (with OpenMusic only
    assisted, it picks Python for the harmonic series and says why). *OpenMusic* with `Produza uma sequência
    intervalar derivada de um espectro harmônico e utilize-a para criar material musical.` stops at **Aguardando
    você** with a task to download; *Híbrido* with `Use um procedimento espectral do OpenMusic para produzir
    material de alturas e organize esse material para quarteto de cordas utilizando Python e Abjad.` runs the
    OpenMusic step, then, after you upload `saida.txt`, the Python orchestration and the engraving. **Motores e
    bibliotecas** lists each engine and library with its real state and what is missing.
12. **Error handling**: set `GEMINI_API_KEY=invalid` in `.env` and restart. You get a friendly message, not a traceback. Remove the key entirely and you get setup instructions.

## Exports

In the sidebar, **Composição → Exportar** offers, for the current version:

| Download | Contents | What it loses (shown next to the button) |
|---|---|---|
| **composição completa (.zip)** | MusicXML, MIDI, `composicao.json`, `experimento.json`, `PERDAS.txt`; plus `.ly` and PDFs of score and parts and the score SVG when LilyPond engraved it, or the Verovio SVG otherwise | only formats actually produced are included |
| MusicXML | all parts, voices, tuplets, quarter tones, techniques | control curves, spectral data, electronic events; edits made in Dorico/MuseScore do not come back |
| MIDI | one track per part, tempo and meter | techniques, articulations, spelling, tuplets as notation, microtones (rounded to the semitone) |

**Arquivo da composição (.json)** saves and opens the whole composition (all versions, the pending OpenMusic step if
any, provenance per event); it is the lossless format. Opening it in a new session (**Nova conversa**, then
**abrir composição**) lets you keep editing. **dados do experimento** downloads the research JSON: for each version
the request, model, plan (with constraints), router decision and justification, procedures, parameters, seed,
dependency versions (Python, Abjad, Verovio, google-genai), constraint violations, diff against the previous version
and descriptive metrics. Versions of external engines (LilyPond, OpenMusic) appear only when detected.

**Métricas descritivas** (in **Inspeção técnica**, next to the plan, router decision, steps, parameters and
intermediate results): number of events and notes, attacks per quarter note summed over all parts, range in midicents, duration
distribution, standard deviation of melodic intervals in cents, constraint violations and diff size. They describe
the material; they are not an aesthetic or creativity score.

## Experiments A–E

Run each script in a clean session of the app (click **Nova conversa** first) with the default configuration:
`MUSCHAT_OPENMUSIC` unset and **Motor = Automático** unless stated. A, D and E need no OpenMusic. A needs LilyPond for
the PDFs; without it the score appears through Verovio and the zip has no PDF. After each experiment, the experiment
JSON is under **Composição → Arquivo da composição → dados do experimento**, and the full package under **Exportar**.

The results below come from real runs on 2026-11-12 with `gemini-3.1-flash-lite`, LilyPond 2.26.0 and Windows 11.
The requests went through the same turn function the app calls (`muschat.reasoning.respond`, real Gemini, real
index), run from a script instead of the browser, and the outputs were saved with the same export functions. The
experiment JSONs from those runs therefore have no `engines` entry; downloaded from the app, they list LilyPond.
The model chooses the plan and the seed, so a rerun gives a different but comparable result. Re-applying a version
with **Ajustar parâmetros e reaplicar** reuses its seed and is deterministic.

### A: Abjad/LilyPond engraving

1. Ask: `Crie oito compassos para quarteto de cordas utilizando quiálteras, harmônicos e transformação progressiva de densidade.`
2. Expect: version 1 with four parts (Violino, Violino, Viola, Violoncelo), 8 bars, the engraved score, and PDF
   buttons for the score and each part.

Result: plan `harmonic_series → subdivide → density → apply_techniques`, all on Python/Abjad. The version has 4
parts, 8 bars, 226 notes, 37 tuplet groups and 116 harmonics. The density ramp (1 → 3 attacks per quarter note) and
the tuplet and harmonic proportions came back as stated hypotheses. No constraint was violated. LilyPond produced 5
PDFs (a 2-page score and four 1-page parts) and the score SVG.

### D: Router

Run each request in a new conversation, with the engine selector as shown, and open **Inspeção técnica** to read the
decision:

| Selector | Request | Real decision |
|---|---|---|
| Automático | `Crie quatro compassos para quarteto de cordas a partir de uma série harmônica sobre Dó 2.` | `harmonic_series` on Python/Abjad, "only engine that runs here" (OpenMusic disabled) |
| Python/Abjad | `Crie quatro compassos para trio de violino, viola e violoncelo com densidade crescente.` | `harmonic_series` and `density` on Python/Abjad, "as selected (automatic)": the parenthesis is the engine's execution state. Hypothesis: density 0.5 → 3 attacks per quarter note |
| Automático | `Produza uma sequência intervalar derivada de um espectro harmônico e utilize-a para criar material musical para quarteto de cordas.` | `harmonic_series` on Python/Abjad, same rule |
| Automático | `Use um procedimento espectral do OpenMusic para produzir material de alturas para quarteto.` | not classified as a composition request: the model explained the OpenMusic spectral workflow in text and no version was created |

The rules (R1–R6) are in `docs/adr/0002-regras-do-roteador.md`. With OpenMusic enabled, the OpenMusic and Híbrido
selectors exercise the assisted rules (see the appendix).

### E: Incremental edit

1. Ask: `Crie oito compassos para quarteto de cordas utilizando uma sequência derivada de parciais harmônicos.`
2. Then: `Aplique um processo às durações do violoncelo, preservando os outros instrumentos.`
3. In **Composição**, compare 1 → 2.

Result: version 2 ran `permute_durations` with selection `{'parts': ['P4']}`. The programmatic diff lists 10
changed events, all in the cello (P4), and nothing in the other three parts. The composition JSON was then saved,
opened in a new session and edited again (`Agora transponha a viola uma terça menor acima, sem mudar os outros
instrumentos.`). Version 3 transposed only the viola (22 events in P3).

### Platforms

The Python path (tests, composition, export and the app without OpenMusic) was smoke-tested on Linux (Ubuntu 24.04
on WSL2, no display, no LilyPond, no OpenMusic). 340 tests passed and 2 were skipped (real LilyPond and real
OpenMusic). The app showed only the Automatic/Python modes and no OpenMusic entry, and the export zip fell back to
the Verovio SVG. This says nothing about the OpenMusic adapter on Linux, which has only been validated on Windows.

## Appendix: OpenMusic experiments B and C

Optional and experimental. Skipping B and C because OpenMusic is missing does not affect A, D, E or the rest of the
app. Muschat does not install OpenMusic, create its workspace or drive its GUI.

Setup (validated on Windows 11 only):

1. Install [OpenMusic 8.0](https://github.com/openmusic-project/openmusic/releases/tag/v8.0) and put
   [Esquisse](https://github.com/openmusic-project/Esquisse/releases) in `~/OM/Libraries/` (validated with Esquisse 1.2).
2. Set `MUSCHAT_OPENMUSIC=1` in `.env`, plus `OPENMUSIC=<path to the executable>` if it is not under
   `C:\Program Files\OpenMusic*`. Restart the app. **Motores e bibliotecas** should list OpenMusic 8.0 and Esquisse
   as assisted.
3. Open OpenMusic once and create or open a workspace yourself.

Integration states: *automático* (runs without you), *intercâmbio de arquivos*, *manual* (assisted: you run the
approved patch) and *indisponível* (with the missing requirement). OpenMusic is **assisted**: every OpenMusic step
needs your hand.

**B: OpenMusic.** Selector **OpenMusic**. Ask `Produza uma sequência intervalar derivada de um espectro harmônico e
utilize-a para criar material musical.` The flow stops at **Aguardando você** with a task folder under
`.muschat/openmusic/<task id>/` (`entrada.txt`, `instrucoes.md` and a copy of the approved `harmonic_series.lisp`)
and a `.zip` of it. In OpenMusic's Listener, use `Lisp → Load File…` on the task's `harmonic_series.lisp`; it writes
`saida.txt` next to `entrada.txt`. Upload `saida.txt` and click **Importar e continuar**.

**C: Hybrid.** Selector **Híbrido**. Ask `Use um procedimento espectral do OpenMusic para produzir material de alturas
e organize esse material para quarteto de cordas utilizando Python e Abjad.` The OpenMusic step is the same; after
the upload, the Python orchestration and the engraving run.

Real result (2026-11-11, OpenMusic 8.0 + Esquisse 1.2). These runs used a fixed plan and seed 7, so the Gemini
planning call and the Streamlit upload were not part of them. Input `3600 1 1 1 16`; OpenMusic output
`3600 4800 5502 6000 6386 6702 6969 7200 7404 7586 7751 7902 8041 8169 8288 8400`, equal to
`round(3600 + 1200·log2(n))` for n = 1..16. B produced a version with OpenMusic provenance (score and one part PDF).
C chained OpenMusic → Python orchestration for two violins, viola and cello → Abjad/LilyPond (score and four part
PDFs). Details and evidence: `docs/pesquisa/openmusic.md` and `docs/adr/0001-integracao-openmusic-assistida.md`.

**More libraries (assisted, validated on Windows on 2026-11-12).** Install the official releases from the
[OpenMusic libraries page](https://openmusic-project.github.io/libraries) in `~/OM/Libraries/`; with OpenMusic enabled,
**Motores e bibliotecas** lists each one with its real state. Each runs like B: Selector **OpenMusic**, a task folder,
`Lisp → Load File…` on the task's `.lisp`, upload `saida.txt`. If a patch fails inside OpenMusic it writes `-2` and the
Lisp error to `erro.txt`, and the upload shows that error.

| Library | Procedure | Example request | Real result (fixed plan, seed 7, no Gemini call) |
|---|---|---|---|
| Chaos 1.5 | `chaotic_density`, `chaotic_register`, `chaotic_dynamics`, `chaotic_durations` (Verhulst logistic map → one value per bar) | `Gere um processo composicional baseado em dinâmica caótica para controlar a densidade dos ataques de um quarteto.` (on an existing piece) | series `0.1 0.352 0.9907 1.0166`, equal to the documented formula; 2, 5, 12, 12 attacks per bar |
| Situation 3.1 | `distribute_by_constraints` (`csolver`, one domain per slot, all different; the plan's range and forbidden-interval constraints go into the patch) | `Distribua doze alturas em quatro instrumentos, evitando uníssonos e mantendo cada parte dentro de seu registro.` | 12 different pitches, each in range, accepted by the checker |
| OMChroma 5.2 + Csound | `additive_synthesis` (notes → `add-1` partials, count rising over time) | `Utilize os eventos da partitura para controlar uma síntese aditiva cuja densidade espectral aumenta progressivamente.` | 159 partials, `.orc`/`.sco` from OMChroma, 8 s `muschat.wav` rendered by Csound 7.0, electronic part added (cues and a curve, no notes) |

The three example requests were also run through Gemini (`gemini-3.1-flash-lite`, Hybrid selector) with the OpenMusic
steps in the real OM; results in `docs/pesquisa/openmusic.md`. In these integrations OpenMusic returns numbers (a
series, a solution); rhythm, register and orchestration are still decided by the Python procedures, so the musical
result is only as good as those procedures.

An impossible Situation problem (more slots than reachable pitches) is refused by Muschat with the conditions that
fail, and never sent to OpenMusic: in the real test, the solver's exhaustive search for a non-existent solution froze
OpenMusic. Csound (`CSOUND` or `PATH`) renders the audio; without it the `.orc`/`.sco` are kept and the part says why
there is no audio. Clouds is installed but has no patch (its constraints are visual boxes). OM-SuperVP needs the
SuperVP executable (IRCAM Forum subscription): Muschat detects it separately and can import its spectral data and
turn it into notes (`events_from_spectrum`), tested with fakes only; no SuperVP patch is validated. Details:
`docs/pesquisa/openmusic.md`.

## Limitations

- The model plans; it can only use the procedures in the Python catalog. A request the analysis does not classify as
  a composition operation gets a text answer instead (experiment D, last row).
- MIDI rounds microtones to the semitone and drops techniques. MusicXML drops curves, spectral data and electronic
  events. Only the JSON is lossless.
- OpenMusic runs assisted only (no headless or GUI automation) and has only been validated on Windows. Approved
  patches: harmonic series (Esquisse), Verhulst density (Chaos), constrained distribution (Situation), additive
  synthesis (OMChroma). The Situation solver has no time limit inside OpenMusic, so Muschat only sends problems that
  have a solution. Every OpenMusic step stays assisted; only the Csound rendering of the OMChroma files, after you
  upload `saida.txt`, runs automatically (with a timeout).
- The Streamlit UI is checked manually (plus `streamlit.testing` smoke runs), not by the automated suite.
- On the free Gemini tier, requests per minute are limited, and a composition turn costs three calls (analysis, plan
  and answer).

## Troubleshooting

| You see | Do |
|---|---|
| Sidebar: "Sem índice do acervo" | Add material to `corpus/` and run the ingestion |
| Sidebar: index built with another model / old version | Re-run `uv run python -m muschat.rag.ingest` |
| Ingestion: "Cota de embeddings ... esgotada" | Daily free-tier quota: try later or set another `GEMINI_EMBED_MODEL` |
| Chat: "Limite de requisições" | Per-minute limit of the chat model: wait a minute |
| A score shows as XML text instead of notation | Verovio could not read that MusicXML; the download still works |

## Tests

```bash
uv run pytest
```

The tests cover the reasoning chain, corpus readers, index, hybrid search, MusicIR, procedures, constraints, router,
orchestrator, versions, the OpenMusic task bridge, the LilyPond/MusicXML/MIDI writers, JSON round trip, export zip
and metrics, all with fakes instead of the API. Tests that need a real LilyPond or OpenMusic are skipped when absent. The UI is checked manually with the steps above.

## Project layout

```
app.py                    Streamlit UI: chat, reasoning panel, scores, sidebar, error display
muschat/config.py         API key and models from env / .env / Streamlit secrets
muschat/prompts.py        system, analysis, turn-context and notation prompts
muschat/reasoning.py      one turn: analysis -> retrieval -> notation (on demand) -> synthesis
muschat/errors.py         maps API/network exceptions to user-facing messages
muschat/rag/readers.py    corpus readers (Markdown, PDF/TXT, MusicXML) -> chunks
muschat/rag/index.py      Gemini embeddings and the local numpy index
muschat/rag/search.py     hybrid search (dense + BM25, RRF) and context assembly
muschat/rag/ingest.py     `python -m muschat.rag.ingest`
muschat/score_ir.py       ScoreIR: parts, voices, typed events, provenance, structural diff
muschat/plan.py           composition plan schema (operations, parameters, selection, sections)
muschat/recipes.py        aesthetic recipes and docs generation (`python -m muschat.recipes`)
muschat/procedures/       Python procedure catalog: generation (harmonic series, distribution by range),
                          melodic (transpose, invert, retrograde, rotate, interval scaling), rhythmic
                          (augmentation/diminution, permutation, tuplet subdivision), density, progressive
                          ramps (register, dynamics), contemporary transformations, and techniques
muschat/constraints.py    constraint checker (range, forbidden interval, no unison, max density)
muschat/instruments.py    orchestral instrument table: family, clef and sounding range (strings, woodwinds,
                          brass, pitched and unpitched percussion, keyboards, harp, guitar, voices)
muschat/sections.py       heterogeneous multi-section insertion helpers
muschat/orchestration.py  idiomatic part adaptation (clef + technique filtering)
muschat/notation/lilypond.py  ScoreIR -> Abjad -> LilyPond text; LilyPond detection and engraving (PDF/SVG)
muschat/orchestrator.py   composition turn: plan -> router -> list of steps (pause at an OpenMusic step, resume
                          after import) -> preservation check per step -> version
muschat/registry.py       capability registry: what each engine/library/engraver can do on this machine, and how
muschat/router.py         explicit, ordered routing rules (docs/adr/0002-regras-do-roteador.md)
muschat/openmusic.py      OpenMusic bridge (assisted): install detection, task, output import
muschat/notagen.py        NotaGen bridge (optional): install detection, Compositor run as a separate process
services/notagen_generate.py runs inside NotaGen's own environment: N bars -> ABC -> MusicXML
muschat/om_patches.py     approved patches: entrada.txt per patch, saida.txt -> ScoreIR, Csound rendering
patches/                  the project's Lisp patches loaded in OpenMusic (Esquisse, Chaos, Situation, OMChroma, Cluster-Engine)
muschat/composition.py    session composition: versions, undo, go back, compare, JSON save/open
muschat/export.py         MIDI writer, experiment JSON, export zip, descriptive metrics, loss notes per format
muschat/notation/         example schema -> ScoreIR, extended-technique table by instrument family, MusicXML
                          builder and reader (open .musicxml/.mxl as a Version), Verovio renderer
muschat/references.py     fixed reference pieces for before/after listening (`python -m muschat.references <label>`)
tests/                    pytest suite
```

## License

Muschat depends on [Abjad](https://github.com/Abjad/abjad), which is GPL-3.0, so a distributed copy of Muschat is a
combined work under GPL-3.0. LilyPond (GPL-3.0) is an external program the user installs; it is called as a process
and never shipped with the project. OpenMusic and its libraries (GPL-3.0 or their own licenses; SuperVP has a
separate IRCAM license) are optional external applications and are not shipped either; NotaGen (MIT) and its
weights are optional, installed by the user and run as a separate process; `patches/harmonic_series.lisp`
is written by this project and only calls Esquisse's public `harm-series`. The license report is in
`docs/pesquisa/openmusic.md`.

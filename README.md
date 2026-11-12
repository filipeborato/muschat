# Muschat

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
   path (`muschat/orquestrador.py`): one more Gemini call returns a typed composition plan that may only name
   procedures from the Python catalog (`muschat/procedimentos.py`) with parameters inside their ranges, plus a
   selection (parts, voices, bars). Invalid plans are refused before anything runs; the model never writes code.
   The procedure runs on MusicIR with a seed, and the orchestrator rejects the result if anything outside the
   selection changed. A single constraint checker (range, forbidden interval, no unison, maximum density) runs
   after every operation and its violations are reported, not hidden. Subjective requests ("mais fragmentado") come
   back as explicit operational hypotheses with numbers, which the sidebar lets you adjust and re-apply without
   calling the model. The result becomes a new version of the session composition, engraved with Abjad +
   LilyPond (score and parts as PDF, `.ly` download) when LilyPond is installed.
4. **Answer**: the chat receives only the question; the analysis, passages and score list go into that
   turn's system instruction, so the conversation history stays small (questions and answers only).

## Quick start

Requirements: Python 3.13+, [uv](https://docs.astral.sh/uv/) and a Gemini API key ([get one here](https://aistudio.google.com/apikey)).
The standard Python composition workflow does **not** require OpenMusic, its libraries, LispWorks, Csound,
SuperVP or an OpenMusic workspace. `uv sync` does not install these external applications.

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
   harmônicos.` shows a four-part score (version 1 in the sidebar's **Composição** section, with its MusicXML
   download). Then `Aplique um processo às durações do violoncelo, preservando os outros instrumentos.` creates
   version 2; comparing 1 → 2 in the sidebar lists changes only in the cello part. **Desfazer** goes back to 1.
10. **Engraving and procedures**: `Crie oito compassos para quarteto de cordas utilizando quiálteras, harmônicos e
    transformação progressiva de densidade.` (with LilyPond installed) shows the engraved score with download
    buttons for the `.ly`, the score PDF and one PDF per instrument. Then `Deixe os compassos finais mais
    fragmentados.` shows the operational hypothesis in the answer and in the sidebar, and changes only the final
    bars; **Ajustar parâmetros e reaplicar** changes a number and re-runs it.
11. **Engines**: in the sidebar pick **Motor**. *Automático* explains its choice in the answer (with OpenMusic only
    assisted, it picks Python for the harmonic series and says why). *OpenMusic* with `Produza uma sequência
    intervalar derivada de um espectro harmônico e utilize-a para criar material musical.` stops at **Aguardando
    você** with a task to download; *Híbrido* with `Use um procedimento espectral do OpenMusic para produzir
    material de alturas e organize esse material para quarteto de cordas utilizando Python e Abjad.` runs the
    OpenMusic step, then, after you upload `saida.txt`, the Python orchestration and the engraving. **Motores e
    bibliotecas** lists each engine and library with its real state and what is missing.
12. **Error handling**: set `GEMINI_API_KEY=invalid` in `.env` and restart. You get a friendly message, not a traceback. Remove the key entirely and you get setup instructions.

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

The tests cover the reasoning chain, corpus readers, index, hybrid search, the MusicXML builder and the
Verovio render, all with fakes instead of the API. The UI is checked manually with the steps above.

## Project layout

```
app.py                    Streamlit UI: chat, reasoning panel, scores, sidebar, error display
muschat/config.py         API key and models from env / .env / Streamlit secrets
muschat/prompts.py        system, analysis, turn-context and notation prompts
muschat/reasoning.py      one turn: analysis -> retrieval -> notation (on demand) -> synthesis
muschat/erros.py          maps API/network exceptions to user-facing messages
muschat/rag/leitores.py   corpus readers (Markdown, PDF/TXT, MusicXML) -> chunks
muschat/rag/indice.py     Gemini embeddings and the local numpy index
muschat/rag/busca.py      hybrid search (dense + BM25, RRF) and context assembly
muschat/rag/ingest.py     `python -m muschat.rag.ingest`
muschat/musicir.py        MusicIR: parts, voices, typed events, provenance, structural diff
muschat/plano.py          composition plan schema (operations, parameters, selection)
muschat/procedimentos/    Python procedure catalog: generation (harmonic series, distribution by range),
                          melodic (transpose, invert, retrograde, rotate, interval scaling), rhythmic
                          (augmentation/diminution, permutation, tuplet subdivision), density and progressive
                          ramps (register, dynamics), techniques
muschat/restricoes.py     constraint checker (range, forbidden interval, no unison, max density)
muschat/instrumentos.py   orchestral instrument table: family, clef and sounding range (strings, woodwinds,
                          brass, pitched and unpitched percussion, keyboards, harp, guitar, voices)
muschat/notacao/lilypond.py  MusicIR -> Abjad -> LilyPond text; LilyPond detection and engraving (PDF/SVG)
muschat/orquestrador.py   composition turn: plan -> router -> list of steps (pause at an OpenMusic step, resume
                          after import) -> preservation check per step -> version
muschat/registro.py       capability registry: what each engine/library/engraver can do on this machine, and how
muschat/roteador.py       explicit, ordered routing rules (docs/adr/0002-regras-do-roteador.md)
muschat/openmusic.py      OpenMusic bridge (assisted): install detection, approved patches, task, output import
muschat/composicao.py     session composition: versions, undo, go back, compare
muschat/notacao/          example schema -> MusicIR, extended-technique table by instrument family, MusicXML
                          builder, Verovio renderer
tests/                    pytest suite
```

## License

Muschat depends on [Abjad](https://github.com/Abjad/abjad), which is GPL-3.0, so a distributed copy of Muschat is a
combined work under GPL-3.0. LilyPond (GPL-3.0) is an external program the user installs; it is called as a process
and never shipped with the project.

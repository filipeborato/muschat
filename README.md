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
4. **Answer**: the chat receives only the question; the analysis, passages and score list go into that
   turn's system instruction, so the conversation history stays small (questions and answers only).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) and a Gemini API key ([get one here](https://aistudio.google.com/apikey)).

```bash
git clone <repo-url> muschat
cd muschat
uv sync                      # creates .venv and installs dependencies
cp .env.example .env         # then put your key in .env
uv run python -m muschat.rag.ingest   # builds index/ from corpus/ (optional, see below)
uv run streamlit run app.py  # opens http://localhost:8501
```

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
9. **Error handling**: set `GEMINI_API_KEY=invalid` in `.env` and restart. You get a friendly message, not a traceback. Remove the key entirely and you get setup instructions.

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
muschat/notacao/          example schema -> MusicIR, technique table, MusicXML builder, Verovio renderer
tests/                    pytest suite
```

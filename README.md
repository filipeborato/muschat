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
| Automatic retry with backoff on transient API errors | ✅ |
| "Nova conversa" button to reset the screen and the model's context | ✅ |
| Chain-of-Thought: hidden analysis step (scope, plan, search queries) shown in a collapsible panel | ✅ |
| Hybrid RAG (Gemini embeddings + BM25, RRF) over a local corpus, with `[source, location]` citations | ✅ |
| Sheet music: real excerpts from the corpus, or an illustrative example generated on demand (JSON → MusicXML → Verovio SVG, offline) with `.musicxml` download | ✅ |

The UI is in Portuguese.

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
rate-limited; the ingestion waits out the per-minute limit and stops with a clear message if the daily quota
is exhausted (switch `GEMINI_EMBED_MODEL` or try later).

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
7. **Error handling**: set `GEMINI_API_KEY=invalid` in `.env` and restart. You get a friendly message, not a traceback. Remove the key entirely and you get setup instructions.

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
muschat/notacao/          score schema, MusicXML builder, Verovio renderer
tests/                    pytest suite
```

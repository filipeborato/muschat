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
| Chain-of-Thought reasoning with visible intermediate steps | 🚧 next |
| RAG over a local corpus (books, treatises, scores) with source citations | 🚧 next |
| Musical examples rendered as sheet music (ABC notation) | 🚧 next |

The UI is in Portuguese.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) and a Gemini API key ([get one here](https://aistudio.google.com/apikey)).

```bash
git clone <repo-url> muschat
cd muschat
uv sync                      # creates .venv and installs dependencies
cp .env.example .env         # then put your key in .env
uv run streamlit run app.py  # opens http://localhost:8501
```

`.env`:

```dotenv
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.1-flash-lite   # optional, this is the default
```

The key can also come from an exported environment variable or from `.streamlit/secrets.toml` (`GEMINI_API_KEY = "your-key"`). Never commit any of these files; they are gitignored.

## Try it

Paste these into the chat to exercise the current features:

1. **Domain answer**: `Como escrevo um multifônico para clarinete em Si♭ e como o intérprete lê isso?`
2. **Memory**: follow up with `E um equivalente no saxofone?` The model should resolve "equivalente" from the previous turn.
3. **Scope**: `Qual a receita de bolo de cenoura?` The request should be politely declined.
4. **Reset**: say `Minha peça se chama "Vidro Partido".`, click **Nova conversa** in the sidebar, then ask `Qual o nome da minha peça?` It should no longer know the name.
5. **Error handling**: set `GEMINI_API_KEY=invalid` in `.env` and restart. You get a friendly message, not a traceback. Remove the key entirely and you get setup instructions.

## Tests

```bash
uv run pytest
```

The tests cover configuration loading and the translation of API and network errors into user messages, without calling the API. The UI is checked manually with the steps above.

## Project layout

```
app.py              Streamlit UI: session state, spinner, sidebar, error display
muschat/config.py   reads the API key and model from env / .env / Streamlit secrets
muschat/prompts.py  system prompt: persona and restricted scope
muschat/erros.py    maps API/network exceptions to user-facing messages
tests/              pytest suite
```

"""Muschat: UI do chat em Streamlit."""

import os
from dataclasses import asdict
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from streamlit.errors import StreamlitSecretNotFoundError

from muschat.config import ChaveAusente, carregar_config
from muschat.erros import mensagem_de_erro
from muschat.rag.busca import abrir_retriever
from muschat.rag.indice import GeminiEmbedder
from muschat.reasoning import responder

load_dotenv()  # .env opcional; não sobrescreve variáveis já definidas

st.set_page_config(page_title="Muschat", page_icon="🎼")
st.title("🎼 Muschat")
st.caption("Música contemporânea, eletroacústica e técnicas estendidas.")


def _secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except StreamlitSecretNotFoundError:
        return {}


try:
    # snapshot antes de st.secrets: o Streamlit copia os secrets para os.environ
    cfg = carregar_config(dict(os.environ), _secrets())
except ChaveAusente:
    st.error(
        "Chave do Gemini não configurada. Defina `GEMINI_API_KEY` num arquivo `.env`, no ambiente "
        "ou em `.streamlit/secrets.toml` (`GEMINI_API_KEY = \"sua-chave\"`) e recarregue a página."
    )
    st.stop()


@st.cache_resource
def _cliente(api_key: str) -> genai.Client:
    # retry nativo do SDK: backoff exponencial em 408/429/5xx
    retry = types.HttpRetryOptions(attempts=3)
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=retry))


@st.cache_resource
def _retriever(api_key: str, embed_model: str):
    embedder = GeminiEmbedder(_cliente(api_key).models, embed_model)
    return abrir_retriever(Path("index"), embedder, embed_model)


# shortcut: histórico (só perguntas e respostas) cresce sem limite e vai inteiro na análise;
# resumir/janelar as mensagens antigas se conversas longas estourarem tokens
def _novo_chat():
    # sem config aqui: cada síntese passa a sua (SISTEMA + contexto do turno) em reasoning.responder
    return _cliente(cfg.api_key).chats.create(model=cfg.model)


def _nova_conversa():
    st.session_state.chat = _novo_chat()
    st.session_state.messages = []


if "chat" not in st.session_state:
    _nova_conversa()

st.sidebar.button("Nova conversa", on_click=_nova_conversa, width="stretch")
retriever, aviso_indice = _retriever(cfg.api_key, cfg.embed_model)
if aviso_indice:
    _retriever.clear()  # não cacheia a falha: após a ingestão, o próximo rerun já acha o índice
    st.sidebar.caption(f"⚠️ {aviso_indice} Respostas sem base no acervo.")


def _render(msg: dict):
    st.markdown(msg["content"])
    if analise := msg.get("analise"):
        with st.expander("Raciocínio (análise interna e trechos do acervo)"):
            st.json(analise)
            trechos = msg.get("trechos") or []
            st.markdown(f"**Trechos recuperados:** {len(trechos) or 'nenhum'}")
            for t in trechos:
                st.markdown(f"**{t['fonte']}** — {t['local']}  \n`score {t['score']} ·{' + '.join(t['origem'])}`")
                st.caption(t["texto"][:400] + ("…" if len(t["texto"]) > 400 else ""))


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        _render(msg)

if pergunta := st.chat_input("Pergunte sobre técnicas estendidas, eletroacústica..."):
    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Pensando..."):
                resposta = responder(
                    pergunta,
                    gerador=_cliente(cfg.api_key).models,
                    chat=st.session_state.chat,
                    retriever=retriever,
                    modelo=cfg.model,
                )
        except Exception as exc:
            if (erro := mensagem_de_erro(exc)) is None:
                raise
            st.error(erro)  # histórico intacto: o turno com falha não é gravado
        else:
            if not resposta.final:  # bloqueio de segurança ou resposta vazia
                st.warning("O modelo não devolveu texto para essa pergunta. Tente reformular.")
                st.stop()
            msg = {
                "role": "assistant",
                "content": resposta.final,
                "analise": resposta.analise,
                "trechos": [asdict(t) for t in resposta.trechos],
            }
            _render(msg)
            st.session_state.messages += [{"role": "user", "content": pergunta}, msg]

"""Muschat: UI do chat em Streamlit."""

import os

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from streamlit.errors import StreamlitSecretNotFoundError

from muschat.config import ChaveAusente, carregar_config
from muschat.erros import mensagem_de_erro
from muschat.prompts import SISTEMA

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


# shortcut: histórico cresce sem limite e vai inteiro a cada turno; resumir/janelar se estourar tokens
def _novo_chat():
    return _cliente(cfg.api_key).chats.create(
        model=cfg.model,
        config=types.GenerateContentConfig(system_instruction=SISTEMA),
    )


def _nova_conversa():
    st.session_state.chat = _novo_chat()
    st.session_state.messages = []


if "chat" not in st.session_state:
    _nova_conversa()

st.sidebar.button("Nova conversa", on_click=_nova_conversa, width="stretch")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if pergunta := st.chat_input("Pergunte sobre técnicas estendidas, eletroacústica..."):
    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Pensando..."):
                resposta = st.session_state.chat.send_message(pergunta).text
        except Exception as exc:
            if (msg := mensagem_de_erro(exc)) is None:
                raise
            st.error(msg)  # histórico intacto: o turno com falha não é gravado
        else:
            if not resposta:  # bloqueio de segurança ou resposta vazia
                st.warning("O modelo não devolveu texto para essa pergunta. Tente reformular.")
                st.stop()
            st.markdown(resposta)
            st.session_state.messages += [
                {"role": "user", "content": pergunta},
                {"role": "assistant", "content": resposta},
            ]

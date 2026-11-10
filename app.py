"""Muschat: UI do chat em Streamlit."""

import os

import streamlit as st
from google import genai
from google.genai import types
from streamlit.errors import StreamlitSecretNotFoundError

from muschat.config import carregar_config
from muschat.prompts import SISTEMA

st.set_page_config(page_title="Muschat", page_icon="🎼")
st.title("🎼 Muschat")
st.caption("Música contemporânea, eletroacústica e técnicas estendidas.")


def _secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except StreamlitSecretNotFoundError:
        return {}


cfg = carregar_config(os.environ, _secrets())


@st.cache_resource
def _cliente(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _novo_chat():
    return _cliente(cfg.api_key).chats.create(
        model=cfg.model,
        config=types.GenerateContentConfig(system_instruction=SISTEMA),
    )


if "chat" not in st.session_state:
    st.session_state.chat = _novo_chat()
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if pergunta := st.chat_input("Pergunte sobre técnicas estendidas, eletroacústica..."):
    st.session_state.messages.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)
    resposta = st.session_state.chat.send_message(pergunta).text
    st.session_state.messages.append({"role": "assistant", "content": resposta})
    with st.chat_message("assistant"):
        st.markdown(resposta)

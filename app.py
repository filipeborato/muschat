"""Muschat: UI do chat em Streamlit."""

import os
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from streamlit.errors import StreamlitSecretNotFoundError

from muschat.composicao import Composicao
from muschat.config import ChaveAusente, carregar_config
from muschat.erros import mensagem_de_erro
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.render import svg_de
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
    # timeout por requisição: sem ele uma conexão presa deixa o spinner girando para sempre
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=90_000, retry_options=retry))


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
    st.session_state.composicao = Composicao()


if "chat" not in st.session_state or "composicao" not in st.session_state:
    _nova_conversa()

st.sidebar.button("Nova conversa", on_click=_nova_conversa, width="stretch")
retriever, aviso_indice = _retriever(cfg.api_key, cfg.embed_model)
if aviso_indice:
    _retriever.clear()  # não cacheia a falha: após a ingestão, o próximo rerun já acha o índice
    st.sidebar.caption(f"⚠️ {aviso_indice} Respostas sem base no acervo.")


def _secao_composicao(c: Composicao):
    with st.sidebar.expander("Composição", expanded=c.atual is not None):
        if c.atual is None or c.indice is None:
            st.caption("Nenhuma composição ainda. Peça, por exemplo: \"crie oito compassos para quarteto de cordas "
                       "a partir de parciais harmônicos\".")
            return
        st.markdown(f"**Versão atual: {c.indice + 1}**  \n{c.atual.resumo}")
        st.button("Desfazer", on_click=c.desfazer, disabled=c.atual.anterior is None, width="stretch")
        st.download_button("baixar versão atual (.musicxml)", data=para_musicxml(c.atual.ir),
                           file_name=f"composicao_v{c.indice + 1}.musicxml",
                           mime="application/vnd.recordare.musicxml+xml", on_click="ignore", width="stretch")
        nomes = [f"{i + 1} · {v.experimento['operacao']} · {v.data:%H:%M} · {v.resumo.partition(': ')[2][:50]}"
                 for i, v in enumerate(c.versoes)]
        escolhida = st.selectbox("Versões", range(len(nomes)), index=c.indice, format_func=nomes.__getitem__)
        st.button("Voltar a esta versão", on_click=c.voltar, args=(escolhida,), disabled=escolhida == c.indice,
                  width="stretch")
        if len(c.versoes) > 1:
            de = st.selectbox("Comparar de", range(len(nomes)), index=max(c.indice - 1, 0),
                              format_func=lambda i: str(i + 1))
            para = st.selectbox("até", range(len(nomes)), index=c.indice, format_func=lambda i: str(i + 1))
            dif = c.comparar(de, para)
            st.markdown(f"{len(dif.adicionados)} adicionados · {len(dif.removidos)} removidos · "
                        f"{len(dif.alterados)} alterados" if dif else "Sem diferenças.")
            por_local = Counter(dif.locais.values())  # (parte, compasso) -> eventos mudados
            for parte in sorted({p for p, _ in por_local}):
                st.caption(f"{parte}: " + ", ".join(f"c.{n} ({k})" for (p, n), k in sorted(por_local.items()) if p == parte))


@st.cache_data(max_entries=64, show_spinner=False)
def _svg(musicxml: str) -> str | None:
    return svg_de(musicxml)


def _render_partituras(partituras: list[dict], chave: str):
    for i, p in enumerate(partituras):
        st.caption(p["legenda"])
        if svg := _svg(p["musicxml"]):
            st.image(svg, width="stretch")  # <img>: o SVG não executa script
        else:
            st.code(p["musicxml"], language="xml")
        nome = re.sub(r"[^\w-]+", "_", p["titulo"]).strip("_")[:60] or "partitura"
        st.download_button(
            "baixar .musicxml",
            data=p["musicxml"],
            file_name=f"{nome}.musicxml",
            mime="application/vnd.recordare.musicxml+xml",
            key=f"dl-{chave}-{i}",
            on_click="ignore",
        )


def _render(msg: dict, chave: str):
    st.markdown(msg["content"])
    _render_partituras(msg.get("partituras") or [], chave)
    if analise := msg.get("analise"):
        with st.expander("Raciocínio (análise interna e trechos do acervo)"):
            st.json(analise)
            trechos = msg.get("trechos") or []
            st.markdown(f"**Trechos recuperados:** {len(trechos) or 'nenhum'}")
            for t in trechos:
                st.markdown(f"**{t['fonte']}** — {t['local']}  \n`score {t['score']} · {' + '.join(t['origem'])}`")
                st.caption(t["texto"][:400] + ("…" if len(t["texto"]) > 400 else ""))


for n, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        _render(msg, str(n))

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
                    composicao=st.session_state.composicao,
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
                "trechos": [{k: v for k, v in asdict(t).items() if k != "musicxml"} for t in resposta.trechos],
                "partituras": [asdict(p) for p in resposta.partituras],
            }
            _render(msg, str(len(st.session_state.messages) + 1))  # mesma chave que terá no histórico
            st.session_state.messages += [{"role": "user", "content": pergunta}, msg]

_secao_composicao(st.session_state.composicao)  # no fim: já reflete a Versão criada neste turno

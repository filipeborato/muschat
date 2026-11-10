"""Orquestração de um turno: análise (CoT) -> recuperação -> síntese."""

import re
from dataclasses import dataclass
from typing import Any, Protocol

from google.genai import types
from pydantic import BaseModel, ValidationError

from muschat import prompts


class Analise(BaseModel):
    no_escopo: bool
    instrumentos: list[str]
    tecnicas: list[str]
    plano: str
    consultas: list[str]


@dataclass(frozen=True)
class Trecho:
    texto: str
    fonte: str
    pagina: int | None = None


@dataclass(frozen=True)
class Resposta:
    final: str
    analise: dict
    trechos: list[Trecho]
    fora_do_escopo: bool


# Protocolos com o mesmo formato do SDK: client.models e o Chat entram direto, sem adaptador.
class Gerador(Protocol):
    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any: ...


class SessaoChat(Protocol):
    def get_history(self) -> list: ...
    def send_message(self, message: str) -> Any: ...


class Retriever(Protocol):
    def buscar(self, consultas: list[str], k: int) -> list[Trecho]: ...


# shortcut: sem RAG ainda, devolve []; trocar pelo Retriever do índice (ticket 04)
class SemRetriever:

    def buscar(self, consultas: list[str], k: int) -> list[Trecho]:
        return []


_TAGS = re.compile(r"</?\s*(pergunta_usuario|analise_interna|trechos?)\b[^>]*>", re.IGNORECASE)


def _neutralizar(texto: str) -> str:
    """Remove nossas tags de delimitação de texto não confiável (prompt injection)."""
    return _TAGS.sub("", texto)


# =============================================================================
# ESTRATÉGIA DE RACIOCÍNIO: Chain-of-Thought encadeado e fundamentado (RAG)
#
#   1. ANÁLISE (oculta): chamada atômica `generate_content`, FORA do histórico
#      do chat, com o histórico como contexto. O modelo raciocina em etapas
#      (escopo -> instrumentos/técnicas -> plano Least-to-Most -> consultas)
#      e devolve JSON validado por schema. JSON inválido -> fallback seguro.
#   2. ESCOPO: fora do domínio -> recusa padrão, sem gastar as etapas 3-4.
#   3. RECUPERAÇÃO: as consultas da análise buscam trechos no acervo.
#   4. SÍNTESE: `chat.send_message` (memória nativa) condicionada na análise e
#      nos trechos, tudo delimitado por tags tratadas como dado, nunca instrução.
# =============================================================================
def responder(
    pergunta: str,
    *,
    gerador: Gerador,
    chat: SessaoChat,
    retriever: Retriever,
    modelo: str,
    k: int = 6,
) -> Resposta:
    pergunta_segura = _neutralizar(pergunta)

    analise = _analisar(pergunta_segura, gerador=gerador, chat=chat, modelo=modelo)
    if not analise.no_escopo:
        return Resposta(prompts.RECUSA, analise.model_dump(), [], fora_do_escopo=True)

    trechos = retriever.buscar(analise.consultas, k)

    resposta = chat.send_message(
        prompts.SINTESE.format(
            pergunta=pergunta_segura,
            analise=_neutralizar(analise.model_dump_json(indent=2)),
            trechos="\n\n".join(_formatar(t) for t in trechos) or "nenhum",
        )
    )
    return Resposta(resposta.text or "", analise.model_dump(), trechos, fora_do_escopo=False)


def _analisar(pergunta: str, *, gerador: Gerador, chat: SessaoChat, modelo: str) -> Analise:
    pedido = types.Content(
        role="user",
        parts=[types.Part(text=f"<pergunta_usuario>\n{pergunta}\n</pergunta_usuario>")],
    )
    resposta = gerador.generate_content(
        model=modelo,
        contents=[*chat.get_history(), pedido],
        config=types.GenerateContentConfig(
            system_instruction=prompts.ANALISE,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=Analise,
        ),
    )
    try:
        return Analise.model_validate_json(resposta.text or "")
    except ValidationError:
        # shortcut: fallback abre o escopo (a recusa fica a cargo do SISTEMA na síntese);
        # reavaliar se respostas bloqueadas/vazias deixarem passar perguntas fora do escopo
        return Analise(no_escopo=True, instrumentos=[], tecnicas=[], plano="", consultas=[pergunta])


def _formatar(t: Trecho) -> str:
    return prompts.TRECHO.format(
        fonte=_neutralizar(t.fonte).replace('"', "'"),
        pagina=t.pagina if t.pagina is not None else "?",
        texto=_neutralizar(t.texto),
    )

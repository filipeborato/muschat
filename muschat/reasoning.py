"""Orquestração de um turno: análise (CoT) -> recuperação -> notação (sob demanda) -> síntese."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx2
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from muschat import prompts
from muschat.composicao import Composicao, Versao
from muschat.notacao.modelo import Partitura, para_ir
from muschat.musicir import MusicIR
from muschat.notacao.lilypond import para_lilypond
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.tecnicas import TECNICAS
from muschat.orquestrador import compor
from muschat.registro import Registro
from muschat.roteador import Modo
from muschat.seguranca import neutralizar as _neutralizar


class Analise(BaseModel):
    no_escopo: bool
    instrumentos: list[str]
    tecnicas: list[str]
    plano: str
    consultas: list[str]
    notacao: Literal["nenhuma", "acervo", "gerar", "compor"] = "nenhuma"


@dataclass(frozen=True)
class Trecho:
    texto: str
    fonte: str
    local: str = ""  # "p. 12" | "m.7–9" | trilha de cabeçalhos | rótulo do exemplo
    id: str = ""
    score: float = 0.0
    origem: tuple[str, ...] = ()  # densa | lexica | obra
    compositor: str = ""
    musicxml: str = ""  # documento autônomo quando o trecho é partitura do acervo


@dataclass(frozen=True)
class Exemplo:
    """Partitura exibida abaixo da resposta: real do acervo ou ilustrativa gerada."""

    titulo: str
    musicxml: str
    legenda: str
    origem: Literal["acervo", "gerado", "composicao"]
    lilypond: dict[str, str] = field(default_factory=dict)  # grade e partes (.ly), só para a composição


@dataclass(frozen=True)
class Resposta:
    final: str
    analise: dict
    trechos: list[Trecho]
    fora_do_escopo: bool
    partituras: list[Exemplo] = field(default_factory=list)


MAX_PARTITURAS_ACERVO = 2
MAX_COMPASSOS_GERADOS = 8
LEGENDA_GERADA = "Exemplo ilustrativo gerado — não é trecho de obra publicada"


# Protocolos com o mesmo formato do SDK: client.models e o Chat entram direto, sem adaptador.
class Gerador(Protocol):
    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any: ...


class SessaoChat(Protocol):
    def get_history(self) -> list: ...
    def send_message(self, message: str, config: Any = None) -> Any: ...


class Retriever(Protocol):
    def buscar(self, consultas: list[str], k: int, instrumentos: Sequence[str] = ()) -> list[Trecho]: ...


class SemRetriever:
    """Retriever nulo: usado quando não há índice utilizável (a síntese declara conhecimento geral)."""


    def buscar(self, consultas: list[str], k: int, instrumentos: Sequence[str] = ()) -> list[Trecho]:
        return []


# =============================================================================
# ESTRATÉGIA DE RACIOCÍNIO: Chain-of-Thought encadeado e fundamentado (RAG)
#
#   1. ANÁLISE (oculta): chamada atômica `generate_content`, FORA do histórico
#      do chat, com o histórico como contexto. O modelo raciocina em etapas
#      (escopo -> instrumentos/técnicas -> plano Least-to-Most -> consultas)
#      e devolve JSON validado por schema. JSON inválido -> fallback seguro.
#   2. ESCOPO: fora do domínio -> recusa padrão, sem gastar as etapas 3-4.
#   3. RECUPERAÇÃO: as consultas da análise (multilíngues) buscam no acervo por
#      busca híbrida densa + BM25 (RRF), com boost pela família dos instrumentos.
#   4. NOTAÇÃO (só se a análise pedir): partitura real do acervo (MusicXML dos
#      trechos) ou, se não houver, uma ilustrativa gerada como JSON validado por
#      schema e convertida em MusicXML por um builder determinístico. Pedido de
#      composição: o orquestrador planeja (Plano validado contra o catálogo),
#      executa o procedimento e grava uma nova Versão na Composição da sessão.
#   5. SÍNTESE: `chat.send_message(pergunta)` (memória nativa); análise, trechos e
#      a lista de partituras entram no system_instruction do turno, delimitados por
#      tags tratadas como dado, e não são gravados no histórico.
# =============================================================================
def responder(
    pergunta: str,
    *,
    gerador: Gerador,
    chat: SessaoChat,
    retriever: Retriever,
    modelo: str,
    k: int = 6,
    composicao: Composicao | None = None,
    modo: Modo = "automatico",
    registro: Registro | None = None,
) -> Resposta:
    pergunta_segura = _neutralizar(pergunta)

    analise = _analisar(pergunta_segura, gerador=gerador, chat=chat, modelo=modelo)
    if not analise.no_escopo:
        return Resposta(prompts.RECUSA, analise.model_dump(), [], fora_do_escopo=True)

    trechos = retriever.buscar(analise.consultas, k, analise.instrumentos)
    operacao = "nenhuma"
    if analise.notacao == "compor":
        composicao = composicao if composicao is not None else Composicao()  # sem sessão: vale só para este turno
        resultado = compor(pergunta_segura, composicao, gerador=gerador, chat=chat, modelo=modelo, modo=modo,
                           registro=registro)
        operacao = resultado.resumo
        partituras = [exemplo_da_versao(resultado.versao)] if resultado.versao else []
    else:
        partituras = _partituras(pergunta_segura, analise, trechos, gerador=gerador, chat=chat, modelo=modelo)

    # análise e trechos vão no system_instruction do turno: o histórico guarda só pergunta/resposta
    contexto = prompts.CONTEXTO_DO_TURNO.format(
        analise=_neutralizar(analise.model_dump_json(indent=2)),
        trechos="\n\n".join(_formatar(t) for t in trechos) or "nenhum",
        partituras="\n".join(f"- {_neutralizar(p.legenda)}: {_neutralizar(p.titulo)}" for p in partituras) or "nenhuma",
        operacao=_neutralizar(operacao),
    )
    resposta = chat.send_message(
        pergunta_segura,
        config=types.GenerateContentConfig(system_instruction=prompts.SISTEMA + contexto, temperature=0.5),
    )
    return Resposta(resposta.text or "", analise.model_dump(), trechos, False, partituras)


def _partituras(
    pergunta: str, analise: Analise, trechos: list[Trecho], *, gerador: Gerador, chat: SessaoChat, modelo: str
) -> list[Exemplo]:
    if analise.notacao == "nenhuma":
        return []
    if analise.notacao == "acervo":
        com_partitura = sorted((t for t in trechos if t.musicxml), key=lambda t: -t.score)  # mais relevantes
        reais = [
            Exemplo(f"{t.fonte} — {t.local}", t.musicxml, _legenda_acervo(t), "acervo")
            for t in com_partitura[:MAX_PARTITURAS_ACERVO]
        ]
        if reais:
            return reais
    gerada = _gerar_partitura(pergunta, analise, gerador=gerador, chat=chat, modelo=modelo)
    return [gerada] if gerada else []


def _legenda_acervo(t: Trecho) -> str:
    return f"Do acervo: {t.fonte}, {t.local}" + (f" — {t.compositor}" if t.compositor else "")


def _gerar_partitura(
    pergunta: str, analise: Analise, *, gerador: Gerador, chat: SessaoChat, modelo: str
) -> Exemplo | None:
    pedido = types.Content(role="user", parts=[types.Part(text=(
        f"<pergunta_usuario>\n{pergunta}\n</pergunta_usuario>\n"
        f"<analise_interna>\n{_neutralizar(analise.model_dump_json())}\n</analise_interna>"
    ))])
    try:
        resposta = gerador.generate_content(
            model=modelo,
            contents=[*chat.get_history(), pedido],
            config=types.GenerateContentConfig(
                system_instruction=prompts.NOTACAO.format(tecnicas=", ".join(TECNICAS)),
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=Partitura,
            ),
        )
        partitura = Partitura.model_validate_json(resposta.text or "")
    except (ValidationError, errors.APIError, httpx2.TransportError):
        return None  # etapa opcional: sem partitura neste turno, a resposta em texto segue normalmente
    partitura.compassos = partitura.compassos[:MAX_COMPASSOS_GERADOS]
    if not partitura.compassos:
        return None
    return Exemplo(partitura.titulo, para_musicxml(para_ir(partitura)), LEGENDA_GERADA, "gerado")


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
        fonte=_atributo(t.fonte),
        local=_atributo(t.local) or "?",
        autor=f' autor="{_atributo(t.compositor)}"' if t.compositor else "",
        texto=_neutralizar(t.texto),
    )


def _atributo(valor: str) -> str:
    return _neutralizar(valor).replace('"', "'")


def exemplo_da_versao(v: Versao) -> Exemplo:
    """Partitura de uma Versão da composição para o chat (MusicXML sempre; LilyPond quando o Abjad conseguir)."""
    return Exemplo(v.ir.titulo, para_musicxml(v.ir), f"Composição da conversa — {v.resumo}", "composicao",
                   _lilypond_ou_nada(v.ir))


def _lilypond_ou_nada(ir: MusicIR) -> dict[str, str]:
    """Fontes LilyPond da composição; se o Abjad não conseguir escrever algo, a partitura segue só pelo Verovio."""
    try:
        return para_lilypond(ir)
    except Exception:  # Abjad levanta tipos variados; a notação de reserva (MusicXML) já foi validada
        return {}

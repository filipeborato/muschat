"""Busca híbrida (densa + BM25, fusão RRF) e montagem do contexto do turno."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from muschat.rag.indice import VERSAO, Embedder, Indice, carregar, normalizar
from muschat.rag.leitores import Chunk, familia_de
from muschat.reasoning import Retriever, SemRetriever, Trecho

RRF_K = 60
BOOST_FAMILIA = 1.25
POR_LISTA = 20
MAX_CARACTERES = 12_000  # ~3.000 tokens

_TOKEN = re.compile(r"(?:[a-z0-9]+\.)+[a-z0-9]*|[a-z0-9]+")


def tokenizar(texto: str) -> list[str]:
    """Minúsculas, sem acento; abreviações (s.p.e., c.l.t., flz.) viram um token só, sem o ponto final."""
    sem_acento = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return [t.rstrip(".") for t in _TOKEN.findall(sem_acento)]


class RetrieverHibrido:
    def __init__(
        self,
        indice: Indice,
        embedder: Embedder,
        por_lista: int = POR_LISTA,
        max_caracteres: int = MAX_CARACTERES,
    ):
        self._indice, self._embedder = indice, embedder
        self._por_lista, self._max_caracteres = por_lista, max_caracteres
        # shortcut: BM25 e cosseno em memória, O(n) por consulta; trocar por FAISS/Tantivy acima de ~100k chunks
        self._bm25 = BM25Okapi([tokenizar(c.texto_embutido) for c in indice.chunks])
        self._obras = {c.arquivo: i for i, c in enumerate(indice.chunks) if c.tipo == "partitura" and c.local == "obra"}

    def buscar(self, consultas: list[str], k: int, instrumentos: Sequence[str] = ()) -> list[Trecho]:
        if not consultas:
            return []
        pontos: dict[int, float] = defaultdict(float)
        origens: dict[int, set[str]] = defaultdict(set)
        vetores = normalizar(np.asarray(self._embedder.embutir(consultas, "consulta"), dtype=np.float32))
        for consulta, vetor in zip(consultas, vetores):
            for origem, ranking in (("densa", self._densa(vetor)), ("lexica", self._lexica(consulta))):
                for posicao, i in enumerate(ranking, 1):
                    pontos[i] += 1 / (RRF_K + posicao)
                    origens[i].add(origem)

        alvo = {f for f in (familia_de(x) for x in instrumentos) if f}
        for i in pontos:
            if self._indice.chunks[i].familia in alvo:
                pontos[i] *= BOOST_FAMILIA
        ranking = sorted(pontos, key=lambda i: -pontos[i])
        return self._montar(ranking, pontos, origens, k)

    def _densa(self, vetor: np.ndarray) -> list[int]:
        sims = self._indice.vetores @ vetor
        return [int(i) for i in np.argsort(-sims)[: self._por_lista] if sims[i] > 0]

    def _lexica(self, consulta: str) -> list[int]:
        scores = self._bm25.get_scores(tokenizar(consulta))
        return [int(i) for i in np.argsort(-scores)[: self._por_lista] if scores[i] > 0]

    def _montar(self, ranking: list[int], pontos, origens, k: int) -> list[Trecho]:
        """Expande fichas de compasso com a ficha da obra, aplica k e orçamento, agrupa por fonte."""
        escolhidos: list[Trecho] = []
        vistos: set[int] = set()
        usados = 0
        for i in ranking:
            if len(escolhidos) >= k:
                break
            if i in vistos:
                continue
            chunk = self._indice.chunks[i]
            grupo = [i]  # ficha de compasso entra junto com a ficha da obra (nunca uma sem a outra)
            obra = self._obras.get(chunk.arquivo)
            if chunk.tipo == "partitura" and obra is not None and obra not in vistos and obra != i:
                grupo.insert(0, obra)
            tamanho = sum(len(self._indice.chunks[j].texto) for j in grupo)
            if len(escolhidos) + len(grupo) > k or usados + tamanho > self._max_caracteres:
                continue  # não cabe: tenta os próximos, menos relevantes
            for j in grupo:
                if j in origens:
                    escolhidos.append(_trecho(self._indice.chunks[j], pontos[j], tuple(sorted(origens[j]))))
                else:  # ficha da obra puxada pela ficha de compasso, não recuperada por si
                    escolhidos.append(_trecho(self._indice.chunks[j], 0.0, ("obra",)))
                vistos.add(j)
            usados += tamanho
        return _agrupar(escolhidos)


def _trecho(c: Chunk, score: float, origem: tuple[str, ...]) -> Trecho:
    return Trecho(
        texto=c.texto, fonte=c.fonte, local=c.local, id=c.id, score=round(score, 4), origem=origem,
        compositor=c.compositor, musicxml=c.extra.get("musicxml", ""),
    )


def _agrupar(trechos: list[Trecho]) -> list[Trecho]:
    ordem = {f: n for n, f in enumerate(dict.fromkeys(t.fonte for t in trechos))}
    return sorted(trechos, key=lambda t: ordem[t.fonte])  # sort estável: mantém a relevância dentro da fonte


def abrir_retriever(pasta: Path, embedder: Embedder, modelo: str) -> tuple[Retriever, str | None]:
    """Retriever híbrido do índice em disco; sem índice utilizável, o nulo + um aviso para a UI."""
    try:
        indice = carregar(pasta)
    except (ValueError, TypeError, OSError) as exc:  # corrompido ou de esquema antigo
        return SemRetriever(), f"Índice ilegível ({exc}); rode `uv run python -m muschat.rag.ingest`."
    if indice is None:
        return SemRetriever(), "Sem índice do acervo: rode `uv run python -m muschat.rag.ingest`."
    if indice.manifest.get("versao") != VERSAO:
        return SemRetriever(), "Índice de uma versão antiga do Muschat: reingira com `uv run python -m muschat.rag.ingest`."
    if (feito_com := indice.manifest.get("modelo")) != modelo:
        return SemRetriever(), f"Índice feito com `{feito_com}`, mas o modelo atual é `{modelo}`: reingira."
    return RetrieverHibrido(indice, embedder), None

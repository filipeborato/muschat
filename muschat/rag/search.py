"""Hybrid search (dense + BM25, RRF fusion) and turn context assembly."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from muschat.rag.index import VERSION, Embedder, Index, load_index, normalize
from muschat.rag.readers import Chunk, family_of
from muschat.reasoning import Excerpt, NullRetriever, Retriever

RRF_K = 60
FAMILY_BOOST = 1.25
PER_LIST = 20
MAX_CHARS = 12_000  # ~3,000 tokens

_TOKEN = re.compile(r"(?:[a-z0-9]+\.)+[a-z0-9]*|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, accent-stripped; abbreviations (`s.p.e.`, `c.l.t.`, `flz.`) become a single token without trailing dot."""
    no_accent = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return [t.rstrip(".") for t in _TOKEN.findall(no_accent)]


class HybridRetriever:
    def __init__(
        self,
        index: Index,
        embedder: Embedder,
        per_list: int = PER_LIST,
        max_chars: int = MAX_CHARS,
    ):
        self._index, self._embedder = index, embedder
        self._per_list, self._max_chars = per_list, max_chars
        self._bm25 = BM25Okapi([tokenize(c.embedded_text) for c in index.chunks])
        self._works = {
            c.file: i for i, c in enumerate(index.chunks) if c.kind == "score" and c.location == "work"
        }

    def search(self, queries: list[str], k: int, instruments: Sequence[str] = ()) -> list[Excerpt]:
        if not queries:
            return []
        scores: dict[int, float] = defaultdict(float)
        origins: dict[int, set[str]] = defaultdict(set)
        vectors = normalize(np.asarray(self._embedder.embed(queries, "query"), dtype=np.float32))
        for query, vector in zip(queries, vectors):
            for origin, ranking in (("dense", self._dense(vector)), ("lexical", self._lexical(query))):
                for position, i in enumerate(ranking, 1):
                    scores[i] += 1 / (RRF_K + position)
                    origins[i].add(origin)

        target_families = {f for f in (family_of(x) for x in instruments) if f}
        for i in scores:
            if self._index.chunks[i].family in target_families:
                scores[i] *= FAMILY_BOOST
        ranking = sorted(scores, key=lambda i: -scores[i])
        return self._assemble(ranking, scores, origins, k)

    def _dense(self, vector: np.ndarray) -> list[int]:
        sims = self._index.vectors @ vector
        return [int(i) for i in np.argsort(-sims)[: self._per_list] if sims[i] > 0]

    def _lexical(self, query: str) -> list[int]:
        bm25_scores = self._bm25.get_scores(tokenize(query))
        return [int(i) for i in np.argsort(-bm25_scores)[: self._per_list] if bm25_scores[i] > 0]

    def _assemble(self, ranking: list[int], scores, origins, k: int) -> list[Excerpt]:
        """Expand measure cards with their work card, enforce `k` and character budget, and group by source."""
        chosen: list[Excerpt] = []
        seen: set[int] = set()
        used_chars = 0
        for i in ranking:
            if len(chosen) >= k:
                break
            if i in seen:
                continue
            chunk = self._index.chunks[i]
            group = [i]
            work_idx = self._works.get(chunk.file)
            if chunk.kind == "score" and work_idx is not None and work_idx not in seen and work_idx != i:
                group.insert(0, work_idx)
            size = sum(len(self._index.chunks[j].text) for j in group)
            if len(chosen) + len(group) > k or used_chars + size > self._max_chars:
                continue
            for j in group:
                if j in origins:
                    chosen.append(_to_excerpt(self._index.chunks[j], scores[j], tuple(sorted(origins[j]))))
                else:
                    chosen.append(_to_excerpt(self._index.chunks[j], 0.0, ("work",)))
                seen.add(j)
            used_chars += size
        return _group_by_source(chosen)


def _to_excerpt(c: Chunk, score: float, origin: tuple[str, ...]) -> Excerpt:
    return Excerpt(
        text=c.text,
        source=c.source,
        location=c.location,
        id=c.id,
        score=round(score, 4),
        origin=origin,
        composer=c.composer,
        musicxml=c.extra.get("musicxml", ""),
    )


def _group_by_source(excerpts: list[Excerpt]) -> list[Excerpt]:
    order = {s: n for n, s in enumerate(dict.fromkeys(e.source for e in excerpts))}
    return sorted(excerpts, key=lambda e: order[e.source])


def open_retriever(directory: Path, embedder: Embedder, model: str) -> tuple[Retriever, str | None]:
    """Open a hybrid retriever from the on-disk index; returns `(NullRetriever, warning)` if unavailable."""
    try:
        index = load_index(directory)
    except (ValueError, TypeError, OSError) as exc:
        return NullRetriever(), f"Unreadable index ({exc}); run `uv run python -m muschat.rag.ingest`."
    if index is None:
        return NullRetriever(), "No corpus index found: run `uv run python -m muschat.rag.ingest`."
    if index.manifest.get("version") != VERSION:
        return NullRetriever(), "Index from an older Muschat version: re-ingest with `uv run python -m muschat.rag.ingest`."
    if (built_with := index.manifest.get("model")) != model:
        return NullRetriever(), f"Index built with `{built_with}`, but current model is `{model}`: re-ingest."
    return HybridRetriever(index, embedder), None

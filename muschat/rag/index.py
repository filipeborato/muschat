"""Embeddings and local on-disk vector index (numpy + jsonl)."""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from google.genai import errors, types

from muschat.rag.readers import Chunk

TextKind = Literal["document", "query"]
_TASK_TYPE = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}
BATCH_SIZE, DIMENSION = 100, 768
VERSION = 2  # increment when chunk/header format changes so old indexes request re-ingestion
QUOTA_ATTEMPTS = 4
MAX_WAIT_SECONDS = 90  # per-minute quota asks for < 60s; above that is daily quota
_RETRY_DELAY = re.compile(r"retryDelay\W+(\d+(?:\.\d+)?)s")


class Embedder(Protocol):
    def embed(self, texts: list[str], kind: TextKind) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(self, models: Any, model: str, dimension: int = DIMENSION, sleep=time.sleep):
        self._models, self._model, self._dimension, self._sleep = models, model, dimension, sleep

    def embed(self, texts: list[str], kind: TextKind) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=_TASK_TYPE[kind], output_dimensionality=self._dimension)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            r = self._with_quota_retry(
                lambda: self._models.embed_content(
                    model=self._model,
                    contents=[types.Content(parts=[types.Part(text=t)]) for t in texts[i : i + BATCH_SIZE]],
                    config=config,
                )
            )
            vectors += [list(e.values) for e in r.embeddings]
        return vectors

    def _with_quota_retry(self, call):
        """On HTTP 429 (per-minute free-tier quota), wait for the API's requested retryDelay."""
        for _ in range(QUOTA_ATTEMPTS - 1):
            try:
                return call()
            except errors.ClientError as exc:
                if exc.code != 429:
                    raise
                m = _RETRY_DELAY.search(str(exc.details))
                delay = float(m.group(1)) if m else 60.0
                if delay > MAX_WAIT_SECONDS:
                    raise
                self._sleep(delay + 1)
        return call()


@dataclass
class Index:
    chunks: list[Chunk]
    vectors: np.ndarray  # (n, d), row-normalized
    manifest: dict = field(default_factory=dict)


def normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def build_index(chunks: list[Chunk], embedder: Embedder, cache: dict[str, np.ndarray] | None = None) -> Index:
    """Embed chunks; any chunk whose `embedded_text` is already in `cache` skips the API."""
    cache = cache or {}
    missing = list(dict.fromkeys(c.embedded_text for c in chunks if c.embedded_text not in cache))
    new_vectors = embedder.embed(missing, "document") if missing else []
    if len(new_vectors) != len(missing):
        raise ValueError(f"embedder returned {len(new_vectors)} vectors for {len(missing)} chunks")
    vectors = {**cache, **dict(zip(missing, new_vectors))}
    return Index(chunks, normalize(np.asarray([vectors[c.embedded_text] for c in chunks], dtype=np.float32)))


def save_index(index: Index, directory: Path, manifest: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "vectors.npy", index.vectors)
    with open(directory / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in index.chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    index.manifest = manifest = {
        **manifest,
        "version": VERSION,
        "chunks": len(index.chunks),
        "dimension": int(index.vectors.shape[1]),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index(directory: Path) -> Index | None:
    files = [directory / n for n in ("vectors.npy", "chunks.jsonl", "manifest.json")]
    if not all(f.exists() for f in files):
        return None
    with open(files[1], encoding="utf-8") as f:
        chunks = [Chunk(**json.loads(line)) for line in f if line.strip()]
    manifest = json.loads(files[2].read_text(encoding="utf-8"))
    vectors = np.load(files[0])
    if vectors.ndim != 2 or len(chunks) != vectors.shape[0] or manifest.get("dimension") != vectors.shape[1]:
        raise ValueError(f"inconsistent index in {directory}; re-run ingestion")
    return Index(chunks, vectors, manifest)

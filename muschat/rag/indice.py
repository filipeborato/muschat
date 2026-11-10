"""Embeddings e índice vetorial local em disco (numpy + jsonl)."""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from google.genai import errors, types

from muschat.rag.leitores import Chunk

TipoTexto = Literal["documento", "consulta"]
_TASK_TYPE = {"documento": "RETRIEVAL_DOCUMENT", "consulta": "RETRIEVAL_QUERY"}
LOTE, DIMENSAO = 100, 768
TENTATIVAS_COTA = 4
_RETRY_DELAY = re.compile(r"retryDelay\W+(\d+(?:\.\d+)?)s")


class Embedder(Protocol):
    def embutir(self, textos: list[str], tipo: TipoTexto) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(self, models: Any, modelo: str, dimensao: int = DIMENSAO, dormir=time.sleep):
        self._models, self._modelo, self._dimensao, self._dormir = models, modelo, dimensao, dormir

    def embutir(self, textos: list[str], tipo: TipoTexto) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=_TASK_TYPE[tipo], output_dimensionality=self._dimensao)
        vetores: list[list[float]] = []
        for i in range(0, len(textos), LOTE):
            r = self._com_cota(lambda: self._models.embed_content(
                model=self._modelo, contents=textos[i : i + LOTE], config=config
            ))
            vetores += [list(e.values) for e in r.embeddings]
        return vetores

    def _com_cota(self, chamada):
        """No 429 (cota por minuto do free tier) espera o retryDelay pedido pela API."""
        for _ in range(TENTATIVAS_COTA - 1):
            try:
                return chamada()
            except errors.ClientError as exc:
                if exc.code != 429:
                    raise
                m = _RETRY_DELAY.search(str(exc.details))
                self._dormir((float(m.group(1)) if m else 60.0) + 1)
        return chamada()


@dataclass
class Indice:
    chunks: list[Chunk]
    vetores: np.ndarray  # (n, d), linhas normalizadas
    manifest: dict = field(default_factory=dict)


def normalizar(m: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(normas == 0, 1, normas)


def construir(chunks: list[Chunk], embedder: Embedder) -> Indice:
    vetores = embedder.embutir([c.texto_embutido for c in chunks], "documento")
    if len(vetores) != len(chunks):
        raise ValueError(f"embedder devolveu {len(vetores)} vetores para {len(chunks)} chunks")
    return Indice(chunks, normalizar(np.asarray(vetores, dtype=np.float32)))


def salvar(indice: Indice, pasta: Path, manifest: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    np.save(pasta / "vetores.npy", indice.vetores)
    with open(pasta / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in indice.chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    indice.manifest = manifest = {**manifest, "chunks": len(indice.chunks), "dimensao": int(indice.vetores.shape[1])}
    (pasta / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar(pasta: Path) -> Indice | None:
    arquivos = [pasta / n for n in ("vetores.npy", "chunks.jsonl", "manifest.json")]
    if not all(a.exists() for a in arquivos):
        return None
    with open(arquivos[1], encoding="utf-8") as f:
        chunks = [Chunk(**json.loads(linha)) for linha in f if linha.strip()]
    manifest = json.loads(arquivos[2].read_text(encoding="utf-8"))
    vetores = np.load(arquivos[0])
    if vetores.ndim != 2 or len(chunks) != vetores.shape[0] or manifest.get("dimensao") != vetores.shape[1]:
        raise ValueError(f"índice inconsistente em {pasta}; rode a ingestão de novo")
    return Indice(chunks, vetores, manifest)

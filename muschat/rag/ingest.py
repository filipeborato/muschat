"""Corpus ingestion: `uv run python -m muschat.rag.ingest [--corpus corpus] [--index index]`."""

import argparse
import hashlib
import os
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

import numpy as np

from muschat.rag.index import VERSION, Embedder, Index, build_index, load_index, save_index
from muschat.rag.readers import EXTENSIONS, read_file


def _corpus_files(corpus: Path) -> list[Path]:
    return sorted(
        p
        for p in corpus.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(corpus).parts)
    )


def ingest(corpus: Path, index_dir: Path, embedder: Embedder, model: str) -> Index:
    """Rebuild the index from `corpus` (idempotent); embeds only new or modified chunks."""
    chunks, hashes = [], {}
    for path in _corpus_files(corpus):
        try:
            read_chunks = read_file(path, corpus)
        except Exception as exc:
            raise ValueError(f"failed to read {path}: {exc}") from exc
        if read_chunks:
            chunks += read_chunks
            hashes[path.relative_to(corpus).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not chunks:
        raise ValueError(f"no readable files ({', '.join(EXTENSIONS)}) in {corpus}")
    index = build_index(chunks, embedder, _load_cache(index_dir, model))
    save_index(index, index_dir, {"model": model, "files": hashes})
    return index


def _load_cache(index_dir: Path, model: str) -> dict[str, np.ndarray]:
    """Reuse vectors from the previous index if built with the same model and schema version."""
    try:
        previous = load_index(index_dir)
    except (ValueError, OSError):
        return {}
    if not previous or previous.manifest.get("model") != model or previous.manifest.get("version") != VERSION:
        return {}
    return {c.embedded_text: v for c, v in zip(previous.chunks, previous.vectors)}


TIMEOUT_MS = 60_000


def _wait_for_quota(seconds: float) -> None:
    print(f"  per-minute embedding quota exhausted: waiting {seconds:.0f}s...", flush=True)
    time.sleep(seconds)


def main() -> int:
    from dotenv import load_dotenv
    from google import genai
    from google.genai import errors, types

    from muschat.config import MissingApiKeyError, load_config
    from muschat.rag.index import GeminiEmbedder

    parser = argparse.ArgumentParser(description="Index the Muschat corpus.")
    parser.add_argument("--corpus", type=Path, default=Path("corpus"))
    parser.add_argument("--index", type=Path, default=Path("index"))
    args = parser.parse_args()

    if not args.corpus.is_dir():
        print(f"Corpus directory not found: {args.corpus}", file=sys.stderr)
        return 1
    load_dotenv()
    secrets = Path(".streamlit/secrets.toml")
    try:
        cfg = load_config(dict(os.environ), tomllib.loads(secrets.read_text("utf-8")) if secrets.exists() else {})
    except MissingApiKeyError:
        print("Set GEMINI_API_KEY (.env, environment, or .streamlit/secrets.toml).", file=sys.stderr)
        return 1

    transient_codes = [408, 500, 502, 503, 504]
    retry = types.HttpOptions(
        timeout=TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=3, http_status_codes=transient_codes),
    )
    client = genai.Client(api_key=cfg.api_key, http_options=retry)
    try:
        embedder = GeminiEmbedder(client.models, cfg.embed_model, sleep=_wait_for_quota)
        print(f"Indexing {args.corpus}/ with {cfg.embed_model}...", flush=True)
        index = ingest(args.corpus, args.index, embedder, cfg.embed_model)
    except errors.ClientError as exc:
        if exc.code != 429:
            raise
        print(
            f"Embedding quota for {cfg.embed_model} exhausted (likely daily free-tier quota). "
            "Try again later or set another model in GEMINI_EMBED_MODEL (e.g., gemini-embedding-001). "
            "The previous index was kept.",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"{len(index.chunks)} chunks from {len(index.manifest['files'])} files -> {args.index}/")
    for field in ("kind", "family"):
        counts = Counter(getattr(c, field) or "(none)" for c in index.chunks)
        print(f"  by {field}: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

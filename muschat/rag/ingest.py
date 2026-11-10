"""Ingestão do corpus: `uv run python -m muschat.rag.ingest [--corpus corpus] [--index index]`."""

import argparse
import hashlib
import os
import sys
import tomllib
from collections import Counter
from pathlib import Path

from muschat.rag.indice import Embedder, Indice, construir, salvar
from muschat.rag.leitores import EXTENSOES, ler


def _arquivos(corpus: Path) -> list[Path]:
    return sorted(
        a for a in corpus.rglob("*")
        if a.is_file() and not any(p.startswith(".") for p in a.relative_to(corpus).parts)
    )


def ingerir(corpus: Path, pasta_indice: Path, embedder: Embedder, modelo: str) -> Indice:
    """Reconstrói o índice inteiro a partir do corpus (idempotente)."""
    # shortcut: reembute tudo a cada execução; usar os hashes do manifest p/ incremental se ficar lento
    chunks, hashes = [], {}
    for arq in _arquivos(corpus):
        try:
            lidos = ler(arq, corpus)
        except Exception as exc:  # XML/PDF/encoding quebrado: diga qual arquivo
            raise ValueError(f"falha ao ler {arq}: {exc}") from exc
        if lidos:
            chunks += lidos
            hashes[arq.relative_to(corpus).as_posix()] = hashlib.sha256(arq.read_bytes()).hexdigest()
    if not chunks:
        raise ValueError(f"nenhum arquivo legível ({', '.join(EXTENSOES)}) em {corpus}")
    indice = construir(chunks, embedder)
    salvar(indice, pasta_indice, {"modelo": modelo, "arquivos": hashes})
    return indice


def main() -> int:
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    from muschat.config import ChaveAusente, carregar_config
    from muschat.rag.indice import GeminiEmbedder

    parser = argparse.ArgumentParser(description="Indexa o corpus do Muschat.")
    parser.add_argument("--corpus", type=Path, default=Path("corpus"))
    parser.add_argument("--index", type=Path, default=Path("index"))
    args = parser.parse_args()

    if not args.corpus.is_dir():
        print(f"Pasta do corpus não encontrada: {args.corpus}", file=sys.stderr)
        return 1
    load_dotenv()
    segredos = Path(".streamlit/secrets.toml")
    try:
        cfg = carregar_config(dict(os.environ), tomllib.loads(segredos.read_text("utf-8")) if segredos.exists() else {})
    except ChaveAusente:
        print("Defina GEMINI_API_KEY (.env, ambiente ou .streamlit/secrets.toml).", file=sys.stderr)
        return 1

    # 429 fica com o GeminiEmbedder (espera o retryDelay); o SDK só repete falhas transitórias
    transitorias = [408, 500, 502, 503, 504]
    retry = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=3, http_status_codes=transitorias))
    cliente = genai.Client(api_key=cfg.api_key, http_options=retry)
    try:
        indice = ingerir(args.corpus, args.index, GeminiEmbedder(cliente.models, cfg.embed_model), cfg.embed_model)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"{len(indice.chunks)} chunks de {len(indice.manifest['arquivos'])} arquivos -> {args.index}/")
    for chave in ("tipo", "familia"):
        contagem = Counter(getattr(c, chave) or "—" for c in indice.chunks)
        print(f"  por {chave}: " + ", ".join(f"{k} {v}" for k, v in contagem.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

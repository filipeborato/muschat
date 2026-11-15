from types import SimpleNamespace

import numpy as np
import pytest
from google.genai import errors

from muschat.rag.index import GeminiEmbedder, build_index, load_index, save_index
from muschat.rag.readers import Chunk
from tests.conftest import FakeEmbedder


def _chunk(i, text, **kw):
    return Chunk(id=f"f.md#{i}", text=text, source="F", file="f.md", location=f"L{i}", kind="text", **kw)


def test_build_index_embeds_header_plus_text_as_document():
    emb = FakeEmbedder()
    build_index([_chunk(0, "aaa")], emb)
    texts, kind = emb.calls[0]
    assert texts == ["F | L0\naaa"]
    assert kind == "document"


def test_vectors_are_row_normalized():
    index = build_index([_chunk(0, "aaa"), _chunk(1, "bb")], FakeEmbedder())
    assert np.allclose(np.linalg.norm(index.vectors, axis=1), 1.0)


def test_save_and_load_preserves_chunks_and_vectors(tmp_path):
    chunks = [_chunk(0, "aaa", family="strings", work="q", extra={"musicxml": "<m/>"}), _chunk(1, "bb")]
    index = build_index(chunks, FakeEmbedder())
    save_index(index, tmp_path / "index", {"model": "fake"})

    loaded = load_index(tmp_path / "index")
    assert loaded is not None
    assert loaded.chunks == chunks
    assert np.allclose(loaded.vectors, index.vectors)
    assert loaded.manifest["model"] == "fake"
    assert loaded.manifest["chunks"] == 2


def test_missing_index_returns_none(tmp_path):
    assert load_index(tmp_path / "missing") is None


class FakeModels:
    def __init__(self):
        self.batches = []

    def embed_content(self, *, model, contents, config):
        self.batches.append((model, len(contents), config.task_type, config.output_dimensionality))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0]) for _ in contents])


def test_gemini_embedder_batches_up_to_100_with_task_type():
    models = FakeModels()
    vectors = GeminiEmbedder(models, "emb-x").embed(["t"] * 250, "query")
    assert len(vectors) == 250
    assert models.batches == [
        ("emb-x", 100, "RETRIEVAL_QUERY", 768),
        ("emb-x", 100, "RETRIEVAL_QUERY", 768),
        ("emb-x", 50, "RETRIEVAL_QUERY", 768),
    ]


def _err_429(delay="7s"):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "details": details}})


class QuotaModels(FakeModels):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def embed_content(self, *, model, contents, config):
        if self.failures:
            self.failures -= 1
            raise _err_429()
        return super().embed_content(model=model, contents=contents, config=config)


def test_gemini_embedder_waits_api_retry_delay_on_429():
    waits = []
    models = QuotaModels(failures=2)
    vectors = GeminiEmbedder(models, "emb-x", sleep=waits.append).embed(["t"] * 3, "document")
    assert len(vectors) == 3
    assert waits == [8.0, 8.0]  # retryDelay + 1s margin


def test_gemini_embedder_gives_up_after_repeated_quota_errors():
    with pytest.raises(errors.ClientError):
        GeminiEmbedder(QuotaModels(failures=99), "emb-x", sleep=lambda s: None).embed(["t"], "document")


def test_load_index_rejects_inconsistent_index(tmp_path):
    save_index(build_index([_chunk(0, "aaa"), _chunk(1, "bb")], FakeEmbedder()), tmp_path, {})
    lines = (tmp_path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "chunks.jsonl").write_text(lines[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistent"):
        load_index(tmp_path)


def test_build_index_rejects_embedder_returning_too_few_vectors():
    class ShortEmbedder:
        def embed(self, texts, kind):
            return [[1.0, 0.0]]

    with pytest.raises(ValueError, match="vectors"):
        build_index([_chunk(0, "a"), _chunk(1, "b")], ShortEmbedder())


def test_daily_quota_exhausted_fails_immediately_without_sleeping_hours():
    class DailyQuota(FakeModels):
        def embed_content(self, *, model, contents, config):
            raise _err_429("38197s")

    waits = []
    with pytest.raises(errors.ClientError):
        GeminiEmbedder(DailyQuota(), "emb-x", sleep=waits.append).embed(["t"], "document")
    assert waits == []

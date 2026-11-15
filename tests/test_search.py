import json
from typing import Any

import numpy as np

from muschat.rag.index import Index, normalize, save_index
from muschat.rag.readers import Chunk
from muschat.rag.search import HybridRetriever, open_retriever, tokenize
from muschat.reasoning import NullRetriever

FILLER = [("some other topic altogether", [0, 1, 0, 0])] * 4


class FixedEmbedder:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def embed(self, texts, kind):
        assert kind == "query"
        return [self.vectors[t] for t in texts]


def _index(docs):
    chunks, vectors = [], []
    for i, (text, vector, *extra) in enumerate(docs + FILLER):
        fields: dict[str, Any] = dict(source=f"F{i}", file=f"f{i}.md", location=f"L{i}", kind="text")
        fields.update(extra[0] if extra else {})
        chunks.append(Chunk(id=f"c{i}", text=text, **fields))
        vectors.append(vector)
    return Index(chunks, normalize(np.asarray(vectors, dtype=np.float32)))


def _ids(excerpts):
    return [e.id for e in excerpts]


def test_tokenize_preserves_abbreviations_and_strips_accents():
    assert tokenize("Sul pont. estr. (s.p.e.) — Técnica c.l.t., violino.") == [
        "sul", "pont", "estr", "s.p.e", "tecnica", "c.l.t", "violino",
    ]


def test_rrf_combines_dense_and_lexical():
    index = _index([
        ("high glassy timbre", [1, 0, 0, 0]),            # c0: dense only
        ("s.p.e. notation on violin", [0, 0, 1, 0]),     # c1: lexical only
        ("s.p.e. timbre", [0.9, 0.1, 0, 0]),             # c2: both
    ])
    r = HybridRetriever(index, FixedEmbedder({"s.p.e.": [1, 0, 0, 0]}), per_list=2)
    excerpts = r.search(["s.p.e."], k=6)
    assert _ids(excerpts)[0] == "c2"
    assert set(_ids(excerpts)) == {"c0", "c1", "c2"}
    by_id = {e.id: e for e in excerpts}
    assert by_id["c2"].origin == ("dense", "lexical")
    assert by_id["c0"].origin == ("dense",) and by_id["c1"].origin == ("lexical",)
    assert by_id["c2"].score > by_id["c0"].score


def test_multiple_queries_accumulate_and_deduplicate():
    index = _index([("a", [1, 0, 0, 0]), ("b", [0, 0, 1, 0])])
    emb = FixedEmbedder({"q1": [1, 0, 0, 0], "q2": [0.9, 0, 0.1, 0]})
    excerpts = HybridRetriever(index, emb, per_list=1).search(["q1", "q2"], k=6)
    assert _ids(excerpts) == ["c0"]


def test_instrument_family_boost_reranks():
    index = _index([
        ("frullato", [1, 0, 0, 0], {"family": "winds"}),
        ("frullato", [0.99, 0.01, 0, 0], {"family": "strings"}),
    ])
    r = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]}))
    assert _ids(r.search(["q"], k=2))[0] == "c0"
    assert _ids(r.search(["q"], k=2, instruments=["violin"]))[0] == "c1"


def test_measure_card_pulls_in_work_card_once():
    score_meta = {"kind": "score", "file": "q.musicxml", "source": "Quartet", "work": "q"}
    index = _index([
        ("m7 scratch", [1, 0, 0, 0], {**score_meta, "location": "m.7–9"}),
        ("m9 morendo", [0.95, 0.05, 0, 0], {**score_meta, "location": "m.9–11"}),
        ("Work: Quartet", [0, 0, 0, 1], {**score_meta, "location": "work"}),
    ])
    excerpts = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]}), per_list=2).search(["q"], k=6)
    assert _ids(excerpts) == ["c2", "c0", "c1"]
    assert excerpts[0].location == "work" and excerpts[0].origin == ("work",) and excerpts[0].score == 0


def test_work_card_never_appears_orphaned_when_measure_does_not_fit():
    score_meta = {"kind": "score", "file": "q.musicxml", "source": "Quartet", "work": "q"}
    index = _index([
        ("m7 scratch", [1, 0, 0, 0], {**score_meta, "location": "m.7–9"}),
        ("Work: Quartet", [0, 0, 0, 1], {**score_meta, "location": "work"}),
        ("loose entry", [0.5, 0.5, 0, 0]),
    ])
    excerpts = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]})).search(["q"], k=1)
    assert _ids(excerpts) == ["c2"]


def test_oversized_excerpt_is_skipped_and_smaller_ones_fit():
    index = _index([("x" * 2000, [1, 0, 0, 0]), ("y" * 300, [0.9, 0.1, 0, 0])])
    r = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]}), max_chars=1000)
    assert _ids(r.search(["q"], k=6))[:1] == ["c1"]


def test_char_budget_trims_less_relevant_excerpts():
    index = _index([("x" * 600, [1, 0, 0, 0]), ("y" * 600, [0.9, 0.1, 0, 0]), ("z" * 600, [0.8, 0.2, 0, 0])])
    r = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]}), max_chars=1300)
    assert _ids(r.search(["q"], k=6)) == ["c0", "c1"]


def test_k_caps_excerpt_count():
    index = _index([(f"doc {i}", [1 - i / 10, i / 10, 0, 0]) for i in range(5)])
    assert len(HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]})).search(["q"], k=3)) == 3


def test_groups_by_source_ordered_by_best_hit():
    index = _index([
        ("a", [1, 0, 0, 0], {"source": "Grimoire"}),
        ("b", [0.9, 0.1, 0, 0], {"source": "Book"}),
        ("c", [0.8, 0.2, 0, 0], {"source": "Grimoire"}),
    ])
    excerpts = HybridRetriever(index, FixedEmbedder({"q": [1, 0, 0, 0]})).search(["q"], k=6)
    assert [e.source for e in excerpts][:3] == ["Grimoire", "Grimoire", "Book"]


def test_empty_queries_skip_embedder():
    class Explode:
        def embed(self, texts, kind):
            raise AssertionError("should not embed")

    assert HybridRetriever(_index([("a", [1, 0, 0, 0])]), Explode()).search([], k=6) == []


def test_open_retriever_missing_index_returns_null_with_warning(tmp_path):
    retriever, warning = open_retriever(tmp_path / "index", FixedEmbedder({}), "emb-x")
    assert isinstance(retriever, NullRetriever)
    assert warning is not None and "ingest" in warning


def test_open_retriever_rejects_different_model(tmp_path):
    save_index(_index([("a", [1, 0, 0, 0])]), tmp_path, {"model": "emb-old"})
    retriever, warning = open_retriever(tmp_path, FixedEmbedder({}), "emb-x")
    assert isinstance(retriever, NullRetriever)
    assert warning is not None and "emb-old" in warning


def test_open_retriever_valid_index(tmp_path):
    save_index(_index([("a", [1, 0, 0, 0])]), tmp_path, {"model": "emb-x"})
    retriever, warning = open_retriever(tmp_path, FixedEmbedder({"q": [1, 0, 0, 0]}), "emb-x")
    assert warning is None
    assert [e.id for e in retriever.search(["q"], k=1)] == ["c0"]


def test_open_retriever_legacy_schema_warns_without_crashing(tmp_path):
    save_index(_index([("a", [1, 0, 0, 0])]), tmp_path, {"model": "emb-x"})
    jsonl = tmp_path / "chunks.jsonl"
    jsonl.write_text(jsonl.read_text(encoding="utf-8").replace('"id"', '"legacy_field": 1, "id"'), encoding="utf-8")
    retriever, warning = open_retriever(tmp_path, FixedEmbedder({}), "emb-x")
    assert isinstance(retriever, NullRetriever) and warning is not None


def test_open_retriever_rejects_older_index_version(tmp_path):
    save_index(_index([("a", [1, 0, 0, 0])]), tmp_path, {"model": "emb-x"})
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("version")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    retriever, warning = open_retriever(tmp_path, FixedEmbedder({}), "emb-x")
    assert isinstance(retriever, NullRetriever) and warning is not None and "re-ingest" in warning

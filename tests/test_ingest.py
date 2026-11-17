import pytest

from muschat.rag.index import load_index
from muschat.rag.ingest import ingest
from tests.conftest import FakeEmbedder


def _corpus(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "strings").mkdir(parents=True)
    (corpus / "strings" / "notes.md").write_text("# Notes\n\n## Sul pont.\n\naaa", encoding="utf-8")
    (corpus / "junk.mscz").write_bytes(b"zip")
    (corpus / ".hidden").mkdir()
    (corpus / ".hidden" / "x.md").write_text("# X\n\nbbb", encoding="utf-8")
    return corpus


def test_ingest_writes_reloadable_index(tmp_path):
    index = ingest(_corpus(tmp_path), tmp_path / "index", FakeEmbedder(), "fake-emb")
    loaded = load_index(tmp_path / "index")
    assert loaded is not None
    assert [c.id for c in loaded.chunks] == [c.id for c in index.chunks] == ["strings/notes.md#0"]
    assert loaded.manifest["model"] == "fake-emb" == index.manifest["model"]
    assert list(loaded.manifest["files"]) == ["strings/notes.md"]


def test_ingest_is_idempotent(tmp_path):
    corpus = _corpus(tmp_path)
    ingest(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    ingest(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    loaded = load_index(tmp_path / "index")
    assert loaded is not None and len(loaded.chunks) == 1


def test_corpus_without_readable_file_fails_with_clear_message(tmp_path):
    (tmp_path / "corpus").mkdir()
    with pytest.raises(ValueError, match="no readable files"):
        ingest(tmp_path / "corpus", tmp_path / "index", FakeEmbedder(), "fake-emb")
    assert load_index(tmp_path / "index") is None


def test_unreadable_file_fails_naming_the_file(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "broken.musicxml").write_text("<score-partwise>", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.musicxml"):
        ingest(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")


def test_reingest_only_embeds_new_or_modified_chunks(tmp_path):
    corpus = _corpus(tmp_path)
    ingest(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    (corpus / "new.md").write_text("# New\n\nccc", encoding="utf-8")

    emb = FakeEmbedder()
    index = ingest(corpus, tmp_path / "index", emb, "fake-emb")
    assert [len(texts) for texts, _ in emb.calls] == [1]  # only new.md
    assert len(index.chunks) == 2

    unchanged = FakeEmbedder()
    ingest(corpus, tmp_path / "index", unchanged, "fake-emb")
    assert unchanged.calls == []


def test_changing_model_reembeds_everything(tmp_path):
    corpus = _corpus(tmp_path)
    ingest(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    emb = FakeEmbedder()
    ingest(corpus, tmp_path / "index", emb, "other-emb")
    assert [len(texts) for texts, _ in emb.calls] == [1]

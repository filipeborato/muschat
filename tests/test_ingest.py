from muschat.rag.indice import carregar
from muschat.rag.ingest import ingerir
from tests.conftest import FakeEmbedder


def _corpus(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "cordas").mkdir(parents=True)
    (corpus / "cordas" / "notas.md").write_text("# Notas\n\n## Sul pont.\n\naaa", encoding="utf-8")
    (corpus / "lixo.mscz").write_bytes(b"zip")
    (corpus / ".oculto").mkdir()
    (corpus / ".oculto" / "x.md").write_text("# X\n\nbbb", encoding="utf-8")
    return corpus


def test_ingestao_grava_indice_que_recarrega(tmp_path):
    indice = ingerir(_corpus(tmp_path), tmp_path / "index", FakeEmbedder(), "fake-emb")
    lido = carregar(tmp_path / "index")
    assert lido is not None
    assert [c.id for c in lido.chunks] == [c.id for c in indice.chunks] == ["cordas/notas.md#0"]
    assert lido.manifest["modelo"] == "fake-emb" == indice.manifest["modelo"]
    assert list(lido.manifest["arquivos"]) == ["cordas/notas.md"]


def test_ingestao_e_idempotente(tmp_path):
    corpus = _corpus(tmp_path)
    ingerir(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    ingerir(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")
    lido = carregar(tmp_path / "index")
    assert lido is not None and len(lido.chunks) == 1


def test_corpus_sem_arquivo_legivel_falha_com_mensagem_clara(tmp_path):
    import pytest

    (tmp_path / "corpus").mkdir()
    with pytest.raises(ValueError, match="nenhum arquivo"):
        ingerir(tmp_path / "corpus", tmp_path / "index", FakeEmbedder(), "fake-emb")
    assert carregar(tmp_path / "index") is None


def test_arquivo_ilegivel_falha_nomeando_o_arquivo(tmp_path):
    import pytest

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "quebrado.musicxml").write_text("<score-partwise>", encoding="utf-8")
    with pytest.raises(ValueError, match="quebrado.musicxml"):
        ingerir(corpus, tmp_path / "index", FakeEmbedder(), "fake-emb")

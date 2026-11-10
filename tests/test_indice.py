from types import SimpleNamespace

import numpy as np

from muschat.rag.indice import GeminiEmbedder, carregar, construir, salvar
from muschat.rag.leitores import Chunk
from tests.conftest import FakeEmbedder


def _chunk(i, texto, **kw):
    return Chunk(id=f"f.md#{i}", texto=texto, fonte="F", arquivo="f.md", local=f"L{i}", tipo="texto", **kw)


def test_construir_embute_texto_com_cabecalho_como_documento():
    emb = FakeEmbedder()
    construir([_chunk(0, "aaa")], emb)
    textos, tipo = emb.chamadas[0]
    assert textos == ["F | L0\naaa"]
    assert tipo == "documento"


def test_vetores_sao_normalizados():
    indice = construir([_chunk(0, "aaa"), _chunk(1, "bb")], FakeEmbedder())
    assert np.allclose(np.linalg.norm(indice.vetores, axis=1), 1.0)


def test_salvar_e_carregar_preserva_chunks_e_vetores(tmp_path):
    chunks = [_chunk(0, "aaa", familia="cordas", obra="q", extra={"musicxml": "<m/>"}), _chunk(1, "bb")]
    indice = construir(chunks, FakeEmbedder())
    salvar(indice, tmp_path / "index", {"modelo": "fake"})

    lido = carregar(tmp_path / "index")
    assert lido is not None
    assert lido.chunks == chunks
    assert np.allclose(lido.vetores, indice.vetores)
    assert lido.manifest["modelo"] == "fake"
    assert lido.manifest["chunks"] == 2


def test_indice_ausente_devolve_none(tmp_path):
    assert carregar(tmp_path / "nao-existe") is None


class FakeModels:
    def __init__(self):
        self.lotes = []

    def embed_content(self, *, model, contents, config):
        self.lotes.append((model, len(contents), config.task_type, config.output_dimensionality))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0]) for _ in contents])


def test_gemini_embedder_envia_lotes_de_ate_100_com_task_type():
    models = FakeModels()
    vetores = GeminiEmbedder(models, "emb-x").embutir(["t"] * 250, "consulta")
    assert len(vetores) == 250
    assert models.lotes == [
        ("emb-x", 100, "RETRIEVAL_QUERY", 768),
        ("emb-x", 100, "RETRIEVAL_QUERY", 768),
        ("emb-x", 50, "RETRIEVAL_QUERY", 768),
    ]


def _erro_429(atraso="7s"):
    from google.genai import errors

    detalhes = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": atraso}]
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "details": detalhes}})


class ModelsComCota(FakeModels):
    def __init__(self, falhas):
        super().__init__()
        self.falhas = falhas

    def embed_content(self, *, model, contents, config):
        if self.falhas:
            self.falhas -= 1
            raise _erro_429()
        return super().embed_content(model=model, contents=contents, config=config)


def test_gemini_embedder_espera_o_retry_delay_da_api_no_429():
    esperas = []
    models = ModelsComCota(falhas=2)
    vetores = GeminiEmbedder(models, "emb-x", dormir=esperas.append).embutir(["t"] * 3, "documento")
    assert len(vetores) == 3
    assert esperas == [8.0, 8.0]  # retryDelay + 1s de folga


def test_gemini_embedder_desiste_apos_varias_cotas_esgotadas():
    import pytest
    from google.genai import errors

    with pytest.raises(errors.ClientError):
        GeminiEmbedder(ModelsComCota(falhas=99), "emb-x", dormir=lambda s: None).embutir(["t"], "documento")


def test_carregar_rejeita_indice_inconsistente(tmp_path):
    import pytest

    salvar(construir([_chunk(0, "aaa"), _chunk(1, "bb")], FakeEmbedder()), tmp_path, {})
    linhas = (tmp_path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "chunks.jsonl").write_text(linhas[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistente"):
        carregar(tmp_path)


def test_construir_rejeita_embedder_que_devolve_vetores_a_menos():
    import pytest

    class Curto:
        def embutir(self, textos, tipo):
            return [[1.0, 0.0]]

    with pytest.raises(ValueError, match="vetores"):
        construir([_chunk(0, "a"), _chunk(1, "b")], Curto())

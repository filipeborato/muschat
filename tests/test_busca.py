from typing import Any

import numpy as np

from muschat.rag.busca import RetrieverHibrido, tokenizar
from muschat.rag.indice import Indice, normalizar
from muschat.rag.leitores import Chunk

FILLER = [("outro assunto qualquer", [0, 1, 0, 0])] * 4


class EmbedderFixo:
    def __init__(self, vetores: dict[str, list[float]]):
        self.vetores = vetores

    def embutir(self, textos, tipo):
        assert tipo == "consulta"
        return [self.vetores[t] for t in textos]


def _indice(docs, **por_id):
    """docs: lista de (texto, vetor) ou (texto, vetor, campos extras do Chunk)."""
    chunks, vetores = [], []
    for i, (texto, vetor, *extra) in enumerate(docs + FILLER):
        campos: dict[str, Any] = dict(fonte=f"F{i}", arquivo=f"f{i}.md", local=f"L{i}", tipo="texto")
        campos.update(extra[0] if extra else {})
        chunks.append(Chunk(id=f"c{i}", texto=texto, **campos))
        vetores.append(vetor)
    return Indice(chunks, normalizar(np.asarray(vetores, dtype=np.float32)))


def _ids(trechos):
    return [t.id for t in trechos]


def test_tokenizar_preserva_abreviacoes_e_remove_acentos():
    assert tokenizar("Sul pont. estr. (s.p.e.) — Técnica c.l.t., violino.") == [
        "sul", "pont", "estr", "s.p.e", "tecnica", "c.l.t", "violino",
    ]


def test_rrf_combina_densa_e_lexica():
    indice = _indice([
        ("timbre vidrento agudo", [1, 0, 0, 0]),          # c0: só denso
        ("grafia s.p.e. no violino", [0, 0, 1, 0]),       # c1: só léxico
        ("s.p.e. timbre", [0.9, 0.1, 0, 0]),              # c2: nos dois
    ])
    r = RetrieverHibrido(indice, EmbedderFixo({"s.p.e.": [1, 0, 0, 0]}), por_lista=2)
    trechos = r.buscar(["s.p.e."], k=6)
    assert _ids(trechos)[0] == "c2"
    assert set(_ids(trechos)) == {"c0", "c1", "c2"}
    por_id = {t.id: t for t in trechos}
    assert por_id["c2"].origem == ("densa", "lexica")
    assert por_id["c0"].origem == ("densa",) and por_id["c1"].origem == ("lexica",)
    assert por_id["c2"].score > por_id["c0"].score


def test_varias_consultas_somam_e_deduplicam():
    indice = _indice([("a", [1, 0, 0, 0]), ("b", [0, 0, 1, 0])])
    emb = EmbedderFixo({"q1": [1, 0, 0, 0], "q2": [0.9, 0, 0.1, 0]})
    trechos = RetrieverHibrido(indice, emb, por_lista=1).buscar(["q1", "q2"], k=6)
    assert _ids(trechos) == ["c0"]


def test_boost_por_familia_dos_instrumentos_reordena():
    indice = _indice([
        ("frullato", [1, 0, 0, 0], {"familia": "sopros"}),
        ("frullato", [0.99, 0.01, 0, 0], {"familia": "cordas"}),
    ])
    r = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]}))
    assert _ids(r.buscar(["q"], k=2))[0] == "c0"
    assert _ids(r.buscar(["q"], k=2, instrumentos=["violino"]))[0] == "c1"


def test_ficha_de_compasso_traz_a_ficha_da_obra_uma_vez():
    partitura = {"tipo": "partitura", "arquivo": "q.musicxml", "fonte": "Quarteto", "obra": "q"}
    indice = _indice([
        ("m7 scratch", [1, 0, 0, 0], {**partitura, "local": "m.7–9"}),
        ("m9 morendo", [0.95, 0.05, 0, 0], {**partitura, "local": "m.9–11"}),
        ("Obra: Quarteto", [0, 0, 0, 1], {**partitura, "local": "obra"}),
    ])
    trechos = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]}), por_lista=2).buscar(["q"], k=6)
    assert _ids(trechos) == ["c2", "c0", "c1"]
    assert trechos[0].local == "obra" and trechos[0].origem == ("obra",) and trechos[0].score == 0


def test_ficha_da_obra_nunca_fica_orfa_quando_o_compasso_nao_cabe():
    partitura = {"tipo": "partitura", "arquivo": "q.musicxml", "fonte": "Quarteto", "obra": "q"}
    indice = _indice([
        ("m7 scratch", [1, 0, 0, 0], {**partitura, "local": "m.7–9"}),
        ("Obra: Quarteto", [0, 0, 0, 1], {**partitura, "local": "obra"}),
        ("verbete solto", [0.5, 0.5, 0, 0]),
    ])
    trechos = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]})).buscar(["q"], k=1)
    assert _ids(trechos) == ["c2"]


def test_trecho_grande_demais_e_pulado_e_os_menores_entram():
    indice = _indice([("x" * 2000, [1, 0, 0, 0]), ("y" * 300, [0.9, 0.1, 0, 0])])
    r = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]}), max_caracteres=1000)
    assert _ids(r.buscar(["q"], k=6))[:1] == ["c1"]


def test_orcamento_de_caracteres_corta_os_menos_relevantes():
    indice = _indice([("x" * 600, [1, 0, 0, 0]), ("y" * 600, [0.9, 0.1, 0, 0]), ("z" * 600, [0.8, 0.2, 0, 0])])
    r = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]}), max_caracteres=1300)
    assert _ids(r.buscar(["q"], k=6)) == ["c0", "c1"]


def test_k_limita_o_numero_de_trechos():
    indice = _indice([(f"doc {i}", [1 - i / 10, i / 10, 0, 0]) for i in range(5)])
    assert len(RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]})).buscar(["q"], k=3)) == 3


def test_agrupa_por_fonte_na_ordem_da_melhor():
    indice = _indice([
        ("a", [1, 0, 0, 0], {"fonte": "Grimório"}),
        ("b", [0.9, 0.1, 0, 0], {"fonte": "Livro"}),
        ("c", [0.8, 0.2, 0, 0], {"fonte": "Grimório"}),
    ])
    trechos = RetrieverHibrido(indice, EmbedderFixo({"q": [1, 0, 0, 0]})).buscar(["q"], k=6)
    assert [t.fonte for t in trechos][:3] == ["Grimório", "Grimório", "Livro"]


def test_sem_consultas_nao_chama_embedder():
    class Explode:
        def embutir(self, textos, tipo):
            raise AssertionError("não devia embutir")

    assert RetrieverHibrido(_indice([("a", [1, 0, 0, 0])]), Explode()).buscar([], k=6) == []


def test_abrir_retriever_sem_indice_devolve_nulo_com_aviso(tmp_path):
    from muschat.rag.busca import abrir_retriever
    from muschat.reasoning import SemRetriever

    retriever, aviso = abrir_retriever(tmp_path / "index", EmbedderFixo({}), "emb-x")
    assert isinstance(retriever, SemRetriever)
    assert aviso is not None and "ingest" in aviso


def test_abrir_retriever_recusa_indice_de_outro_modelo(tmp_path):
    from muschat.rag.busca import abrir_retriever
    from muschat.rag.indice import salvar
    from muschat.reasoning import SemRetriever

    salvar(_indice([("a", [1, 0, 0, 0])]), tmp_path, {"modelo": "emb-antigo"})
    retriever, aviso = abrir_retriever(tmp_path, EmbedderFixo({}), "emb-x")
    assert isinstance(retriever, SemRetriever)
    assert aviso is not None and "emb-antigo" in aviso


def test_abrir_retriever_com_indice_valido(tmp_path):
    from muschat.rag.busca import abrir_retriever
    from muschat.rag.indice import salvar

    salvar(_indice([("a", [1, 0, 0, 0])]), tmp_path, {"modelo": "emb-x"})
    retriever, aviso = abrir_retriever(tmp_path, EmbedderFixo({"q": [1, 0, 0, 0]}), "emb-x")
    assert aviso is None
    assert [t.id for t in retriever.buscar(["q"], k=1)] == ["c0"]


def test_abrir_retriever_com_indice_de_esquema_antigo_avisa_sem_quebrar(tmp_path):
    from muschat.rag.busca import abrir_retriever
    from muschat.rag.indice import salvar
    from muschat.reasoning import SemRetriever

    salvar(_indice([("a", [1, 0, 0, 0])]), tmp_path, {"modelo": "emb-x"})
    jsonl = tmp_path / "chunks.jsonl"
    jsonl.write_text(jsonl.read_text(encoding="utf-8").replace('"id"', '"campo_velho": 1, "id"'), encoding="utf-8")
    retriever, aviso = abrir_retriever(tmp_path, EmbedderFixo({}), "emb-x")
    assert isinstance(retriever, SemRetriever) and aviso is not None

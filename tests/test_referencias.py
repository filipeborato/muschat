import json

from muschat.referencias import PECAS, gerar


def test_pecas_de_referencia_sao_completas_e_reprodutiveis(tmp_path):
    a = gerar("a", raiz=tmp_path)
    b = gerar("b", raiz=tmp_path)
    nomes = [nome for nome, _, _ in PECAS]
    dados = json.loads((a / "metricas.json").read_text(encoding="utf-8"))
    assert list(dados) == nomes
    assert all(dados[n]["notas"] > 0 and "saltos_acima_da_oitava" in dados[n] for n in nomes)
    checklist = (a / "checklist.md").read_text(encoding="utf-8")
    assert all(f"## {n}" in checklist for n in nomes)
    for n in nomes:  # mesma semente, mesmo plano: a mesma partitura
        assert (a / f"{n}.musicxml").read_text(encoding="utf-8") == (b / f"{n}.musicxml").read_text(encoding="utf-8")

"""Unit tests for canonical English reference piece generation (`muschat/references.py`) and baseline equivalence."""

import json

from muschat.references import PIECES, generate_references
from muschat.referencias import gerar as legacy_gerar


def test_reference_pieces_are_complete_reproducible_and_match_baseline(tmp_path):
    a = generate_references("a", root=tmp_path)
    b = generate_references("b", root=tmp_path)
    legacy = legacy_gerar("legacy", raiz=tmp_path)
    names = [name for name, _, _ in PIECES]
    data = json.loads((a / "metricas.json").read_text(encoding="utf-8"))
    legacy_data = json.loads((legacy / "metricas.json").read_text(encoding="utf-8"))
    assert list(data) == names
    for n in names:
        assert {k: v for k, v in data[n].items() if k not in ("request", "summary")} == {
            k: v for k, v in legacy_data[n].items() if k not in ("pedido", "resumo")
        }
    assert all(data[n]["notas"] > 0 and "saltos_acima_da_oitava" in data[n] for n in names)
    checklist = (a / "checklist.md").read_text(encoding="utf-8")
    assert all(f"## {n}" in checklist for n in names)
    for n in names:
        xml_a = (a / f"{n}.musicxml").read_text(encoding="utf-8")
        xml_b = (b / f"{n}.musicxml").read_text(encoding="utf-8")
        xml_legacy = (legacy / f"{n}.musicxml").read_text(encoding="utf-8")
        assert xml_a == xml_b == xml_legacy

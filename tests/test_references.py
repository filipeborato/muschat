"""Unit tests for canonical English reference piece generation (`muschat/references.py`)."""

import json

from muschat.references import PIECES, generate_references


def test_reference_pieces_are_complete_and_reproducible(tmp_path):
    a = generate_references("a", root=tmp_path)
    b = generate_references("b", root=tmp_path)
    names = [name for name, _, _ in PIECES]
    data = json.loads((a / "metrics.json").read_text(encoding="utf-8"))
    assert list(data) == names
    assert all(data[n]["notes"] > 0 and "leaps_over_octave" in data[n] for n in names)
    checklist = (a / "checklist.md").read_text(encoding="utf-8")
    assert all(f"## {n}" in checklist for n in names)
    for n in names:
        xml_a = (a / f"{n}.musicxml").read_text(encoding="utf-8")
        xml_b = (b / f"{n}.musicxml").read_text(encoding="utf-8")
        assert xml_a == xml_b

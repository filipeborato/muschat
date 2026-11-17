import math
import random
import xml.etree.ElementTree as ET
from fractions import Fraction as F
from pathlib import Path

import pytest

from muschat.notation.lilypond import to_lilypond
from muschat.notation.musicxml import spell_pitch, to_musicxml
from muschat.om_patches import PATCHES, PatchContext, material_score
from muschat.score_ir import Note, Provenance
from tests.test_procedures import _run

LETTERS = "C C G C E G B C D E F G A B B C D D E E F F G G G A A B B B B C".split()


@pytest.mark.parametrize("fundamental,offset", [(3600, 0), (3800, 1), (4100, 3), (3650, 0)])
@pytest.mark.parametrize("microtone", [0, 1, 2])
def test_harmonic_import_spells_all_32_partials(fundamental, offset, microtone):
    values = [fundamental + 1200 * math.log2(n) for n in range(1, 33)]
    ctx = PatchContext(
        dict(fundamental=fundamental, partials=32, microtone=microtone),
        "",
        Path("."),
        None,
        None,
        random.Random(1),
        Provenance(source="openmusic"),
    )
    ir = PATCHES["harmonic_series"].read_output(values, ctx)
    pitches = [e.pitches[0] for _, _, e in ir.events() if isinstance(e, Note)]
    step = 100 // 2**microtone
    assert [p.midicents for p in pitches] == [round(v / step) * step for v in values]
    assert [p.spelling for p in pitches] == ["CDEFGAB"[("CDEFGAB".index(l) + offset) % 7] for l in LETTERS]
    if fundamental == 3600 and microtone == 1:
        assert spell_pitch(pitches[6]) == ("B", F(-3, 2), 4)
        assert spell_pitch(pitches[10]) == ("F", F(1, 2), 5)
        assert spell_pitch(pitches[12]) == ("A", F(-1, 2), 5)
        notes = ET.fromstring(to_musicxml(ir)).findall(".//note")
        for idx, letter, alter, accidental in [
            (6, "B", "-1.5", "three-quarters-flat"),
            (10, "F", "0.5", "quarter-sharp"),
            (12, "A", "-0.5", "quarter-flat"),
        ]:
            assert notes[idx].findtext("pitch/step") == letter
            assert notes[idx].findtext("pitch/alter") == alter
            assert notes[idx].findtext("accidental") == accidental
        ly = to_lilypond(ir)["score"]
        assert "btqf'4" in ly and "fqs''4" in ly and "aqf''4" in ly


def test_harmonic_series_preserves_spelling_across_octaves():
    ir = _run("harmonic_series", fundamental=3600, partials=16, microtone=1, measures=32, rests=0)
    expected = {
        round((3600 + 1200 * math.log2(n)) / 50) * 50 % 1200: l for n, l in enumerate(LETTERS[:16], 1)
    }
    pitches = [p for _, _, e in ir.events() if isinstance(e, Note) for p in e.pitches]
    assert {p.midicents % 1200 for p in pitches} == set(expected)
    assert all(p.spelling == expected[p.midicents % 1200] for p in pitches)
    orchestrated = _run("orchestrate_material", ir)
    assert all(
        p.spelling == expected[p.midicents % 1200]
        for _, _, e in orchestrated.events()
        if isinstance(e, Note)
        for p in e.pitches
    )


def test_spectralize_preserves_spelling_on_all_chord_pitches():
    current = material_score([6950, 7750, 8050], title="Spectrum", provenance=Provenance(source="composer"))
    for _, _, e in current.events():
        if isinstance(e, Note):
            e.pitches.append(e.pitches[0])
    ir = _run("spectralize", current, fundamental=3600, partials=16, microtone=1)
    expected = {round((3600 + 1200 * math.log2(n)) / 50) * 50: l for n, l in enumerate(LETTERS[:16], 1)}
    assert all(
        p.spelling == expected[p.midicents] for _, _, e in ir.events() if isinstance(e, Note) for p in e.pitches
    )

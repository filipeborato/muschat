"""Unit tests for canonical English `adapt_instrumentation` (`muschat/orchestration.py`)."""

import random

import pytest

from muschat.notation.lilypond import to_lilypond
from muschat.notation.musicxml import to_musicxml
from muschat.orchestration import adapt_instrumentation
from muschat.plan import Operation, Plan
from muschat.procedures import CATALOG
from muschat.procedures.common import unplayable_techniques
from muschat.score_ir import Note, ScoreIR
from tests.test_contemporary import material, run_proc


def _quartet(measures: int = 8) -> ScoreIR:
    proc = CATALOG["harmonic_series"]
    params = {k: f.default for k, f in proc.parameters.items()} | {"measures": float(measures)}
    plan = Plan(goal="quartet", operations=[Operation(procedure="harmonic_series")])
    assert proc.fn is not None
    return proc.fn(None, plan, params, random.Random(1))


def test_quartet_becomes_duo_with_ranges_clefs_and_active_lines():
    source = _quartet(measures=8)
    before = source.model_dump_json()
    duo, warning = adapt_instrumentation(source, ["flute", "cello"])
    assert [p.instrument for p in duo.parts] == ["Flute", "Cello"]
    assert [p.clef for p in duo.parts] == ["treble", "bass"]
    assert duo.measures == source.measures
    assert "4 to 2" in warning and "cello ← Cello" in warning
    for part in duo.parts:
        notes = [e for e in part.voices[0].events if isinstance(e, Note)]
        assert notes
        assert all(
            len(n.pitches) == 1 and part.range[0] <= n.pitches[0].midicents <= part.range[1]
            for n in notes
        )
    assert not unplayable_techniques(duo)
    assert source.model_dump_json() == before
    assert to_musicxml(duo) and to_lilypond(duo)


def test_expansion_renames_ids_and_preserves_tuplets():
    source = run_proc("deform_rhythm", material(), ratio=1)
    source.parts = source.parts[:1]
    duo, _ = adapt_instrumentation(source, ["flute", "cello"])
    ScoreIR.model_validate(duo.model_dump())
    for part in duo.parts:
        assert [(e.start, e.duration) for e in part.voices[0].events] == [
            (e.start, e.duration) for e in source.parts[0].voices[0].events
        ]
        assert len(part.voices[0].tuplets) == len(source.parts[0].voices[0].tuplets)
    assert to_musicxml(duo) and to_lilypond(duo)


def test_entirely_silent_source_does_not_invent_notes():
    source = material()
    for part in source.parts:
        for voice in part.voices:
            voice.events = []
    with pytest.raises(ValueError, match="no voice with notes"):
        adapt_instrumentation(source, ["flute", "cello"])

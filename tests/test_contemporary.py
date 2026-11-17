"""Unit tests for contemporary procedures (`detune`, `timbral_substitution`, `rarefy`, `micropolyphony`,
`deform_rhythm`) and aesthetic recipes (`muschat/recipes.py`).
"""

import json
import random
from fractions import Fraction as F

import pytest

from muschat.instruments import instrument
from muschat.notation.lilypond import to_lilypond
from muschat.notation.musicxml import from_musicxml, to_musicxml
from muschat.plan import Operation, Plan, Selection
from muschat.procedures import CATALOG
from muschat.procedures.common import unplayable_techniques
from muschat.recipes import RECIPES, documentation, expand_recipe
from muschat.references import generate_recipe_references
from muschat.score_ir import Measure, Note, Part, Pitch, Provenance, Rest, ScoreIR, Tuplet, Voice, ties_to


def material() -> ScoreIR:
    parts = []
    for k, (name, pitch) in enumerate((("flute", 7600), ("violin", 6900), ("cello", 4800)), 1):
        inst = instrument(name)
        events = [
            Note(
                id=f"P{k}E{n}",
                start=F(n, 8),
                duration=F(1, 8),
                pitches=[Pitch(midicents=pitch + (n % 3) * 100)],
                dynamic="mf",
                provenance=Provenance(source="composer"),
            )
            for n in range(16)
        ]
        events[7] = Rest(
            id=f"P{k}E7",
            start=F(7, 8),
            duration=F(1, 8),
            provenance=Provenance(source="composer"),
        )
        parts.append(
            Part(
                id=f"P{k}",
                instrument=name,
                range=inst.range,
                clef=inst.clef,
                voices=[Voice(id=f"P{k}V", events=events)],
            )
        )
    return ScoreIR(title="Reference", measures=[Measure(numerator=4, denominator=4)] * 2, parts=parts)


def run_proc(name: str, current: ScoreIR | None = None, selection: Selection | None = None, techniques=(), **params) -> ScoreIR:
    current = current or material()
    proc = CATALOG[name]
    plan = Plan(
        goal="t",
        operations=[Operation(procedure=name)],
        selection=selection or Selection(),
        techniques=list(techniques),
    )
    complete = {n: f.default for n, f in proc.parameters.items()} | params
    assert proc.fn is not None
    updated = proc.fn(current, plan, complete, random.Random(17))
    updated = ScoreIR.model_validate(updated.model_dump())
    xml = to_musicxml(updated)
    assert from_musicxml(xml.encode())[0]
    assert to_lilypond(updated)
    return updated


def notes(piece: ScoreIR, part: int = 0) -> list[Note]:
    return [e for e in piece.parts[part].voices[0].events if isinstance(e, Note)]


@pytest.mark.parametrize("name", ["detune", "timbral_substitution", "rarefy", "micropolyphony", "deform_rhythm"])
def test_contemporary_procedures_are_notatable_deterministic_and_preserve_outside_selection(name):
    before = material()
    sel = Selection(parts=["P2"], start_measure=2, end_measure=2)
    a = run_proc(name, before, sel)
    assert a == run_proc(name, before, sel)
    assert a.parts[0] == before.parts[0] and a.parts[2] == before.parts[2]
    assert [e for e in a.parts[1].voices[0].events if e.start < 1] == before.parts[1].voices[0].events[:8]


def test_detune_curves_and_glissandi_without_crossing_rests():
    before = material()
    updated = run_proc("detune", before, deviation=100, resolution=2, curve=0, glissandos=1)
    ns, orig = notes(updated), notes(before)
    deviations = [b.pitches[0].midicents - a.pitches[0].midicents for a, b in zip(orig, ns)]
    assert deviations[0] == 0 and deviations[-1] == 100 and deviations == sorted(deviations)
    assert 25 in deviations and all(d % 25 == 0 for d in deviations)
    assert not ns[6].glissando and not ns[-1].glissando and ns[0].glissando
    assert [(n.start, n.duration, n.dynamic, n.techniques) for n in ns] == [
        (n.start, n.duration, n.dynamic, n.techniques) for n in orig
    ]
    osc = run_proc("detune", before, curve=1, resolution=2)
    ds = [b.pitches[0].midicents - a.pitches[0].midicents for a, b in zip(orig, notes(osc))]
    assert min(ds) < 0 < max(ds)
    assert "glissando" in to_musicxml(updated) and "glissando" in to_lilypond(updated)["score"]


def test_idiomatic_noise_preserves_rhythm_dynamics_and_filters_family():
    before = material()
    updated = run_proc("timbral_substitution", before, start=1, end=1, noise_family=1)
    assert notes(updated, 0) == notes(before, 0)
    assert all(n.techniques for n in notes(updated, 1))
    assert not unplayable_techniques(updated)
    assert [(e.id, e.start, e.duration, getattr(e, "dynamic", None)) for _, _, e in updated.events()] == [
        (e.id, e.start, e.duration, getattr(e, "dynamic", None)) for _, _, e in before.events()
    ]
    assert "<notehead>x</notehead>" in to_musicxml(updated)


def test_rarefy_removes_attacks_prolongs_and_produces_playable_harmonics():
    before = material()
    updated = run_proc("rarefy", before, keep=0.3, harmonics=1, accents=0)
    assert len(notes(updated, 1)) < len(notes(before, 1))
    assert any(n.duration > F(1, 8) for n in notes(updated, 1))
    assert all(n.dynamic in (None, "pppp") for n in notes(updated, 1))
    assert all("harmonic" in n.techniques for n in notes(updated, 1))
    assert not unplayable_techniques(updated)
    assert all(not notes(run_proc("rarefy", before, keep=0), k) for k in range(3))


def test_micropolyphony_offsets_parts_within_pitch_range():
    updated = run_proc("micropolyphony", variation=0, offset=1, spread=100)
    assert [notes(updated, k)[0].start for k in range(3)] == [F(0), F(1, 16), F(1, 8)]
    for part in updated.parts:
        assert all(
            part.range[0] <= a.midicents <= part.range[1]
            for e in part.voices[0].events
            if isinstance(e, Note)
            for a in e.pitches
        )


@pytest.mark.parametrize("mask,ratio", [(1, (5, 4)), (2, (7, 4)), (4, (6, 4)), (8, (5, 6))])
def test_deform_rhythm_preserves_notes_and_writes_tuplet_ratios(mask, ratio):
    before = material()
    updated = run_proc("deform_rhythm", before, ratio=1, tuplets=mask)
    assert [(n.id, n.pitches, n.dynamic, n.techniques) for n in notes(updated)] == [
        (n.id, n.pitches, n.dynamic, n.techniques) for n in notes(before)
    ]
    assert {(q.numerator, q.denominator) for p in updated.parts for v in p.voices for q in v.tuplets} == {ratio}


@pytest.mark.parametrize("recipe_name", list(RECIPES))
def test_recipes_expand_into_valid_english_operations(recipe_name):
    plan = Plan(
        goal="recipe",
        recipe=recipe_name,
        recipe_rationale="transform material",
        seed=17,
        operations=[Operation(procedure="harmonic_series", parameters=[{"name": "measures", "value": 2}])],
    )
    expanded = expand_recipe(plan)
    assert len(expanded.operations) > 1
    assert all(op.procedure in CATALOG for op in expanded.operations)
    assert expanded.hypotheses


def test_recipes_documentation_renders_english_markdown():
    doc = documentation()
    for name, rec in RECIPES.items():
        assert f"- **{name}**: {rec.description}" in doc


def test_detune_preserves_sustained_ties_at_boundary():
    before = material()
    voice = before.parts[1].voices[0]
    voice.events[1].pitches = voice.events[0].pitches
    voice.events[0].tied = True
    updated = run_proc("detune", before, deviation=200, resolution=2)
    assert ties_to(*updated.parts[1].voices[0].events[:2])
    voice.events[7] = voice.events[6].model_copy(update={"id": "P2E7", "start": F(7, 8), "tied": True})
    voice.events[8].pitches = voice.events[7].pitches
    updated = run_proc("detune", before, Selection(start_measure=2), deviation=200, curve=1)
    assert ties_to(*updated.parts[1].voices[0].events[7:9])


def test_canon_fills_empty_imported_destination_within_pitch_range():
    before = material()
    before.parts[2].range = None
    before.parts[2].voices[0].events = []
    updated = run_proc("micropolyphony", before, variation=0)
    assert notes(updated, 2)
    low, high = instrument("cello").range
    assert all(low <= a.midicents <= high for n in notes(updated, 2) for a in n.pitches)


def test_canon_refuses_event_that_would_vanish_under_quantization():
    before = material()
    voice = before.parts[0].voices[0]
    voice.events[0].duration = F(1, 192)
    voice.tuplets = [Tuplet(id="short", numerator=3, denominator=1, events=[voice.events[0].id])]
    with pytest.raises(ValueError, match="too short"):
        run_proc("micropolyphony", before, variation=0)


def test_rounded_deviation_never_exceeds_maximum():
    before = material()
    updated = run_proc("detune", before, deviation=75, resolution=1)
    assert all(
        abs(a.pitches[0].midicents - b.pitches[0].midicents) <= 75
        for a, b in zip(notes(before), notes(updated))
    )


def test_deform_rhythm_lengthens_gesture_using_available_rest():
    before = material()
    voice = before.parts[0].voices[0]
    voice.events = [
        voice.events[0],
        Rest(id="breath", start=F(1, 8), duration=F(7, 8), provenance=voice.events[0].provenance),
        *voice.events[8:],
    ]
    updated = run_proc("deform_rhythm", before, ratio=1, min_factor=2, max_factor=2, tuplets=8)
    assert notes(updated)[0].duration > F(1, 8)


def test_rarefy_absorbs_notes_inside_tuplet():
    before = material()
    voice = before.parts[1].voices[0]
    voice.events = [voice.events[k].model_copy(update={"start": F(k, 12), "duration": F(1, 12)}) for k in range(3)]
    voice.events += [
        Rest(id="rest1", start=F(1, 4), duration=F(3, 4), provenance=voice.events[0].provenance),
        Rest(id="rest2", start=F(1), duration=F(1), provenance=voice.events[0].provenance),
    ]
    voice.tuplets = [Tuplet(id="q", numerator=3, denominator=2, events=[e.id for e in voice.events[:3]])]
    updated = run_proc("rarefy", before, Selection(parts=["P2"]), keep=0.6, harmonics=0, accents=0)
    assert any(n.duration > F(1, 12) for n in notes(updated, 1))


def test_generate_recipes_across_four_reference_pieces_with_metrics(tmp_path):
    folder = generate_recipe_references("recipes", root=tmp_path)
    data = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    assert len(data) == 24
    assert len(list(folder.glob("*.musicxml"))) == 24
    for name, m in data.items():
        if name.endswith("sciarrino"):
            assert m["after"]["attacks_per_quarter_summed"] < m["before"]["attacks_per_quarter_summed"]
        if name.endswith("lachenmann"):
            assert not m["after"]["unplayable_techniques"]

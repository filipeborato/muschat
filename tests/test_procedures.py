"""Unit tests for canonical English procedures (`muschat/procedures/`), `check_constraints`, and seed determinism."""

import math
import random
from fractions import Fraction as F

import pytest

from muschat.constraints import Constraint, check_constraints
from muschat.notation.musicxml import to_musicxml
from muschat.plan import Plan, Selection
from muschat.procedures import CATALOG
from muschat.procedures.common import unplayable_techniques
from muschat.score_ir import Note, Rest, gestures, locate


def _run(name: str, ir=None, selection=None, seed=1, instruments=None, **params):
    proc = CATALOG[name]
    complete = {k: params.get(k, f.default) for k, f in proc.parameters.items()}
    plan = Plan(
        goal="test",
        operations=[{"procedure": name}],
        selection=selection or Selection(),
        instruments=instruments or [],
        techniques=["harmonic"],
    )
    assert proc.fn is not None
    updated = proc.fn(ir, plan, complete, random.Random(seed))
    to_musicxml(updated)
    return updated


@pytest.fixture(scope="module")
def quartet():
    return _run("harmonic_series", measures=4)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_density_preserves_register_spelling_and_leap_bound(seed):
    initial = _run("harmonic_series", measures=16, partials=32, rests=0.5, seed=seed)
    updated = _run("density", initial, start=1, end=4, seed=seed)
    for before, after in zip(initial.parts, updated.parts):
        pool = {
            p.midicents: p.spelling
            for v in before.voices
            for e in v.events
            if isinstance(e, Note)
            for p in e.pitches
        }
        line = [e.pitches[0] for e in after.voices[0].events if isinstance(e, Note)]
        assert all(p.midicents in pool and p.spelling == pool[p.midicents] for p in line)
        assert all(abs(a.midicents - b.midicents) <= 1200 for a, b in zip(line, line[1:]))


def _notes(ir, part=None):
    return [e for p, _, e in ir.events() if isinstance(e, Note) and (part is None or p.id == part)]


def _rhythm(ir):
    return [(e.id, e.start, e.duration, type(e)) for _, _, e in ir.events()]


def _pitches(ir, part=None):
    return [[a.midicents for a in e.pitches] for e in _notes(ir, part)]


# --- Melodic procedures ------------------------------------------------------------------------------------------------


def test_transpose_preserves_rhythm_and_adds_interval(quartet):
    transposed = _run("transpose", quartet, interval=150)
    assert _rhythm(transposed) == _rhythm(quartet)
    assert _pitches(transposed) == [[a + 150 for a in chord] for chord in _pitches(quartet)]


def test_invert_twice_returns_to_original(quartet):
    twice = _run("invert", _run("invert", quartet, axis=6000), axis=6000)
    assert _pitches(twice) == _pitches(quartet)
    assert _pitches(_run("invert", quartet, axis=6000), "P1")[0][0] == 12000 - _pitches(quartet, "P1")[0][0]


def test_invert_without_axis_mirrors_around_first_pitch_of_each_voice(quartet):
    inv = _run("invert", quartet)
    for part in ("P1", "P4"):
        first = _pitches(quartet, part)[0][0]
        assert [a for [a] in _pitches(inv, part)] == [2 * first - a for [a] in _pitches(quartet, part)]


def test_retrograde_reverses_pitch_order_and_preserves_rhythm(quartet):
    ret = _run("retrograde", quartet)
    assert _rhythm(ret) == _rhythm(quartet)
    assert _pitches(ret, "P2") == _pitches(quartet, "P2")[::-1]


def test_rotate_shifts_pitches(quartet):
    original = _pitches(quartet, "P3")
    assert _pitches(_run("rotate", quartet, steps=2), "P3") == original[2:] + original[:2]


def test_scale_intervals_scales_distance_from_first_pitch(quartet):
    sel = Selection(parts=["P4"])
    scaled = _run("scale_intervals", quartet, selection=sel, factor=0.5)
    base = _pitches(quartet, "P4")[0][0]
    expected = [base + round((a - base) / 2 / 50) * 50 for [a] in _pitches(quartet, "P4")]
    assert [a for [a] in _pitches(scaled, "P4")] == expected
    assert _pitches(scaled, "P1") == _pitches(quartet, "P1")


def test_melodic_operation_respects_measure_selection(quartet):
    transposed = _run("transpose", quartet, selection=Selection(start_measure=3), interval=100)
    locations = locate(quartet)
    for before, after in zip(_notes(quartet), _notes(transposed)):
        changed = before.pitches != after.pitches
        assert changed == (locations[before.id][2] >= 3)


# --- Rhythmic procedures -----------------------------------------------------------------------------------------------


def _total_duration(ir, part):
    return sum((e.duration for p, _, e in ir.events() if p.id == part), F(0))


def test_augmentation_doubles_durations_within_region(quartet):
    aug = _run("augment_diminish", quartet, selection=Selection(parts=["P1"]), exponent=1)
    assert _total_duration(aug, "P1") <= _total_duration(quartet, "P1")
    first_before, first_after = _notes(quartet, "P1")[0], _notes(aug, "P1")[0]
    if first_before.duration <= F(1, 4):
        assert first_after.duration == 2 * first_before.duration
    assert _rhythm(aug)[-1][1] < 4


def test_diminution_repeats_material_to_fill_region(quartet):
    dim = _run("augment_diminish", quartet, selection=Selection(parts=["P2"]), exponent=-1)
    assert len(_notes(dim, "P2")) == 2 * len(_notes(quartet, "P2"))
    assert _pitches(dim, "P2") == _pitches(quartet, "P2") * 2


def test_subdivide_creates_tuplets_with_repeated_pitches(quartet):
    sub = _run("subdivide", quartet, selection=Selection(parts=["P4"]), division=3, ratio=1)
    [voice] = sub.parts[3].voices
    assert voice.tuplets and all(t.numerator == 3 and t.denominator == 2 for t in voice.tuplets)
    events = {e.id: e for e in voice.events}
    for t in voice.tuplets:
        members = [events[i] for i in t.events]
        assert len({tuple(a.midicents for a in m.pitches) for m in members}) == 1
        assert sum((m.duration for m in members), F(0)) in {F(1, 2), F(1, 4), F(1, 8), F(3, 8)}
    assert sub.parts[0] == quartet.parts[0]


def test_permute_durations_preserves_measure_durations(quartet):
    permuted = _run("permute_durations", quartet, seed=4)
    locations = locate(quartet)
    for part in ("P1", "P4"):
        by_m = lambda ir: sorted((locations[e.id][2], e.duration) for q, _, e in ir.events() if q.id == part)
        assert by_m(permuted) == by_m(quartet)


# --- Density and progressive procedures --------------------------------------------------------------------------------


def test_density_reaches_target_attacks_per_measure(quartet):
    dense = _run("density", quartet, start=0.5, end=2)
    locations = locate(dense)
    for part in ("P1", "P2", "P3", "P4"):
        attacks = [sum(1 for e in _notes(dense, part) if locations[e.id][2] == n) for n in range(1, 5)]
        assert attacks == [2, 4, 6, 8]


def test_progressive_register_shifts_more_at_end(quartet):
    reg = _run("progressive_register", quartet, selection=Selection(parts=["P1"]), start=0, end=1200)
    shifts = [b[0] - a[0] for a, b in zip(_pitches(quartet, "P1"), _pitches(reg, "P1"))]
    assert shifts[0] == 0 and shifts == sorted(shifts) and shifts[-1] > 900


def test_progressive_dynamics_marks_first_attack_of_each_measure(quartet):
    dyn = _run("progressive_dynamics", quartet, start=2, end=8)
    marked = [e.dynamic for e in _notes(dyn, "P4") if e.dynamic]
    assert marked[0] == "pp" and marked[-1] == "fff" and len(marked) == 4


def test_distribute_spreads_source_respecting_ranges(quartet):
    dist = _run("distribute", quartet, source_part=1)
    source = sorted(a for [a] in _pitches(quartet, "P1"))
    received = sorted(a for p in ("P1", "P2", "P3", "P4") for chord in _pitches(dist, p) for a in chord)
    assert len(received) == len(source)
    assert [v for v in check_constraints(dist, []) if v.constraint == "range"] == []


# --- Constraint checker and seed determinism ---------------------------------------------------------------------------


def test_constraint_checker_pinpoints_violation(quartet):
    below = _run(
        "transpose",
        quartet,
        selection=Selection(parts=["P4"], start_measure=2, end_measure=2),
        interval=-2400,
    )
    [v, *_] = [v for v in check_constraints(below, []) if v.constraint == "range"]
    assert v.part == "P4" and v.measure == 2 and v.event.startswith("P4V1E")
    assert check_constraints(quartet, []) == []


def test_constraint_checker_forbidden_interval_no_unison_and_max_density(quartet):
    first_two = _notes(quartet, "P1")[:2]
    interval = abs(first_two[1].pitches[0].midicents - first_two[0].pitches[0].midicents)
    violations = check_constraints(
        quartet,
        [
            Constraint(kind="forbidden_interval", value=interval, parts=["P1"]),
            Constraint(kind="max_density", value=0.25),
        ],
    )
    kinds = {v.constraint for v in violations}
    assert "forbidden_interval" in kinds and "max_density" in kinds
    p1 = quartet.parts[0]
    copied = p1.voices[0].model_copy(
        update={"id": "XV1", "events": [e.model_copy(update={"id": "X" + e.id}) for e in p1.voices[0].events]}
    )
    doubled = quartet.model_copy(
        update={"parts": [*quartet.parts, p1.model_copy(update={"id": "X", "voices": [copied]})]}
    )
    unisons = check_constraints(doubled, [Constraint(kind="no_unison", parts=["P1", "X"])])
    assert unisons and all(v.part == "X" for v in unisons)


@pytest.mark.parametrize(
    "name,params",
    [
        ("density", {"start": 3, "end": 1}),
        ("subdivide", {"ratio": 0.5}),
        ("distribute", {}),
        ("augment_diminish", {"exponent": -2}),
        ("permute_durations", {}),
    ],
)
def test_same_seed_produces_identical_result(quartet, name, params):
    assert _run(name, quartet, seed=9, **params) == _run(name, quartet, seed=9, **params)


def test_all_procedures_preserve_or_create_valid_events(quartet):
    for name, proc in CATALOG.items():
        if proc.requires_score and proc.fn and name != "events_from_spectrum":
            updated = _run(name, quartet)
            assert all(isinstance(e, (Note, Rest)) for _, _, e in updated.events())


def test_apply_techniques_marks_notes_in_selection(quartet):
    proc = CATALOG["apply_techniques"]
    plan = Plan(
        goal="t",
        operations=[{"procedure": "apply_techniques"}],
        techniques=["harmonic"],
        selection=Selection(parts=["P2"]),
    )
    assert proc.fn is not None
    updated = proc.fn(quartet, plan, {"ratio": 1}, random.Random(1))
    assert all(e.techniques == ["harmonic"] for e in _notes(updated, "P2"))
    assert all(e.techniques == [] for e in _notes(updated, "P1"))
    with pytest.raises(ValueError):
        proc.fn(quartet, plan.model_copy(update={"techniques": []}), {"ratio": 1}, random.Random(1))


def test_apply_techniques_draws_one_technique_per_note(quartet):
    plan = Plan(
        goal="t",
        operations=[{"procedure": "apply_techniques"}],
        techniques=["sul ponticello", "pizzicato", "col legno battuto"],
    )
    fn = CATALOG["apply_techniques"].fn
    assert fn is not None
    updated = fn(quartet, plan, {"ratio": 1}, random.Random(3))
    used = [e.techniques for e in _notes(updated)]
    assert all(len(t) == 1 for t in used) and len({t[0] for t in used}) == 3


# --- Spectralize -------------------------------------------------------------------------------------------------------


def _signs(seq):
    return [(b > a) - (b < a) for a, b in zip(seq, seq[1:])]


def _melodies(ir):
    return {
        (p.id, v.id): [e.pitches[0].midicents for e in v.events if isinstance(e, Note)]
        for p in ir.parts
        for v in p.voices
    }


def test_spectralize_uses_only_partials_preserves_contour_and_limits_leap(quartet):
    updated = _run("spectralize", quartet, fundamental=3600, partials=16, microtone=1, max_leap=1200)
    valid_partials = {round((3600 + 1200 * math.log2(n)) / 50) * 50 for n in range(1, 17)}
    assert {a for pitches in _pitches(updated) for a in pitches} <= valid_partials
    before, after = _melodies(quartet), _melodies(updated)
    same = inverted = 0
    for key, seq in after.items():
        for s0, s1 in zip(_signs(before[key]), _signs(seq)):
            same += s0 == s1
            inverted += s0 * s1 < 0
        assert all(abs(b - a) <= 1200 for a, b in zip(seq, seq[1:]))
    assert inverted == 0 and same >= 0.8 * sum(len(s) - 1 for s in after.values())
    for p in updated.parts:
        assert all(
            p.range[0] <= a <= p.range[1] for pitches in _pitches(updated, p.id) for a in pitches
        )
    assert _rhythm(updated) == _rhythm(quartet)


def test_spectralize_plans_register_from_high_to_low_and_respects_selection(quartet):
    updated = _run("spectralize", quartet)
    mean_pitch = {
        p.id: sum(a for al in _pitches(updated, p.id) for a in al) / len(_pitches(updated, p.id))
        for p in updated.parts
    }
    assert mean_pitch["P1"] > mean_pitch["P3"] > mean_pitch["P4"] and mean_pitch["P2"] > mean_pitch["P4"]
    only_p2 = _run("spectralize", quartet, selection=Selection(parts=["P2"]))
    assert _pitches(only_p2, "P1") == _pitches(quartet, "P1") and _pitches(only_p2, "P2") != _pitches(quartet, "P2")


def test_spectralize_only_requires_partials_for_selected_parts():
    duo = _run("harmonic_series", instruments=["piccolo", "cello"], measures=2)
    updated = _run("spectralize", duo, selection=Selection(parts=["P2"]), fundamental=3600, partials=4)
    assert _pitches(updated, "P1") == _pitches(duo, "P1")
    with pytest.raises(ValueError, match="Piccolo"):
        _run("spectralize", duo, fundamental=3600, partials=4)


# --- Gestural techniques -----------------------------------------------------------------------------------------------


def _by_gesture(ir, techniques, selection=None, seed=1, **params):
    plan = Plan(
        goal="t",
        operations=[{"procedure": "gestural_techniques"}],
        techniques=techniques,
        selection=selection or Selection(),
    )
    complete = {k: params.get(k, f.default) for k, f in CATALOG["gestural_techniques"].parameters.items()}
    fn = CATALOG["gestural_techniques"].fn
    assert fn is not None
    updated = fn(ir, plan, complete, random.Random(seed))
    return updated, to_musicxml(updated)


def test_gestural_techniques_apply_to_entire_gesture_and_only_where_playable(quartet):
    updated, xml = _by_gesture(quartet, ["sul ponticello", "harmonic"], ratio=1)
    with_tech = 0
    for p in updated.parts:
        for g in gestures(p.voices[0].events):
            techs = {tuple(n.techniques) for n in g}
            assert len(techs) == 1
            with_tech += techs != {()}
    assert with_tech and xml.count(">sul pont.<") <= with_tech
    assert unplayable_techniques(updated) == []
    assert _by_gesture(quartet, ["sul ponticello"], seed=4)[0] == _by_gesture(quartet, ["sul ponticello"], seed=4)[0]
    only_p3 = _by_gesture(quartet, ["sul ponticello"], selection=Selection(parts=["P3"]), ratio=1)[0]
    assert all(n.techniques == [] for n in _notes(only_p3, "P1")) and any(n.techniques for n in _notes(only_p3, "P3"))


def test_gestural_techniques_refuse_technique_unplayable_in_all_gestures(quartet):
    with pytest.raises(ValueError, match="no gesture"):
        _by_gesture(quartet, ["whistle tone"])


def test_gestural_techniques_preserve_existing_articulations(quartet):
    marked = quartet.model_copy(deep=True)
    for _, _, e in marked.events():
        if isinstance(e, Note):
            e.techniques = ["staccato"]
    updated, _ = _by_gesture(marked, ["sul ponticello"], ratio=1)
    assert all("staccato" in n.techniques for n in _notes(updated))
    assert any("sul ponticello" in n.techniques for n in _notes(updated))

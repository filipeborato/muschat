"""Semitone pitch grid by default; microtonality only when the user explicitly requests it."""

import json

import pytest

from muschat.composition import Composition
from muschat.notation.model import ExampleMeasure, ExampleNote, ScoreExample, to_score_ir
from muschat.orchestrator import compose, reapply, resume
from muschat.plan import requests_microtones
from muschat.recipes import RECIPES
from muschat.score_ir import Note
from tests.test_composition import FakeChat, FakeGenerator, _plan


def _pitches(ir, parts=None):
    return [
        p.midicents
        for part, _, e in ir.events()
        if isinstance(e, Note) and (parts is None or part.id in parts)
        for p in e.pitches
    ]


def _run(request, plan, composition=None):
    c = composition or Composition()
    r = compose(request, c, generator=FakeGenerator(plan), chat=FakeChat(), model="m")
    assert r.version is not None, r.summary
    return c, r.version


def test_spectral_request_without_explicit_microtones_stays_on_semitones():
    _, v = _run("spectral quartet over the partials of C2", _plan(parameters={"microtone": 1, "partials": 16}))
    assert all(p % 100 == 0 for p in _pitches(v.ir))
    assert v.experiment.parameters[0]["microtone"] == 0 and not v.experiment.plan.microtonal


def test_explicit_quarter_tone_request_keeps_requested_resolution():
    _, v = _run("spectral quartet over C2 partials in quarter-tones", _plan(parameters={"microtone": 1, "partials": 16}))
    assert any(p % 100 == 50 for p in _pitches(v.ir))
    assert v.experiment.plan.microtonal


@pytest.mark.parametrize("recipe", sorted(RECIPES))
def test_recipes_do_not_enable_microtones_implicitly(recipe):
    plan = _plan(parameters={"partials": 16})
    plan["recipe"] = recipe
    _, v = _run(f"eight measures in the {recipe} aesthetic", plan)
    assert all(p % 100 == 0 for p in _pitches(v.ir))


def test_explicit_eighth_tone_detune_keeps_eighth_tones():
    c, _ = _run("quartet", _plan(parameters={"partials": 16}))
    detune = _plan("detune", {"deviation": 100, "resolution": 2, "curve": 1})
    _, v = _run("detune it in eighth-tones", detune, c)
    assert any(p % 50 == 25 for p in _pitches(v.ir))


def test_tempered_edit_preserves_existing_microtonal_material():
    c, base = _run("quartet in quarter-tones", _plan(parameters={"microtone": 1, "partials": 16}))
    transpose = _plan("transpose", {"interval": 150}, selection={"parts": ["P4"]})
    _, v = _run("transpose the cello up", transpose, c)
    assert v.experiment.parameters[0]["interval"] == 200
    assert _pitches(v.ir, {"P1", "P2", "P3"}) == _pitches(base.ir, {"P1", "P2", "P3"})
    assert _pitches(v.ir, {"P4"}) == [p + 200 for p in _pitches(base.ir, {"P4"})]
    assert any(p % 100 for p in _pitches(v.ir))


def test_legacy_experiment_reapply_keeps_recorded_microtones():
    c, base = _run("quartet in quarter-tones", _plan(parameters={"microtone": 1, "partials": 16}))
    raw = json.loads(c.to_json())
    for version in raw["versions"]:
        del version["experiment"]["plan"]["microtonal"]
    legacy = Composition.from_json(json.dumps(raw))
    assert legacy.current.experiment.plan.microtonal
    r = reapply(legacy, legacy.current.experiment.parameters)
    assert r.version is not None, r.summary
    assert _pitches(r.version.ir) == _pitches(base.ir)


@pytest.mark.parametrize(("request_text", "microtonal"), [("spectral quartet", False), ("quartet with eighth-tones", True)])
def test_openmusic_harmonic_series_follows_request_policy(tmp_path, request_text, microtonal):
    from tests.test_engines import OUTPUT_16, WITH_OM

    c = Composition()
    plan = _plan(parameters={"partials": 16, "microtone": 2}, instruments=[])
    r = compose(request_text, c, generator=FakeGenerator(plan), chat=FakeChat(), model="m", mode="openmusic", registry=WITH_OM, tasks_dir=tmp_path)
    assert r.task is not None, r.summary
    done = resume(c, OUTPUT_16)
    assert done.version is not None, done.summary
    assert any(p % 100 for p in _pitches(done.version.ir)) == microtonal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("spectral music with harmonics and sul ponticello", False),
        ("detune by 1200 cents", False),
        ("quarter-tone cluster", True),
        ("use microtones", True),
        ("acordes em quartos de tom", True),
        ("com microtonalismo", True),
        ("detune by 30 cents", True),
        ("without quarter-tones please", False),
        ("sem microtons", False),
        ("1/4 tone cluster", True),
        ("A4 half-sharp", True),
        ("detune by 1.5 semitones", True),
    ],
)
def test_explicit_microtonal_intent_detection(text, expected):
    assert requests_microtones(text) == expected


def test_illustrative_example_snaps_quarter_tones_unless_requested():
    example = ScoreExample(title="t", instrument="violin", measures=[ExampleMeasure(notes=[ExampleNote(pitches=["A4+"], duration="whole")])])
    assert _pitches(to_score_ir(example, microtonal=False)) == [7000]
    assert _pitches(to_score_ir(example)) == [6950]


def test_explicit_quarter_tones_with_recipe_reach_the_recipe_chain():
    c, _ = _run("quartet", _plan(parameters={"partials": 16}))
    plan = {**_plan("transpose", {"interval": 0}), "recipe": "scelsi"}
    _, v = _run("scelsi in quarter-tones", plan, c)
    assert v.experiment.parameters[-1]["resolution"] == 1
    assert any(p % 100 == 50 for p in _pitches(v.ir))


def test_small_tempered_detune_still_moves_pitches():
    c, base = _run("quartet", _plan(parameters={"partials": 16}))
    _, v = _run("detune it slightly", _plan("detune", {"deviation": 50, "curve": 1}), c)
    assert v.experiment.parameters[0]["deviation"] == 100
    assert _pitches(v.ir) != _pitches(base.ir) and all(p % 100 == 0 for p in _pitches(v.ir))


@pytest.mark.parametrize(
    ("procedure", "parameters"),
    [("scale_intervals", {"factor": 1.5}), ("progressive_register", {"start": 0, "end": 350}), ("invert", {"axis": 6025})],
)
def test_tempered_transformations_stay_on_semitones(procedure, parameters):
    c, _ = _run("quartet", _plan(parameters={"partials": 16}))
    _, v = _run("transform it", _plan(procedure, parameters), c)
    assert all(p % 100 == 0 for p in _pitches(v.ir))


def test_extended_techniques_without_microtones_keep_notes_and_techniques():
    plan = _plan(parameters={"partials": 16})
    plan["techniques"] = ["sul ponticello", "harmonic"]
    plan["operations"].append({"procedure": "apply_techniques", "parameters": [{"name": "ratio", "value": 1}]})
    _, v = _run("quartet with extended techniques", plan)
    notes = [e for _, _, e in v.ir.events() if isinstance(e, Note)]
    assert notes and any(e.techniques for e in notes)
    assert all(p % 100 == 0 for p in _pitches(v.ir))

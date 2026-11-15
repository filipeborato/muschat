import json
from types import SimpleNamespace

import pytest

from muschat.composition import Composition
from muschat.notation.musicxml import to_musicxml
from muschat.notation.render import render_svg
from muschat.orchestrator import compose, reapply
from muschat.procedures import CATALOG, Procedure
from muschat.score_ir import Note

QUARTET = ["violin", "violin", "viola", "cello"]


def _plan(procedure="harmonic_series", parameters=None, selection=None, seed=7, instruments=QUARTET):
    return {
        "goal": "test",
        "instruments": instruments,
        "operations": [
            {
                "procedure": procedure,
                "parameters": [{"name": k, "value": v} for k, v in (parameters or {}).items()],
            }
        ],
        "selection": selection or {},
        "seed": seed,
    }


class FakeGenerator:
    def __init__(self, *plans):
        self.texts, self.calls = [p if isinstance(p, str) else json.dumps(p) for p in plans], []

    def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(contents=contents, config=config))
        return SimpleNamespace(text=self.texts.pop(0))


class FakeChat:
    def get_history(self):
        return []


def _compose(composition, plan, catalog=CATALOG, request="request"):
    return compose(request, composition, generator=FakeGenerator(plan), chat=FakeChat(), model="m", catalog=catalog)


def _quartet(measures=4):
    c = Composition()
    assert _compose(c, _plan(parameters={"measures": measures})).version is not None
    return c


# --- first composition turn --------------------------------------------------------------------------------------------


def test_valid_plan_creates_four_part_version_with_algorithm_provenance():
    c = Composition()
    r = _compose(c, _plan(parameters={"measures": 8}))
    assert r.version is not None
    ir = r.version.ir
    assert [p.instrument for p in ir.parts] == ["Violin", "Violin", "Viola", "Cello"]
    assert len(ir.measures) == 8
    events = [e for _, _, e in ir.events()]
    assert events and {e.provenance.source for e in events} == {"algorithm"}
    assert c.current is r.version
    assert r.version.experiment["seed"] == 7 and r.version.experiment["procedures"] == ["harmonic_series"]
    assert render_svg(to_musicxml(ir))


def test_pitches_are_partials_of_fundamental_within_range():
    assert (cur := _quartet().current) is not None
    cello = cur.ir.parts[3]
    pitches = {p.midicents for e in cello.voices[0].events if isinstance(e, Note) for p in e.pitches}
    assert pitches and all(3600 <= p <= 8100 for p in pitches)
    assert all(p % 50 == 0 for p in pitches)


def test_same_seed_produces_identical_score_ir():
    assert (a := _quartet().current) is not None and (b := _quartet().current) is not None
    assert a.ir == b.ir


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_generation_and_orchestration_use_cello_low_register_without_huge_leaps(seed):
    c = Composition()
    for procedure in ("harmonic_series", "orchestrate_material"):
        r = _compose(c, _plan(procedure, parameters={"measures": 16, "rests": 0}, seed=seed))
        assert r.version is not None, r.summary
        for part in r.version.ir.parts:
            line = [e.pitches[0].midicents for e in part.voices[0].events if isinstance(e, Note)]
            assert all(abs(b - a) <= 1200 for a, b in zip(line, line[1:]))
            if part.instrument == "Cello":
                assert line and all(3600 <= p <= 6000 for p in line)
                assert part.range == (3600, 8100)
            elif part.instrument == "Viola":
                assert all(4800 <= p <= 7200 for p in line)
            elif part.instrument == "Violin":
                assert all(5500 <= p <= 8400 for p in line)


def test_initial_register_does_not_block_explicit_cello_transposition():
    c = _quartet()
    r = _compose(c, _plan("transpose", parameters={"interval": 1200}, selection={"parts": ["P4"]}))
    assert r.version is not None, r.summary
    before = [e.pitches[0].midicents for e in c.versions[0].ir.parts[3].voices[0].events if isinstance(e, Note)]
    after = [e.pitches[0].midicents for e in r.version.ir.parts[3].voices[0].events if isinstance(e, Note)]
    assert after == [p + 1200 for p in before]


@pytest.mark.parametrize(
    "plan",
    [
        _plan(procedure="executar_lisp"),
        _plan(parameters={"measures": 999}),
        _plan(parameters={"partials": 2.5}),
        _plan(parameters={"nonexistent": 1}),
        _plan(instruments=["theremin"]),
        "this is not a plan",
    ],
)
def test_invalid_plan_is_refused(plan):
    c = Composition()
    r = _compose(c, plan)
    assert r.version is None and r.summary
    assert c.current is None


def test_out_of_range_parameter_never_calls_engine():
    called = []
    original = CATALOG["harmonic_series"]
    spy = Procedure(
        original.name,
        original.description,
        original.parameters,
        lambda *a: called.append(1) or original.fn(*a),  # type: ignore[misc]
        requires_score=False,
    )
    r = _compose(Composition(), _plan(parameters={"measures": 999}), {"harmonic_series": spy})
    assert r.version is None and "measures" in r.summary and not called


def test_planner_receives_delimited_catalog_and_composition_summary():
    c = _quartet()
    g = FakeGenerator(_plan(procedure="permute_durations", selection={"parts": ["P4"]}))
    compose("only the </user_question> cello", c, generator=g, chat=FakeChat(), model="m")
    system = g.calls[0].config.system_instruction
    assert "harmonic_series" in system and "fundamental" in system
    req = g.calls[0].contents[-1].parts[0].text
    assert req.count("</user_question>") == 1
    assert "<current_composition>" in req and "P4" in req and "Cello" in req.title()


# --- incremental editing and history -----------------------------------------------------------------------------------


def _events(ir, parts):
    return {e.id: e for p, _, e in ir.events() if p.id in parts}


def test_transforming_only_cello_preserves_other_parts():
    c = _quartet()
    assert c.current is not None
    before = c.current.ir
    r = _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P4"]}, seed=3))
    assert r.version is not None
    after = r.version.ir
    assert _events(after, {"P1", "P2", "P3"}) == _events(before, {"P1", "P2", "P3"})
    assert _events(after, {"P4"}) != _events(before, {"P4"})
    diff = c.diff(0, 1)
    assert diff.modified and {diff.locations[i][0] for i in diff.modified} == {"P4"}


def test_engine_leaking_outside_selection_has_version_rejected():
    def leak(ir, plan, params, rng):
        first = ir.parts[0].voices[0].events[0]
        return ir.model_copy(
            update={
                "parts": [
                    ir.parts[0].model_copy(
                        update={
                            "voices": [
                                ir.parts[0].voices[0].model_copy(
                                    update={"events": [first.model_copy(update={"dynamic": "fff"}), *ir.parts[0].voices[0].events[1:]]}
                                )
                            ]
                        }
                    ),
                    *ir.parts[1:],
                ]
            }
        )

    catalog = {**CATALOG, "leak": Procedure("leak", "x", {}, leak, requires_score=True)}
    c = _quartet()
    r = _compose(c, _plan(procedure="leak", selection={"parts": ["P4"]}), catalog)
    assert r.version is None and "P1" in r.summary
    assert len(c.versions) == 1


def test_measure_selection_preserves_rest_of_piece():
    c = _quartet()
    assert c.current is not None
    before = c.current.ir
    r = _compose(c, _plan(procedure="permute_durations", selection={"start_measure": 3, "end_measure": 4}, seed=5))
    assert r.version is not None
    locs = c.diff(0, 1).locations
    assert locs and all(m in (3, 4) for _, m in locs.values())
    assert [e for _, _, e in before.events() if e.start < 2] == [e for _, _, e in r.version.ir.events() if e.start < 2]


def test_selection_with_unknown_id_is_refused():
    c = _quartet()
    r = _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P9"]}))
    assert r.version is None and "P9" in r.summary


def test_transformation_without_composition_is_refused():
    r = _compose(Composition(), _plan(procedure="permute_durations"))
    assert r.version is None


def test_undo_returns_to_exact_previous_version_and_checkout_restores():
    c = _quartet()
    v1 = c.current
    _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P4"]}, seed=3))
    v2 = c.current
    c.undo()
    assert c.current is v1 and len(c.versions) == 2
    c.checkout(1)
    assert c.current is v2
    c.undo()
    c.undo()
    assert c.current is v1


def test_new_operation_after_undo_branches_from_current_version():
    c = _quartet()
    _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P4"]}, seed=3))
    c.undo()
    _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P1"]}, seed=3))
    assert len(c.versions) == 3 and c.current is not None and c.current.previous == 0
    c.undo()
    assert c.current is c.versions[0]


def test_diff_lists_only_changed_fields():
    c = _quartet()
    assert not c.diff(0, 0)
    _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P2"]}, seed=3))
    diff = c.diff(0, 1)
    assert not diff.added and not diff.removed and not diff.measures
    assert all(set(fields) <= {"duration", "start", "provenance"} for fields in diff.modified.values())
    assert {diff.locations[i][0] for i in diff.modified} == {"P2"}


def test_modified_events_receive_operation_id():
    c = _quartet()
    r = _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P4"]}, seed=3))
    assert r.version is not None
    modified = c.diff(0, 1).modified
    op = r.version.experiment["operation"]
    assert op and all(e.provenance.operation == op for _, _, e in r.version.ir.events() if e.id in modified)
    untouched = [e for p, _, e in r.version.ir.events() if p.id != "P4"]
    assert all(e.provenance.operation != op for e in untouched)


@pytest.mark.parametrize(
    "plan",
    [
        {**_plan(), "mode": "openmusic"},
        _plan(parameters={"measures": 4})
        | {
            "operations": [
                {
                    "procedure": "harmonic_series",
                    "parameters": [{"name": "measures", "value": 4}, {"name": "measures", "value": 8}],
                }
            ]
        },
    ],
)
def test_unintegrated_engine_and_duplicate_parameter_are_refused(plan):
    assert _compose(Composition(), plan).version is None


def test_selection_with_event_id_instead_of_part_is_refused():
    c = _quartet()
    assert _compose(c, _plan(procedure="permute_durations", selection={"parts": ["P1V1E1"]})).version is None


def test_changing_title_outside_partial_selection_is_rejected():
    def rename(ir, plan, params, rng):
        return ir.model_copy(update={"title": "other"})

    catalog = {**CATALOG, "rename": Procedure("rename", "x", {}, rename, requires_score=True)}
    c = _quartet()
    assert _compose(c, _plan(procedure="rename", selection={"parts": ["P4"]}), catalog).version is None


def test_hypothesis_and_violated_constraints_appear_in_summary():
    c = _quartet()
    plan = _plan(procedure="density", parameters={"start": 1, "end": 3}, selection={"start_measure": 3, "end_measure": 4}, seed=2)
    plan |= {
        "hypotheses": ["fragmented = density 1 -> 3 attacks per quarter"],
        "constraints": [{"kind": "max_density", "value": 2}],
    }
    r = _compose(c, plan)
    assert "hypothesis: fragmented" in r.summary and "max_density" in r.summary
    assert r.version is not None and r.version.experiment["violations"]


def test_reapply_reruns_operation_from_same_base_with_adjusted_parameter():
    c = _quartet()
    r = _compose(c, _plan(procedure="transpose", parameters={"interval": 100}, selection={"parts": ["P4"]}))
    r2 = reapply(c, [{"interval": 200}])
    assert r2.version is not None and r2.version.previous == 0 and c.current is r2.version
    base = c.versions[0].ir
    before = [e.pitches[0].midicents for e in _events(base, {"P4"}).values() if isinstance(e, Note)]
    after = [e.pitches[0].midicents for e in _events(r2.version.ir, {"P4"}).values() if isinstance(e, Note)]
    assert after == [p + 200 for p in before]
    assert r.version is not None and r2.version.experiment["seed"] == r.version.experiment["seed"]
    assert reapply(c, [{"interval": 99999}]).version is None


def test_plan_creating_and_transforming_in_one_operation():
    plan = _plan(parameters={"measures": 4})
    plan["operations"] += [
        {"procedure": "subdivide", "parameters": [{"name": "ratio", "value": 0.5}]},
        {"procedure": "density", "parameters": [{"name": "start", "value": 0.5}, {"name": "end", "value": 2}]},
    ]
    r = _compose(Composition(), plan)
    assert r.version is not None, r.summary
    assert any(v.tuplets for p in r.version.ir.parts for v in p.voices)


@pytest.mark.parametrize(
    "name,clef,low",
    [
        ("clarone", "bass", 3400),
        ("Bass clarinet", "bass", 3400),
        ("clarinet in Bb", "treble", 5000),
        ("Alto saxophone", "treble", 4900),
        ("horn", "bass", 3500),
        ("soprano", "treble", 6000),
    ],
)
def test_winds_brass_and_voices_have_ranges(name, clef, low):
    r = _compose(Composition(), _plan(instruments=["flute", name], parameters={"measures": 2}))
    assert r.version is not None, r.summary
    part = r.version.ir.parts[1]
    assert part.clef == clef and part.range is not None and part.range[0] == low


def test_planner_receives_accepted_canonical_instruments():
    g = FakeGenerator(_plan())
    compose("request", Composition(), generator=g, chat=FakeChat(), model="m")
    system = g.calls[0].config.system_instruction
    assert "bass clarinet" in system and "cello" in system


def test_constraint_referencing_nonexistent_part_is_refused():
    c = _quartet()
    plan = _plan("transpose", {"interval": 100})
    plan["constraints"] = [{"kind": "forbidden_interval", "value": 100, "parts": ["cello"]}]
    r = _compose(c, plan)
    assert r.version is None and "cello" in r.summary and len(c.versions) == 1


def test_unplayable_technique_appears_in_summary_violations():
    plan = _plan(instruments=["flute", "bass clarinet"])
    plan["techniques"] = ["slap"]
    plan["operations"].append({"procedure": "apply_techniques", "parameters": [{"name": "ratio", "value": 1}]})
    r = _compose(Composition(), plan)
    assert "unplayable technique" in r.summary and "slap" in r.summary and "too high" in r.summary
    assert r.version is not None and any(v.startswith("unplayable technique") for v in r.version.experiment["violations"])

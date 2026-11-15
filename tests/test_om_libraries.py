"""Unit tests for canonical English OpenMusic patches (`muschat/om_patches.py`) and task bridge (`muschat/openmusic.py`):
Chaos, Situation, OMChroma/Csound, OM-SuperVP, and Cluster Engine.
"""

import random
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

import pytest

from muschat.constraints import Constraint
from muschat.om_patches import PATCHES, PatchContext, find_csound, render_csound
from muschat.openmusic import OmInstallation, create_task, import_output
from muschat.plan import Operation, Plan
from muschat.procedures import CATALOG
from muschat.procedures.spectral import partials
from muschat.registry import detect_capabilities
from muschat.score_ir import Measure, Note, Part, Pitch, Provenance, Rest, ScoreIR, Voice

LIBS = {
    n: Path(f"C:/OM/Libraries/{n}")
    for n in ("esquisse", "chaos", "situation", "om2csound", "omchroma", "cluster-engine")
}
OM = OmInstallation(Path("C:/OM/OM 8.0.exe"), "8.0", LIBS)
APPROVED = {name: replace(p, file=p.file or __file__, validated=True) for name, p in PATCHES.items()}
WITH_LIBS = detect_capabilities(
    lilypond=None,
    openmusic=OM,
    abjad_version="3.31",
    which=lambda e: f"C:/bin/{e}.exe",
    patches=APPROVED,
)


def _verhulst(x0: float, lam: float, n: int) -> list[float]:
    xs = [x0]
    for _ in range(n):
        xs.append(xs[-1] + lam * xs[-1] * (1 - xs[-1]))
    return xs


def _quartet_score(measures: int = 4) -> ScoreIR:
    proc = CATALOG["harmonic_series"]
    params = {k: f.default for k, f in proc.parameters.items()} | {"measures": float(measures)}
    plan = Plan(goal="quartet", operations=[Operation(procedure="harmonic_series")])
    assert proc.fn is not None
    return proc.fn(None, plan, params, random.Random(1))


def _complete_params(procedure: str, overrides: dict[str, float] | None = None) -> dict[str, float]:
    base = {k: f.default for k, f in CATALOG[procedure].parameters.items()}
    if overrides:
        base.update(overrides)
    return base


def _attacks(ir: ScoreIR, part: int, measure: int) -> int:
    start = ir.measure_starts()[measure - 1]
    end = start + ir.measures[measure - 1].length
    return sum(
        1
        for v in ir.parts[part].voices
        for e in v.events
        if isinstance(e, Note) and start <= e.start < end
    )


# --- Chaos -------------------------------------------------------------------------------------------------------------


def test_chaos_verhulst_controls_density_of_each_measure(tmp_path):
    current = _quartet_score(4)
    params = _complete_params("chaotic_density", {"x0": 0.1, "turbulence": 2.8, "min_value": 0, "max_value": 2})
    plan = Plan(goal="chaos", operations=[Operation(procedure="chaotic_density")])
    task = create_task("chaotic_density", params, root=tmp_path, current=current, plan=plan)
    assert (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8") == "0.1 2.8 3\n"
    values = _verhulst(0.1, 2.8, 3)
    ir = import_output(
        task,
        " ".join(f"{v:.6f}" for v in values),
        operation="chaotic_density",
        current=current,
        plan=plan,
    )
    for part in range(4):
        assert _attacks(ir, part, 1) == 0
        assert _attacks(ir, part, 4) == 8


def test_chaos_output_with_wrong_count_is_refused(tmp_path):
    current = _quartet_score(4)
    params = _complete_params("chaotic_density", {"x0": 0.1, "turbulence": 2.8})
    plan = Plan(goal="chaos", operations=[Operation(procedure="chaotic_density")])
    task = create_task("chaotic_density", params, root=tmp_path, current=current, plan=plan)
    with pytest.raises(ValueError, match="4 values"):
        import_output(task, "0.1 0.3", operation="chaotic_density", current=current, plan=plan)


# --- Situation ---------------------------------------------------------------------------------------------------------


def _situation_task(tmp_path, base=5500, step=100, constraints=None, instruments=None, pitches=12):
    params = _complete_params(
        "distribute_by_constraints",
        {"pitches": float(pitches), "base": float(base), "step": float(step)},
    )
    plan = Plan(
        goal="situation",
        operations=[Operation(procedure="distribute_by_constraints")],
        constraints=constraints or [],
        instruments=instruments or [],
    )
    task = create_task("distribute_by_constraints", params, root=tmp_path, plan=plan)
    return task, plan


def test_situation_input_has_one_domain_per_slot_and_solution_builds_quartet(tmp_path):
    task, plan = _situation_task(tmp_path)
    lines = (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "12" and len(lines) == 13
    assert lines[4].split()[:2] == ["12", "5500"]
    solution = [5500 + 100 * k for k in range(12)]
    ir = import_output(task, " ".join(map(str, solution)), operation="distribute_by_constraints", plan=plan)
    assert [p.instrument for p in ir.parts] == ["Violin", "Violin", "Viola", "Cello"]
    assert len(ir.measures) == 3
    assert [e.pitches[0].midicents for e in ir.parts[3].voices[0].events] == [5800, 6200, 6600]


def test_situation_solution_violating_constraint_is_rejected_with_location(tmp_path):
    task, plan = _situation_task(tmp_path, base=5000)
    bad = [5000 + 100 * k for k in range(12)]
    with pytest.raises(ValueError, match=r"range.*P1 m\.1"):
        import_output(task, " ".join(map(str, bad)), operation="distribute_by_constraints", plan=plan)
    repeated = [5500 + 100 * k for k in range(12)]
    repeated[1] = repeated[0]
    task2, plan2 = _situation_task(tmp_path)
    with pytest.raises(ValueError, match=r"m\.1.*(unison|repeated)"):
        import_output(task2, " ".join(map(str, repeated)), operation="distribute_by_constraints", plan=plan2)


def test_situation_with_no_solution_lists_conditions_and_creates_no_task(tmp_path):
    with pytest.raises(ValueError, match="no solution, nothing sent to OpenMusic") as exc:
        _situation_task(tmp_path, base=4800)
    msg = str(exc.value)
    assert "no unison" in msg and "Violin: 3 slots" in msg
    assert "Violin + Violin reach 5 pitches for 6 slots" in msg
    assert not any(tmp_path.iterdir())


def test_situation_returning_minus_one_lists_conditions(tmp_path):
    task, plan = _situation_task(tmp_path)
    with pytest.raises(ValueError, match="found no solution") as exc:
        import_output(task, "-1", operation="distribute_by_constraints", plan=plan)
    assert "Cello: 3 slots" in str(exc.value)


def test_patch_failure_in_openmusic_reports_erro_txt(tmp_path):
    task, plan = _situation_task(tmp_path)
    (Path(task.directory) / "erro.txt").write_text("Undefined function CSOLVER", encoding="utf-8")
    with pytest.raises(ValueError, match="Undefined function CSOLVER"):
        import_output(task, "-2", operation="distribute_by_constraints", plan=plan)


# --- OMChroma + Csound -------------------------------------------------------------------------------------------------


def test_additive_synthesis_sends_partials_and_appends_electronic_part_without_notes(tmp_path, monkeypatch):
    def unexpected_render(*args, **kwargs):
        raise AssertionError("parsing must not invoke Csound")

    monkeypatch.setattr("muschat.audio.render_csound", unexpected_render)
    current = _quartet_score(2)
    notes_before = [e for _, _, e in current.events() if isinstance(e, Note)]
    params = _complete_params("additive_synthesis", {"initial_partials": 1, "final_partials": 3, "amplitude": -18})
    plan = Plan(goal="omchroma", operations=[Operation(procedure="additive_synthesis")])
    task = create_task("additive_synthesis", params, root=tmp_path, current=current, plan=plan)
    lines = (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8").splitlines()
    n = int(lines[0])
    assert n == len(lines) - 1 and len(notes_before) <= n <= 3 * len(notes_before)
    t, d, f, a = map(float, lines[1].split())
    assert t == 0 and d > 0 and f > 20 and a == -18
    ir = import_output(task, str(n), operation="additive_synthesis", current=current, plan=plan)
    electronic = ir.parts[-1]
    assert electronic.instrument == "Electronics (additive synthesis)"
    assert "audio" not in electronic.voices[0].events[0].description
    events = [e for voice in electronic.voices for e in voice.events]
    assert {e.kind for e in events} == {"electronic", "curve"}
    assert ir.parts[:4] == current.parts


def test_registry_and_render_use_same_csound(monkeypatch):
    monkeypatch.setenv("CSOUND", "D:/csound/bin/csound.exe")
    reg = detect_capabilities(
        lilypond=None, openmusic=OM, abjad_version="3.31", which=lambda e: None, patches=APPROVED
    )
    cap = reg.capability("Csound")
    assert cap is not None and cap.state == "automatic" and "D:/csound" in cap.reason


def test_patch_without_file_cannot_create_task(tmp_path):
    with pytest.raises(ValueError, match="no approved patch"):
        create_task("supervp_analysis", {"partials": 4, "frames": 2}, root=tmp_path)
    assert not any(tmp_path.iterdir())


def test_real_openmusic_evidence_outputs_import(tmp_path):
    evid = Path("docs/pesquisa/evidencias-openmusic")
    chaos = [float(x) for x in (evid / "chaos-saida.txt").read_text(encoding="utf-8").split()]
    assert chaos == [round(v, 10) for v in _verhulst(0.1, 2.8, 3)]
    task, plan = _situation_task(tmp_path)
    ir = import_output(
        task,
        (evid / "situation-saida.txt").read_text(encoding="utf-8"),
        operation="distribute_by_constraints",
        plan=plan,
    )
    assert len({e.pitches[0].midicents for p in ir.parts for e in p.voices[0].events}) == 12


def test_missing_csound_or_missing_files_reports_reason(tmp_path):
    assert "did not write muschat.orc" in render_csound(tmp_path, "csound")
    (tmp_path / "muschat.orc").write_text("", encoding="utf-8")
    (tmp_path / "muschat.sco").write_text("", encoding="utf-8")
    assert "Csound not found" in render_csound(tmp_path, None)


_ORC = "sr = 44100\nksmps = 32\nnchnls = 1\n0dbfs = 1\ninstr 1\na1 poscil 0.1, p4\nout a1\nendin\n"


def test_real_csound_renders_orc_and_sco(tmp_path):
    if find_csound() is None:
        pytest.skip("Csound not installed")
    (tmp_path / "muschat.orc").write_text(_ORC, encoding="utf-8")
    (tmp_path / "muschat.sco").write_text("i1 0 0.2 440\n", encoding="utf-8")
    assert render_csound(tmp_path, find_csound()).startswith("audio: muschat.wav")
    assert (tmp_path / "muschat.wav").stat().st_size > 1000


# --- OM-SuperVP --------------------------------------------------------------------------------------------------------


def test_missing_supervp_marks_capability_unavailable():
    without_supervp = detect_capabilities(
        lilypond=None,
        openmusic=OmInstallation(OM.executable, "8.0", {**LIBS, "om-supervp": Path("x")}),
        abjad_version="3.31",
        which=lambda e: None,
        patches=APPROVED,
    )
    svp = without_supervp.capability("SuperVP")
    om_svp = without_supervp.capability("OM-SuperVP")
    assert svp is not None and svp.state == "unavailable" and "supervp" in svp.reason
    assert om_svp is not None and om_svp.reason == "installed, but missing executable: supervp"
    assert "supervp_analysis" not in without_supervp.available_procedures()


def test_imported_spectral_data_becomes_spectral_event_and_then_notes(tmp_path, monkeypatch):
    monkeypatch.setitem(PATCHES, "supervp_analysis", replace(PATCHES["supervp_analysis"], file=__file__))
    params = _complete_params("supervp_analysis", {"partials": 2, "frames": 2})
    plan = Plan(goal="supervp", operations=[Operation(procedure="supervp_analysis")])
    task = create_task("supervp_analysis", params, root=tmp_path, plan=plan)
    analysis = "3  0 220 1  0 440 0.5  0.5 330 0.8"
    spectral_ir = import_output(task, analysis, operation="supervp_analysis", plan=plan)
    assert all(e.kind == "spectral" for e in spectral_ir.parts[0].voices[0].events)
    assert spectral_ir.parts[0].instrument.startswith("Spectral analysis")
    fn = CATALOG["events_from_spectrum"].fn
    assert fn is not None
    ev_params = _complete_params("events_from_spectrum", {"pitches_per_frame": 2})
    full_ir = fn(spectral_ir, plan, ev_params, random.Random(1))
    notes = [e for p in full_ir.parts[1:] for v in p.voices for e in v.events if isinstance(e, Note)]
    assert notes and {e.provenance.source for e in notes} == {"algorithm"}


# --- Chaos controlling register, dynamics, and durations ---------------------------------------------------------------


_SERIES = " ".join(f"{v:.10f}" for v in _verhulst(0.1, 2.8, 3))


def _run_chaos(tmp_path, procedure: str, overrides: dict[str, float]):
    before = _quartet_score(4)
    params = _complete_params(procedure, {"x0": 0.1, "turbulence": 2.8, **overrides})
    plan = Plan(goal=procedure, operations=[Operation(procedure=procedure)])
    task = create_task(procedure, params, root=tmp_path, current=before, plan=plan)
    assert (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8") == "0.1 2.8 3\n"
    after = import_output(task, _SERIES, operation=procedure, current=before, plan=plan)
    return before, after


def _measure_notes(ir: ScoreIR, part: int, n: int) -> list[Note]:
    start = ir.measure_starts()[n - 1]
    return [
        e
        for v in ir.parts[part].voices
        for e in v.events
        if isinstance(e, Note) and start <= e.start < start + ir.measures[n - 1].length
    ]


def test_chaotic_register_shifts_each_measure_by_series_value(tmp_path):
    before, after = _run_chaos(tmp_path, "chaotic_register", {"min_value": 0, "max_value": 1200})
    for n, shift in [(1, 0), (2, 350), (3, 1150), (4, 1200)]:
        a = [x.pitches[0].midicents for x in _measure_notes(before, 3, n)]
        d = [x.pitches[0].midicents for x in _measure_notes(after, 3, n)]
        assert d == [x + shift for x in a]


def test_chaotic_dynamics_marks_first_attack_of_each_measure(tmp_path):
    _, after = _run_chaos(tmp_path, "chaotic_dynamics", {"min_value": 0, "max_value": 9})
    for part in range(4):
        marks = [
            _measure_notes(after, part, n)[0].dynamic
            for n in range(1, 5)
            if _measure_notes(after, part, n)
        ]
        assert marks[0] == "pppp" and marks[-1] == "ffff"


def test_chaotic_durations_change_note_value_per_measure_and_preserve_pitches(tmp_path):
    before, after = _run_chaos(tmp_path, "chaotic_durations", {"min_value": 1, "max_value": 3})
    for n, fig in [(1, F(1, 2)), (4, F(1, 8))]:
        notes = _measure_notes(after, 3, n)
        assert notes and {x.duration for x in notes} == {fig}
        orig = {x.pitches[0].midicents for x in _measure_notes(before, 3, n)}
        assert {x.pitches[0].midicents for x in notes} <= orig


# --- Situation with Plan Constraints -----------------------------------------------------------------------------------


def test_situation_forbidden_interval_builds_pairs_in_input(tmp_path):
    task, _ = _situation_task(tmp_path, constraints=[Constraint(kind="forbidden_interval", value=100)])
    lines = (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8").splitlines()
    assert lines[13] == "8"
    assert "0 4 100" in lines[14:] and "7 11 100" in lines[14:]


def test_situation_solution_violating_plan_interval_is_rejected_with_location(tmp_path):
    task, plan = _situation_task(tmp_path, constraints=[Constraint(kind="forbidden_interval", value=100)])
    bad = [5500, 5900, 6300, 6100, 5600, 6000, 6400, 6200, 5700, 5800, 6500, 6600]
    with pytest.raises(ValueError, match=r"forbidden_interval.*P1 m\.2"):
        import_output(task, " ".join(map(str, bad)), operation="distribute_by_constraints", plan=plan)


def test_situation_range_constraint_narrows_part_domain(tmp_path):
    task, _ = _situation_task(
        tmp_path,
        constraints=[Constraint(kind="range", low=6000, high=6600, parts=["P4"])],
    )
    lines = (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8").splitlines()
    assert lines[4].split()[:2] == ["7", "6000"]
    assert lines[1].split()[:2] == ["12", "5500"]


def test_situation_impossible_with_forbidden_intervals_is_refused_before_openmusic(tmp_path):
    forbidden = [
        Constraint(kind="forbidden_interval", value=100),
        Constraint(kind="forbidden_interval", value=200),
    ]
    with pytest.raises(ValueError, match="no solution, nothing sent to OpenMusic") as exc:
        _situation_task(tmp_path, constraints=forbidden, instruments=["violin"], pitches=4)
    assert "forbidden interval of 100" in str(exc.value)
    assert not any(tmp_path.iterdir())


def test_situation_constraint_with_unknown_part_is_refused(tmp_path):
    with pytest.raises(ValueError, match=r"\['1', '2'\].*P1"):
        _situation_task(tmp_path, constraints=[Constraint(kind="range", low=3600, high=9600, parts=["1", "2"])])


# --- Cluster Engine ----------------------------------------------------------------------------------------------------


def _duo_with_tie() -> ScoreIR:
    prov = Provenance(source="composer")

    def note(i, start, dur, mc, **kw):
        return Note(id=i, start=start, duration=dur, pitches=[Pitch(midicents=mc)], provenance=prov, **kw)

    violin = Part(
        id="vl",
        instrument="Violin",
        voices=[
            Voice(
                id="vlv",
                events=[
                    note("a", F(0), F(1, 4), 7200),
                    Rest(id="b", start=F(1, 4), duration=F(1, 4), provenance=prov),
                    note("c", F(1, 2), F(1, 8), 7400, tied=True),
                    note("d", F(5, 8), F(1, 8), 7400),
                    note("e", F(3, 4), F(1, 4), 7600),
                ],
            )
        ],
    )
    cello = Part(
        id="vc",
        instrument="Cello",
        clef="bass",
        voices=[Voice(id="vcv", events=[note("f", F(0), F(1), 4800)])],
    )
    return ScoreIR(title="duo", measures=[Measure(numerator=4, denominator=4)], parts=[violin, cello])


def _cluster_task(tmp_path, current=None, **overrides):
    current = current or _duo_with_tie()
    params = _complete_params("pitches_by_constraints", {"common_tones": 1, **overrides})
    plan = Plan(goal="ce", operations=[Operation(procedure="pitches_by_constraints")])
    task = create_task("pitches_by_constraints", params, root=tmp_path, current=current, plan=plan)
    return task, current, plan


def test_cluster_engine_input_encodes_fixed_rhythm_summed_tie_and_range_domain(tmp_path):
    task, _, _ = _cluster_task(tmp_path)
    spectrum = partials(3600, 16, 1)
    violin = [a for a in spectrum if 5500 <= a <= 10300]
    cello = [a for a in spectrum if 3600 <= a <= 8100]
    assert (Path(task.directory) / "entrada.txt").read_text(encoding="utf-8") == (
        "2 700 1 1 1 4\n4 4\n"
        f"4 1 4 -1 4 1 4 1 4\n{len(violin)} {' '.join(map(str, violin))}\n"
        f"1 1 1\n{len(cello)} {' '.join(map(str, cello))}\n"
    )


def test_cluster_engine_output_applies_one_pitch_per_note_and_same_pitch_across_tie(tmp_path):
    task, current, plan = _cluster_task(tmp_path)
    ir = import_output(
        task,
        "2 3 6000 6400 6700 1 3600",
        operation="pitches_by_constraints",
        current=current,
        plan=plan,
    )
    violin, cello = ir.parts
    notes = [e for e in violin.voices[0].events if isinstance(e, Note)]
    assert [n.pitches[0].midicents for n in notes] == [6000, 6400, 6400, 6700]
    assert [(e.start, e.duration) for e in violin.voices[0].events] == [
        (F(0), F(1, 4)),
        (F(1, 4), F(1, 4)),
        (F(1, 2), F(1, 8)),
        (F(5, 8), F(1, 8)),
        (F(3, 4), F(1, 4)),
    ]
    assert cello.voices[0].events[0].pitches[0].midicents == 3600
    assert notes[0].provenance.source == "openmusic"


def test_cluster_engine_output_outside_domain_or_short_is_refused(tmp_path):
    task, current, plan = _cluster_task(tmp_path)
    with pytest.raises(ValueError, match="outside the allowed"):
        import_output(task, "2 3 6000 6400 6050 1 3600", operation="pitches_by_constraints", current=current, plan=plan)
    with pytest.raises(ValueError, match="2 pitches"):
        import_output(task, "2 2 6000 6400 1 3600", operation="pitches_by_constraints", current=current, plan=plan)
    with pytest.raises(ValueError, match="no solution"):
        import_output(task, "-1", operation="pitches_by_constraints", current=current, plan=plan)


def test_cluster_engine_problem_with_empty_domain_never_creates_task(tmp_path):
    with pytest.raises(ValueError, match="Violin"):
        _cluster_task(tmp_path, fundamental=2400, partials=2)
    assert not any(tmp_path.iterdir())


def test_cluster_engine_chord_tracks_lead_pitch_within_partials(tmp_path):
    duo = _duo_with_tie()
    duo.parts[0].voices[0].events[0].pitches.append(Pitch(midicents=7900))
    task, current, plan = _cluster_task(tmp_path, current=duo)
    ir = import_output(
        task,
        "2 3 6000 6400 6700 1 3600",
        operation="pitches_by_constraints",
        current=current,
        plan=plan,
    )
    chord = ir.parts[0].voices[0].events[0].pitches
    assert [a.midicents for a in chord] == [6000, 6700]


def test_real_cluster_engine_evidence_imports_onto_notagen_material():
    evid = Path("docs/pesquisa/evidencias-openmusic")
    material = ScoreIR.model_validate_json((evid / "cluster-engine-material.json").read_text(encoding="utf-8"))
    base = _complete_params("pitches_by_constraints", {"common_tones": 1})
    plan = Plan(goal="v", operations=[Operation(procedure="pitches_by_constraints")])
    input_text = PATCHES["pitches_by_constraints"].build_input(base, material, plan)
    assert input_text == (evid / "cluster-engine-entrada.txt").read_text(encoding="utf-8")
    ctx = PatchContext(
        parameters=base,
        input_text=input_text,
        directory=evid,
        current=material,
        plan=plan,
        rng=random.Random(0),
        provenance=Provenance(source="openmusic"),
    )
    values = [float(x) for x in (evid / "cluster-engine-saida.txt").read_text(encoding="utf-8").split()]
    updated = PATCHES["pitches_by_constraints"].read_output(values, ctx)
    assert all(
        e.pitches[0].midicents in partials(3600, 16, 1)
        for _, _, e in updated.events()
        if isinstance(e, Note)
    )
    assert [(e.start, e.duration) for _, _, e in updated.events()] == [
        (e.start, e.duration) for _, _, e in material.events()
    ]

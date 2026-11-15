"""Registry, router, assisted OpenMusic bridge, and pause/resume pipeline tests."""

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from muschat.composition import Composition
from muschat.om_patches import PATCHES
from muschat.openmusic import OmInstallation, detect_openmusic
from muschat.orchestrator import compose, discard_task, resume
from muschat.plan import Plan
from muschat.procedures import CATALOG, Procedure
from muschat.registry import Registry, detect_capabilities
from muschat.router import NoRouteError, route
from tests.test_composition import QUARTET, FakeChat, FakeGenerator, _plan

NO_OM = detect_capabilities(lilypond=None, openmusic=None, abjad_version="3.31", which=lambda e: None)
OM = OmInstallation(Path("C:/OM/OM 8.0.exe"), "8.0", {"esquisse": Path("C:/OM/Libraries/Esquisse")})
TEST_PATCH = {"harmonic_series": replace(PATCHES["harmonic_series"], file=__file__, validated=True)}
WITH_OM = detect_capabilities(lilypond=None, openmusic=OM, abjad_version="3.31", which=lambda e: None, patches=TEST_PATCH)
OUTPUT_16 = "(3600 4800 5502 6000 6386 6702 6969 7200 7404 7586 7751 7902 8041 8169 8288 8400)"


def _run(plan, *, mode="auto", registry=NO_OM, composition=None, catalog=CATALOG, tasks_dir=None):
    c = composition or Composition()
    g = FakeGenerator(plan)
    r = compose(
        "request",
        c,
        generator=g,
        chat=FakeChat(),
        model="m",
        mode=mode,
        registry=registry,
        catalog=catalog,
        tasks_dir=tasks_dir or Path("do-not-use"),
    )
    return c, r, g


def _hybrid(*extras, instruments=QUARTET):
    plan = _plan(parameters={"partials": 16}, instruments=instruments)
    plan["operations"] += [
        {"procedure": "orchestrate_material", "parameters": [{"name": "measures", "value": 4}]},
        *extras,
    ]
    return plan


# --- registry & router ------------------------------------------------------------------------------------------------


def test_manual_openmusic_without_openmusic_refuses_with_alternatives_without_running():
    called = []
    spy = {
        **CATALOG,
        "harmonic_series": Procedure(
            "harmonic_series",
            "x",
            CATALOG["harmonic_series"].parameters,
            lambda *a: called.append(1) or CATALOG["harmonic_series"].fn(*a),  # type: ignore[misc]
            requires_score=False,
        ),
    }
    c, r, _ = _run(_plan(), mode="openmusic", catalog=spy)
    assert r.version is None and r.task is None and not called and c.pending is None
    assert "OpenMusic" in r.summary and "not found" in r.summary and "Alternatives: Python/Abjad" in r.summary


def test_auto_chooses_python_for_harmonic_series_with_rationale():
    c, r, _ = _run(_plan())
    assert r.version is not None
    assert r.version.experiment["decision"]["steps"] == [{"engine": "python", "procedure": "harmonic_series"}]
    assert "OpenMusic unavailable" in r.summary


def test_auto_with_openmusic_prefers_automatic_and_records_r5():
    c, r, _ = _run(_plan(), registry=WITH_OM)
    assert r.version is not None
    rationale = " ".join(r.version.experiment["decision"]["rationale"])
    assert r.version.experiment["decision"]["steps"][0]["engine"] == "python"
    assert "OpenMusic also implements it" in rationale and "R5" in rationale


def test_procedure_outside_registry_never_enters_prompt():
    _, _, g = _run(_plan(), registry=Registry.python_only(("harmonic_series",)))
    system = g.calls[0].config.system_instruction
    assert "harmonic_series" in system and "- transpose" not in system and "- density" not in system


def test_planning_respects_selected_mode_and_prepares_task(tmp_path):
    c, r, g = _run(_plan(), mode="openmusic", registry=WITH_OM, tasks_dir=tmp_path)
    system = g.calls[0].config.system_instruction
    assert "Selected engine mode in the UI: openmusic" in system
    assert "- harmonic_series:" in system
    assert "- gestural_techniques:" in system and "- density:" in system
    assert r.task is not None and c.pending is not None
    assert not c.versions


def test_plan_mode_acts_as_manual_choice():
    c, r, _ = _run({**_plan(), "mode": "openmusic"})
    assert r.version is None and "OpenMusic" in r.summary


@pytest.mark.parametrize(
    "mode,registry,operations,expected",
    [
        ("auto", NO_OM, ["harmonic_series"], ["python"]),
        ("auto", WITH_OM, ["harmonic_series"], ["python"]),
        ("python", WITH_OM, ["harmonic_series", "density"], ["python", "python"]),
        ("openmusic", WITH_OM, ["harmonic_series"], ["openmusic"]),
        ("openmusic", WITH_OM, ["harmonic_series", "density"], ["openmusic", "python"]),
        ("hybrid", WITH_OM, ["harmonic_series", "orchestrate_material"], ["openmusic", "python"]),
        ("hybrid", NO_OM, ["harmonic_series", "orchestrate_material"], None),
        ("hybrid", WITH_OM, ["transpose"], None),
    ],
)
def test_routing_decision_and_rationale_recorded(mode, registry, operations, expected):
    plan = Plan(goal="d", operations=[{"procedure": o} for o in operations])
    if expected is None:
        with pytest.raises(NoRouteError, match="Alternatives|no "):
            route(plan, mode, registry)
        return
    decision = route(plan, mode, registry)
    assert [s.engine for s in decision.steps] == expected
    assert len(decision.rationale) >= len(operations) and all(decision.rationale)


def test_registry_detects_libraries_and_external_dependencies(tmp_path, monkeypatch):
    monkeypatch.delenv("CSOUND", raising=False)
    monkeypatch.setattr("muschat.om_patches.os.path.isdir", lambda p: False)
    root = tmp_path / "OpenMusic 8.0"
    root.mkdir()
    (root / "OM 8.0.exe").write_bytes(b"")
    libs = tmp_path / "Libraries"
    for name in ("Esquisse 2.1", "Chaos", "OMChroma-6.2"):
        (libs / name).mkdir(parents=True)
    om = detect_openmusic(roots=[root], user_libraries_dir=libs)
    assert om is not None and om.version == "8.0" and set(om.libraries) == {"esquisse", "chaos", "omchroma"}
    reg = detect_capabilities(lilypond=None, openmusic=om, abjad_version="3.31", which=lambda e: None, patches=TEST_PATCH)
    state = {c.name: (c.state, c.reason) for c in reg.capabilities}
    assert state["OpenMusic"][0] == "manual" and state["Esquisse"][0] == "manual"
    no_omp = detect_capabilities(lilypond=None, openmusic=om, abjad_version="3.31", which=lambda e: None, patches={})
    esquisse_cap = no_omp.capability("Esquisse")
    assert esquisse_cap is not None and esquisse_cap.state == "unavailable"
    assert state["Chaos"] == ("unavailable", "installed, but no approved patch in project yet")
    assert state["OMChroma"] == ("unavailable", "installed, but no approved patch in project yet")
    assert state["Csound"][0] == "unavailable" and "csound" in state["Csound"][1]
    assert state["SuperVP"][0] == "unavailable" and "IRCAM" in state["SuperVP"][1]
    assert state["OM-SuperVP"] == ("unavailable", "not installed in ~/OM/Libraries")
    assert state["LilyPond"][0] == "unavailable" and state["Python"][0] == "automatic"


def test_without_openmusic_dependent_capabilities_are_unavailable():
    state = {c.name: (c.state, c.reason) for c in NO_OM.capabilities}
    assert state["OpenMusic"][0] == "unavailable" and "OPENMUSIC" in state["OpenMusic"][1]
    assert all(state[n] == ("unavailable", "requires OpenMusic") for n in ("Esquisse", "Chaos", "OM-SuperVP"))
    assert NO_OM.available_procedures() == [n for n, p in CATALOG.items() if p.fn is not None]


# --- assisted OpenMusic -----------------------------------------------------------------------------------------------


def _om(tmp_path, plan=None, composition=None):
    return _run(plan or _plan(parameters={"partials": 16}), mode="openmusic", registry=WITH_OM, composition=composition, tasks_dir=tmp_path)


def test_openmusic_step_creates_task_and_awaits_composer(tmp_path):
    c, r, _ = _om(tmp_path)
    assert r.version is None and r.task is not None and c.pending is not None and "Awaiting you" in r.summary
    folder = Path(r.task.directory)
    assert (folder / "entrada.txt").read_text() == "3600 1 1 1 16\n"
    assert "harm-series" in (folder / "instrucoes.md").read_text(encoding="utf-8")
    assert b"entrada.txt" in r.task.package()
    patch = Path(PATCHES["harmonic_series"].file)
    assert (folder / patch.name).read_bytes() == patch.read_bytes()
    with zipfile.ZipFile(io.BytesIO(r.task.package())) as pkg:
        assert pkg.read(patch.name) == patch.read_bytes()
        assert pkg.read("entrada.txt") == b"3600 1 1 1 16\n"


def test_valid_output_becomes_version_with_openmusic_provenance(tmp_path):
    c, r, _ = _om(tmp_path, _plan(instruments=[]))
    r2 = resume(c, OUTPUT_16)
    assert r2.version is not None and c.pending is None
    events = [e for _, _, e in r2.version.ir.events()]
    assert {e.provenance.source for e in events} == {"openmusic"}
    assert {e.provenance.author for e in events} == {"OpenMusic/Esquisse"}
    pitches = [p.midicents for e in events if hasattr(e, "pitches") for p in e.pitches]
    assert pitches[:3] == [3600, 4800, 5500]


@pytest.mark.parametrize("mode", ["openmusic", "hybrid"])
def test_duo_with_techniques_completes_after_patch_in_both_modes(tmp_path, mode):
    plan = _plan(parameters={"measures": 8}, instruments=["flute", "cello"])
    plan["techniques"] = ["frullato", "sul ponticello"]
    plan["operations"].append({"procedure": "gestural_techniques", "parameters": [{"name": "ratio", "value": 1}]})
    c, r, _ = _run(plan, mode=mode, registry=WITH_OM, tasks_dir=tmp_path)
    assert r.task and not c.versions
    done = resume(c, OUTPUT_16)
    assert done.version, done.summary
    assert len(done.version.ir.measures) == 8
    assert [p.instrument for p in done.version.ir.parts] == ["Flute", "Cello"]
    assert [s["engine"] for s in done.version.experiment["steps"]] == ["openmusic", "python", "python"]


def test_hybrid_replans_once_when_model_omits_om_step(tmp_path):
    bad = _plan("apply_techniques", instruments=["flute", "cello"])
    good = _plan(parameters={"measures": 8}, instruments=["flute", "cello"])
    good["techniques"] = ["frullato", "sul ponticello"]
    g, c = FakeGenerator(bad, good), Composition()
    r = compose(
        "eight measures for flute and cello",
        c,
        generator=g,
        chat=FakeChat(),
        model="m",
        mode="hybrid",
        registry=WITH_OM,
        tasks_dir=tmp_path,
    )
    assert r.task and len(g.calls) == 2
    assert "CORRECTION" in g.calls[1].config.system_instruction
    assert resume(c, OUTPUT_16).version
    g, c = FakeGenerator(bad, bad), Composition()
    r = compose("compose", c, generator=g, chat=FakeChat(), model="m", mode="hybrid", registry=WITH_OM)
    assert not r.task and not r.version and not c.pending
    assert "after correction" in r.summary and len(g.calls) == 2


def test_chat_imports_pending_output_without_reinterpreting_as_hz(tmp_path):
    from muschat.reasoning import respond
    from tests.test_reasoning import FakeChat as LogChat, FakeGenerator as LogGen, FakeRetriever as LogRet

    c, _, _ = _om(tmp_path)
    log = []
    g, chat, retriever = LogGen(log, RuntimeError("do not call model")), LogChat(log), LogRet(log)

    def send(text):
        return respond(text, generator=g, chat=chat, retriever=retriever, model="m", composition=c)

    waiting = send("create the composition")
    assert "awaiting" in waiting.final and not waiting.scores and c.pending
    invalid = send("3600 4800")
    assert "rejected" in invalid.final and c.pending and not c.versions
    ready = send(OUTPUT_16.strip("()"))
    assert not c.pending and len(c.versions) == 1 and len(ready.scores) == 1
    assert "midicents" in ready.final and "not Hz" in ready.final
    assert log == []


def test_creating_task_in_chat_never_invents_completed_composition():
    from muschat.reasoning import respond
    from tests.test_reasoning import ANALYSIS_OK, FakeChat as LogChat, FakeRetriever as LogRet
    from tests.test_reasoning_notation import SequentialGenerator

    log = []
    c = Composition()
    g = SequentialGenerator(log, json.dumps({**ANALYSIS_OK, "notation": "compose"}), json.dumps(_plan()))
    try:
        r = respond(
            "compose",
            generator=g,
            chat=LogChat(log),
            retriever=LogRet(log),
            model="m",
            composition=c,
            registry=WITH_OM,
            mode="openmusic",
        )
        assert c.pending and "not yet been completed" in r.final
        assert not r.scores and "synthesis" not in log
    finally:
        discard_task(c)


@pytest.mark.parametrize(
    "output,snippet",
    [
        ('(defun x () (shell "rm"))', "non-numeric"),
        ("3600,5 4800", "non-numeric"),
        ("3600 4800", "2 values"),
        (OUTPUT_16.replace("8400", "99999"), "0–12700"),
        ("1 " * 140_000, "limit"),
        (b"\xff\xfe\x00", "UTF-8"),
    ],
)
def test_malformed_output_is_rejected_and_task_remains_pending(tmp_path, output, snippet):
    c, r, _ = _om(tmp_path)
    r2 = resume(c, output)
    assert r2.version is None and snippet in r2.summary and c.pending is not None and c.versions == []


def test_pending_task_survives_serialization_roundtrip(tmp_path):
    c, r, _ = _om(tmp_path)
    restored = Composition.from_json(c.to_json())
    assert restored.pending is not None and restored.pending.task is not None and r.task is not None
    assert restored.pending.task.id == r.task.id
    assert resume(restored, OUTPUT_16).version is not None


def test_exported_and_reimported_json_reproduces_composition(tmp_path):
    from tests.test_composition import _quartet

    c, _, _ = _om(tmp_path, composition=_quartet(), plan=_plan(parameters={"partials": 16}, selection={"parts": ["P4"]}))
    text = c.to_json()
    restored = Composition.from_json(text)
    assert len(restored.versions) == 1 and restored.pending is not None
    assert restored.versions[0].ir == c.versions[0].ir and restored.pending == c.pending
    assert restored.to_json() == text


def test_new_request_while_task_pending_never_calls_model(tmp_path):
    c, _, _ = _om(tmp_path)
    _, r2, g = _run(_plan(), composition=c)
    assert r2.version is None and "Awaiting you" in r2.summary and g.calls == []
    discard_task(c)
    assert c.pending is None


_REAL_OM = detect_openmusic()


@pytest.mark.skipif(_REAL_OM is None, reason="OpenMusic not installed")
def test_real_openmusic_detected():
    assert _REAL_OM is not None and _REAL_OM.executable.is_file() and _REAL_OM.version


# --- hybrid ------------------------------------------------------------------------------------------------------------


def test_hybrid_with_incomplete_plan_composes_instead_of_stopping_at_reservoir(tmp_path):
    plan = _plan(parameters={"partials": 16, "measures": 8, "rests": 0})
    plan["techniques"] = ["sul tasto"]
    c, r, _ = _run(plan, mode="hybrid", registry=WITH_OM, tasks_dir=tmp_path)
    assert r.task is not None and c.pending is not None and len(c.pending.decision.steps) == 3
    assert c.pending.parameters[1] == {"measures": 8, "rests": 0}
    r = resume(c, OUTPUT_16)
    assert r.version is not None, r.summary
    assert len(r.version.ir.parts) == 4 and len(r.version.ir.measures) == 8
    assert any(e.techniques for _, _, e in r.version.ir.events() if hasattr(e, "techniques"))
    assert r.version.experiment["procedures"] == ["harmonic_series", "orchestrate_material", "gestural_techniques"]


def test_hybrid_explicit_material_without_instruments_stays_material(tmp_path):
    c, r, _ = _run(_plan(instruments=[]), mode="hybrid", registry=WITH_OM, tasks_dir=tmp_path)
    assert c.pending is not None and len(c.pending.decision.steps) == 1
    r = resume(c, OUTPUT_16)
    assert r.version is not None and r.version.ir.parts[0].instrument == "Spectral material"


def test_hybrid_pauses_at_openmusic_and_resume_finishes_remaining_steps(tmp_path):
    plan = _hybrid({"procedure": "apply_techniques"}) | {"techniques": ["harmonic"]}
    c, r, _ = _run(plan, mode="hybrid", registry=WITH_OM, tasks_dir=tmp_path)
    assert r.task is not None and c.pending is not None and c.pending.step == 0 and c.versions == []
    r2 = resume(c, OUTPUT_16)
    v = r2.version
    assert v is not None and [p.instrument for p in v.ir.parts] == ["Violin", "Violin", "Viola", "Cello"]
    assert [s["engine"] for s in v.experiment["steps"]] == ["openmusic", "python", "python"]
    assert len(v.experiment["intermediates"]) == 3
    assert v.experiment["intermediates"][0]["parts"][0]["instrument"] == "Spectral material"
    used = {p.midicents % 1200 for _, _, e in v.ir.events() if hasattr(e, "pitches") for p in e.pitches}
    spectrum = {round(int(x) / 50) * 50 % 1200 for x in OUTPUT_16.strip("()").split()}
    assert used <= spectrum
    assert {e.provenance.source for _, _, e in v.ir.events()} == {"algorithm"}


def test_failure_in_later_step_after_resume_does_not_save_partial_version(tmp_path):
    plan = _hybrid({"procedure": "apply_techniques"}) | {"techniques": ["whistle tone"]}
    c, r, _ = _run(plan, mode="hybrid", registry=WITH_OM, tasks_dir=tmp_path)
    r2 = resume(c, OUTPUT_16)
    assert r2.version is None and "step 3" in r2.summary and c.versions == [] and c.pending is None


def test_preservation_invariant_applies_to_openmusic_step(tmp_path):
    from tests.test_composition import _quartet

    c = _quartet()
    plan = _plan(parameters={"partials": 16}, selection={"parts": ["P4"]})
    c, r, _ = _run(plan, mode="openmusic", registry=WITH_OM, composition=c, tasks_dir=tmp_path)
    r2 = resume(c, OUTPUT_16)
    assert r2.version is None and "outside the selection" in r2.summary and len(c.versions) == 1


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda d: d["pending"].update(step=7),
        lambda d: d["pending"]["task"]["parameters"].update(microtone=99),
        lambda d: d["pending"]["task"].update(procedure="executar_lisp"),
        lambda d: d.update(index=5),
        lambda d: d.pop("versions"),
    ],
)
def test_inconsistent_imported_json_is_rejected(tmp_path, corrupt):
    c, _, _ = _om(tmp_path)
    data = json.loads(c.to_json())
    corrupt(data)
    with pytest.raises(ValueError):
        Composition.from_json(json.dumps(data))


def test_discard_deletes_task_directory(tmp_path):
    c, r, _ = _om(tmp_path)
    discard_task(c)
    assert r.task is not None and not Path(r.task.directory).exists() and c.pending is None

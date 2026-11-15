"""Optional NotaGen engine tests: installation detection, Registry/router integration, and composition steps."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from muschat.composition import Composition
from muschat.notagen import WEIGHTS_FILE, LocalNotaGen, NotaGenInstallation, detect_notagen
from muschat.notation.musicxml import to_musicxml
from muschat.orchestrator import compose, reapply
from muschat.plan import Operation, Plan
from muschat.reasoning import respond
from muschat.registry import detect_capabilities
from muschat.router import route
from tests.test_composition import FakeChat, FakeGenerator, _plan
from tests.test_musicxml_reader import ATTRIBUTES, _note_xml, _xml
from tests.test_reasoning import ANALYSIS_OK, FakeChat as LogChat, FakeRetriever as LogRet

CONDITIONS = ["Romantic_Ravel, Maurice_Chamber", "Classical_Mozart, Wolfgang Amadeus_Keyboard"]


def _installation(tmp_path: Path, *, weights: bool = True):
    python = tmp_path / "venv" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    gradio = tmp_path / "NotaGen" / "gradio"
    gradio.mkdir(parents=True, exist_ok=True)
    (gradio / "prompts.txt").write_text("\n".join(CONDITIONS) + "\n", encoding="utf-8")
    if weights:
        (gradio / WEIGHTS_FILE).write_bytes(b"")
    return detect_notagen(str(python), str(tmp_path / "NotaGen"))


def _registry(notagen):
    return detect_capabilities(lilypond=None, openmusic=None, abjad_version="3.31", which=lambda e: None, notagen=notagen)


def test_detection_reads_conditions_or_reports_missing_item(tmp_path):
    ok = _installation(tmp_path)
    assert isinstance(ok, NotaGenInstallation) and ok.conditions == tuple(CONDITIONS)
    missing_weights = detect_notagen(ok.python, str(tmp_path / "empty"))
    assert isinstance(missing_weights, str) and "weights" in missing_weights
    missing_py = detect_notagen(str(tmp_path / "missing.exe"), str(tmp_path / "NotaGen"))
    assert isinstance(missing_py, str) and "NOTAGEN_PYTHON" in missing_py


def test_detected_notagen_is_automatic_engine_with_conditions(tmp_path):
    inst = _installation(tmp_path)
    reg = _registry(inst)
    cap = reg.capability("NotaGen")
    assert cap is not None and cap.state == "automatic"
    assert cap.procedures == ("generate_notagen",) and cap.options == tuple(CONDITIONS)
    assert "generate_notagen" in reg.available_procedures()
    plan = Plan(goal="test", operations=[Operation(procedure="generate_notagen", condition=CONDITIONS[0])])
    decision = route(plan, "auto", reg)
    assert [s.engine for s in decision.steps] == ["notagen"]


def test_missing_notagen_is_unavailable_with_reason_and_disabled_is_absent(tmp_path):
    reg = _registry(_installation(tmp_path, weights=False))
    cap = reg.capability("NotaGen")
    assert cap is not None and cap.state == "unavailable" and "weights" in cap.reason
    assert "generate_notagen" not in reg.available_procedures()
    assert _registry(None).capability("NotaGen") is None


# --- Composition step integration -------------------------------------------------------------------------------------


class FakeComposer:
    def __init__(self, failure: str | None = None):
        self.calls, self.failure = [], failure

    def generate(self, condition: str, measures: int, seed: int) -> bytes:
        self.calls.append((condition, measures, seed))
        if self.failure:
            raise ValueError(self.failure)
        bars = "".join(
            f'<measure number="{n}">{ATTRIBUTES if n == 1 else ""}{_note_xml("C", 4, 6, "half", extra="<dot/>")}</measure>'
            for n in range(1, measures + 1)
        )
        return _xml(
            f'<part id="P1">{bars}</part><part id="P2">{bars.replace("C</step>", "E</step>")}</part>',
            '<score-part id="P1"><part-name>Violin</part-name></score-part>'
            '<score-part id="P2"><part-name>Cello</part-name></score-part>',
        )


def _generate(tmp_path, composer, *extras, condition=CONDITIONS[0], instruments=("violin", "cello"), comp=None):
    plan = _plan("generate_notagen", parameters={"measures": 2}, instruments=list(instruments))
    plan["operations"][0]["condition"] = condition
    plan["operations"] += list(extras)
    comp = comp or Composition()
    result = compose(
        "request",
        comp,
        generator=FakeGenerator(plan),
        chat=FakeChat(),
        model="m",
        registry=_registry(_installation(tmp_path)),
        composer=composer,
    )
    return comp, result


def test_notagen_generates_external_version_with_plan_instruments(tmp_path):
    fake = FakeComposer()
    _, result = _generate(tmp_path, fake)
    assert result.version is not None, result.summary
    assert fake.calls == [(CONDITIONS[0], 2, fake.calls[0][2])] and isinstance(fake.calls[0][2], int)
    events = [e for _, _, e in result.version.ir.events()]
    assert {(e.provenance.source, e.provenance.author) for e in events} == {("external", "NotaGen-X")}
    assert [p.instrument for p in result.version.ir.parts] == ["Violin", "Cello"]
    assert [p.clef for p in result.version.ir.parts] == ["treble", "bass"]
    assert result.version.experiment["steps"][0]["engine"] == "notagen"
    assert list(result.version.experiment["external_outputs"]) == [0]


def test_notagen_material_flows_into_python_transformations(tmp_path):
    transpose_op = {"procedure": "transpose", "parameters": [{"name": "interval", "value": 50}]}
    _, result = _generate(tmp_path, FakeComposer(), transpose_op)
    assert result.version is not None
    assert [s["engine"] for s in result.version.experiment["steps"]] == ["notagen", "python"]
    assert result.version.ir.parts[0].voices[0].events[0].pitches[0].midicents == 6050


def test_notagen_mode_requires_local_call_and_executes_transformation(tmp_path):
    reg = _registry(_installation(tmp_path))
    fake = FakeComposer()
    plan = _plan("generate_notagen", parameters={"measures": 2}, instruments=["violin", "cello"])
    plan["operations"][0]["condition"] = CONDITIONS[0]
    plan["operations"].append({"procedure": "transpose", "parameters": [{"name": "interval", "value": 50}]})
    comp = Composition()
    gen = FakeGenerator(plan)
    result = compose(
        "compose", comp, generator=gen, chat=FakeChat(), model="m", mode="notagen", registry=reg, composer=fake
    )
    assert result.version is not None, result.summary
    assert len(fake.calls) == 1
    assert [s["engine"] for s in result.version.experiment["steps"]] == ["notagen", "python"]
    assert "Start mandatorily with generate_notagen" in gen.calls[0].config.system_instruction
    result = compose(
        "compose",
        comp,
        generator=FakeGenerator(_plan(), _plan()),
        chat=FakeChat(),
        model="m",
        mode="notagen",
        registry=reg,
        composer=fake,
    )
    assert result.version is None and "even after correction" in result.summary and len(comp.versions) == 1


def test_unknown_condition_is_refused_without_running_model(tmp_path):
    fake = FakeComposer()
    _, result = _generate(tmp_path, fake, condition="Contemporary_Lachenmann_Chamber")
    assert result.version is None and "condition" in result.summary and fake.calls == []


def test_model_failure_becomes_refusal_and_nothing_is_saved(tmp_path):
    comp, result = _generate(tmp_path, FakeComposer(failure="GPU out of memory"))
    assert result.version is None and "GPU out of memory" in result.summary and comp.versions == []
    comp, result = _generate(tmp_path, None)
    assert result.version is None and "NotaGen" in result.summary and comp.versions == []


def test_reapply_reuses_stored_musicxml_and_regenerates_only_if_parameter_changes(tmp_path):
    fake = FakeComposer()
    comp, result = _generate(tmp_path, fake)
    assert result.version is not None
    same = reapply(comp, [{"measures": 2}], registry=_registry(_installation(tmp_path / "b")), composer=fake)
    assert same.version is not None and len(fake.calls) == 1
    assert to_musicxml(same.version.ir) == to_musicxml(result.version.ir)
    longer = reapply(comp, [{"measures": 3}], registry=_registry(_installation(tmp_path / "c")), composer=fake)
    assert longer.version is not None and len(fake.calls) == 2 and len(longer.version.ir.measures) == 3


def test_planner_prompt_lists_conditions_only_when_notagen_is_available(tmp_path):
    gen = FakeGenerator(_plan())
    compose("request", Composition(), generator=gen, chat=FakeChat(), model="m", registry=_registry(_installation(tmp_path)))
    system = gen.calls[0].config.system_instruction
    assert "- generate_notagen:" in system and CONDITIONS[0] in system
    gen = FakeGenerator(_plan())
    compose("request", Composition(), generator=gen, chat=FakeChat(), model="m", registry=_registry("no GPU"))
    assert "- generate_notagen:" not in gen.calls[0].config.system_instruction


def test_installation_without_prompts_file_returns_reason(tmp_path):
    inst = _installation(tmp_path)
    assert isinstance(inst, NotaGenInstallation)
    (Path(inst.directory) / "gradio" / "prompts.txt").unlink()
    res = detect_notagen(inst.python, inst.directory)
    assert isinstance(res, str) and "prompts.txt" in res


def test_manual_python_mode_with_generate_notagen_refuses_with_alternative(tmp_path):
    plan = _plan("generate_notagen", parameters={"measures": 2})
    plan["operations"][0]["condition"] = CONDITIONS[0]
    fake = FakeComposer()
    result = compose(
        "request",
        Composition(),
        generator=FakeGenerator(plan),
        chat=FakeChat(),
        model="m",
        mode="python",
        registry=_registry(_installation(tmp_path)),
        composer=fake,
    )
    assert result.version is None and "NotaGen (automatic)" in result.summary and fake.calls == []


def test_different_part_count_is_adapted_and_documented(tmp_path):
    fake = FakeComposer()
    comp, result = _generate(tmp_path, fake, instruments=("flute", "oboe", "bassoon"))
    assert result.version is not None and len(comp.versions) == 1
    assert [p.instrument for p in result.version.ir.parts] == ["Flute", "Oboe", "Bassoon"]
    assert "orchestration adapted from 2 to 3 parts" in result.summary
    redone = reapply(
        comp,
        result.version.experiment["parameters"],
        registry=_registry(_installation(tmp_path / "reuse")),
        composer=fake,
    )
    assert redone.version is not None and to_musicxml(redone.version.ir) == to_musicxml(result.version.ir)
    assert len(fake.calls) == 1


def test_fewer_measures_returned_produces_warning(tmp_path):
    fake = FakeComposer()
    orig_generate = fake.generate
    fake.generate = lambda condition, measures, seed: orig_generate(condition, 1, seed)  # type: ignore[method-assign]
    _, result = _generate(tmp_path, fake)
    assert "1 of 2 measures" in result.summary


def test_duo_request_with_solo_output_adapts_before_contemporary_layer(tmp_path):
    comp, previous = _generate(tmp_path / "base", FakeComposer())

    class SoloComposer:
        def generate(self, condition, measures, seed):
            root = ET.fromstring(FakeComposer().generate(condition, measures, seed))
            part = root.find("part")
            assert part is not None
            root.remove(part)
            part_list = root.find("part-list")
            assert part_list is not None
            sp = part_list.find("score-part")
            assert sp is not None
            part_list.remove(sp)
            pname = part_list.find("score-part/part-name")
            assert pname is not None
            pname.text = "Violoncello"
            return ET.tostring(root)

    plan = _plan("generate_notagen", parameters={"measures": 8}, instruments=["flute", "cello"])
    plan["operations"][0]["condition"] = CONDITIONS[0]
    plan["operations"].append({"procedure": "rarefy"})
    chat = LogChat([])
    response = respond(
        "create eight measures for flute and cello",
        composition=comp,
        generator=FakeGenerator({**ANALYSIS_OK, "notation": "compose"}, plan),
        chat=chat,
        retriever=LogRet([]),
        model="m",
        mode="notagen",
        registry=_registry(_installation(tmp_path / "duo")),
        composer=SoloComposer(),
    )
    assert comp.versions[0] is previous.version and len(comp.versions) == 2
    assert response.scores and chat.messages and comp.current is not None
    assert [p.instrument for p in comp.current.ir.parts] == ["Flute", "Cello"]
    assert len(comp.current.ir.measures) == 8
    assert "orchestration adapted from 1 to 2 parts" in chat.messages[0].config.system_instruction


def test_subprocess_failure_becomes_message_instead_of_raw_exception(tmp_path):
    gradio = tmp_path / "gradio"
    gradio.mkdir()
    local = LocalNotaGen(NotaGenInstallation(sys.executable, str(tmp_path), ("A_B_C",)))
    with pytest.raises(ValueError, match="NotaGen failed"):
        local.generate("A_B_C", 1, 1)
    with pytest.raises(ValueError, match="not accepted"):
        local.generate("X_Y_Z", 1, 1)


def test_two_section_form_and_section_edit_preserves_first_section(tmp_path):
    class WithTempo(FakeComposer):
        def generate(self, *args):
            data = super().generate(*args)
            if len(self.calls) == 2:
                data = data.replace(b"<beats>3</beats>", b"<beats>4</beats>")
            return data.replace(
                b"</attributes>", b'</attributes><direction><sound tempo="120"/></direction>'
            )

    fake = WithTempo()
    reg = _registry(_installation(tmp_path))
    plan = _plan("generate_notagen", instruments=["violin", "cello"])
    plan["sections"] = [
        dict(name=n, character="calm", material="notes", techniques=[], start_measure=i, end_measure=i + 1)
        for n, i in [("A", 1), ("B", 3)]
    ]
    plan["operations"] = [
        op
        for n in ["A", "B"]
        for op in [
            dict(procedure="generate_notagen", condition=CONDITIONS[0], section=n),
            dict(procedure="transpose", section=n, parameters=[dict(name="interval", value=50)]),
        ]
    ]
    comp = Composition()

    def run_plan(p):
        return compose("edit B", comp, generator=FakeGenerator(p), chat=FakeChat(), model="m", registry=reg, composer=fake)

    huge = {
        **plan,
        "sections": [dict(name="A", character="calm", material="notes", start_measure=1, end_measure=1000000)],
        "operations": plan["operations"][:2],
    }
    assert run_plan(huge).version is None
    incomplete = {**plan, "operations": plan["operations"][:2]}
    assert run_plan(incomplete).version is None
    assert fake.calls == []
    result = run_plan(plan)
    assert result.version is not None, result.summary
    assert [v[1] for v in fake.calls] == [2, 2]
    before = result.version.ir
    assert [m.numerator for m in before.measures] == [3, 3, 4, 4]
    assert before.measures[2].tempo == 120
    edit = _plan("transpose", parameters={"interval": 100})
    edit["operations"][0]["section"] = "B"
    result = run_plan(edit)
    assert result.version is not None, result.summary
    assert result.version.experiment["plan"]["sections"] == plan["sections"]
    for pa, pn in zip(before.parts, result.version.ir.parts):
        assert pa.voices[0].events[:2] == pn.voices[0].events[:2]
        assert pn.voices[0].events[2].pitches[0].midicents == pa.voices[0].events[2].pitches[0].midicents + 100
    edit["sections"] = [{**section, "character": "new character"} for section in plan["sections"]]
    evolved = run_plan(edit)
    assert evolved.version is not None, evolved.summary
    assert evolved.version.experiment.plan.sections[0].character == "new character"
    edit["operations"][0]["section"] = "missing"
    assert run_plan(edit).version is None
    assert len(comp.versions) == 3


def test_notagen_planner_retries_missing_generation_once(tmp_path):
    plan = _plan("generate_notagen", parameters={"measures": 2}, instruments=["violin", "cello"])
    plan["operations"][0]["condition"] = CONDITIONS[0]
    generator = FakeGenerator(_plan(), plan)
    fake = FakeComposer()
    result = compose("compose", Composition(), generator=generator, chat=FakeChat(), model="m", mode="notagen", registry=_registry(_installation(tmp_path)), composer=fake)
    assert result.version is not None, result.summary
    assert len(generator.calls) == 2 and len(fake.calls) == 1
    assert "CORRECTION" in generator.calls[1].config.system_instruction


@pytest.mark.parametrize("relative", [True, False])
def test_local_notagen_resolves_paths_before_changing_working_directory(tmp_path, monkeypatch, relative):
    import os
    import muschat.notagen as notagen

    installation_dir = tmp_path / "model with spaces"
    gradio = installation_dir / "gradio"
    gradio.mkdir(parents=True)
    runner = tmp_path / "fake runner.py"
    runner.write_text("import os, sys\nfrom pathlib import Path\nroot = Path(sys.argv[1])\nos.chdir(root / 'gradio')\nassert (root / 'gradio').is_dir()\nPath(sys.argv[5]).write_bytes(b'<score/>')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(notagen, "SCRIPT_PATH", runner)
    directory = os.path.relpath(installation_dir, tmp_path) if relative else str(installation_dir)
    python = os.path.relpath(sys.executable, tmp_path) if relative else sys.executable
    backend = LocalNotaGen(NotaGenInstallation(python, directory, ("A_B_C",)))
    assert backend.generate("A_B_C", 1, 1) == b"<score/>"


def test_notagen_compound_rest_is_split_without_changing_rhythm(tmp_path):
    from fractions import Fraction
    from muschat.score_ir import Rest

    class CompoundRestComposer:
        def generate(self, *args):
            attributes = ATTRIBUTES.replace("<beats>3</beats>", "<beats>4</beats>")
            music = _note_xml("C", 4, 1, "eighth") + "<note><rest/><duration>7</duration><voice>1</voice><type>half</type><dot/><dot/></note>"
            return _xml(f'<part id="P1"><measure number="1">{attributes}{music}</measure></part>')

    _, result = _generate(tmp_path, CompoundRestComposer(), instruments=("violin",))
    assert result.version is not None, result.summary
    events = result.version.ir.parts[0].voices[0].events
    assert [e.duration for e in events] == [Fraction(1,8), Fraction(1,2), Fraction(3,8)]
    assert isinstance(events[1], Rest) and isinstance(events[2], Rest)
    assert events[-1].end == 1


def test_notagen_cutoff_closes_outgoing_tie_at_voice_end(tmp_path):
    class TiedEnding:
        def generate(self, *args):
            return _xml(f'<part id="P1"><measure number="1">{ATTRIBUTES}{_note_xml("C", 4, 6, "half", extra="<dot/><tie type='start'/>")}</measure></part>')

    _, result = _generate(tmp_path, TiedEnding(), instruments=("violin",))
    assert result.version is not None, result.summary
    assert not result.version.ir.parts[0].voices[0].events[-1].tied

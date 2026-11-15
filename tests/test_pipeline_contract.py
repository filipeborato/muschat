"""Public pipeline boundaries for structural validation and post-processing."""

from fractions import Fraction
import pytest
from muschat.composition import Composition
from muschat.errors import RefusalError
from muschat.orchestrator import compose, resume
from muschat.preservation import validate_ir
from tests.test_composition import FakeGenerator, FakeChat, _plan, _quartet
from tests.test_om_libraries import WITH_LIBS


def test_pipeline_serializes_only_consolidated_score(monkeypatch):
    from muschat.notation.musicxml import to_musicxml

    calls = []

    def serialize(ir):
        calls.append(ir)
        return to_musicxml(ir)

    monkeypatch.setattr("muschat.orchestrator.to_musicxml", serialize)
    plan = _plan()
    plan["operations"].append(
        {"procedure": "transpose", "parameters": [{"name": "interval", "value": 50}]}
    )
    result = compose(
        "test", Composition(), generator=FakeGenerator(plan), chat=FakeChat(), model="m"
    )
    assert result.version is not None, result.summary
    assert calls == [result.version.ir]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"duration": Fraction(1, 7)}, "notated duration"),
        ({"tied": True}, "invalid tie"),
        ({"id": "P1"}, "duplicate ids"),
    ],
)
def test_structural_validation_rejects_invalid_copied_notes(change, message):
    ir = _quartet().current.ir.model_copy(deep=True)
    voice = ir.parts[0].voices[0]
    voice.events[0] = voice.events[0].model_copy(update=change)
    with pytest.raises(RefusalError, match=message):
        validate_ir(ir)


def test_assisted_audio_post_processing_is_recorded_and_persisted(
    tmp_path, monkeypatch
):
    comp = _quartet()
    plan = _plan("additive_synthesis")
    result = compose(
        "synthesize",
        comp,
        generator=FakeGenerator(plan),
        chat=FakeChat(),
        model="m",
        mode="openmusic",
        registry=WITH_LIBS,
        tasks_dir=tmp_path,
    )
    assert result.task is not None, result.summary
    expected = {"status": "rendered", "path": "test.wav"}
    directories = []

    def render(directory):
        directories.append(directory)
        return expected

    monkeypatch.setattr("muschat.audio.synthesize_audio", render)
    count = result.task.content.splitlines()[0]
    result = resume(comp, count)
    assert result.version is not None, result.summary
    assert len(directories) == 1 and result.version.experiment.audio == expected
    assert Composition.from_json(comp.to_json()).current.experiment.audio == expected


def test_structural_validation_rejects_noncontiguous_tuplet():
    from muschat.score_ir import Tuplet

    ir = _quartet().current.ir.model_copy(deep=True)
    voice = ir.parts[0].voices[0]
    voice.tuplets = [
        Tuplet(
            id="T1",
            numerator=3,
            denominator=2,
            events=[voice.events[0].id, voice.events[2].id],
        )
    ]
    with pytest.raises(RefusalError, match="contiguous notated events"):
        validate_ir(ir)

import io
import json
import struct
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction as F

from muschat.export import experiment_json, metrics, to_midi, version_package
from muschat.notation.lilypond import EngravingResult
from muschat.score_ir import Measure, Note, Part, Pitch, Provenance, Rest, ScoreIR, Voice
from tests.examples import quartet


def _read_midi(data: bytes) -> tuple[int, list[list[tuple[int, int, int]]]]:
    """Minimal SMF reader: (ticks per quarter, [(start, pitch, duration) per track])."""
    assert data[:4] == b"MThd"
    _, n, tpq = struct.unpack(">HHH", data[8:14])
    pos, tracks = 14, []
    for _ in range(n):
        assert data[pos : pos + 4] == b"MTrk"
        end = pos + 8 + struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        pos, t, status, active, notes = pos + 8, 0, 0, {}, []
        while pos < end:
            delta = 0
            while True:
                b = data[pos]
                pos += 1
                delta = (delta << 7) | (b & 0x7F)
                if b < 0x80:
                    break
            t += delta
            if data[pos] >= 0x80:
                status = data[pos]
                pos += 1
            if status == 0xFF:
                pos += 1
                size = data[pos]
                pos += 1 + size
            elif status & 0xF0 in (0x80, 0x90):
                pitch, vel = data[pos], data[pos + 1]
                pos += 2
                if status & 0xF0 == 0x90 and vel:
                    active[pitch] = t
                else:
                    notes.append((active.pop(pitch), pitch, t))
            elif status & 0xF0 in (0xC0, 0xD0):
                pos += 1
            else:
                pos += 2
        tracks.append(sorted((i, p, f - i) for i, p, f in notes))
    return tpq, tracks


def test_reread_midi_has_expected_pitches_and_durations():
    tpq, tracks = _read_midi(to_midi(quartet()))
    whole = 4 * tpq
    violin, cello = tracks
    # A4+50c rounds to nearest semitone (declared loss); rests do not become notes
    assert violin == [(0, 70, whole // 2)]
    # triplets: 1/6 of whole each; curve and spectral frame do not become notes
    assert cello == [(int(F(k, 6) * whole), p, whole // 6) for k, p in enumerate([36, 43, 48])]


def test_descriptive_metrics_match_handcrafted_score_ir():
    ir = quartet()
    m = metrics(ir)
    assert m["events"] == 8 and m["notes"] == 4
    assert m["attacks_per_quarter_summed"] == 1.0
    assert m["pitch_range_midicents"] == [3600, 6950]
    assert m["durations"] == {"1/6": 3, "1/2": 1}
    assert m["interval_stdev_cents"] == 100.0
    assert "violations" not in m and "diff" not in m


def _melody(pid: str, instrument_name: str, events: list[tuple[int | None, F] | tuple[int, F, list[str]]]) -> Part:
    end, created = F(0), []
    for k, (pitch, dur, *techs) in enumerate(events):
        eid, prov = f"{pid}{k}", Provenance(source="algorithm")
        created.append(
            Rest(id=eid, start=end, duration=dur, provenance=prov)
            if pitch is None
            else Note(
                id=eid,
                start=end,
                duration=dur,
                pitches=[Pitch(midicents=pitch)],
                techniques=techs[0] if techs else [],
                provenance=prov,
            )
        )
        end += dur
    return Part(id=pid, instrument=instrument_name, voices=[Voice(id=f"{pid}v", events=created)])


def test_line_crossing_phrase_and_technique_metrics():
    q = F(1, 4)
    flute = _melody(
        "fl",
        "Flute",
        [
            (7200, q),
            (7400, q),
            (7400, q),
            (None, q),
            (6000, F(1, 2), ["jet whistle"]),
            (6200, F(1, 2), ["slap"]),
        ],
    )
    cello = _melody("vc", "Cello", [(None, F(1)), (4800, F(1, 2)), (6500, F(1, 2))])
    ir = ScoreIR(title="t", measures=[Measure(numerator=4, denominator=4)] * 2, parts=[flute, cello])
    m = metrics(ir)
    assert m["intervals"] == {"repetitions": 1, "steps": 2, "leaps": 2}
    assert m["leaps_over_octave"] == {"fl": 1, "vc": 1}
    assert m["voice_crossings"] == 1
    assert m["density_by_phrase"] == {"fl": [1.0, 0.5, 0.5], "vc": [0.5, 0.5]}
    assert m["unplayable_techniques"] == []
    note_c = cello.voices[0].events[1]
    assert isinstance(note_c, Note)
    note_c.techniques = ["slap"]
    assert metrics(ir)["unplayable_techniques"] == ["vc m.2: slap (Cello): not playable in this instrument family"]


def test_metrics_with_previous_version_and_constraints():
    before = quartet()
    after = before.model_copy(deep=True)
    note0 = after.parts[0].voices[0].events[0]
    assert isinstance(note0, Note)
    note0.dynamic = "ff"
    m = metrics(after, previous=before, violations=["tessitura (vc m.1 c): too low"])
    assert m["diff"] == {"added": 0, "removed": 0, "modified": 1}
    assert m["violations"] == 1


def _names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return set(z.namelist())


def test_zip_contains_only_actually_generated_formats():
    from tests.test_composition import _quartet

    c = _quartet()
    base = {"composition_v1.musicxml", "composition_v1.mid", "composition.json", "experiment.json", "LOSSES.txt"}
    assert _names(version_package(c, 0)) == base
    with_verovio = version_package(c, 0, svg="<svg/>")
    assert _names(with_verovio) == base | {"composition_v1.svg"}
    without_engraving = version_package(
        c, 0, lilypond={"score": "x"}, engraving=EngravingResult(warning="no LilyPond"), svg="<svg/>"
    )
    assert _names(without_engraving) == base | {"composition_v1.svg"}
    engraving = EngravingResult(svgs=["<svg/>", "<svg/>"], pdfs={"score": b"%PDF", "P1": b"%PDF"})
    full = version_package(
        c, 0, lilypond={"score": r"\score{}", "P1": r"\score{}"}, engraving=engraving, svg="<svg/>"
    )
    assert _names(full) == base | {
        "composition_v1_score.ly",
        "composition_v1_P1.ly",
        "composition_v1_score.pdf",
        "composition_v1_P1.pdf",
        "composition_v1_score-1.svg",
        "composition_v1_score-2.svg",
    }


def test_experiment_json_records_prompt_model_seed_dependencies_and_diff():
    from tests.test_composition import _quartet

    c = _quartet()
    exp = json.loads(experiment_json(c, engines={"LilyPond": "2.24.4"}))
    v = exp["versions"][0]
    assert v["prompt"] == "request" and v["model"] == "m" and v["seed"] == 7
    assert v["procedures"] == ["harmonic_series"] and "decision" in v and "constraints" in v["plan"]
    assert v["dependencies"]["abjad"] and v["dependencies"]["python"]
    assert v["diff"]["added"] and v["metrics"]["notes"] > 0
    assert exp["engines"] == {"LilyPond": "2.24.4"}


def test_midi_merges_tied_notes_into_single_attack():
    prov = Provenance(source="composer")
    voice = Voice(
        id="v",
        events=[
            Note(id="a", start=F(0), duration=F(3, 4), pitches=[Pitch(midicents=6000)], tied=True, provenance=prov),
            Note(
                id="b", start=F(3, 4), duration=F(1, 2), pitches=[Pitch(midicents=6000)], tied=True, provenance=prov
            ),
            Note(id="c", start=F(5, 4), duration=F(1, 4), pitches=[Pitch(midicents=6000)], provenance=prov),
            Note(id="d", start=F(3, 2), duration=F(1, 2), pitches=[Pitch(midicents=6200)], provenance=prov),
        ],
    )
    ir = ScoreIR(
        title="t",
        measures=[Measure(numerator=4, denominator=4)] * 2,
        parts=[Part(id="p", instrument="Flute", voices=[voice])],
    )
    tpq, (track,) = _read_midi(to_midi(ir))
    assert track == [(0, 60, int(F(3, 2) * 4 * tpq)), (int(F(3, 2) * 4 * tpq), 62, 2 * tpq)]


def test_technique_outside_register_too_short_or_imprecise_is_flagged():
    bass_cl = _melody(
        "cb",
        "Bass Clarinet",
        [(7400, F(1, 4), ["slap"]), (4000, F(1, 4), ["slap"]), (6000, F(1, 2), ["multiphonic"])],
    )
    flute = _melody(
        "fl",
        "Flute",
        [(8000, F(1, 8), ["whistle tone"]), (7250, F(1, 8), ["jet whistle"]), (8000, F(3, 4), ["whistle tone"])],
    )
    ir = ScoreIR(title="t", measures=[Measure(numerator=4, denominator=4)], parts=[flute, bass_cl])
    assert metrics(ir)["unplayable_techniques"] == [
        "fl m.1: whistle tone (Flute): too short (minimum 1/4)",
        "fl m.1: jet whistle (Flute): lacks precise pitch (microtonal notation)",
        "cb m.1: slap (Bass Clarinet): outside register (too high)",
    ]


def test_checker_uses_parts_own_range():
    flute = _melody("fl", "Flute", [(6250, F(1), ["slap"])])
    narrow = flute.model_copy(update={"range": (6100, 6300)})
    ir = ScoreIR(title="t", measures=[Measure(numerator=4, denominator=4)], parts=[narrow])
    assert metrics(ir)["unplayable_techniques"] == ["fl m.1: slap (Flute): outside register (too high)"]


def test_zip_respects_transposed_score_without_changing_midi():
    c = __import__("tests.test_composition", fromlist=["_quartet"])._quartet()
    c.versions[0].ir.parts[0].instrument = "Clarinet"
    with zipfile.ZipFile(io.BytesIO(version_package(c, 0, transposed=True))) as z:
        root = ET.fromstring(z.read("composition_v1.musicxml"))
        assert root.findtext(".//transpose/chromatic") == "-2"
        assert z.read("composition_v1.mid") == to_midi(c.versions[0].ir)

import xml.etree.ElementTree as ET
from fractions import Fraction as F

import pytest

from muschat.notation.model import ExampleMeasure, ExampleNote, ScoreExample, to_score_ir
from muschat.notation.musicxml import from_musicxml, to_musicxml
from muschat.notation.render import render_svg
from muschat.score_ir import Measure, Note, Part, Pitch, Provenance, ScoreIR, Voice
from tests.examples import quartet


def _example(*measures, **kw):
    base = dict(title="Example", instrument="Violin", clef="treble", meter="4/4", tempo=60)
    return to_score_ir(ScoreExample(**{**base, **kw}, measures=[ExampleMeasure(notes=list(m)) for m in measures]))


def _notes_el(xml: str) -> list[ET.Element]:
    return list(ET.fromstring(xml).iter("note"))


def _words(xml: str) -> list[str]:
    return [w.text or "" for w in ET.fromstring(xml).iter("words")]


def test_header_title_part_meter_clef_tempo():
    root = ET.fromstring(to_musicxml(_example([ExampleNote(pitches=["C4"], duration="whole")])))
    assert root.tag == "score-partwise"
    assert root.findtext("work/work-title") == "Example"
    assert root.findtext("part-list/score-part/part-name") == "Violin"
    attrs = root.find("part/measure/attributes")
    assert attrs is not None
    assert (attrs.findtext("time/beats"), attrs.findtext("time/beat-type")) == ("4", "4")
    assert (attrs.findtext("clef/sign"), attrs.findtext("clef/line")) == ("G", "2")
    assert root.findtext(".//metronome/per-minute") == "60"


@pytest.mark.parametrize(
    "pitch,step,alter,octave,accidental",
    [
        ("E5", "E", None, "5", None),
        ("F#4", "F", "1", "4", "sharp"),
        ("Bb3", "B", "-1", "3", "flat"),
        ("A4+", "A", "0.5", "4", "quarter-sharp"),
        ("A4-", "A", "-0.5", "4", "quarter-flat"),
        ("C#+5", "C", "1.5", "5", "three-quarters-sharp"),
    ],
)
def test_pitches_with_accidentals_and_microtones(pitch, step, alter, octave, accidental):
    note = _notes_el(to_musicxml(_example([ExampleNote(pitches=[pitch], duration="whole")])))[0]
    assert note.findtext("pitch/step") == step
    assert note.findtext("pitch/alter") == alter
    assert note.findtext("pitch/octave") == octave
    assert note.findtext("accidental") == accidental


def test_durations_dots_and_rest():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["C4"], duration="half", dotted=True),
                ExampleNote(pitches=[], duration="quarter"),
            ]
        )
    )
    a, b = _notes_el(xml)
    assert (a.findtext("type"), a.find("dot") is not None, a.findtext("duration")) == ("half", True, "24")
    assert b.find("rest") is not None and b.findtext("type") == "quarter"


def test_chord_marks_subsequent_notes_with_chord():
    notes = _notes_el(to_musicxml(_example([ExampleNote(pitches=["G4", "C5", "E5"], duration="whole")])))
    assert [n.find("chord") is not None for n in notes] == [False, True, True]


def test_grimoire_techniques_become_notehead_text_and_technical():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["D5"], duration="quarter", techniques=["col legno battuto"]),
                ExampleNote(pitches=["E6"], duration="quarter", techniques=["harmonic"]),
                ExampleNote(pitches=["A4"], duration="quarter", techniques=["sul ponticello", "tremolo"]),
                ExampleNote(pitches=["G3"], duration="quarter", techniques=["pizz. bartok"]),
            ]
        )
    )
    clb, harm, sp, bartok = _notes_el(xml)
    assert clb.findtext("notehead") == "x"
    assert harm.findtext("notehead") == "diamond" and harm.find("notations/technical/harmonic") is None
    assert sp.find("notations/ornaments/tremolo") is not None
    assert bartok.find("notations/technical/snap-pizzicato") is not None
    assert {"c.l. batt.", "sul pont."} <= set(_words(xml))


def test_text_only_appears_when_technique_changes():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["A4"], duration="quarter", techniques=["sul ponticello"]),
                ExampleNote(pitches=["B4"], duration="quarter", techniques=["sul ponticello"]),
                ExampleNote(pitches=["C5"], duration="quarter"),
            ]
        )
    )
    assert _words(xml) == ["sul pont.", "ord."]


def test_unknown_technique_becomes_text_without_failing():
    xml = to_musicxml(_example([ExampleNote(pitches=["C5"], duration="whole", techniques=["sopro com chave"])]))
    assert "sopro com chave" in _words(xml)


def test_glissando_connects_note_to_following_note():
    a, b = _notes_el(
        to_musicxml(
            _example(
                [
                    ExampleNote(pitches=["A5"], duration="half", glissando=True),
                    ExampleNote(pitches=["A5-"], duration="half"),
                ]
            )
        )
    )
    ga, gb = a.find("notations/glissando"), b.find("notations/glissando")
    assert ga is not None and gb is not None
    assert (ga.get("type"), gb.get("type")) == ("start", "stop")


def test_dynamic_and_free_text():
    xml = to_musicxml(_example([ExampleNote(pitches=["C5"], duration="whole", dynamic="pppp", text="come un respiro")]))
    root = ET.fromstring(xml)
    assert root.find(".//direction/direction-type/dynamics/pppp") is not None
    assert "come un respiro" in _words(xml)


def test_invalid_pitch_becomes_rest_instead_of_crashing():
    note = _notes_el(to_musicxml(_example([ExampleNote(pitches=["H9"], duration="whole")])))[0]
    assert note.find("rest") is not None


def test_multiple_numbered_measures():
    root = ET.fromstring(
        to_musicxml(_example([ExampleNote(pitches=["C4"], duration="whole")], [ExampleNote(pitches=["D4"], duration="whole")]))
    )
    assert [m.get("number") for m in root.iter("measure")] == ["1", "2"]


def test_result_renders_in_verovio():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["D5"], duration="half", techniques=["col legno battuto"], dynamic="mf"),
                ExampleNote(pitches=["A4+"], duration="half", techniques=["sul ponticello", "tremolo"]),
            ]
        )
    )
    svg = render_svg(xml)
    assert svg is not None and svg.lstrip().startswith("<svg")


def test_render_invalid_xml_returns_none():
    assert render_svg("<not-musicxml/>") is None


def test_dotted_16th_has_integer_duration():
    note = _notes_el(to_musicxml(_example([ExampleNote(pitches=["C5"], duration="16th", dotted=True)])))[0]
    assert note.findtext("duration") == "3"


def test_natural_accidental_when_canceling_within_measure():
    a, b, c = _notes_el(
        to_musicxml(
            _example(
                [
                    ExampleNote(pitches=["F#4"], duration="quarter"),
                    ExampleNote(pitches=["F4"], duration="quarter"),
                    ExampleNote(pitches=["G4"], duration="half"),
                ]
            )
        )
    )
    assert (a.findtext("accidental"), b.findtext("accidental"), c.findtext("accidental")) == ("sharp", "natural", None)


def test_duplicate_microtone_is_rejected():
    note = _notes_el(to_musicxml(_example([ExampleNote(pitches=["A#+4+"], duration="whole")])))[0]
    assert note.find("rest") is not None


def test_glissando_into_rest_or_from_invalid_pitch_is_not_dangling():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["A5"], duration="quarter", glissando=True),
                ExampleNote(pitches=[], duration="quarter"),
                ExampleNote(pitches=["H9"], duration="quarter", glissando=True),
                ExampleNote(pitches=["C5"], duration="quarter"),
            ]
        )
    )
    assert ET.fromstring(xml).find(".//glissando") is None


def test_exiting_pizzicato_writes_arco():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["G3"], duration="half", techniques=["pizzicato"]),
                ExampleNote(pitches=["G3"], duration="half"),
            ]
        )
    )
    assert _words(xml) == ["pizz.", "arco"]


def test_rest_does_not_change_ongoing_technique():
    xml = to_musicxml(
        _example(
            [
                ExampleNote(pitches=["C6"], duration="quarter", techniques=["frullato"]),
                ExampleNote(pitches=[], duration="quarter"),
                ExampleNote(pitches=["D6"], duration="half", techniques=["frullato"]),
            ]
        )
    )
    assert _words(xml) == ["flz."]


def test_free_text_matching_technique_is_not_duplicated():
    xml = to_musicxml(_example([ExampleNote(pitches=["E5"], duration="whole", techniques=["aria"], text="Aria")]))
    assert _words(xml) == ["aria"]


def test_svg_has_white_background_for_dark_theme():
    svg = render_svg(to_musicxml(_example([ExampleNote(pitches=["C5"], duration="whole")])))
    assert svg is not None and 'fill="white"' in svg[:2000]


def test_repeated_dynamic_is_not_rewritten():
    xml = to_musicxml(
        _example(
            [ExampleNote(pitches=["C5"], duration="half", dynamic="p"), ExampleNote(pitches=["D5"], duration="half", dynamic="p")],
            [ExampleNote(pitches=["E5"], duration="half", dynamic="p"), ExampleNote(pitches=["F5"], duration="half", dynamic="f")],
        )
    )
    assert [d[0].tag for d in ET.fromstring(xml).iter("dynamics")] == ["p", "f"]


def test_two_part_score_ir_with_voices_and_tuplet_renders_in_verovio():
    root = ET.fromstring(xml := to_musicxml(quartet()))
    assert [p.findtext("part-name") for p in root.iter("score-part")] == ["Violin", "Cello"]
    vln, vc = root.findall("part")
    assert vln.findtext(".//pitch/step") == "A" and vln.findtext(".//pitch/alter") == "0.5"
    assert vc.findtext("measure/attributes/clef/sign") == "F"
    triplet = [n for n in vc.iter("note") if n.find("time-modification") is not None]
    assert [n.findtext("type") for n in triplet] == ["quarter"] * 3
    assert {n.findtext("time-modification/actual-notes") for n in triplet} == {"3"}
    assert [t.get("type") for n in triplet for t in n.iter("tuplet")] == ["start", "stop"]
    assert {n.findtext("voice") for n in vc.iter("note")} == {"1"}
    svg = render_svg(xml)
    assert svg is not None and svg.count("<g") > 10


def test_notes_without_spelling_use_sharp_and_explicit_spelling_forces_letter():
    def note(i, mc, spelling=None):
        return Note(
            id=f"n{i}",
            start=F(i, 4),
            duration=F(1, 4),
            pitches=[Pitch(midicents=mc, spelling=spelling)],
            provenance=Provenance(source="algorithm"),
        )

    ir = ScoreIR(
        title="t",
        measures=[Measure(numerator=4, denominator=4)],
        parts=[Part(id="P1", instrument="Flute", voices=[Voice(id="v", events=[note(0, 6100), note(1, 6100, "D"), note(2, 6150), note(3, 6025)])])],
    )
    steps = [(n.findtext("pitch/step"), n.findtext("pitch/alter"), n.findtext("accidental")) for n in _notes_el(to_musicxml(ir))]
    assert steps == [("C", "1", "sharp"), ("D", "-1", "flat"), ("C", "1.5", "three-quarters-sharp"), ("C", "0.25", "natural-up")]


@pytest.mark.parametrize(
    "name,diatonic,chromatic,octaves,letter,octave",
    [
        ("clarinet", -1, -2, 0, "D", "4"),
        ("bass clarinet", -1, -2, -1, "D", "5"),
        ("horn", -4, -7, 0, "G", "4"),
        ("alto saxophone", -5, -9, 0, "A", "4"),
        ("double bass", 0, 0, -1, "C", "5"),
        ("piccolo", 0, 0, 1, "C", "3"),
        ("contrabass clarinet", -1, -2, -2, "D", "6"),
        ("trumpet", -1, -2, 0, "D", "4"),
        ("soprano saxophone", -1, -2, 0, "D", "4"),
        ("tenor saxophone", -1, -2, -1, "D", "5"),
        ("baritone saxophone", -5, -9, -1, "A", "5"),
        ("eb clarinet", 2, 3, 0, "A", "3"),
        ("english horn", -4, -7, 0, "G", "4"),
        ("contrabassoon", 0, 0, -1, "C", "5"),
        ("guitar", 0, 0, -1, "C", "5"),
        ("electric guitar", 0, 0, -1, "C", "5"),
        ("alto flute", -3, -5, 0, "F", "4"),
        ("bass flute", 0, 0, -1, "C", "5"),
        ("oboe d'amore", -2, -3, 0, "E", "4"),
        ("piccolo trumpet", 6, 10, 0, "D", "3"),
        ("cornet", -1, -2, 0, "D", "4"),
        ("flugelhorn", -1, -2, 0, "D", "4"),
        ("xylophone", 0, 0, 1, "C", "3"),
        ("glockenspiel", 0, 0, 2, "C", "2"),
        ("crotales", 0, 0, 2, "C", "2"),
        ("celesta", 0, 0, 1, "C", "3"),
    ],
)
def test_transposed_score_declares_interval_and_restores_sounding_pitch(name, diatonic, chromatic, octaves, letter, octave):
    ir = _example([ExampleNote(pitches=["C4+", "E4"], duration="whole")], instrument=name)
    before = ir.model_dump()
    xml = to_musicxml(ir, transposed=True)
    root = ET.fromstring(xml)
    tr = root.find(".//transpose")
    assert tr is not None
    assert tr.findtext("diatonic") == str(diatonic)
    assert tr.findtext("chromatic") == str(chromatic)
    assert tr.findtext("octave-change") == (str(octaves) if octaves else None)
    note = root.find(".//note")
    assert note is not None
    assert (note.findtext("pitch/step"), note.findtext("pitch/alter"), note.findtext("pitch/octave")) == (
        letter,
        "-0.5" if name == "oboe d'amore" else "0.5",
        octave,
    )
    for transposed in (False, True):
        loaded, warnings = from_musicxml(to_musicxml(ir, transposed=transposed).encode())
        assert not warnings
        loaded_note = loaded.parts[0].voices[0].events[0]
        assert isinstance(loaded_note, Note)
        assert [p.midicents for p in loaded_note.pitches] == [6050, 6400]
    assert ET.fromstring(to_musicxml(ir)).find(".//transpose") is None
    assert ir.model_dump() == before


@pytest.mark.parametrize(
    "name,pitch,expected",
    [
        ("Clarinet in Bb", "Bb3", ("C", None, "4")),
        ("Eb Clarinet", "C4-", ("A", "-0.5", "3")),
        ("Invented Instrument", "C4+", ("C", "0.5", "4")),
        ("Violin", "C4+", ("C", "0.5", "4")),
    ],
)
def test_transposition_preserves_enharmonic_spelling_and_accepts_free_name(name, pitch, expected):
    ir = _example([ExampleNote(pitches=[pitch], duration="whole")], instrument=name)
    note = _notes_el(to_musicxml(ir, transposed=True))[0]
    assert tuple(note.findtext(f"pitch/{field}") for field in ("step", "alter", "octave")) == expected

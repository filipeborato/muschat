import io
import zipfile
from fractions import Fraction as F

import pytest

from muschat.notation.musicxml import MAX_MUSICXML_BYTES, from_musicxml, to_musicxml
from muschat.score_ir import Measure, Note, Part, Pitch, Provenance, Rest, ScoreIR, Tuplet, Voice


def _xml(parts: str, part_list: str = '<score-part id="P1"><part-name>Violin</part-name></score-part>') -> bytes:
    return (
        '<?xml version="1.0"?><score-partwise version="4.0"><work><work-title>Teste</work-title></work>'
        f"<part-list>{part_list}</part-list>{parts}</score-partwise>"
    ).encode()


def _note_xml(step: str, octave: int, duration: int, note_type: str, alter: str | None = None, extra: str = "") -> str:
    acc = f"<alter>{alter}</alter>" if alter else ""
    return (
        f"<note><pitch><step>{step}</step>{acc}<octave>{octave}</octave></pitch>"
        f"<duration>{duration}</duration><voice>1</voice><type>{note_type}</type>{extra}</note>"
    )


ATTRIBUTES = (
    "<attributes><divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time>"
    "<clef><sign>F</sign><line>4</line></clef></attributes>"
)


def test_reads_part_with_notes_rest_accidental_dynamic_meter_and_tempo():
    m1 = (
        ATTRIBUTES
        + '<direction><direction-type><metronome><beat-unit>quarter</beat-unit>'
        '<per-minute>72</per-minute></metronome></direction-type><sound tempo="72"/></direction>'
        + "<direction><direction-type><dynamics><pp/></dynamics></direction-type></direction>"
        + _note_xml("C", 3, 2, "quarter")
        + _note_xml("F", 3, 3, "quarter", "1", "<dot/>")
        + "<note><rest/><duration>1</duration><voice>1</voice><type>eighth</type></note>"
    )
    m2 = _note_xml("A", 3, 6, "half", "-0.5", "<dot/>")
    ir, warnings = from_musicxml(_xml(f'<part id="P1"><measure number="1">{m1}</measure><measure number="2">{m2}</measure></part>'))
    assert warnings == []
    assert ir.title == "Teste"
    assert [(m.numerator, m.denominator, m.tempo) for m in ir.measures] == [(3, 4, 72), (3, 4, None)]
    part = ir.parts[0]
    assert (part.instrument, part.clef) == ("Violin", "bass")
    events = part.voices[0].events
    assert [(type(e), e.start, e.duration) for e in events] == [
        (Note, F(0), F(1, 4)),
        (Note, F(1, 4), F(3, 8)),
        (Rest, F(5, 8), F(1, 8)),
        (Note, F(3, 4), F(3, 4)),
    ]
    assert [p.midicents for e in events if isinstance(e, Note) for p in e.pitches] == [4800, 5400, 5650]
    assert [p.spelling for e in events if isinstance(e, Note) for p in e.pitches] == ["C", "F", "A"]
    assert isinstance(events[0], Note) and events[0].dynamic == "pp"
    assert isinstance(events[1], Note) and events[1].dynamic is None
    assert events[0].provenance.source == "composer"


def _content(ir: ScoreIR) -> list:
    return [
        (
            p.instrument,
            p.clef,
            [
                [
                    (
                        e.kind,
                        e.start,
                        e.duration,
                        [a.midicents for a in getattr(e, "pitches", [])],
                        e.dynamic,
                        getattr(e, "tied", False),
                    )
                    for e in v.events
                ]
                for v in p.voices
            ],
        )
        for p in ir.parts
    ]


def test_tie_across_barline_is_read_and_rewritten():
    tie_start = '<tie type="start"/>', '<notations><tied type="start"/></notations>'
    tie_stop = '<tie type="stop"/>', '<notations><tied type="stop"/></notations>'

    def with_tie(note: str, tie: tuple[str, str]) -> str:
        return note.replace("</duration>", "</duration>" + tie[0]).replace("</note>", tie[1] + "</note>")

    m1 = ATTRIBUTES + _note_xml("C", 3, 4, "half") + with_tie(_note_xml("D", 3, 2, "quarter"), tie_start)
    m2 = with_tie(_note_xml("D", 3, 2, "quarter"), tie_stop) + _note_xml("E", 3, 4, "half")
    ir, _ = from_musicxml(_xml(f'<part id="P1"><measure number="1">{m1}</measure><measure number="2">{m2}</measure></part>'))
    assert [getattr(e, "tied", False) for e in ir.parts[0].voices[0].events] == [False, True, False, False]
    xml = to_musicxml(ir)
    assert xml.count('<tie type="start"') == 1 and xml.count('<tie type="stop"') == 1
    assert xml.count('<tied type="start"') == 1 and xml.count('<tied type="stop"') == 1
    assert _content(from_musicxml(xml.encode())[0]) == _content(ir)


def test_roundtrip_preserves_parts_voices_chords_rests_and_dynamics():
    prov = Provenance(source="algorithm")

    def note(i, start, dur, *mc, dyn=None):
        return Note(
            id=i, start=start, duration=dur, pitches=[Pitch(midicents=m) for m in mc], dynamic=dyn, provenance=prov
        )

    violin = Part(
        id="a",
        instrument="Violin",
        voices=[
            Voice(
                id="a1",
                events=[
                    note("n1", F(0), F(1, 4), 7600, 8100, dyn="mf"),
                    Rest(id="n2", start=F(1, 4), duration=F(1, 4), provenance=prov),
                    note("n3", F(1, 2), F(1, 2), 6950),
                    note("n4", F(1), F(1), 7200, dyn="pp"),
                ],
            )
        ],
    )
    piano = Part(
        id="b",
        instrument="Piano",
        clef="bass",
        voices=[
            Voice(id="b1", events=[note("m1", F(0), F(1), 4800), note("m2", F(1), F(1, 2), 5000)]),
            Voice(id="b2", events=[note("m3", F(1, 2), F(1, 2), 3600, 4300), note("m4", F(3, 2), F(1, 2), 3800)]),
        ],
    )
    ir = ScoreIR(
        title="Roundtrip",
        measures=[Measure(numerator=4, denominator=4, tempo=60), Measure(numerator=4, denominator=4)],
        parts=[violin, piano],
    )
    loaded, warnings = from_musicxml(to_musicxml(ir).encode())
    assert warnings == []
    assert loaded.title == "Roundtrip"
    assert [(m.numerator, m.denominator, m.tempo) for m in loaded.measures] == [(4, 4, 60), (4, 4, None)]
    assert _content(loaded) == _content(ir)


def test_simple_tuplets_roundtrip_as_groups():
    prov = Provenance(source="algorithm")
    events: list[Note | Rest] = [
        Note(id=f"t{k}", start=F(k, 6), duration=F(1, 6), pitches=[Pitch(midicents=6000 + 200 * k)], provenance=prov)
        for k in range(3)
    ]
    events.append(Note(id="end", start=F(1, 2), duration=F(1, 2), pitches=[Pitch(midicents=6700)], provenance=prov))
    ir = ScoreIR(
        title="q",
        measures=[Measure(numerator=4, denominator=4)],
        parts=[
            Part(
                id="p",
                instrument="Flute",
                voices=[Voice(id="v", events=events, tuplets=[Tuplet(id="q1", numerator=3, denominator=2, events=["t0", "t1", "t2"])])],
            )
        ],
    )
    loaded, warnings = from_musicxml(to_musicxml(ir).encode())
    voice = loaded.parts[0].voices[0]
    assert warnings == [] and _content(loaded) == _content(ir)
    assert [(t.numerator, t.denominator, t.events) for t in voice.tuplets] == [
        (3, 2, [e.id for e in voice.events[:3]])
    ]


def test_unrepresented_elements_become_warnings_and_words_become_text():
    grace = "<note><grace/><pitch><step>B</step><octave>3</octave></pitch><voice>1</voice><type>eighth</type></note>"
    words = "<direction><direction-type><words>sul pont.</words></direction-type></direction>"
    rfz = "<direction><direction-type><dynamics><rfz/></dynamics></direction-type></direction>"
    two_staves = (
        '<attributes><divisions>2</divisions><staves>2</staves><clef number="1"><sign>G</sign>'
        '<line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes>'
    )
    m1 = two_staves + grace + words + rfz + _note_xml("C", 4, 8, "whole")
    ir, warnings = from_musicxml(_xml(f'<part id="P1"><measure number="1">{m1}</measure></part>'))
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note)
    assert (note.text, note.dynamic, note.duration) == ("sul pont.", None, F(1))
    assert warnings == [
        "Violin: 1 grace note(s) discarded",
        "Violin: dynamic 'rfz' discarded",
        "Violin: 2 staves merged into one, using the first clef",
    ]


@pytest.mark.parametrize(
    "data,snippet",
    [
        (b"this is not xml", "invalid MusicXML"),
        (b'<?xml version="1.0"?><score-timewise/>', "score-partwise"),
        (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><score-partwise/>', "entity declaration"),
        (b'<?xml version="1.0"?><score-partwise><part-list/></score-partwise>', "no parts"),
        (b"<" * (MAX_MUSICXML_BYTES + 1), "limit"),
    ],
)
def test_invalid_file_is_rejected_with_message(data, snippet):
    with pytest.raises(ValueError, match=snippet):
        from_musicxml(data)


def test_reads_compressed_mxl_via_container():
    xml = _xml('<part id="P1"><measure number="1">' + ATTRIBUTES + _note_xml("C", 4, 6, "half", extra="<dot/>") + "</measure></part>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="piece/score.xml"/></rootfiles></container>')
        z.writestr("piece/score.xml", xml)
    ir, _ = from_musicxml(buf.getvalue())
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note) and note.pitches[0].midicents == 6000


def test_pickup_measure_becomes_short_measure():
    m0 = ATTRIBUTES + _note_xml("G", 3, 2, "quarter")
    m1 = _note_xml("C", 4, 6, "half", extra="<dot/>")
    ir, _ = from_musicxml(_xml(f'<part id="P1"><measure number="0" implicit="yes">{m0}</measure><measure number="1">{m1}</measure></part>'))
    assert [m.length for m in ir.measures] == [F(1, 4), F(3, 4)]
    assert [e.start for e in ir.parts[0].voices[0].events] == [F(0), F(1, 4)]
    to_musicxml(ir)


def test_known_articulations_become_techniques_and_other_notations_warn():
    marks = '<notations><articulations><staccato/><strong-accent/></articulations><slur type="start"/><fermata/></notations>'
    m1 = ATTRIBUTES + _note_xml("C", 4, 6, "half", extra="<dot/><notehead>diamond</notehead>" + marks)
    ir, warnings = from_musicxml(_xml(f'<part id="P1"><measure number="1">{m1}</measure></part>'))
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note) and note.techniques == ["staccato", "marcato"]
    assert warnings == ["Violin: notation discarded: fermata (1), notehead diamond (1), slur (1)"]


def test_unpitched_percussion_uses_written_display_step_and_orphan_chord_is_rejected():
    drum = '<note><unpitched><display-step>E</display-step><display-octave>4</display-octave></unpitched><duration>8</duration><voice>1</voice><type>whole</type></note>'
    ir, _ = from_musicxml(_xml(f'<part id="P1"><measure number="1">{ATTRIBUTES}{drum}</measure></part>'))
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note) and note.pitches[0].midicents == 6400
    orphan = _note_xml("C", 4, 8, "whole", extra="").replace("<note>", "<note><chord/>")
    with pytest.raises(ValueError, match="chord"):
        from_musicxml(_xml(f'<part id="P1"><measure number="1">{ATTRIBUTES}{orphan}</measure></part>'))


def test_entity_declaration_in_any_encoding_is_rejected():
    xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]><score-partwise/>'
    with pytest.raises(ValueError, match="entity declaration"):
        from_musicxml(xml.encode("utf-16"))


def test_mxl_container_also_enforces_size_limit():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", b" " * (MAX_MUSICXML_BYTES + 1))
    with pytest.raises(ValueError, match="limit"):
        from_musicxml(buf.getvalue())


def test_transposing_part_returns_sounding_pitch_and_clef_change_warns():
    clarinet = (
        "<attributes><divisions>2</divisions><time><beats>4</beats><beat-type>4</beat-type></time>"
        '<clef><sign>G</sign><line>2</line></clef><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>'
    )
    new_clef = "<attributes><clef><sign>F</sign><line>4</line></clef></attributes>"
    tempo = '<sound tempo="90"/>'
    m1 = clarinet + tempo + _note_xml("D", 5, 8, "whole")
    m2 = new_clef + _note_xml("D", 5, 8, "whole")
    ir, warnings = from_musicxml(_xml(f'<part id="P1"><measure number="1">{m1}</measure><measure number="2">{m2}</measure></part>'))
    assert [e.pitches[0].midicents for e in ir.parts[0].voices[0].events if isinstance(e, Note)] == [7200, 7200]
    assert ir.measures[0].tempo == 90
    assert warnings == ["Violin: clef change after first measure discarded"]

"""Opening a MusicXML file into a Composition."""

from muschat.notation.musicxml import to_musicxml
from muschat.orchestrator import open_musicxml, reapply
from tests.test_composition import _quartet
from tests.test_engines import _om
from tests.test_musicxml_reader import ATTRIBUTES, _note_xml, _xml

GRACE = "<note><grace/><pitch><step>B</step><octave>3</octave></pitch><voice>1</voice><type>eighth</type></note>"
FILE_DATA = _xml(f'<part id="P1"><measure number="1">{ATTRIBUTES}{GRACE}{_note_xml("C", 4, 6, "half", extra="<dot/>")}</measure></part>')


def test_opened_file_becomes_composer_version_and_undo_restores_previous():
    c = _quartet()
    r = open_musicxml(c, FILE_DATA, "piece.musicxml")
    assert r.version is not None and c.index == 1 and c.current is not None and c.current.previous == 0
    events = [e for _, _, e in r.version.ir.events()]
    assert {(e.provenance.source, e.provenance.operation) for e in events} == {("composer", "op2")}
    assert "piece.musicxml" in r.summary and "1 grace note(s) discarded" in r.summary
    assert r.version.experiment["warnings"] == ["Violin: 1 grace note(s) discarded"]
    to_musicxml(r.version.ir)
    c.undo()
    assert c.index == 0


def test_opened_file_version_has_no_operation_to_reapply():
    c = _quartet()
    open_musicxml(c, FILE_DATA, "piece.musicxml")
    r = reapply(c, [])
    assert r.version is None and "file" in r.summary and c.index == 1


def test_invalid_file_is_refused_without_changing_composition():
    c = _quartet()
    r = open_musicxml(c, b"nothing", "x.musicxml")
    assert r.version is None and "invalid MusicXML" in r.summary and len(c.versions) == 1


def test_while_openmusic_task_is_pending_open_is_rejected(tmp_path):
    c, _, _ = _om(tmp_path)
    r = open_musicxml(c, FILE_DATA, "piece.musicxml")
    assert r.version is None and "Awaiting you" in r.summary and c.versions == []


def test_external_engine_opens_with_own_source_and_author():
    c = _quartet()
    r = open_musicxml(c, FILE_DATA, "notagen.xml", source="llm", author="NotaGen-X")
    assert r.version is not None
    assert {(e.provenance.source, e.provenance.author) for _, _, e in r.version.ir.events()} == {("llm", "NotaGen-X")}

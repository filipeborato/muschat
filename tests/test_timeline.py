"""Both notation backends consume the same exact tuplet/gap timeline."""

from fractions import Fraction as F
import xml.etree.ElementTree as ET
from muschat.notation.timeline import timeline
from muschat.notation.musicxml import to_musicxml, from_musicxml
from muschat.notation.lilypond import to_lilypond
from muschat.score_ir import Tuplet
from tests.test_lilypond import _solo, _note


def test_triplet_gap_is_written_at_the_accumulated_ratio_in_both_formats():
    score = _solo(
        [_note("a", F(0), F(1, 12), 4800), _note("b", F(1, 6), F(1, 12), 5000)],
        [Tuplet(id="T", numerator=3, denominator=2, events=["a", "b"])],
    )
    voice = score.parts[0].voices[0]
    items = list(timeline(voice, [F(0)], F(1)))
    assert (
        items[1].event is None
        and items[1].duration == F(1, 12)
        and items[1].ratio == F(3, 2)
    )
    xml = to_musicxml(score)
    root = ET.fromstring(xml)
    assert root.find(".//forward") is not None
    read, _ = from_musicxml(xml.encode())
    assert read.parts[0].voices[0].events[1].start == F(1, 6)
    lilypond = to_lilypond(score)["score"]
    assert lilypond.count(r"\tuplet 3/2") == 1 and "r8" in lilypond

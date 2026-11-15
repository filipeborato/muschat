"""Deterministic ScoreIR <-> MusicXML (score-partwise) builder and reader: multiple parts and voices, tuplets,
microtones, and grimoire techniques. Electronic events, control curves, and spectral data have no staff notation and
are omitted."""

import io
import math
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from fractions import Fraction
from itertools import accumulate
from typing import get_args
from xml.parsers import expat

from muschat.instruments import instrument as get_instrument, transposition
from muschat.notation.techniques import TECHNIQUES, Technique, VoiceDirections, get_technique
from muschat.score_ir import (
    PITCH_CLASS,
    Dynamic,
    Measure,
    Note,
    Part,
    Pitch,
    Provenance,
    Rest,
    ScoreIR,
    Tuplet,
    Voice,
    spell_pitch,
    ties_to,
)

__all__ = ["MAX_MUSICXML_BYTES", "from_musicxml", "spell_pitch", "to_musicxml"]

_NOTE_TYPES = {
    Fraction(1, 2**k): name
    for k, name in enumerate(["whole", "half", "quarter", "eighth", "16th", "32nd", "64th"])
}
_CLEFS = {
    "treble": ("G", "2"),
    "bass": ("F", "4"),
    "alto": ("C", "3"),
    "tenor": ("C", "4"),
    "percussion": ("percussion", None),
}
_ACCIDENTALS = {
    Fraction(n, 4): name
    for n, name in [
        (4, "sharp"),
        (-4, "flat"),
        (2, "quarter-sharp"),
        (-2, "quarter-flat"),
        (6, "three-quarters-sharp"),
        (-6, "three-quarters-flat"),
        (8, "double-sharp"),
        (-8, "flat-flat"),
        (1, "natural-up"),
        (-1, "natural-down"),
        (3, "sharp-down"),
        (-3, "flat-up"),
        (5, "sharp-up"),
        (-5, "flat-down"),
    ]
}


def to_musicxml(ir: ScoreIR, *, transposed: bool = False) -> str:
    """Full score in concert pitch by default; `transposed=True` writes and declares instrumental transpositions."""
    root = ET.Element("score-partwise", version="4.0")
    _sub(_sub(root, "work"), "work-title", ir.title)
    part_list = _sub(root, "part-list")
    for part in ir.parts:
        _sub(_sub(part_list, "score-part", id=part.id), "part-name", part.instrument)
    divisions = _divisions(ir)
    for k, part in enumerate(ir.parts):
        _write_part(
            _sub(root, "part", id=part.id),
            part,
            ir.measures,
            divisions,
            with_tempo=k == 0,
            transposed=transposed,
        )
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


# --- reading: MusicXML -> ScoreIR -------------------------------------------------------------------------------------

MAX_MUSICXML_BYTES = 5_000_000  # bytes; a long orchestral score fits comfortably
_READ_CLEF = {sign_line: clef for clef, sign_line in _CLEFS.items() if sign_line[1]}  # percussion defaults to "treble"
_DYNAMICS = set(get_args(Dynamic))
_MARKS = {
    (group, getattr(t, attr)): name
    for name, t in TECHNIQUES.items()
    for group, attr in (("articulations", "articulation"), ("technical", "technical"))
    if getattr(t, attr)
}


def from_musicxml(data: bytes, provenance: Provenance | None = None) -> tuple[ScoreIR, list[str]]:
    """score-partwise -> (ScoreIR, warnings). Anything ScoreIR does not represent is discarded and reported in warnings,
    never silently. Raises ValueError when the file cannot be used."""
    prov = provenance or Provenance(source="composer")
    root = _parse_root(data)
    try:
        return _read_score(root, prov)
    except (TypeError, KeyError, AttributeError, IndexError, ZeroDivisionError) as exc:
        raise ValueError(f"incomplete or non-standard MusicXML ({type(exc).__name__}: {exc})") from exc


def _parse_root(data: bytes) -> ET.Element:
    """Validate document before reading: size, .mxl (zip) via container, no entities, score-partwise."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                container = _safe_xml(_read_from_zip(z, "META-INF/container.xml"))
                data = _read_from_zip(z, container.find(".//rootfile").get("full-path"))
        except (zipfile.BadZipFile, KeyError, AttributeError, ET.ParseError) as exc:
            raise ValueError("invalid .mxl archive without a readable MusicXML inside") from exc
    if len(data) > MAX_MUSICXML_BYTES:
        raise ValueError(f"file exceeds the limit of {MAX_MUSICXML_BYTES} bytes")
    root = _safe_xml(data)
    if root.tag != "score-partwise":
        raise ValueError(f"expected score-partwise, got {root.tag} (export as partwise MusicXML)")
    if root.find("part") is None:
        raise ValueError("MusicXML contains no parts")
    return root


def _read_from_zip(z: zipfile.ZipFile, path: str) -> bytes:
    if z.getinfo(path).file_size > MAX_MUSICXML_BYTES:
        raise ValueError(f"uncompressed {path} exceeds the limit of {MAX_MUSICXML_BYTES} bytes")
    return z.read(path)


def _safe_xml(data: bytes) -> ET.Element:
    """Parse without entity declarations (prevents billion-laughs expansion) in any encoding."""

    def reject(*_):
        raise ValueError("DOCTYPE with entity declaration is not accepted; re-export without entities")

    parser = expat.ParserCreate()
    parser.EntityDeclHandler = reject
    try:
        parser.Parse(data, True)
        return ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError(f"invalid MusicXML ({exc})") from exc


def _read_score(root: ET.Element, prov: Provenance) -> tuple[ScoreIR, list[str]]:
    names = {p.get("id"): (p.findtext("part-name") or "").strip() for p in root.iter("score-part")}
    measures: list[Measure] = []
    parts, warnings = [], []
    for k, el in enumerate(root.findall("part"), 1):
        name = names.get(el.get("id")) or f"Part {k}"
        reader = _PartReader(f"P{k}", prov)
        for n, measure_el in enumerate(el.findall("measure")):
            reader.read_measure(measure_el, measures if k == 1 else None, n)
        warnings += [f"{name}: {w}" for w in reader.warnings()]
        parts.append(
            Part(
                id=f"P{k}",
                instrument=name,
                clef=reader.clef,
                voices=[
                    Voice(id=v, events=e, tuplets=reader.groups.get(v, []))
                    for v, e in reader.voices.items()
                ],
            )
        )
    title = (root.findtext("work/work-title") or root.findtext("movement-title") or "Untitled").strip()
    return ScoreIR(title=title, measures=measures, parts=parts), warnings


class _PartReader:
    def __init__(self, pid: str, prov: Provenance):
        self.pid, self.prov, self.clef = pid, prov, "treble"
        self.divisions, self.meter, self.offset = 1, (4, 4), Fraction(0)
        self.voices: dict[str, list] = {}
        self.groups: dict[str, list[Tuplet]] = {}
        self.open_tuplet: dict[str, Tuplet] = {}
        self.dynamic: str | None = None
        self.text: str | None = None
        self.grace_notes, self.staves, self.discarded_dynamics, self.discarded_notations = 0, 1, [], Counter()
        self.transposition, self.discarded_clefs = 0, False  # transposition: semitones from written to sounding

    def warnings(self) -> list[str]:
        return (
            ([f"{self.grace_notes} grace note(s) discarded"] if self.grace_notes else [])
            + [f"dynamic '{d}' discarded" for d in self.discarded_dynamics]
            + (
                [
                    f"notation discarded: {', '.join(f'{k} ({n})' for k, n in sorted(self.discarded_notations.items()))}"
                ]
                if self.discarded_notations
                else []
            )
            + ([f"{self.staves} staves merged into one, using the first clef"] if self.staves > 1 else [])
            + (["clef change after first measure discarded"] if self.discarded_clefs else [])
        )

    def read_measure(self, measure_el: ET.Element, measures: list[Measure] | None, n: int) -> None:
        tempo, cursor, max_cursor = None, Fraction(0), Fraction(0)
        for el in measure_el:
            if el.tag == "attributes":
                self.divisions = int(el.findtext("divisions") or self.divisions)
                if (time_el := el.find("time")) is not None:
                    self.meter = (int(time_el.findtext("beats")), int(time_el.findtext("beat-type")))
                self.staves = max(self.staves, int(el.findtext("staves") or 1))
                if (transpose_el := el.find("transpose")) is not None:
                    self.transposition = (
                        int(transpose_el.findtext("chromatic") or 0)
                        + 12 * int(transpose_el.findtext("octave-change") or 0)
                    )
                if (clef_el := el.find("clef")) is not None:
                    if n == 0:
                        self.clef = _READ_CLEF.get((clef_el.findtext("sign"), clef_el.findtext("line")), "treble")
                    else:
                        self.discarded_clefs = True
            elif el.tag == "sound" and el.get("tempo"):
                tempo = round(float(el.get("tempo")))
            elif el.tag == "direction":
                if (sound_el := el.find("sound")) is not None and sound_el.get("tempo"):
                    tempo = round(float(sound_el.get("tempo")))
                for d in el.iterfind("direction-type/dynamics/*"):
                    if d.tag in _DYNAMICS:
                        self.dynamic = d.tag
                    elif d.tag not in self.discarded_dynamics:
                        self.discarded_dynamics.append(d.tag)
                words = " ".join(w.text.strip() for w in el.iterfind("direction-type/words") if w.text)
                if words:
                    self.text = f"{self.text}; {words}" if self.text else words
            elif el.tag in ("backup", "forward"):
                step = self._duration(el)
                cursor += step if el.tag == "forward" else -step
                max_cursor = max(max_cursor, cursor)
            elif el.tag == "note":
                if el.find("grace") is not None:
                    self.grace_notes += 1
                    continue
                duration = self._duration(el)
                voice_id = f"{self.pid}V{el.findtext('voice') or '1'}"
                events = self.voices.setdefault(voice_id, [])
                if el.find("chord") is not None:
                    if not events or not isinstance(events[-1], Note):
                        raise ValueError(f"{voice_id}: chord note without preceding principal note")
                    events[-1].pitches.append(self._pitch(el))
                    continue
                common = dict(
                    id=f"{voice_id}E{len(events) + 1}",
                    start=self.offset + cursor,
                    duration=duration,
                    provenance=self.prov,
                    dynamic=self.dynamic,
                    text=self.text,
                )
                self.dynamic = self.text = None
                self._tuplet(el, voice_id, common["id"])
                if el.find("rest") is not None:
                    events.append(Rest(**common))
                else:
                    events.append(
                        Note(
                            **common,
                            pitches=[self._pitch(el)],
                            tied=el.find("tie[@type='start']") is not None,
                            techniques=self._marks(el),
                        )
                    )
                cursor += duration
                max_cursor = max(max_cursor, cursor)
        length = max_cursor if measure_el.get("implicit") == "yes" and max_cursor else Fraction(*self.meter)
        if measures is not None:
            measures.append(
                Measure(
                    numerator=self.meter[0],
                    denominator=self.meter[1],
                    tempo=tempo,
                    duration=None if length == Fraction(*self.meter) else length,
                )
            )
        self.offset += length

    def _marks(self, el: ET.Element) -> list[str]:
        techniques = []
        if (notehead := el.findtext("notehead")) and notehead != "normal":
            self.discarded_notations[f"notehead {notehead}"] += 1
        for notation in el.iterfind("notations/*"):
            if notation.tag in ("tied", "tuplet"):
                continue
            group = list(notation) if notation.tag in ("articulations", "technical") else [notation]
            for mark in group:
                name = _MARKS.get((notation.tag, mark.tag))
                if name:
                    techniques.append(name)
                else:
                    self.discarded_notations[mark.tag] += 1
        return techniques

    def _tuplet(self, el: ET.Element, voice_id: str, eid: str) -> None:
        tuplets = {t.get("type") for t in el.iterfind("notations/tuplet")}
        open_group = self.open_tuplet.get(voice_id)
        if "start" in tuplets:
            if open_group:
                raise ValueError(f"{voice_id}: nested tuplets are not yet imported")
            mod = el.find("time-modification")
            if mod is None:
                raise ValueError(f"{voice_id}: tuplet without time-modification")
            open_group = self.open_tuplet[voice_id] = Tuplet(
                id=f"{voice_id}Q{len(self.groups.setdefault(voice_id, [])) + 1}",
                numerator=int(mod.findtext("actual-notes")),
                denominator=int(mod.findtext("normal-notes")),
                events=[eid],
            )
        elif open_group:
            open_group.events.append(eid)
        elif el.find("time-modification") is not None:
            raise ValueError(f"{voice_id}: tuplet note without group start marker")
        if open_group and "stop" in tuplets:
            self.groups.setdefault(voice_id, []).append(self.open_tuplet.pop(voice_id))

    def _pitch(self, el: ET.Element) -> Pitch:
        if (pitch_el := el.find("pitch")) is not None:
            pitch = _read_pitch(pitch_el.findtext("step"), pitch_el.findtext("octave"), pitch_el.findtext("alter"))
            if self.transposition:
                pitch = Pitch(midicents=pitch.midicents + 100 * self.transposition)
            return pitch
        unpitched = el.find("unpitched")
        return _read_pitch(unpitched.findtext("display-step"), unpitched.findtext("display-octave"), None)

    def _duration(self, el: ET.Element) -> Fraction:
        return Fraction(int(el.findtext("duration")), 4 * self.divisions)


def _read_pitch(step: str, octave: str, alter: str | None) -> Pitch:
    return Pitch(
        midicents=round(((int(octave) + 1) * 12 + PITCH_CLASS[step] + Fraction(alter or "0")) * 100),
        spelling=step,
    )


def _divisions(ir: ScoreIR) -> int:
    times = [m.length for m in ir.measures]
    times += [t for _, _, e in ir.events() if isinstance(e, (Note, Rest)) for t in (e.start, e.duration)]
    return math.lcm(8, *((t * 4).denominator for t in times))


def _write_part(
    el: ET.Element,
    part: Part,
    measures: list[Measure],
    divisions: int,
    *,
    with_tempo: bool,
    transposed: bool,
) -> None:
    interval = transposition(part.instrument) if transposed else (0, 0, 0)
    voices = [v for v in part.voices if any(isinstance(e, (Note, Rest)) for e in v.events)]
    writers = [_VoiceWriter(v, divisions, interval) for v in voices]
    offsets = list(accumulate((m.length for m in measures), initial=Fraction(0)))
    for n, measure in enumerate(measures, 1):
        measure_el = _sub(el, "measure", number=str(n))
        _attributes(measure_el, measure, measures[n - 2] if n > 1 else None, part, divisions, interval)
        if with_tempo and measure.tempo:
            _tempo(measure_el, measure.tempo)
        accidentals: dict[tuple[str, int], Fraction] = {}
        for k, writer in enumerate(writers, 1):
            writer.write(measure_el, k, offsets[n - 1], offsets[n], accidentals, polyphonic=len(writers) > 1)
    for writer in writers:
        if writer.pending():
            raise ValueError(f"voice {writer.voice.id}: events after the last measure ({writer.pending()})")


class _VoiceWriter:
    """Write one voice measure-by-measure, maintaining active technique, dynamic, and glissando state."""

    def __init__(self, voice: Voice, divisions: int, interval: tuple[int, int, int]):
        self.interval = interval
        self.voice, self.divisions, self.directions, self.glissando_open = voice, divisions, VoiceDirections(), False
        self.tie_open = False
        self.events = [e for e in voice.events if isinstance(e, (Note, Rest))]
        self.i = 0
        groups = {t.id: t for t in voice.tuplets}
        self.chain: dict[str, list[str]] = {}
        for t in voice.tuplets:
            for event_id in t.events:
                chain: list[str] = []
                g: Tuplet | None = t
                while g is not None:
                    chain.insert(0, g.id)
                    g = groups.get(g.parent) if g.parent else None
                self.chain[event_id] = chain
        members: dict[str, list[str]] = {}
        for e in self.events:
            for gid in self.chain.get(e.id, []):
                members.setdefault(gid, []).append(e.id)
        self.groups, self.members = groups, members

    def pending(self) -> list[str]:
        return [e.id for e in self.events[self.i :]]

    def write(
        self,
        measure_el: ET.Element,
        number: int,
        start: Fraction,
        end: Fraction,
        accidentals: dict[tuple[str, int], Fraction],
        *,
        polyphonic: bool,
    ) -> None:
        cursor = start
        if number > 1 and end > start:
            _sub(_sub(measure_el, "backup"), "duration", str(self._div(end - start)))
        while self.i < len(self.events) and self.events[self.i].start < end:
            e = self.events[self.i]
            if e.end > end:
                raise ValueError(f"event {e.id} crosses the barline; split it with a tie")
            if e.start > cursor:
                _advance(measure_el, "forward", self._div(e.start - cursor), number)
            next_ev = self.events[self.i + 1] if self.i + 1 < len(self.events) else None
            self._write_event(measure_el, e, next_ev, number, accidentals)
            cursor = e.end
            self.i += 1
        if polyphonic and cursor < end:
            _advance(measure_el, "forward", self._div(end - cursor), number)

    def _div(self, t: Fraction) -> int:
        return int(t * 4 * self.divisions)

    def _write_event(
        self,
        measure_el: ET.Element,
        e: Note | Rest,
        next_ev: Note | Rest | None,
        number: int,
        accidentals: dict[tuple[str, int], Fraction],
    ) -> None:
        techniques = [get_technique(t) for t in e.techniques] if isinstance(e, Note) else []
        italics, free_texts, dynamic = self.directions.next_event(techniques, isinstance(e, Note), e.text, e.dynamic)
        for text in italics:
            _text_direction(measure_el, text, italic=True)
        for text in free_texts:
            _text_direction(measure_el, text)
        if dynamic:
            _dynamic(measure_el, dynamic)
        opens_gliss = isinstance(e, Note) and e.glissando and isinstance(next_ev, Note)
        chain = self.chain.get(e.id, [])
        ratio = math.prod(
            (Fraction(self.groups[g].numerator, self.groups[g].denominator) for g in chain),
            start=Fraction(1),
        )
        tuplets = [(n, "start") for n, g in enumerate(chain, 1) if self.members[g][0] == e.id]
        tuplets += [(n, "stop") for n, g in reversed(list(enumerate(chain, 1))) if self.members[g][-1] == e.id]
        ties_to_next = isinstance(e, Note) and ties_to(e, next_ev)
        _write_note(
            measure_el,
            e,
            techniques,
            interval=self.interval,
            number=number,
            duration=self._div(e.duration),
            ratio=ratio,
            tuplets=tuplets,
            closes_glissando=self.glissando_open and isinstance(e, Note),
            opens_glissando=opens_gliss,
            accidentals=accidentals,
            ties=[t for t, yes in (("stop", self.tie_open), ("start", ties_to_next)) if yes],
        )
        self.glissando_open, self.tie_open = opens_gliss, ties_to_next


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def _advance(measure_el: ET.Element, tag: str, duration: int, voice: int) -> None:
    el = _sub(measure_el, tag)
    _sub(el, "duration", str(duration))
    _sub(el, "voice", str(voice))


def _attributes(
    measure_el: ET.Element,
    m: Measure,
    previous: Measure | None,
    part: Part,
    divisions: int,
    interval: tuple[int, int, int],
) -> None:
    new_meter = previous is None or (m.numerator, m.denominator) != (previous.numerator, previous.denominator)
    if previous is not None and not new_meter:
        return
    attrs = _sub(measure_el, "attributes")
    if previous is None:
        _sub(attrs, "divisions", str(divisions))
        _sub(_sub(attrs, "key"), "fifths", "0")
    time_el = _sub(attrs, "time")
    _sub(time_el, "beats", str(m.numerator))
    _sub(time_el, "beat-type", str(m.denominator))
    if previous is None:
        written_clef = get_instrument(part.instrument).written_clef if any(interval) else None
        sign, line = _CLEFS[written_clef or part.clef]
        clef_el = _sub(attrs, "clef")
        _sub(clef_el, "sign", sign)
        if line:
            _sub(clef_el, "line", line)
        if any(interval):
            transpose_el = _sub(attrs, "transpose")
            _sub(transpose_el, "diatonic", str(interval[0]))
            _sub(transpose_el, "chromatic", str(interval[1]))
            if interval[2]:
                _sub(transpose_el, "octave-change", str(interval[2]))


def _tempo(measure_el: ET.Element, tempo: int) -> None:
    direction = _sub(measure_el, "direction", placement="above")
    metronome = _sub(_sub(direction, "direction-type"), "metronome")
    _sub(metronome, "beat-unit", "quarter")
    _sub(metronome, "per-minute", str(tempo))
    _sub(direction, "sound", tempo=str(tempo))


def _text_direction(measure_el: ET.Element, text: str, italic: bool = False) -> None:
    dtype = _sub(_sub(measure_el, "direction", placement="above"), "direction-type")
    _sub(dtype, "words", text, **({"font_style": "italic"} if italic else {}))


def _dynamic(measure_el: ET.Element, dynamic: str) -> None:
    dtype = _sub(_sub(measure_el, "direction", placement="below"), "direction-type")
    _sub(_sub(dtype, "dynamics"), dynamic)


def _note_type(notated: Fraction, event_id: str) -> tuple[str, bool]:
    for base, name in _NOTE_TYPES.items():
        if notated in (base, base * Fraction(3, 2)):
            return name, notated != base
    raise ValueError(f"event {event_id}: notated duration {notated} is neither a simple nor a dotted note value")


def _write_note(
    measure_el: ET.Element,
    e: Note | Rest,
    techniques: list[Technique],
    *,
    interval: tuple[int, int, int],
    number: int,
    duration: int,
    ratio: Fraction,
    tuplets: list[tuple[int, str]],
    closes_glissando: bool,
    opens_glissando: bool,
    accidentals: dict[tuple[str, int], Fraction],
    ties: list[str],
) -> None:
    ntype, dotted = _note_type(e.duration * ratio, e.id)
    pitches = [spell_pitch(p, interval) for p in e.pitches] if isinstance(e, Note) else [None]
    notehead = next((t.notehead for t in techniques if t.notehead), None)
    for i, pitch_tuple in enumerate(pitches):
        el = _sub(measure_el, "note")
        if i > 0:
            _sub(el, "chord")
        if pitch_tuple is None:
            _sub(el, "rest")
        else:
            step, alter, octave = pitch_tuple
            pitch_el = _sub(el, "pitch")
            _sub(pitch_el, "step", step)
            if alter:
                _sub(pitch_el, "alter", f"{float(alter):g}")
            _sub(pitch_el, "octave", str(octave))
        _sub(el, "duration", str(duration))
        for tie_type in ties:
            _sub(el, "tie", type=tie_type)
        _sub(el, "voice", str(number))
        _sub(el, "type", ntype)
        if dotted:
            _sub(el, "dot")
        if pitch_tuple is not None:
            step, alter, octave = pitch_tuple
            if alter:
                _sub(el, "accidental", _ACCIDENTALS.get(Fraction(round(alter * 4), 4), "natural"))
            elif accidentals.get((step, octave)):
                _sub(el, "accidental", "natural")
            accidentals[(step, octave)] = alter
        if ratio != 1:
            mod = _sub(el, "time-modification")
            _sub(mod, "actual-notes", str(ratio.numerator))
            _sub(mod, "normal-notes", str(ratio.denominator))
        if pitch_tuple is not None and notehead:
            _sub(el, "notehead", notehead)
        _notations(
            el,
            techniques if i == 0 else [],
            tuplets if i == 0 else [],
            i == 0 and closes_glissando,
            i == 0 and opens_glissando,
            ties,
        )


def _notations(
    el: ET.Element,
    techniques: list[Technique],
    tuplets: list[tuple[int, str]],
    closes_glissando: bool,
    opens_glissando: bool,
    ties: list[str],
) -> None:
    children: list[tuple[str, dict[str, str]]] = [("tied", {"type": t}) for t in ties]
    children += [("tuplet", {"type": t, "number": str(n)}) for n, t in tuplets]
    if closes_glissando:
        children.append(("glissando", {"type": "stop", "number": "1"}))
    if opens_glissando:
        children.append(("glissando", {"type": "start", "number": "1", "line-type": "solid"}))
    if not children and not any(t.technical or t.tremolo or t.articulation for t in techniques):
        return
    notations = _sub(el, "notations")
    for tag, attrs in children:
        ET.SubElement(notations, tag, attrs)
    if any(t.tremolo for t in techniques):
        _sub(_sub(notations, "ornaments"), "tremolo", "3", type="single")
    articulations = [t.articulation for t in techniques if t.articulation]
    if articulations:
        block = _sub(notations, "articulations")
        for tag in articulations:
            _sub(block, tag)
    technicals = [t.technical for t in techniques if t.technical]
    if technicals:
        tech_block = _sub(notations, "technical")
        for tag in technicals:
            _sub(tech_block, tag)

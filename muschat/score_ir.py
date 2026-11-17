"""ScoreIR: intermediate musical representation independent of Abjad, OpenMusic, and Verovio.

Time is measured in whole notes as exact Fractions (1 = whole note, 1/4 = quarter note).
Pitches are stored in midicents (6900 = A4). Immutable by convention: operations return new instances.
"""

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator
from fractions import Fraction
from itertools import accumulate
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, Field, model_validator

Dynamic = Literal["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
Clef = Literal["treble", "bass", "alto", "tenor", "percussion"]
PitchLetter = Literal["C", "D", "E", "F", "G", "A", "B"]
PITCH_CLASS: dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
Source = Literal["llm", "algorithm", "openmusic", "corpus", "composer", "external"]
Id = Annotated[str, Field(pattern=r"^[A-Za-z_][\w.-]*$")]
Position = Annotated[Fraction, Field(ge=0)]
Duration = Annotated[Fraction, Field(gt=0)]
Midicents = Annotated[int, Field(ge=0, le=12700)]

_CLEF_ALIASES: dict[str, Clef] = {
    "sol": "treble",
    "treble": "treble",
    "fa": "bass",
    "bass": "bass",
    "do3": "alto",
    "alto": "alto",
    "do4": "tenor",
    "tenor": "tenor",
    "percussao": "percussion",
    "percussion": "percussion",
}
_SOURCE_ALIASES: dict[str, Source] = {
    "llm": "llm",
    "algoritmo": "algorithm",
    "algorithm": "algorithm",
    "openmusic": "openmusic",
    "acervo": "corpus",
    "corpus": "corpus",
    "compositor": "composer",
    "composer": "composer",
    "externo": "external",
    "external": "external",
}
TECHNIQUE_ALIASES: dict[str, str] = {
    "harmonico": "harmonic",
    "harmonic": "harmonic",
    "armonico": "harmonic",
    "arm.": "harmonic",
    "arm. naturale": "harmonic",
    "flatterzunge": "frullato",
    "flz.": "frullato",
    "flutter tongue": "frullato",
    "snap pizzicato": "pizz. bartok",
    "bartok pizzicato": "pizz. bartok",
    "c.l. batt.": "col legno battuto",
    "c.l.b.": "col legno battuto",
    "c.l. tratto": "col legno tratto",
    "c.l.t.": "col legno tratto",
    "s.p.e.": "sul ponticello estremo",
    "sul pont.": "sul ponticello",
    "sul pont. estr.": "sul ponticello estremo",
    "s.t.": "sul tasto",
    "multifonico": "multiphonic",
    "multiphonic": "multiphonic",
    "suono eolico": "air sound",
    "aria": "air sound",
    "airtone": "air sound",
    "air tone": "air sound",
    "som de ar": "air sound",
    "chiavi": "key click",
    "stacc.": "staccato",
    "accento": "accent",
    "acento": "accent",
    "accent": "accent",
    "ten.": "tenuto",
    "jete": "jete",
    "con sord.": "con sordino",
    "senza sord.": "senza sordino",
    "stopped": "gestopft",
    "bouche": "gestopft",
    "aberto": "open",
    "harmon": "harmon mute",
    "surdina harmon": "harmon mute",
    "surdina cup": "cup mute",
    "surdina plunger": "plunger mute",
    "meia valvula": "half valve",
    "half valve": "half valve",
    "so bocal": "mouthpiece only",
    "l.v.": "laissez vibrer",
    "p.d.l.t.": "pres de la table",
    "bisb.": "bisbigliando",
    "sprechgesang": "sprechstimme",
    "b.ch.": "bocca chiusa",
    "sing and play": "canta e suona",
    "cantar e tocar": "canta e suona",
    "sussurrato": "whispered",
    "sussurrado": "whispered",
    "ingressivo": "ingressive",
    "estalo de lingua": "tongue click",
    "tongue click": "tongue click",
    "rulo": "roll",
    "rufo": "roll",
    "roll": "roll",
    "pizzicato de lingua": "tongue pizzicato",
    "tongue pizzicato": "tongue pizzicato",
    "slap tongue": "slap",
    "pizzicato de mao esquerda": "left-hand pizzicato",
    "left-hand pizzicato": "left-hand pizzicato",
    "percussao no corpo": "body percussion",
    "com arco": "bowed",
    "bowed": "bowed",
    "baqueta dura": "hard mallet",
    "baqueta macia": "soft mallet",
    "vassourinha": "brushes",
    "pizzicato nas cordas": "string pizzicato",
    "cordas abafadas": "muted strings",
    "harmonico no piano": "piano harmonic",
    "glissando nas cordas": "string glissando",
    "piano preparado": "prepared piano",
    "xilofonico": "xylophonic",
    "com a unha": "fingernail",
    "glissando de pedal": "pedal glissando",
}


def normalize_clef(value: str) -> Clef:
    return _CLEF_ALIASES.get(str(value).strip().lower(), value)  # type: ignore[return-value]


def normalize_source(value: str) -> Source:
    return _SOURCE_ALIASES.get(str(value).strip().lower(), value)  # type: ignore[return-value]


def normalize_technique(name: str) -> str:
    key = unicodedata.normalize("NFKD", str(name).strip().lower()).encode("ascii", "ignore").decode()
    return TECHNIQUE_ALIASES.get(key, key)


class Provenance(BaseModel):
    source: Source
    author: str | None = None
    operation: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: object) -> object:
        if isinstance(data, dict) and "source" in data and isinstance(data["source"], str):
            return {**data, "source": normalize_source(data["source"])}
        return data


class Pitch(BaseModel):
    midicents: Midicents
    spelling: PitchLetter | None = Field(
        default=None,
        description="Preferred pitch letter for notation (enharmonic / quarter-tone spelling).",
    )


class _Event(BaseModel):
    id: Id
    start: Position
    duration: Duration
    provenance: Provenance

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


class Note(_Event):
    """Single note or chord (multiple pitches)."""

    kind: Literal["note"] = "note"
    pitches: list[Pitch] = Field(min_length=1)
    techniques: list[str] = Field(default_factory=list, description="Canonical technique names.")
    dynamic: Dynamic | None = None
    text: str | None = None
    glissando: bool = Field(default=False, description="Glissando to the next note in the voice.")
    tied: bool = Field(
        default=False,
        description="Tie to the next note in the voice (same pitches, starting where this note ends).",
    )

    @model_validator(mode="after")
    def _normalize_techniques(self) -> "Note":
        object.__setattr__(self, "techniques", [normalize_technique(t) for t in self.techniques])
        return self


def ties_to(note: Note, next_event: object) -> bool:
    """Return True only when `note` is tied to an adjacent `Note` with identical pitches."""
    return (
        note.tied
        and isinstance(next_event, Note)
        and next_event.start == note.end
        and [p.midicents for p in next_event.pitches] == [p.midicents for p in note.pitches]
    )


LONG_NOTE = Fraction(1, 2)  # half note closes a gesture unless tied


def gestures(events: list) -> list[list[Note]]:
    """Split a voice's events into gestures (phrases of consecutive notes separated by rests or untied long notes)."""
    output: list[list[Note]] = [[]]
    for event in events:
        if isinstance(event, Note):
            output[-1].append(event)
            if event.duration >= LONG_NOTE and not event.tied:
                output.append([])
        elif output[-1]:
            output.append([])
    return [g for g in output if g]


class Rest(_Event):
    kind: Literal["rest"] = "rest"
    dynamic: Dynamic | None = None
    text: str | None = None


class ElectronicEvent(_Event):
    """Electronic trigger or cue."""

    kind: Literal["electronic"] = "electronic"
    duration: Position  # point triggers have zero duration
    description: str


class Curve(_Event):
    """Continuous parameter control curve; points are (relative offset from start, value)."""

    kind: Literal["curve"] = "curve"
    parameter: str
    points: list[tuple[Position, float]] = Field(min_length=1)


class Partial(BaseModel):
    frequency: float = Field(gt=0)
    amplitude: float = Field(ge=0)
    phase: float = 0.0


class SpectralFrame(_Event):
    """Spectral analysis frame (never converted directly into fake notes)."""

    kind: Literal["spectral"] = "spectral"
    partials: list[Partial] = Field(min_length=1)


Event = Annotated[Note | Rest | ElectronicEvent | Curve | SpectralFrame, Field(discriminator="kind")]


class Tuplet(BaseModel):
    """`numerator` in the time of `denominator` (e.g. 3:2 triplet). Nestable via `parent`."""

    id: Id
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)
    events: list[Id] = Field(min_length=1)
    parent: Id | None = None


class Voice(BaseModel):
    id: Id
    events: list[Event] = Field(default_factory=list)
    tuplets: list[Tuplet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_coherence(self) -> "Voice":
        for a, b in zip(self.events, self.events[1:]):
            if b.start < a.end:
                raise ValueError(f"events {a.id} and {b.id} overlap or are out of order in voice {self.id}")
        event_ids = {e.id for e in self.events}
        parents = {t.id: t.parent for t in self.tuplets}
        for tuplet in self.tuplets:
            if not set(tuplet.events) <= event_ids or (tuplet.parent is not None and tuplet.parent not in parents):
                raise ValueError(f"tuplet {tuplet.id} references unknown event or parent group in voice {self.id}")
            seen, group = {tuplet.id}, tuplet.parent
            while group is not None:
                if group in seen:
                    raise ValueError(f"tuplet cycle in voice {self.id}: {tuplet.id}")
                seen.add(group)
                group = parents[group]
        members = [event_id for t in self.tuplets for event_id in t.events]
        if len(members) != len(set(members)):
            raise ValueError(f"event belongs to multiple tuplet groups in voice {self.id} (use `parent` to nest)")
        return self


class Part(BaseModel):
    id: Id
    instrument: str
    clef: Clef = "treble"
    range: tuple[Midicents, Midicents] | None = Field(default=None, description="(low, high) in midicents.")
    voices: list[Voice] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_clef(cls, data: object) -> object:
        if isinstance(data, dict) and "clef" in data and isinstance(data["clef"], str):
            return {**data, "clef": normalize_clef(data["clef"])}
        return data

    @model_validator(mode="after")
    def _validate_range_order(self) -> "Part":
        if self.range and self.range[0] > self.range[1]:
            raise ValueError(f"inverted range in part {self.id}: {self.range}")
        return self


class Measure(BaseModel):
    materialized: bool = True
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)
    tempo: int | None = Field(default=None, gt=0, description="Quarter notes per minute starting at this measure.")
    # shortcut: incomplete/overflow measure inherited from LLM example; remove once metric validation is strict
    duration: Fraction | None = Field(default=None, ge=0)

    @property
    def length(self) -> Fraction:
        return self.duration if self.duration is not None else Fraction(self.numerator, self.denominator)


class ScoreIR(BaseModel):
    title: str
    composer: str | None = None
    measures: list[Measure] = Field(min_length=1)
    parts: list[Part] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        if isinstance(data, dict) and ("titulo" in data or "compassos" in data or "partes" in data):
            return migrate_legacy_score_ir_dict(data)
        return data

    def measure_starts(self) -> list[Fraction]:
        """Start position of each measure in whole notes."""
        return list(accumulate((m.length for m in self.measures[:-1]), initial=Fraction(0)))

    def events(self) -> Iterator[tuple[Part, Voice, Event]]:
        for part in self.parts:
            for voice in part.voices:
                for event in voice.events:
                    yield part, voice, event

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "ScoreIR":
        ids = [p.id for p in self.parts] + [v.id for p in self.parts for v in p.voices]
        ids += [e.id for _, _, e in self.events()]
        duplicates = [i for i, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate ids: {duplicates}")
        return self


_DEFAULT_LETTERS = "C C D D E F F G G A A B".split()


def spell_pitch(pitch: Pitch, interval: tuple[int, int, int] = (0, 0, 0)) -> tuple[str, Fraction, int]:
    """Compatibility entry point for shared notation spelling."""
    from muschat.notation.spelling import spell_pitch as spell
    return spell(pitch, interval)


_LEGACY_EVENT_KIND = {
    "nota": "note",
    "pausa": "rest",
    "eletronico": "electronic",
    "curva": "curve",
    "espectral": "spectral",
}


def migrate_legacy_score_ir_dict(raw: dict) -> dict:
    """Convert a legacy Portuguese `MusicIR` dict into a canonical English `ScoreIR` dict."""
    if "measures" in raw and "parts" in raw:
        return raw

    def migrate_pitch(p: dict) -> dict:
        return {
            "midicents": p["midicents"],
            "spelling": p.get("spelling", p.get("grafia")),
        }

    def migrate_provenance(prov: dict) -> dict:
        return {
            "source": normalize_source(prov.get("source", prov.get("origem", "composer"))),
            "author": prov.get("author", prov.get("autor")),
            "operation": prov.get("operation", prov.get("operacao")),
        }

    def migrate_event(ev: dict) -> dict:
        kind = _LEGACY_EVENT_KIND.get(ev.get("kind", ev.get("tipo", "note")), ev.get("kind", ev.get("tipo", "note")))
        base = {
            "id": ev["id"],
            "start": ev.get("start", ev.get("inicio")),
            "duration": ev.get("duration", ev.get("duracao")),
            "provenance": migrate_provenance(ev.get("provenance", ev.get("proveniencia", {}))),
            "kind": kind,
        }
        if kind == "note":
            return {
                **base,
                "pitches": [migrate_pitch(p) for p in ev.get("pitches", ev.get("alturas", []))],
                "techniques": [normalize_technique(t) for t in ev.get("techniques", ev.get("tecnicas", []))],
                "dynamic": ev.get("dynamic", ev.get("dinamica")),
                "text": ev.get("text", ev.get("texto")),
                "glissando": ev.get("glissando", False),
                "tied": ev.get("tied", ev.get("ligada", False)),
            }
        if kind == "rest":
            return {
                **base,
                "dynamic": ev.get("dynamic", ev.get("dinamica")),
                "text": ev.get("text", ev.get("texto")),
            }
        if kind == "electronic":
            return {
                **base,
                "description": ev.get("description", ev.get("descricao", "")),
            }
        if kind == "curve":
            return {
                **base,
                "parameter": ev.get("parameter", ev.get("parametro", "")),
                "points": ev.get("points", ev.get("pontos", [])),
            }
        if kind == "spectral":
            return {
                **base,
                "partials": [
                    {
                        "frequency": pt.get("frequency", pt.get("frequencia")),
                        "amplitude": pt.get("amplitude"),
                        "phase": pt.get("phase", pt.get("fase", 0.0)),
                    }
                    for pt in ev.get("partials", ev.get("parciais", []))
                ],
            }
        return base

    def migrate_tuplet(t: dict) -> dict:
        return {
            "id": t["id"],
            "numerator": t.get("numerator", t.get("numerador")),
            "denominator": t.get("denominator", t.get("denominador")),
            "events": t.get("events", t.get("eventos", [])),
            "parent": t.get("parent", t.get("pai")),
        }

    def migrate_voice(v: dict) -> dict:
        return {
            "id": v["id"],
            "events": [migrate_event(e) for e in v.get("events", v.get("eventos", []))],
            "tuplets": [migrate_tuplet(t) for t in v.get("tuplets", v.get("quialteras", []))],
        }

    def migrate_part(p: dict) -> dict:
        return {
            "id": p["id"],
            "instrument": p.get("instrument", p.get("instrumento", "")),
            "clef": normalize_clef(p.get("clef", p.get("clave", "treble"))),
            "range": p.get("range", p.get("tessitura")),
            "voices": [migrate_voice(v) for v in p.get("voices", p.get("vozes", []))],
        }

    def migrate_measure(m: dict) -> dict:
        return {
            "numerator": m.get("numerator", m.get("numerador")),
            "denominator": m.get("denominator", m.get("denominador")),
            "tempo": m.get("tempo", m.get("andamento")),
            "duration": m.get("duration", m.get("duracao")),
        }

    return {
        "title": raw.get("title", raw.get("titulo", "Untitled")),
        "composer": raw.get("composer", raw.get("compositor")),
        "measures": [migrate_measure(m) for m in raw.get("measures", raw.get("compassos", []))],
        "parts": [migrate_part(p) for p in raw.get("parts", raw.get("partes", []))],
    }


_POWERS_OF_TWO = [Fraction(1, 2**k) for k in range(7)]  # whole note .. 64th note


def is_figure(duration: Fraction) -> bool:
    """Return True if `duration` can be written as a single simple or dotted note value."""
    return any(duration in (base, base * Fraction(3, 2)) for base in _POWERS_OF_TWO)


def figures(duration: Fraction) -> list[Fraction]:
    """Decompose `duration` into tied note values (largest first): 5/8 -> [1/2, 1/8]."""
    if is_figure(duration):
        return [duration]
    largest = next((base for base in _POWERS_OF_TWO if base <= duration), None)
    if largest is None or (duration * 64).denominator != 1:
        raise ValueError(f"duration {duration} cannot be represented with note values down to 64th notes")
    return [largest, *figures(duration - largest)]


def slices(starts: list[Fraction], start: Fraction, duration: Fraction) -> list[tuple[Fraction, Fraction]]:
    """Split `(start, duration)` across barlines (`starts`) and into representable note figures."""
    cuts = [start, *(b for b in starts if start < b < start + duration), start + duration]
    output: list[tuple[Fraction, Fraction]] = []
    for a, b in zip(cuts, cuts[1:]):
        for fig in figures(b - a):
            output.append((a, fig))
            a += fig
    return output


def locate(ir: ScoreIR) -> dict[str, tuple[str, str | None, int | None]]:
    """Map `id -> (part_id, voice_id, measure_number 1..n)`; parts and voices have `None` for lower levels."""
    starts = ir.measure_starts()
    locations: dict[str, tuple[str, str | None, int | None]] = {}
    for part in ir.parts:
        locations[part.id] = (part.id, None, None)
        for voice in part.voices:
            locations[voice.id] = (part.id, voice.id, None)
            for event in voice.events:
                locations[event.id] = (part.id, voice.id, bisect_right(starts, event.start))
    return locations


class ScoreDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: dict[str, list[str]] = Field(default_factory=dict, description="id -> modified field names")
    measures: list[int] = Field(default_factory=list, description="1-based indices of modified measures")
    locations: dict[str, tuple[str, int]] = Field(
        default_factory=dict, description="event id -> (part_id, measure_number)"
    )

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.measures)


def _by_id(ir: ScoreIR) -> dict[str, dict]:
    """Flatten parts, voices, and events by id without children; attach parent id (`part`, `voice`)."""
    items: dict[str, dict] = {}
    for part in ir.parts:
        items[part.id] = part.model_dump(exclude={"id", "voices"})
        for voice in part.voices:
            items[voice.id] = {**voice.model_dump(exclude={"id", "events"}), "part": part.id}
            for event in voice.events:
                items[event.id] = {**event.model_dump(exclude={"id"}), "voice": voice.id}
    return items


def diff_scores(before: ScoreIR, after: ScoreIR) -> ScoreDiff:
    """Compute structural difference by id (parts, voices, events) and by 1-based measure number."""
    a, b = _by_id(before), _by_id(after)
    ma, mb = before.measures, after.measures
    modified = {
        i: sorted(field for field in a[i].keys() | b[i].keys() if a[i].get(field) != b[i].get(field))
        for i in a.keys() & b.keys()
    }
    modified = {i: fields for i, fields in modified.items() if fields}
    added, removed = [i for i in b if i not in a], [i for i in a if i not in b]
    locs = {**locate(before), **locate(after)}
    return ScoreDiff(
        added=added,
        removed=removed,
        modified=modified,
        locations={
            i: (part_id, measure_no)
            for i in [*added, *removed, *modified]
            for part_id, _, measure_no in [locs[i]]
            if measure_no is not None
        },
        measures=[
            n
            for n in range(1, max(len(ma), len(mb)) + 1)
            if n > min(len(ma), len(mb)) or ma[n - 1] != mb[n - 1]
        ],
    )

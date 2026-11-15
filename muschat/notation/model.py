"""Compact symbolic score schema filled by the LLM (`response_schema`) and converted into `ScoreIR`."""

import re
from fractions import Fraction
from typing import Literal, cast

from pydantic import BaseModel, Field, model_validator

from muschat.score_ir import (
    PITCH_CLASS,
    Clef,
    Dynamic,
    Event,
    Measure,
    Note,
    Part,
    Pitch,
    PitchLetter,
    Provenance,
    Rest,
    ScoreIR,
    Voice,
    normalize_clef,
)

DurationName = Literal["whole", "half", "quarter", "eighth", "16th"]
_DURATION_ALIASES: dict[str, DurationName] = {
    "whole": "whole",
    "inteira": "whole",
    "half": "half",
    "minima": "half",
    "quarter": "quarter",
    "seminima": "quarter",
    "eighth": "eighth",
    "colcheia": "eighth",
    "16th": "16th",
    "sixteenth": "16th",
    "semicolcheia": "16th",
}


class ExampleNote(BaseModel):
    pitches: list[str] = Field(
        default_factory=list,
        description='Pitches in scientific notation: "C4", "F#5", "Bb3"; quarter-tones with trailing + or - '
        '("A4+", "A4-"). Multiple pitches = chord/multiphonic; empty list = rest.',
    )
    duration: DurationName
    dotted: bool = False
    techniques: list[str] = Field(default_factory=list, description="Techniques from the prompt list.")
    dynamic: Dynamic | None = None
    text: str | None = Field(default=None, description="Extra textual direction above the note.")
    glissando: bool = Field(default=False, description="Glissando from this note to the next.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            d = dict(data)
            if "alturas" in d and "pitches" not in d:
                d["pitches"] = d.pop("alturas")
            if "duracao" in d and "duration" not in d:
                d["duration"] = d.pop("duracao")
            if "pontuada" in d and "dotted" not in d:
                d["dotted"] = d.pop("pontuada")
            if "tecnicas" in d and "techniques" not in d:
                d["techniques"] = d.pop("tecnicas")
            if "dinamica" in d and "dynamic" not in d:
                d["dynamic"] = d.pop("dinamica")
            if "texto" in d and "text" not in d:
                d["text"] = d.pop("texto")
            if isinstance(d.get("duration"), str):
                d["duration"] = _DURATION_ALIASES.get(d["duration"].strip().lower(), d["duration"])
            return d
        return data


class ExampleMeasure(BaseModel):
    notes: list[ExampleNote]

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: object) -> object:
        if isinstance(data, dict) and "notas" in data and "notes" not in data:
            return {**data, "notes": data["notas"]}
        return data


class ScoreExample(BaseModel):
    title: str
    instrument: str
    clef: Clef = "treble"
    meter: str = "4/4"
    tempo: int | None = None
    measures: list[ExampleMeasure]

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            d = dict(data)
            for src, dst in (
                ("titulo", "title"),
                ("instrumento", "instrument"),
                ("clave", "clef"),
                ("metrica", "meter"),
                ("andamento", "tempo"),
                ("compassos", "measures"),
            ):
                if src in d and dst not in d:
                    d[dst] = d.pop(src)
            if isinstance(d.get("clef"), str):
                d["clef"] = normalize_clef(d["clef"])
            return d
        return data


_FIGURE: dict[DurationName, Fraction] = {
    "whole": Fraction(1),
    "half": Fraction(1, 2),
    "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8),
    "16th": Fraction(1, 16),
}
_ALTER = {"#": 1, "##": 2, "b": -1, "bb": -2, "+": 0.5, "-": -0.5, "#+": 1.5, "b-": -1.5}
_PITCH_RE = re.compile(r"^([A-G])(##|#\+|#|bb|b-|b|\+|-)?(\d)([+-])?$")


def parse_pitch(text: str) -> Pitch | None:
    """"A4+" -> midicents 6950 with spelling "A"; None when notation is invalid or ambiguous."""
    m = _PITCH_RE.match(text.strip())
    if not m:
        return None
    letter, accidental, octave, quarter = m.groups()
    if quarter and accidental and accidental[-1] in "+-":
        return None  # quarter-tone written twice ("A#+4+"): ambiguous
    alter = _ALTER.get(accidental or "", 0) + _ALTER.get(quarter or "", 0)
    midicents = round((12 * (int(octave) + 1) + PITCH_CLASS[letter] + alter) * 100)
    return Pitch(midicents=midicents, spelling=cast(PitchLetter, letter)) if 0 <= midicents <= 12700 else None


def to_score_ir(example: ScoreExample, provenance: Provenance | None = None) -> ScoreIR:
    """Convert an illustrative `ScoreExample` into a single-part, single-voice `ScoreIR` (`P1`, `P1V1`, `P1V1E1`...)."""
    prov = provenance or Provenance(source="llm")
    num_text, _, den_text = example.meter.partition("/")
    valid_meter = num_text.isdigit() and den_text.isdigit() and int(num_text) > 0 and int(den_text) > 0
    num, den = (int(num_text), int(den_text)) if valid_meter else (4, 4)
    tempo = example.tempo if example.tempo and example.tempo > 0 else None
    events: list[Event] = []
    measures: list[Measure] = []
    start = Fraction(0)
    for m in example.measures:
        bar_start = start
        for note in m.notes:
            duration = _FIGURE[note.duration] * (Fraction(3, 2) if note.dotted else 1)
            pitches = [p for p in map(parse_pitch, note.pitches) if p is not None]
            eid = f"P1V1E{len(events) + 1}"
            if pitches:
                events.append(
                    Note(
                        id=eid,
                        start=start,
                        duration=duration,
                        provenance=prov,
                        pitches=pitches,
                        techniques=note.techniques,
                        dynamic=note.dynamic,
                        text=note.text,
                        glissando=note.glissando,
                    )
                )
            else:
                events.append(
                    Rest(
                        id=eid,
                        start=start,
                        duration=duration,
                        provenance=prov,
                        dynamic=note.dynamic,
                        text=note.text,
                    )
                )
            start += duration
        actual = start - bar_start
        measures.append(
            Measure(
                numerator=num,
                denominator=den,
                tempo=None if measures else tempo,
                duration=None if actual == Fraction(num, den) else actual,
            )
        )
    voice = Voice(id="P1V1", events=events)
    return ScoreIR(
        title=example.title,
        measures=measures,
        parts=[Part(id="P1", instrument=example.instrument, clef=example.clef, voices=[voice])],
    )

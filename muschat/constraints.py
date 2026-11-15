"""Single constraint verifier: runs after any operation and reports each violation with its location."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from muschat.score_ir import Note, ScoreIR, locate

ConstraintType = Literal["range", "forbidden_interval", "no_unison", "max_density"]

_CONSTRAINT_ALIASES: dict[str, ConstraintType] = {
    "tessitura": "range",
    "range": "range",
    "intervalo_proibido": "forbidden_interval",
    "forbidden_interval": "forbidden_interval",
    "sem_unissono": "no_unison",
    "no_unison": "no_unison",
    "densidade_maxima": "max_density",
    "max_density": "max_density",
}


def normalize_constraint_type(value: str) -> ConstraintType:
    return _CONSTRAINT_ALIASES.get(str(value).strip().lower(), value)  # type: ignore[return-value]


class Constraint(BaseModel):
    kind: ConstraintType
    value: float | None = Field(
        default=None,
        description="forbidden_interval: cents; max_density: attacks per quarter note; unused for range and no_unison.",
    )
    low: int | None = Field(default=None, description="range: lower bound in midicents.")
    high: int | None = Field(default=None, description="range: upper bound in midicents.")
    parts: list[str] = Field(default_factory=list, description="Target part IDs; empty = all parts.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: object) -> object:
        if isinstance(data, dict) and "kind" in data and isinstance(data["kind"], str):
            return {**data, "kind": normalize_constraint_type(data["kind"])}
        return data

    @model_validator(mode="after")
    def _validate_value(self) -> "Constraint":
        if self.kind in ("forbidden_interval", "max_density") and self.value is None:
            raise ValueError(f"constraint {self.kind} requires a value")
        if self.kind == "range" and (self.low is None or self.high is None or self.low > self.high):
            raise ValueError("range constraint requires low <= high")
        return self


@dataclass(frozen=True)
class Violation:
    constraint: str
    part: str
    measure: int | None
    event: str | None
    detail: str

    def __str__(self) -> str:
        where = " ".join(x for x in (self.part, f"m.{self.measure}" if self.measure else "", self.event or "") if x)
        return f"{self.constraint} ({where}): {self.detail}"


def check_constraints(ir: ScoreIR, constraints: list[Constraint]) -> list[Violation]:
    """Check part ranges unconditionally and check explicit constraints when requested."""
    locations = locate(ir)
    output: list[Violation] = []

    def report(kind: str, part: str, event: str | None, detail: str, measure: int | None = None) -> None:
        output.append(Violation(kind, part, measure if event is None else locations[event][2], event, detail))

    ranges = [(p, p.range) for p in ir.parts if p.range]
    ranges += [
        (p, (c.low, c.high))
        for c in constraints
        if c.kind == "range" and c.low is not None and c.high is not None
        for p in ir.parts
        if not c.parts or p.id in c.parts
    ]
    for part, (low, high) in ranges:
        for voice in part.voices:
            for event in voice.events:
                for pitch in event.pitches if isinstance(event, Note) else []:
                    if not low <= pitch.midicents <= high:
                        report("range", part.id, event.id, f"{pitch.midicents} mc outside {low}–{high}")

    for c in constraints:
        target_parts = [p for p in ir.parts if not c.parts or p.id in c.parts]
        if c.kind == "forbidden_interval":
            for part in target_parts:
                for voice in part.voices:
                    notes = [e for e in voice.events if isinstance(e, Note)]
                    for prev, curr in zip(notes, notes[1:]):
                        if abs(curr.pitches[0].midicents - prev.pitches[0].midicents) == c.value:
                            report(c.kind, part.id, curr.id, f"interval of {c.value:g} cents after {prev.id}")
        elif c.kind == "no_unison":
            sounding = sorted(
                ((e, p.id) for p in target_parts for v in p.voices for e in v.events if isinstance(e, Note)),
                key=lambda x: x[0].start,
            )
            for i, (first, part_a) in enumerate(sounding):
                for second, part_b in sounding[i + 1 :]:
                    if second.start >= first.end:
                        break
                    shared = {x.midicents for x in first.pitches} & {x.midicents for x in second.pitches}
                    if part_a != part_b and shared:
                        report(c.kind, part_b, second.id, f"unison {sorted(shared)} mc with {first.id} ({part_a})")
        elif c.kind == "max_density":
            assert c.value is not None
            for part in target_parts:
                for voice in part.voices:
                    counts: dict[int, int] = {}
                    for event in voice.events:
                        m = locations[event.id][2]
                        if isinstance(event, Note) and m is not None and m <= len(ir.measures):
                            counts[m] = counts.get(m, 0) + 1
                    for m, attacks in sorted(counts.items()):
                        per_quarter = attacks / float(ir.measures[m - 1].length * 4)
                        if per_quarter > c.value:
                            report(
                                c.kind,
                                part.id,
                                None,
                                f"{per_quarter:g} attacks per quarter note in {voice.id} (max {c.value:g})",
                                measure=m,
                            )
    return output

"""Shared symbolic procedure helpers: selection-aware voice/measure traversal, barline splitting, ID generation,
interpolation, and idiomatic extended-technique playability checks.
"""

from collections.abc import Callable
from fractions import Fraction

from muschat.notation.techniques import (
    TECHNIQUES,
    Technique,
    VoiceDirections,
    family_techniques,
    fits_instrument,
    get_technique,
    technique_issue,
    unplayable_techniques,
)
from muschat.plan import Selection
from muschat.score_ir import Dynamic, Event, Note, Part, Pitch, Rest, ScoreIR, Voice, locate, slices

__all__ = [
    "DEFAULT_QUARTET",
    "DYNAMICS",
    "TECHNIQUES",
    "IdGenerator",
    "MeasureGrid",
    "MeasureSliceFn",
    "Technique",
    "VoiceDirections",
    "VoiceSliceFn",
    "by_measure",
    "by_voice",
    "family_techniques",
    "fits_instrument",
    "get_technique",
    "interpolate",
    "map_voices",
    "require_score",
    "split_event",
    "technique_issue",
    "to_rest",
    "unplayable_techniques",
    "with_pitches",
]

DEFAULT_QUARTET = ["violin", "violin", "viola", "cello"]
DYNAMICS: list[Dynamic] = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff"]


def require_score(current: ScoreIR | None) -> ScoreIR:
    if current is None:
        raise ValueError("this procedure transforms an existing composition, and none exists yet")
    return current


class IdGenerator:
    """Generate unique event and tuplet IDs (`base.1`, `base.2`, ...) without colliding with existing score IDs."""

    def __init__(self, current: ScoreIR) -> None:
        self.used = set(locate(current)) | {t.id for p in current.parts for v in p.voices for t in v.tuplets}

    def new_id(self, base: str) -> str:
        n = 1
        while f"{base}.{n}" in self.used:
            n += 1
        self.used.add(f"{base}.{n}")
        return f"{base}.{n}"


class MeasureGrid:
    """Measure boundaries (1-based) in whole notes."""

    def __init__(self, current: ScoreIR) -> None:
        self.starts = current.measure_starts()
        self.ends = [*self.starts[1:], self.starts[-1] + current.measures[-1].length]

    def span(self, sel: Selection) -> tuple[int, int]:
        """First and last measure of the selection (1..n)."""
        return sel.start_measure or 1, sel.end_measure or len(self.starts)

    def slices(self, start: Fraction, duration: Fraction) -> list[tuple[Fraction, Fraction]]:
        return slices(self.starts, start, duration)


def split_event(
    event: Event,
    start: Fraction,
    duration: Fraction,
    grid: MeasureGrid,
    ids: IdGenerator,
    *,
    sustain: bool = False,
) -> list[Event]:
    """Split a moved/resized event across barlines and representable figures; first slice keeps ID, dynamic, and text."""
    parts = grid.slices(start, duration)
    output: list[Event] = []
    for k, (s, d) in enumerate(parts):
        fields: dict = {"id": event.id if k == 0 else ids.new_id(event.id), "start": s, "duration": d}
        if k > 0 and isinstance(event, (Note, Rest)):
            fields |= {"dynamic": None, "text": None}
        if k < len(parts) - 1 and isinstance(event, Note):
            fields["glissando"] = False
            if sustain:
                fields["tied"] = True
        output.append(event.model_copy(update=fields))
    return output


def to_rest(event: Event) -> Rest:
    return Rest(
        id=event.id,
        start=event.start,
        duration=event.duration,
        provenance=event.provenance,
        dynamic=getattr(event, "dynamic", None),
        text=getattr(event, "text", None),
    )


def with_pitches(note: Note, pitches: list[int]) -> Note:
    return note.model_copy(update={"pitches": [Pitch(midicents=a) for a in pitches]})


def map_voices(current: ScoreIR, fn: Callable[[Part, Voice], Voice]) -> ScoreIR:
    return current.model_copy(
        update={"parts": [p.model_copy(update={"voices": [fn(p, v) for v in p.voices]}) for p in current.parts]}
    )


VoiceSliceFn = Callable[[Part, Voice, list[Event]], list[Event]]


def by_voice(current: ScoreIR, sel: Selection, fn: VoiceSliceFn) -> ScoreIR:
    """Apply `fn` to the selected events of each voice; the returned list replaces only those events."""
    locations = locate(current)

    def transform_voice(part: Part, v: Voice) -> Voice:
        target = [e for e in v.events if sel.contains(part.id, v.id, locations[e.id][2])]
        if not target:
            return v
        replaced = {e.id for e in target}
        events = [e for e in v.events if e.id not in replaced] + fn(part, v, target)
        return v.model_copy(update={"events": sorted(events, key=lambda e: e.start)})

    return map_voices(current, transform_voice)


MeasureSliceFn = Callable[[Part, Voice, list[Event], int], list[Event]]


def by_measure(current: ScoreIR, sel: Selection, fn: MeasureSliceFn) -> ScoreIR:
    """Like `by_voice`, measure by measure; measures containing tuplets are left untouched."""
    locations = locate(current)

    def slice_fn(part: Part, v: Voice, target: list[Event]) -> list[Event]:
        in_tuplet = {i for t in v.tuplets for i in t.events}
        groups: dict[int, list[Event]] = {}
        for e in target:
            groups.setdefault(locations[e.id][2] or 0, []).append(e)
        output: list[Event] = []
        for n, group in groups.items():
            output += group if in_tuplet & {e.id for e in group} else fn(part, v, group, n)
        return output

    return by_voice(current, sel, slice_fn)


def interpolate(start: float, end: float, n: int, first: int, last: int) -> float:
    """Linearly interpolate value at measure `n` between `first` and `last` measures of the selection."""
    return start if last == first else start + (end - start) * (n - first) / (last - first)

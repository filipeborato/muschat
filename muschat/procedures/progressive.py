"""Density and progressive transformations: linear ramps across the measures of the selected span."""

import random
from collections.abc import Callable
from fractions import Fraction

from muschat.spectrum import quantize
from muschat.plan import Plan, Selection
from muschat.procedures.common import (
    IdGenerator,
    MeasureGrid,
    by_measure,
    by_voice,
    fits_instrument,
    get_technique,
    interpolate,
    require_score,
    technique_issue,
    to_rest,
    with_pitches,
)
from muschat.score_ir import Event, Note, Part, Rest, ScoreIR, Voice, gestures


def _halves(d: Fraction) -> tuple[Fraction, Fraction] | None:
    """Two rhythmic figures summing to `d` (down to 32nd notes): 1/4 -> 1/8 + 1/8; 3/8 -> 1/4 + 1/8."""
    if d.numerator == 1 and d >= Fraction(1, 16):
        return d / 2, d / 2
    if d.numerator == 3 and d >= Fraction(3, 32):
        return d * 2 / 3, d / 3
    return None


def density(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Ramp attacks per quarter note linearly from the first to the last selected measure."""
    current = require_score(current)
    first, last = MeasureGrid(current).span(plan.selection)
    return density_by_measure(
        current, plan.selection, lambda n: interpolate(p["start"], p["end"], n, first, last), rng
    )


def density_by_measure(
    current: ScoreIR,
    selection: Selection | Plan,
    target_for: Callable[[int], float] | dict[int, float],
    rng: random.Random,
) -> ScoreIR:
    """Set attacks per quarter note in measure `n` to `target_for(n)`: remove attacks (turn into rests) or add attacks
    (turn rests into notes with a pitch already used in the voice, or split notes in two).
    """
    sel = selection.selection if isinstance(selection, Plan) else selection
    target_fn: Callable[[int], float] = (
        (lambda n: target_for[n]) if isinstance(target_for, dict) else target_for
    )
    ids = IdGenerator(current)
    processed: dict[str, list[Event]] = {}

    def transform_measure(part: Part, voice: Voice, group: list[Event], n: int) -> list[Event]:
        target = round(target_fn(n) * 4 * current.measures[n - 1].length)
        pool = {a.midicents: a for e in voice.events if isinstance(e, Note) for a in e.pitches}
        events = list(group)

        def note_indices() -> list[int]:
            return [k for k, e in enumerate(events) if isinstance(e, Note)]

        while len(note_indices()) > target:
            k = rng.choice(note_indices())
            events[k] = to_rest(events[k])
        while len(note_indices()) < target:
            rests = [k for k, e in enumerate(events) if isinstance(e, Rest)]
            fillable = []
            previous = processed.get(voice.id, [])
            processed_start = previous[0].start if previous else group[0].start
            outside = previous + [
                e for e in voice.events if e.start < processed_start or e.start >= group[-1].end
            ]
            for k in rests:
                e = events[k]
                neighbor_notes = sorted(
                    (x for x in [*outside, *events] if isinstance(x, Note)), key=lambda x: x.start
                )
                before = [x for x in neighbor_notes if x.start < e.start]
                after = [x for x in neighbor_notes if x.start > e.start]
                neighbors = before[-1:] + after[:1]
                options = [
                    a
                    for a in sorted(pool)
                    if all(abs(a - x.pitches[0].midicents) <= 1200 for x in neighbors)
                ]
                if options:
                    fillable.append((k, options))
            if fillable:
                k, options = rng.choice(fillable)
                e = events[k]
                events[k] = Note(
                    id=e.id,
                    start=e.start,
                    duration=e.duration,
                    provenance=e.provenance,
                    pitches=[pool[rng.choice(options)]],
                )
                continue
            divisible = [k for k in note_indices() if _halves(events[k].duration)]
            if not divisible:
                break
            k = rng.choice(divisible)
            e = events[k]
            a, b = _halves(e.duration)  # type: ignore[misc]
            events[k : k + 1] = [
                e.model_copy(update={"duration": a, "glissando": False}),
                e.model_copy(
                    update={
                        "id": ids.new_id(e.id),
                        "start": e.start + a,
                        "duration": b,
                        "dynamic": None,
                        "text": None,
                    }
                ),
            ]
        processed.setdefault(voice.id, []).extend(events)
        return events

    return by_measure(current, sel, transform_measure)


def progressive_register(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    """Shift pitches by a linear ramp in cents from start to end of the span (quantized to quarter-tones)."""
    current = require_score(current)
    grid = MeasureGrid(current)
    first, last = grid.span(plan.selection)
    start_pos, _end_pos = grid.starts[first - 1], grid.ends[last - 1]

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        last_attack = max((e.start for e in target if isinstance(e, Note)), default=start_pos)

        def shift(e: Note) -> Note:
            t = float((e.start - start_pos) / (last_attack - start_pos)) if last_attack > start_pos else 0.0
            n = quantize(p["start"] + (p["end"] - p["start"]) * t, 1)
            return with_pitches(e, [a.midicents + n for a in e.pitches])

        return [shift(e) if isinstance(e, Note) else e for e in target]

    return by_voice(current, plan.selection, transform_span)


def progressive_dynamics(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    """Set one dynamic per measure on its first attack, ramping linearly between two levels; clear other dynamics."""
    current = require_score(current)
    first, last = MeasureGrid(current).span(plan.selection)

    from muschat.dynamics import dynamics_by_measure
    return dynamics_by_measure(current, plan.selection, lambda n: interpolate(p["start"], p["end"], n, first, last))


def gestural_techniques(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    """Assign one technique from the plan per gesture (notes between rests), chosen among those playable on all notes
    of the gesture (family, register, duration); gestures where none fits stay ordinario.
    """
    if not plan.techniques:
        raise ValueError("gestural_techniques requires techniques in the plan (field techniques)")
    current = require_score(current)
    playable_count = 0

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        nonlocal playable_count
        updated: dict[str, Note] = {}
        for gesture in gestures(target):
            possible = [
                t for t in plan.techniques if all(technique_issue(t, part, n) is None for n in gesture)
            ]
            playable_count += bool(possible)
            if possible and rng.random() < p["ratio"]:
                chosen = rng.choice(possible)
                updated |= {
                    n.id: n.model_copy(
                        update={
                            "techniques": [
                                *(t for t in n.techniques if get_technique(t).articulation),
                                chosen,
                            ]
                        }
                    )
                    for n in gesture
                }
        return [updated.get(e.id, e) for e in target]

    result = by_voice(current, plan.selection, transform_span)
    if not playable_count:
        raise ValueError(
            f"no gesture in the selection supports {plan.techniques} (instrument, register, or duration)"
        )
    return result


def apply_techniques(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    """Assign one randomly chosen technique from the plan to each sampled note in the selection."""
    if not plan.techniques:
        raise ValueError("apply_techniques requires techniques in the plan (field techniques)")
    current = require_score(current)

    def fitting(part: Part) -> list[str]:
        return [t for t in plan.techniques if fits_instrument(t, part.instrument)]

    if not any(fitting(part) for part in current.parts):
        raise ValueError(f"none of the techniques {plan.techniques} is playable on the score's instruments")

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        possible = fitting(part)
        return [
            e.model_copy(update={"techniques": [rng.choice(possible)]})
            if possible and isinstance(e, Note) and rng.random() < p["ratio"]
            else e
            for e in target
        ]

    return by_voice(current, plan.selection, transform_span)

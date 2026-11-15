"""Rhythmic transformations over the selected span, always within the measures it occupies."""

import math
import random
from fractions import Fraction

from muschat.plan import Plan
from muschat.procedures.common import (
    IdGenerator,
    MeasureGrid,
    by_measure,
    by_voice,
    map_voices,
    require_score,
    split_event,
)
from muschat.score_ir import Event, Note, Part, ScoreIR, Tuplet, Voice, locate


def permute_durations(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    def transform_measure(part: Part, voice: Voice, group: list[Event], n: int) -> list[Event]:
        durations = [e.duration for e in group]
        rng.shuffle(durations)
        start, output = group[0].start, []
        for e, d in zip(group, durations):
            output.append(e.model_copy(update={"start": start, "duration": d}))
            start += d
        return output

    return by_measure(require_score(current), plan.selection, transform_measure)


def augment_diminish(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Scale durations by 2^exponent from the start of the span: augmentation truncates overflow, diminution repeats."""
    current = require_score(current)
    factor = Fraction(2) ** int(p["exponent"])
    grid, ids, locations = MeasureGrid(current), IdGenerator(current), locate(current)

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        if {i for q in voice.tuplets for i in q.events} & {e.id for e in target}:
            raise ValueError(f"augment_diminish does not rescale tuplets yet ({voice.id}); select another voice")
        if factor == 1:
            return target
        start_pos = grid.starts[(locations[target[0].id][2] or 1) - 1]
        end_pos = grid.ends[(locations[target[-1].id][2] or 1) - 1]
        period = (end_pos - start_pos) * factor if factor < 1 else end_pos - start_pos
        output: list[Event] = []
        for k in range(math.ceil((end_pos - start_pos) / period)):
            for e in target:
                start = start_pos + k * period + (e.start - start_pos) * factor
                if start >= end_pos:
                    break
                copy = e if k == 0 else e.model_copy(update={"id": ids.new_id(e.id)})
                output += split_event(copy, start, min(e.duration * factor, end_pos - start), grid, ids)
        return output

    return by_voice(current, plan.selection, transform_span)


def subdivide(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Split each sampled note into `n` equal attacks with the same pitch: tuplet `n:2^k` when `n` is not a power of 2."""
    current = require_score(current)
    n, ids = int(p["division"]), IdGenerator(current)
    base = 2 ** int(math.log2(n))
    groups: dict[str, list[Tuplet]] = {}

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        in_tuplet = {i for q in voice.tuplets for i in q.events}
        output: list[Event] = []
        for e in target:
            divisible = (
                e.id not in in_tuplet and e.duration.numerator == 1 and e.duration / base >= Fraction(1, 32)
            )
            if not (isinstance(e, Note) and divisible and rng.random() < p["ratio"]):
                output.append(e)
                continue
            d = e.duration / n
            pieces = [
                e.model_copy(
                    update={
                        "id": e.id if k == 0 else ids.new_id(e.id),
                        "start": e.start + k * d,
                        "duration": d,
                        "glissando": e.glissando if k == n - 1 else False,
                        **({} if k == 0 else {"dynamic": None, "text": None}),
                    }
                )
                for k in range(n)
            ]
            output += pieces
            if base != n:
                groups.setdefault(voice.id, []).append(
                    Tuplet(
                        id=ids.new_id(f"{voice.id}Q"),
                        numerator=n,
                        denominator=base,
                        events=[x.id for x in pieces],
                    )
                )
        return output

    subdivided = by_voice(current, plan.selection, transform_span)
    return map_voices(
        subdivided, lambda part, v: v.model_copy(update={"tuplets": v.tuplets + groups.get(v.id, [])})
    )

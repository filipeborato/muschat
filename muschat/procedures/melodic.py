"""Melodic transformations: only the pitches of selected notes change; rhythm, rests, and IDs are preserved."""

import random
from collections.abc import Callable

from muschat.spectrum import quantize
from muschat.plan import Plan
from muschat.procedures.common import by_voice, require_score, with_pitches
from muschat.score_ir import Event, Note, Part, ScoreIR, Voice

Sequence = list[list[int]]  # pitches (midicents) of each note in the voice, in order; chord = multiple


def _over_sequence(current: ScoreIR | None, plan: Plan, f: Callable[[Sequence], Sequence]) -> ScoreIR:
    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        notes = [e for e in target if isinstance(e, Note)]
        updated_seq = f([[a.midicents for a in n.pitches] for n in notes]) if notes else []
        replaced = {n.id: with_pitches(n, pitches) for n, pitches in zip(notes, updated_seq)}
        return [replaced.get(e.id, e) for e in target]

    return by_voice(require_score(current), plan.selection, transform_span)


def transpose(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    n = int(p["interval"])
    return _over_sequence(current, plan, lambda seq: [[a + n for a in chord] for chord in seq])


def invert(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    def mirror(seq: Sequence) -> Sequence:
        axis = int(p["axis"]) or seq[0][0]
        return [[2 * axis - a for a in chord] for chord in seq]

    return _over_sequence(current, plan, mirror)


def retrograde(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    return _over_sequence(current, plan, lambda seq: seq[::-1])


def rotate(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    def spin(seq: Sequence) -> Sequence:
        k = int(p["steps"]) % len(seq)
        return seq[k:] + seq[:k]

    return _over_sequence(current, plan, spin)


def scale_intervals(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    def scale(seq: Sequence) -> Sequence:
        base = seq[0][0]
        return [[base + quantize((a - base) * p["factor"], 1) for a in chord] for chord in seq]

    return _over_sequence(current, plan, scale)

"""Pitch deviation, idiomatic noise substitution, and rarefaction of existing material."""

from muschat.spectrum import partials, quantize, quantization_step
import math
import random
from fractions import Fraction as F

from muschat.instruments import identify, part_range
from muschat.plan import Plan
from muschat.procedures.common import (
    DYNAMICS,
    IdGenerator,
    MeasureGrid,
    by_voice,
    map_voices,
    require_score,
    split_event,
    technique_issue,
    to_rest,
)
from muschat.score_ir import Event, Note, Part, Pitch, ScoreIR, Tuplet, Voice, figures, gestures, ties_to

NOISE_FAMILIES: dict[int, tuple[str, ...]] = {
    1: ("scratch", "col legno tratto", "dietro il ponticello", "sulla cordiera"),
    2: ("col legno battuto", "slap", "tongue ram", "key click"),
    3: ("air sound",),
}
OPEN_STRINGS: dict[str, tuple[int, ...]] = {
    "violin": (5500, 6200, 6900, 7600),
    "viola": (4800, 5500, 6200, 6900),
    "cello": (3600, 4300, 5000, 5700),
    "double bass": (2800, 3300, 3800, 4300),
}


def detune(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    current = require_score(current)
    microtone = int(p["resolution"])
    step = quantization_step(microtone)
    amplitude = math.floor(p["deviation"] / step) * step

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        notes = [e for e in target if isinstance(e, Note)]
        if not notes:
            return target
        start, end = notes[0].start, notes[-1].start
        selected = {n.id for n in notes}
        attacks: dict[str, F] = {}
        fixed: set[str] = set()
        chain: list[Note] = []
        for e, nxt in zip(voice.events, [*voice.events[1:], None]):
            if not isinstance(e, Note):
                continue
            chain.append(e)
            if ties_to(e, nxt):
                continue
            for n in chain:
                attacks[n.id] = chain[0].start
            if any(n.id not in selected for n in chain):
                fixed.update(n.id for n in chain)
            chain = []
        glissandos = {
            a.id
            for gesture in gestures(target)
            for a, b in zip(gesture, gesture[1:])
            if a.end == b.start and not ties_to(a, b) and rng.random() < p["glissandos"]
        }
        reg = part_range(part) or (0, 12700)
        updated: dict[str, Note] = {}
        for note in notes:
            if note.id in fixed:
                continue
            t = float((attacks[note.id] - start) / (end - start)) if end > start else 0.0
            curve = t if p["curve"] == 0 else math.sin(2 * math.pi * t)
            shift = quantize(amplitude * curve, microtone)
            min_shift = max(
                -amplitude, math.ceil((reg[0] - min(a.midicents for a in note.pitches)) / step) * step
            )
            max_shift = min(
                amplitude, math.floor((reg[1] - max(a.midicents for a in note.pitches)) / step) * step
            )
            if min_shift > max_shift:
                raise ValueError(f"chord does not fit within the range of {part.instrument}")
            shift = min(max(shift, min_shift), max_shift)
            updated[note.id] = note.model_copy(
                update={
                    "pitches": [Pitch(midicents=a.midicents + shift) for a in note.pitches],
                    "glissando": note.id in glissandos,
                }
            )
        return [updated.get(e.id, e) for e in target]

    return by_voice(current, plan.selection, transform_span)


def timbral_substitution(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    current = require_score(current)
    allowed = (
        NOISE_FAMILIES[int(p["noise_family"])]
        if p["noise_family"]
        else sum(NOISE_FAMILIES.values(), ())
    )

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        notes = [e for e in target if isinstance(e, Note)]
        updated: dict[str, Note] = {}
        for k, note in enumerate(notes):
            ratio = p["start"] + (p["end"] - p["start"]) * k / max(len(notes) - 1, 1)
            if rng.random() >= ratio:
                continue
            # Tempered reference pitch: noise does not receive fictitious microtonal precision.
            candidate = note.model_copy(
                update={"pitches": [Pitch(midicents=round(a.midicents / 100) * 100) for a in note.pitches]}
            )
            options = [t for t in allowed if technique_issue(t, part, candidate) is None]
            if options:
                updated[note.id] = candidate.model_copy(
                    update={"techniques": [rng.choice(options)], "glissando": False, "tied": False}
                )
        return [updated.get(e.id, e) for e in target]

    return by_voice(current, plan.selection, transform_span)


def rarefy(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    current = require_score(current)
    grid, ids = MeasureGrid(current), IdGenerator(current)
    groups: dict[str, list[Tuplet]] = {}

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        updated: dict[str, list[Event]] = {}
        in_tuplet = {i: q.id for q in voice.tuplets for i in q.events}
        by_id = {q.id: q for q in voice.tuplets}
        for gesture in gestures(target):
            kept = [n for n in gesture if rng.random() < p["keep"]]
            for n in gesture:
                updated[n.id] = [to_rest(n)]
            for n in kept:
                following = gesture[gesture.index(n) + 1 :]
                absorbed = []
                end = n.end
                for nxt in following:
                    if (
                        nxt in kept
                        or nxt.start != end
                        or in_tuplet.get(n.id) != in_tuplet.get(nxt.id)
                    ):
                        break
                    absorbed.append(nxt)
                    end = nxt.end
                copy = n.model_copy(
                    update={
                        "duration": end - n.start,
                        "glissando": False,
                        "tied": False,
                        "dynamic": DYNAMICS[int(p["dynamic"])],
                    }
                )
                if rng.random() < p["harmonics"]:
                    try:
                        strings = OPEN_STRINGS.get(identify(part.instrument), ())
                    except ValueError:
                        strings = ()
                    if strings:
                        # shortcut: standard tuning, partials 2–8; extend for scordatura when string model exists.
                        naturals = sorted(
                            {pitch for c in strings for pitch in partials(c, 8, plan.grid(2))[1:]}
                        )
                        candidates = [
                            copy.model_copy(update={"pitches": [Pitch(midicents=a)], "techniques": ["harmonic"]})
                            for a in naturals
                        ]
                        candidates = [
                            c for c in candidates if technique_issue("harmonic", part, c) is None
                        ]
                        if candidates:
                            copy = min(
                                candidates,
                                key=lambda c: abs(c.pitches[0].midicents - n.pitches[0].midicents),
                            )
                    elif technique_issue("whistle tone", part, copy) is None:
                        copy = copy.model_copy(update={"techniques": ["whistle tone"]})
                if rng.random() < p["accents"]:
                    copy = copy.model_copy(update={"techniques": [*copy.techniques, "accent"]})
                pieces: list[Event] = []
                if n.id in in_tuplet:
                    factor = F(1)
                    group_id: str | None = in_tuplet[n.id]
                    while group_id is not None:
                        q = by_id[group_id]
                        factor *= F(q.denominator, q.numerator)
                        group_id = q.parent
                    cursor = copy.start
                    for dur in figures(copy.duration / factor):
                        pieces.append(
                            copy.model_copy(
                                update={
                                    "id": n.id if not pieces else ids.new_id(n.id),
                                    "start": cursor,
                                    "duration": dur * factor,
                                    "tied": True,
                                }
                            )
                        )
                        cursor += dur * factor
                    pieces[-1] = pieces[-1].model_copy(update={"tied": False})
                else:
                    pieces = split_event(copy, copy.start, copy.duration, grid, ids, sustain=True)
                updated[n.id] = pieces
                for removed in absorbed:
                    updated[removed.id] = []
        groups[voice.id] = [
            q.model_copy(
                update={
                    "events": [
                        n.id
                        for i in q.events
                        for n in updated.get(i, [next(e for e in voice.events if e.id == i)])
                    ]
                }
            )
            for q in voice.tuplets
        ]
        return [n for e in target for n in updated.get(e.id, [e])]

    result = by_voice(current, plan.selection, transform_span)
    return map_voices(
        result, lambda part, v: v.model_copy(update={"tuplets": groups.get(v.id, v.tuplets)})
    )

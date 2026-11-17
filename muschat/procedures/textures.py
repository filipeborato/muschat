"""Close canons and gesture deformation with explicit rhythmic notation."""

import random
from fractions import Fraction as F

from muschat.spectrum import quantize
from muschat.instruments import in_register, part_range
from muschat.plan import Plan
from muschat.procedures.common import (
    IdGenerator,
    MeasureGrid,
    by_voice,
    map_voices,
    require_score,
    split_event,
)
from muschat.score_ir import (
    Event,
    Note,
    Part,
    Pitch,
    Provenance,
    Rest,
    ScoreIR,
    Tuplet,
    Voice,
    figures,
    gestures,
    is_figure,
)

RATIOS = ((5, 4), (7, 4), (6, 4), (5, 6))


def micropolyphony(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    current = require_score(current)
    source_idx = int(p["source_part"]) - 1
    if source_idx >= len(current.parts):
        raise ValueError("source part does not exist")
    grid, ids = MeasureGrid(current), IdGenerator(current)
    first, last = grid.span(plan.selection)
    start_pos, end_pos = grid.starts[first - 1], grid.ends[last - 1]
    source = current.parts[source_idx].voices[0]
    events = [e for e in source.events if start_pos <= e.start < end_pos]
    if any(e.end > end_pos for e in events) or any(e.start < start_pos < e.end for e in source.events):
        raise ValueError("source event crosses selection boundary")
    destinations = [
        (part.id, v.id)
        for part in current.parts
        for v in part.voices
        if plan.selection.contains(part.id, v.id, first)
    ]
    groups: dict[str, list[Tuplet]] = {}

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        selected = {e.id for e in target}
        if any(set(q.events) & selected and not set(q.events) <= selected for q in voice.tuplets):
            raise ValueError("destination tuplet crosses selection boundary")
        groups[voice.id] = [q for q in voice.tuplets if not set(q.events) & selected]
        k = destinations.index((part.id, voice.id))
        delay = F(int(p["offset"]) * k, 16)
        factor = F(16 + rng.randint(-int(p["variation"]), int(p["variation"])), 16)
        shift = quantize((k / max(len(destinations) - 1, 1) - 0.5) * p["spread"], plan.grid(2))
        output: list[Event] = []
        cursor = start_pos
        for e in events:
            a = start_pos + delay + F(round((e.start - start_pos) * factor * 64), 64)
            b = start_pos + delay + F(round((e.end - start_pos) * factor * 64), 64)
            a, b = max(a, cursor), min(b, end_pos)
            if a >= end_pos:
                continue
            if b <= a:
                raise ValueError(
                    "micropolyphony: event too short for the 1/64 grid; no note was discarded"
                )
            if a > cursor:
                gap = Rest(
                    id=ids.new_id(voice.id), start=cursor, duration=a - cursor, provenance=e.provenance
                )
                output += split_event(gap, cursor, a - cursor, grid, ids)
            fields: dict = {"id": ids.new_id(voice.id)}
            if isinstance(e, Note):
                fields |= {
                    "pitches": [
                        Pitch(midicents=in_register(h.midicents + shift, part_range(part)))
                        for h in e.pitches
                    ],
                    "glissando": False,
                    "techniques": [],
                }
            copy = e.model_copy(update=fields)
            output += split_event(copy, a, b - a, grid, ids, sustain=True)
            cursor = b
        if cursor < end_pos:
            gap = Rest(
                id=ids.new_id(voice.id),
                start=cursor,
                duration=end_pos - cursor,
                provenance=Provenance(source="algorithm"),
            )
            output += split_event(gap, cursor, end_pos - cursor, grid, ids)
        return output

    def rewrite(part: Part, voice: Voice) -> Voice:
        if (part.id, voice.id) not in destinations:
            return voice
        target = [e for e in voice.events if start_pos <= e.start < end_pos]
        if any(e.end > end_pos for e in target) or any(e.start < start_pos < e.end for e in voice.events):
            raise ValueError("destination event crosses selection boundary")
        outside = [e for e in voice.events if not start_pos <= e.start < end_pos]
        return voice.model_copy(
            update={"events": sorted(outside + transform_span(part, voice, target), key=lambda e: e.start)}
        )

    result = map_voices(current, rewrite)
    return map_voices(
        result, lambda part, v: v.model_copy(update={"tuplets": groups.get(v.id, v.tuplets)})
    )


def deform_rhythm(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    current = require_score(current)
    if p["min_factor"] > p["max_factor"]:
        raise ValueError("minimum augmentation factor is greater than maximum")
    grid, ids = MeasureGrid(current), IdGenerator(current)
    ratios = [r for k, r in enumerate(RATIOS) if int(p["tuplets"]) & (1 << k)]
    groups: dict[str, list[Tuplet]] = {}

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        members = {e for q in voice.tuplets for e in q.events}
        updated: dict[str, list[Event]] = {}
        for gesture in gestures(target):
            if rng.random() >= p["ratio"]:
                continue
            if any(n.id in members for n in gesture):
                raise ValueError("deform_rhythm: gesture already contains a tuplet; select another span")
            # Split at barlines before deforming: no tuplet crosses measures.
            by_bar: dict[int, list[Note]] = {}
            for note in gesture:
                bar = next(k for k, end in enumerate(grid.ends) if note.start < end)
                if note.end > grid.ends[bar]:
                    raise ValueError("note crosses barline; split with a tie before deforming")
                by_bar.setdefault(bar, []).append(note)
            for notes in by_bar.values():
                start, end = notes[0].start, notes[-1].end
                span = end - start
                limit = next(b for b in grid.ends if start < b)
                breaths = []
                for e in target[target.index(notes[-1]) + 1 :]:
                    if not isinstance(e, Rest) or e.start != end or e.end > limit or e.id in members:
                        break
                    breaths.append(e)
                    end = e.end
                numerator, denominator = rng.choice(ratios)
                factor = F(denominator, numerator)
                desired = float(span) * rng.uniform(p["min_factor"], p["max_factor"])
                # The block occupies `denominator` binary units; any remainder becomes a breath outside the tuplet.
                units = [F(1, 2**k) for k in range(7) if F(denominator, 2**k) <= end - start]
                if not units:
                    raise ValueError("gesture too short for the requested tuplet")
                unit = min(units, key=lambda u: abs(float(u * denominator) - desired))
                duration = unit * denominator
                written_total = unit * numerator
                scale = F(1)
                while span * scale * 2 <= written_total:
                    scale *= 2
                while span * scale > written_total:
                    scale /= 2
                written = [n.duration * scale for n in notes]
                if any(not is_figure(d) for d in written):
                    raise ValueError("deformation would require a figure shorter than a 64th note")
                cursor = start
                output: list[Event] = []
                for n, w in zip(notes, written):
                    output.append(
                        n.model_copy(
                            update={"start": cursor, "duration": w * factor, "glissando": False, "tied": False}
                        )
                    )
                    cursor += w * factor
                    updated[n.id] = []
                remainder = written_total - sum(written)
                for w in figures(remainder) if remainder else []:
                    output.append(
                        Rest(
                            id=ids.new_id(voice.id),
                            start=cursor,
                            duration=w * factor,
                            provenance=notes[-1].provenance,
                        )
                    )
                    cursor += w * factor
                groups.setdefault(voice.id, []).append(
                    Tuplet(
                        id=ids.new_id(voice.id),
                        numerator=numerator,
                        denominator=denominator,
                        events=[e.id for e in output],
                    )
                )
                if start + duration < end:
                    rest = Rest(
                        id=ids.new_id(voice.id),
                        start=start + duration,
                        duration=end - start - duration,
                        provenance=notes[-1].provenance,
                    )
                    output += split_event(rest, rest.start, rest.duration, grid, ids)
                updated[notes[0].id] = output
                for rest in breaths:
                    updated[rest.id] = []
        return [n for e in target for n in updated.get(e.id, [e])]

    result = by_voice(current, plan.selection, transform_span)
    return map_voices(
        result, lambda part, v: v.model_copy(update={"tuplets": v.tuplets + groups.get(v.id, [])})
    )

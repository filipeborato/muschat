"""Material-generating procedures: harmonic series, material orchestration, and part distribution."""

import random
from fractions import Fraction

from muschat.instruments import in_register, instrument
from muschat.plan import Plan
from muschat.procedures.common import DEFAULT_QUARTET, IdGenerator, MeasureGrid, require_score
from muschat.procedures.spectral import partial_spellings, partials
from muschat.score_ir import Event, Measure, Note, Part, Pitch, PitchLetter, Provenance, Rest, ScoreIR, Voice

_SAMPLED_DURATIONS = [Fraction(1, 2), Fraction(3, 8), Fraction(1, 4), Fraction(1, 8)]


def harmonic_series(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Generate a piece over partials 1..n of `fundamental`, quantized and distributed by register in 4/4."""
    pool = partials(p["fundamental"], int(p["partials"]), int(p["microtone"]))
    letters = partial_spellings(p["fundamental"], int(p["partials"]), int(p["microtone"]))
    pitches = [Pitch(midicents=a, spelling=l) for a, l in zip(pool, letters)]
    return _orchestrate(pitches, plan, p, rng, f"Harmonic series on {p['fundamental']:.0f} mc")


def orchestrate_material(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Use the pitches of `current` (e.g. spectral material imported from OpenMusic) as the pitch pool for a new piece
    across the planned instruments within their ranges.
    """
    current = require_score(current)
    pitches = {a.midicents: a for _, _, e in current.events() if isinstance(e, Note) for a in e.pitches}
    if not pitches:
        raise ValueError("orchestrate_material requires notes in the current score")
    return _orchestrate([pitches[a] for a in sorted(pitches)], plan, p, rng, f"{current.title} — orchestrated")


def _orchestrate(
    pool: list[Pitch], plan: Plan, p: dict[str, float], rng: random.Random, title: str
) -> ScoreIR:
    prov = Provenance(source="algorithm")
    parts: list[Part] = []
    for k, name in enumerate(plan.instruments or DEFAULT_QUARTET, 1):
        inst = instrument(name)
        clef, reg = inst.clef, inst.range
        registered: dict[int, PitchLetter | None] = {
            in_register(a.midicents, inst.initial_register or reg): a.spelling for a in pool
        }
        pitches = [Pitch(midicents=a, spelling=registered[a]) for a in sorted(registered)]
        previous: Pitch | None = None
        events: list[Event] = []
        start = Fraction(0)
        for _ in range(int(p["measures"])):
            remaining = Fraction(1)
            while remaining:
                duration = rng.choice([f for f in _SAMPLED_DURATIONS if f <= remaining])
                eid = f"P{k}V1E{len(events) + 1}"
                if rng.random() < p["rests"]:
                    events.append(Rest(id=eid, start=start, duration=duration, provenance=prov))
                else:
                    # shortcut: default maximum leap of one octave; parameterize if another limit is needed.
                    candidates = [
                        a for a in pitches if previous is None or abs(a.midicents - previous.midicents) <= 1200
                    ]
                    previous = rng.choice(candidates)
                    events.append(
                        Note(id=eid, start=start, duration=duration, provenance=prov, pitches=[previous])
                    )
                start, remaining = start + duration, remaining - duration
        parts.append(
            Part(
                id=f"P{k}",
                instrument=name.strip().capitalize(),
                clef=clef,
                range=reg,
                voices=[Voice(id=f"P{k}V1", events=events)],
            )
        )
    # shortcut: always 4/4 at 60 BPM; meter and tempo become parameters when requested
    measures = [
        Measure(numerator=4, denominator=4, tempo=60 if n == 0 else None) for n in range(int(p["measures"]))
    ]
    return ScoreIR(title=title, measures=measures, parts=parts)


def distribute(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Distribute each note of a source part in the selected span to a randomly chosen selected part whose range
    contains it, rewriting the first voice of destination parts in the span and filling gaps with rests.
    """
    current = require_score(current)
    n = int(p["source_part"])
    if n > len(current.parts):
        raise ValueError(f"source_part {n}: score has {len(current.parts)} parts")
    grid, ids, sel = MeasureGrid(current), IdGenerator(current), plan.selection
    first, last = grid.span(sel)
    start_pos, end_pos = grid.starts[first - 1], grid.ends[last - 1]
    source = current.parts[n - 1].voices[0]
    if source.tuplets:
        raise ValueError(f"distribute does not copy tuplets yet ({source.id})")
    material = [e for e in source.events if isinstance(e, Note) and start_pos <= e.start and e.end <= end_pos]
    destinations = [part for part in current.parts if not sel.parts or part.id in sel.parts]
    for part in destinations:
        if part.voices[0].tuplets:
            raise ValueError(f"distribute does not rewrite voices with tuplets yet ({part.voices[0].id})")

    received: dict[str, list[Note]] = {part.id: [] for part in destinations}
    for e in material:
        fitting = [
            d
            for d in destinations
            if d.range is None or all(d.range[0] <= a.midicents <= d.range[1] for a in e.pitches)
        ]
        dest = rng.choice(fitting or destinations)
        pitches = [Pitch(midicents=in_register(a.midicents, dest.range)) for a in e.pitches]
        received[dest.id].append(
            e.model_copy(
                update={"id": ids.new_id(f"{dest.voices[0].id}D"), "pitches": pitches, "glissando": False}
            )
        )

    def rewrite(part: Part) -> Part:
        if part.id not in received:
            return part
        voice = part.voices[0]
        events: list[Event] = [e for e in voice.events if not start_pos <= e.start < end_pos]
        cursor = start_pos
        for e in [*received[part.id], None]:
            until = e.start if e else end_pos
            for s, dur in grid.slices(cursor, until - cursor) if until > cursor else []:
                events.append(
                    Rest(
                        id=ids.new_id(f"{voice.id}D"),
                        start=s,
                        duration=dur,
                        provenance=Provenance(source="algorithm"),
                    )
                )
            if e:
                events.append(e)
                cursor = e.end
        voice = voice.model_copy(update={"events": sorted(events, key=lambda x: x.start)})
        return part.model_copy(update={"voices": [voice, *part.voices[1:]]})

    return current.model_copy(update={"parts": [rewrite(part) for part in current.parts]})

"""Spectral material: harmonic partials of a fundamental, spectral analysis frames (e.g. OM-SuperVP) -> instrumental
events, and score pitches mapped onto harmonic partials while preserving melodic contour.
"""

import math
import random
from fractions import Fraction

from muschat.instruments import in_register, instrument, part_range
from muschat.plan import Plan
from muschat.procedures.common import (
    DEFAULT_QUARTET,
    IdGenerator,
    MeasureGrid,
    by_voice,
    require_score,
    split_event,
)
from muschat.score_ir import Event, Note, Part, Pitch, PitchLetter, Provenance, Rest, ScoreIR, SpectralFrame, Voice, spell_pitch


def partials(fundamental: float, count: int, microtone: int) -> list[int]:
    """Return partials 1..n of `fundamental` (midicents), quantized to semitones, quarter-tones, or eighth-tones (0, 1, 2)."""
    step = 100 // (2**microtone)
    return [round((fundamental + 1200 * math.log2(n)) / step) * step for n in range(1, count + 1)]


# Relative scale degrees: 1..16 conventional; 17..32 by proximity to just-intonation scale degrees.
_DEGREES = (
    0, 0, 4, 0, 2, 4, 6, 0, 1, 2, 3, 4, 5, 6, 6, 0,
    1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 6, 6, 6, 0,
)


def partial_spellings(fundamental: float, count: int, microtone: int) -> list[PitchLetter]:
    """Return pitch letters for partials 1..32 relative to the quantized fundamental's pitch letter."""
    if not 1 <= count <= len(_DEGREES):
        raise ValueError("spectral spelling requires between 1 and 32 partials")
    letters: tuple[PitchLetter, ...] = ("C", "D", "E", "F", "G", "A", "B")
    base = letters.index(spell_pitch(Pitch(midicents=partials(fundamental, 1, microtone)[0]))[0])  # type: ignore[arg-type]
    return [letters[(base + degree) % 7] for degree in _DEGREES[:count]]


def spectralize(current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random) -> ScoreIR:
    """Map selected pitches onto harmonic partials of `fundamental`: each part receives a spectral band (upper parts get
    higher partials, lower parts get lower partials) within its range, and each voice preserves melodic contour with
    bounded leaps. Rhythm, rests, dynamics, and techniques are preserved.
    """
    current = require_score(current)
    pitches = partials(p["fundamental"], int(p["partials"]), int(p["microtone"]))
    letters = partial_spellings(p["fundamental"], int(p["partials"]), int(p["microtone"]))
    pool = sorted(set(pitches))
    spellings = dict(zip(pitches, letters))
    max_leap = int(p["max_leap"])
    sel = plan.selection
    pitched_parts = [
        part
        for part in current.parts
        if (not sel.parts or part.id in sel.parts) and len(set(part_range(part) or (0, 1))) == 2
    ]
    bands = {part.id: _band(pool, part, k, len(pitched_parts)) for k, part in enumerate(pitched_parts)}

    def transform_span(part: Part, voice: Voice, target: list[Event]) -> list[Event]:
        notes = [e for e in target if isinstance(e, Note)]
        if part.id not in bands or not notes:
            return target
        band, fitting = bands[part.id]
        line = _contour([n.pitches[0].midicents for n in notes], band, fitting, max_leap)
        updated = {
            n.id: n.model_copy(
                update={
                    "pitches": [
                        Pitch(midicents=a, spelling=spellings[a]) for a in chord_over(n, top, fitting)
                    ]
                }
            )
            for n, top in zip(notes, line)
        }
        return [updated.get(e.id, e) for e in target]

    return by_voice(current, plan.selection, transform_span)


def _band(pool: list[int], part: Part, k: int, n: int) -> tuple[list[int], list[int]]:
    """Return `(planned_band, fitting_partials)` for part `k` of `n` (top to bottom), overlapping by half."""
    reg = part_range(part)
    fitting = [a for a in pool if reg is None or reg[0] <= a <= reg[1]]
    if not fitting:
        raise ValueError(
            f"no partial fits within the range of {part.instrument} ({part.id}); change fundamental or partials"
        )
    last = max(len(pool) - 1, 1)
    low, high = (n - 1 - k) / (n + 1), (n - k + 1) / (n + 1)
    band = [a for a in fitting if low <= pool.index(a) / last <= high]
    # shortcut: fixed minimum band of 3 partials; make configurable if narrower lines are requested
    if len(band) < 3:
        center = (low + high) / 2 * last
        band = sorted(sorted(fitting, key=lambda a: abs(pool.index(a) - center))[:3])
    return band, fitting


def _contour(original: list[int], band: list[int], fitting: list[int], max_leap: int) -> list[int]:
    """Build a new pitch line in `band` following the directions and step sizes of `original` up to `max_leap`."""
    lo, hi = min(original), max(original)
    position = (original[0] - lo) / (hi - lo) if hi > lo else 0.5
    line = [band[round(position * (len(band) - 1))]]
    for before, after in zip(original, original[1:]):
        cur, step = line[-1], after - before
        if step == 0:
            line.append(cur)
            continue
        target_step = min(abs(step), max_leap)
        for candidate_pool in (band, fitting):
            options = [a for a in candidate_pool if (a - cur) * step > 0 and abs(a - cur) <= max_leap]
            if options:
                line.append(min(options, key=lambda a: (abs(abs(a - cur) - target_step), abs(a - cur))))
                break
        else:
            line.append(cur)
    return line


def chord_over(note: Note, top: int, fitting: list[int]) -> list[int]:
    """Map the first pitch of `note` to `top` and any chordal pitches to the nearestpartials preserving intervals."""
    base = note.pitches[0].midicents
    pitches = [top]
    for a in note.pitches[1:]:
        free = [c for c in fitting if c not in pitches] or fitting
        pitches.append(min(free, key=lambda c: abs(c - (top + a.midicents - base))))
    return pitches


def events_from_spectrum(
    current: ScoreIR | None, plan: Plan, p: dict[str, float], rng: random.Random
) -> ScoreIR:
    """In each spectral frame, convert the strongest partials into quantized pitches distributed from high to low
    across the planned instruments within their ranges; the original spectral part remains intact.
    """
    current = require_score(current)
    frames = sorted((e for _, _, e in current.events() if isinstance(e, SpectralFrame)), key=lambda e: e.start)
    if not frames:
        raise ValueError("events_from_spectrum requires spectral analysis data in the score (e.g. OM-SuperVP)")
    step = 100 // (2 ** int(p["microtone"]))
    names = plan.instruments or DEFAULT_QUARTET
    grid, ids = MeasureGrid(current), IdGenerator(current)
    end = grid.ends[-1]
    used = {part.id for part in current.parts}
    new_parts: list[Part] = []
    for k, name in enumerate(names, 1):
        inst = instrument(name)
        pid = f"E{k}"
        while pid in used:
            pid += "b"
        used.add(pid)
        events: list[Event] = []
        cursor = Fraction(0)
        for frame in frames:
            if frame.start < cursor:
                continue
            if frame.start > cursor:
                events += _rests(ids.new_id(f"{pid}V1E"), cursor, frame.start - cursor, grid, ids)
            strongest = sorted(frame.partials, key=lambda x: -x.amplitude)[: int(p["pitches_per_frame"])]
            pitches = sorted(
                (round((6900 + 1200 * math.log2(x.frequency / 440)) / step) * step for x in strongest),
                reverse=True,
            )
            frame_end = min(frame.end, end)
            if k - 1 < len(pitches) and 0 <= pitches[k - 1] <= 12700:
                note = Note(
                    id=ids.new_id(f"{pid}V1E"),
                    start=frame.start,
                    duration=frame_end - frame.start,
                    provenance=Provenance(source="algorithm"),
                    pitches=[Pitch(midicents=in_register(pitches[k - 1], inst.range))],
                )
                events += split_event(note, frame.start, frame_end - frame.start, grid, ids)
            else:
                events += _rests(ids.new_id(f"{pid}V1E"), frame.start, frame_end - frame.start, grid, ids)
            cursor = frame_end
        if cursor < end:
            events += _rests(ids.new_id(f"{pid}V1E"), cursor, end - cursor, grid, ids)
        new_parts.append(
            Part(
                id=pid,
                instrument=name.strip().capitalize(),
                clef=inst.clef,
                range=inst.range,
                voices=[Voice(id=f"{pid}V1", events=events)],
            )
        )
    return current.model_copy(update={"parts": [*current.parts, *new_parts]})


def _rests(eid: str, start: Fraction, duration: Fraction, grid: MeasureGrid, ids: IdGenerator) -> list[Event]:
    rest = Rest(id=eid, start=start, duration=duration, provenance=Provenance(source="algorithm"))
    return split_event(rest, start, duration, grid, ids)

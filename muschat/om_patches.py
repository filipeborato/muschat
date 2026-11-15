"""Approved OpenMusic patches (`patches/*.lisp`): deterministic input builders (`entrada.txt`) and output importers
(`saida.txt` -> `ScoreIR`). No Lisp is generated at runtime; patches are static files in `patches/`.
"""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from muschat.audio import find_csound as find_csound, render_csound as render_csound
from muschat.spectrum import midicents_to_hz, quantize
from muschat.dynamics import dynamics_by_measure
from muschat.constraints import Constraint, check_constraints
from muschat.instruments import instrument, part_range
from muschat.plan import Plan
from muschat.procedures.common import (
    DEFAULT_QUARTET,
    IdGenerator,
    MeasureGrid,
    by_measure,
    map_voices,
    require_score,
    with_pitches,
)
from muschat.procedures.progressive import density_by_measure
from muschat.procedures.spectral import chord_over, partial_spellings, partials
from muschat.score_ir import (
    Curve,
    ElectronicEvent,
    Event,
    Measure,
    Note,
    Part,
    Partial,
    Pitch,
    Provenance,
    Rest,
    ScoreIR,
    SpectralFrame,
    Voice,
    locate,
    ties_to,
)

NO_SOLUTION = -1.0
PATCH_FAILED = -2.0
MAX_COMPONENTS = 5000  # partials sent to OMChroma in a single task
MAX_CE_ATTACKS = 400
SEARCH_LIMIT = 200_000


@dataclass(frozen=True)
class PatchContext:
    """Context required by patch output readers."""

    parameters: dict[str, float]
    input_text: str
    directory: Path
    current: ScoreIR | None
    plan: Plan | None
    rng: random.Random
    provenance: Provenance


BuildInputFn = Callable[[dict[str, float], ScoreIR | None, Plan | None], str]
ReadOutputFn = Callable[[list[float], PatchContext], ScoreIR]


@dataclass(frozen=True)
class ApprovedPatch:
    procedure: str
    library: str
    om_function: str
    description: str
    input_format: str
    output_format: str
    steps: tuple[str, ...]
    build_input: BuildInputFn
    read_output: ReadOutputFn
    author: str
    file: str | None = None
    validated: bool = False
    executables: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.file and Path(self.file).is_file())


def _check_count(values: list[float], n: int) -> None:
    if len(values) != n:
        raise ValueError(f"output has {len(values)} values; task expected {n} values")


def _steps(filename: str, extra: str = "") -> tuple[str, ...]:
    return (
        f"Extract the entire ZIP into a folder; keep entrada.txt and {filename} together.",
        "Open OpenMusic with a workspace.",
        f"In the Listener, choose Lisp › Load File and select {filename} from the task folder.",
        f"Confirm library loading if prompted. The script reads entrada.txt and writes saida.txt in the same folder "
        f"(and erro.txt if an error occurs).{extra}",
        "Return to Muschat and upload `saida.txt` under 'Waiting for you'.",
    )


def _measures(n: int) -> list[Measure]:
    return [Measure(numerator=4, denominator=4, tempo=60 if k == 0 else None) for k in range(n)]


def material_score(pitches: list[int], *, title: str, provenance: Provenance) -> ScoreIR:
    """Wrap midicent pitches as a quarter-note line in 4/4 measures, padding the final measure with a rest."""
    num_measures = max(1, math.ceil(len(pitches) / 4))
    events: list[Event] = [
        Note(
            id=f"M1V1E{k + 1}",
            start=Fraction(k, 4),
            duration=Fraction(1, 4),
            provenance=provenance,
            pitches=[Pitch(midicents=a)],
        )
        for k, a in enumerate(pitches)
    ]
    remainder = Fraction(num_measures) - Fraction(len(pitches), 4)
    if remainder:
        events.append(
            Rest(
                id=f"M1V1E{len(pitches) + 1}",
                start=Fraction(len(pitches), 4),
                duration=remainder,
                provenance=provenance,
            )
        )
    median = sorted(pitches)[len(pitches) // 2] if pitches else 6000
    part = Part(
        id="M1",
        instrument="Spectral material",
        clef="treble" if median >= 6000 else "bass",
        voices=[Voice(id="M1V1", events=events)],
    )
    return ScoreIR(title=title, measures=_measures(num_measures), parts=[part])


# --- Esquisse: harmonic series ---------------------------------------------------------------------------------------

def _series_input(p: dict[str, float], _current: ScoreIR | None, _plan: Plan | None) -> str:
    return f"{int(p['fundamental'])} 1 1 1 {int(p['partials'])}\n"


def _series_output(values: list[float], ctx: PatchContext) -> ScoreIR:
    _check_count(values, int(ctx.parameters["partials"]))
    if out_of_bounds := [v for v in values if not 0 <= v <= 12700]:
        raise ValueError(f"values outside 0–12700 midicents: {out_of_bounds[:5]}")
    score = material_score(
        [quantize(v, int(ctx.parameters.get("microtone", 1))) for v in values],
        title=f"Harmonic series on {ctx.parameters['fundamental']:.0f} mc (OpenMusic)",
        provenance=ctx.provenance,
    )
    letters = partial_spellings(
        ctx.parameters["fundamental"], len(values), int(ctx.parameters.get("microtone", 1))
    )
    spellings = iter(letters)
    return score.model_copy(update={"parts": [p.model_copy(update={"voices": [
        v.model_copy(update={"events": [
            e.model_copy(update={"pitches": [Pitch(midicents=e.pitches[0].midicents, spelling=next(spellings))]})
            if isinstance(e, Note) else e for e in v.events
        ]}) for v in p.voices
    ]}) for p in score.parts]})



# --- Chaos: Verhulst logistic orbit ----------------------------------------------------------------------------------

def _selection_range(current: ScoreIR | None, plan: Plan | None) -> tuple[ScoreIR, int, int]:
    base = require_score(current)
    if plan is None:
        raise ValueError("Chaos step requires a plan (selection)")
    first, last = MeasureGrid(base).span(plan.selection)
    return base, first, last


def _chaos_input(p: dict[str, float], current: ScoreIR | None, plan: Plan | None) -> str:
    _, first, last = _selection_range(current, plan)
    return f"{p['x0']:g} {p['turbulence']:g} {last - first}\n"


TargetFn = Callable[[int], float]


def _chaos_reader(apply_fn: Callable[[ScoreIR, Plan, TargetFn, random.Random], ScoreIR]) -> ReadOutputFn:
    def read(values: list[float], ctx: PatchContext) -> ScoreIR:
        base, first, last = _selection_range(ctx.current, ctx.plan)
        _check_count(values, last - first + 1)
        if any(not math.isfinite(v) or abs(v) > 1e6 for v in values):
            raise ValueError("chaotic series contains non-finite or out-of-scale values")
        lo, hi, p = min(values), max(values), ctx.parameters

        def target(n: int) -> float:
            t = (values[n - first] - lo) / (hi - lo) if hi > lo else 0.5
            return p["min_value"] + (p["max_value"] - p["min_value"]) * t

        assert ctx.plan is not None
        return apply_fn(base, ctx.plan, target, ctx.rng)

    return read


def _apply_chaos_density(base: ScoreIR, plan: Plan, target: TargetFn, rng: random.Random) -> ScoreIR:
    return density_by_measure(base, plan.selection, target, rng)


def _apply_chaos_register(base: ScoreIR, plan: Plan, target: TargetFn, _rng: random.Random) -> ScoreIR:
    def transform_measure(part: Part, voice: Voice, group: list[Event], n: int) -> list[Event]:
        shift = quantize(target(n), 1)
        return [
            with_pitches(e, [a.midicents + shift for a in e.pitches]) if isinstance(e, Note) else e
            for e in group
        ]

    return by_measure(base, plan.selection, transform_measure)


def _apply_chaos_dynamics(base: ScoreIR, plan: Plan, target: TargetFn, _rng: random.Random) -> ScoreIR:
    return dynamics_by_measure(base, plan.selection, target)


def _apply_chaos_durations(base: ScoreIR, plan: Plan, target: TargetFn, _rng: random.Random) -> ScoreIR:
    ids = IdGenerator(base)

    def transform_measure(part: Part, voice: Voice, group: list[Event], n: int) -> list[Event]:
        notes = [e for e in group if isinstance(e, Note)]
        fig = Fraction(1, 2 ** min(4, max(0, round(target(n)))))
        start, end = group[0].start, group[-1].end
        if not notes or ((end - start) / fig).denominator != 1:
            return group
        count = int((end - start) / fig)
        reused = [e.id for e in group]
        output: list[Event] = []
        for k in range(count):
            src = notes[k % len(notes)]
            ev_id = reused[k] if k < len(reused) else ids.new_id(src.id)
            output.append(
                src.model_copy(
                    update={
                        "id": ev_id,
                        "start": start + k * fig,
                        "duration": fig,
                        "glissando": False,
                        "dynamic": src.dynamic if k == 0 else None,
                        "text": None,
                    }
                )
            )
        return output

    return by_measure(base, plan.selection, transform_measure)


# --- Situation: pitch distribution under constraints -----------------------------------------------------------------

@dataclass(frozen=True)
class _SituationProblem:
    names: list[str]
    pitches: list[int]
    domains: list[list[int]]
    forbidden: list[tuple[int, int, int]]
    constraints: list[Constraint]


def _build_situation_problem(p: dict[str, float], plan: Plan | None) -> _SituationProblem:
    names = list(plan.instruments) if plan and plan.instruments else list(DEFAULT_QUARTET)
    constraints = list(plan.constraints) if plan else []
    k = len(names)
    pitches = [int(p["base"] + i * p["step"]) for i in range(int(p["pitches"]))]
    if pitches[-1] > 12700:
        raise ValueError(f"pitches exceed 12700 midicents ({pitches[-1]})")
    valid_parts = [f"P{j + 1}" for j in range(k)]
    if unknown := sorted({pid for r in constraints for pid in r.parts if pid not in valid_parts}):
        raise ValueError(f"constraints reference parts not present in the new piece: {unknown} (valid: {valid_parts})")

    def applies_to_part(r: Constraint, j: int) -> bool:
        return not r.parts or valid_parts[j] in r.parts

    ranges = [
        [instrument(n).range]
        + [
            (r.low, r.high)
            for r in constraints
            if r.kind == "range" and applies_to_part(r, j) and r.low is not None and r.high is not None
        ]
        for j, n in enumerate(names)
    ]
    domains = [
        [a for a in pitches if all(lo <= a <= hi for lo, hi in ranges[s % k])]
        for s in range(len(pitches))
    ]
    forbidden = [
        (s, s + k, int(r.value))
        for r in constraints
        if r.kind == "forbidden_interval" and r.value
        for s in range(len(pitches) - k)
        if applies_to_part(r, s % k)
    ]
    return _SituationProblem(list(names), pitches, domains, forbidden, constraints)


def _search_situation(pr: _SituationProblem, limit: int = SEARCH_LIMIT) -> list[int] | None | bool:
    n, nodes = len(pr.domains), 0
    neighbors: dict[int, list[tuple[int, int]]] = {}
    for s, t, d in pr.forbidden:
        neighbors.setdefault(s, []).append((t, d))
        neighbors.setdefault(t, []).append((s, d))
    assigned: dict[int, int] = {}

    def fits(s: int, a: int) -> bool:
        return a not in assigned.values() and all(
            t not in assigned or abs(assigned[t] - a) != d for t, d in neighbors.get(s, [])
        )

    def step() -> bool | None:
        nonlocal nodes
        if len(assigned) == n:
            return True
        nodes += 1
        if nodes > limit:
            return None
        free = [s for s in range(n) if s not in assigned]
        s = min(free, key=lambda v: sum(fits(v, a) for a in pr.domains[v]))
        for a in pr.domains[s]:
            if fits(s, a):
                assigned[s] = a
                r = step()
                if r is not False:
                    return r
                del assigned[s]
        return False

    r = step()
    return [assigned[s] for s in range(n)] if r else (False if r is None else None)


def _situation_conditions(pr: _SituationProblem) -> tuple[str, bool]:
    names, pitches, domains = pr.names, pr.pitches, pr.domains
    lines = [f"all {len(pitches)} pitches ({pitches[0]}–{pitches[-1]} mc) used once (no unison)"]
    slots = [[s for s in range(len(pitches)) if s % len(names) == k] for k in range(len(names))]
    for k, name in enumerate(names):
        reach = sorted({a for s in slots[k] for a in domains[s]})
        span = f"{reach[0]}–{reach[-1]} mc" if reach else "no pitches"
        lines.append(f"{name.capitalize()}: {len(slots[k])} slots in range ({span})")
    lines += [
        f"forbidden interval of {d} cents between consecutive notes"
        for d in sorted({d for _, _, d in pr.forbidden})
    ]
    issues: list[str] = []
    culprits: list[int] = []
    for group in sorted((g for g in range(1, 2 ** len(names))), key=lambda g: bin(g).count("1")):
        members = [k for k in range(len(names)) if (group >> k) & 1]
        if any((c & group) == c for c in culprits):
            continue
        n_slots = sum(len(slots[k]) for k in members)
        group_reach = {a for k in members for s in slots[k] for a in domains[s]}
        if len(group_reach) < n_slots:
            culprits.append(group)
            who = " + ".join(names[k].capitalize() for k in members)
            verb = "reach" if len(members) > 1 else "reaches"
            issues.append(f"{who} {verb} {len(group_reach)} pitches for {n_slots} slots")
    if unreachable := [a for a in pitches if not any(a in d for d in domains)]:
        issues.append(f"no instrument reaches {unreachable}")
    if slow := [r.value for r in pr.constraints if r.kind == "max_density" and r.value is not None and r.value < 0.25]:
        issues.append(f"max density {min(slow):g} below 0.25 attacks per quarter note (one note per measure)")
    if not issues and pr.forbidden:
        found = _search_situation(pr)
        if found is None:
            issues.append("forbidden intervals leave no valid ordering")
        elif found is False:
            issues.append(f"could not guarantee a solution within {SEARCH_LIMIT} steps; relax constraints")
    return "; ".join(lines) + (". Impossible: " + "; ".join(issues) if issues else ""), bool(issues)


def _situation_input(p: dict[str, float], _current: ScoreIR | None, plan: Plan | None) -> str:
    pr = _build_situation_problem(p, plan)
    conditions, impossible = _situation_conditions(pr)
    if impossible:
        raise ValueError(f"no solution, nothing sent to OpenMusic. Conditions: {conditions}")
    text = f"{len(pr.pitches)}\n" + "".join(f"{len(d)} {' '.join(map(str, d))}\n" for d in pr.domains)
    if pr.forbidden:
        text += f"{len(pr.forbidden)}\n" + "".join(f"{s} {t} {d}\n" for s, t, d in pr.forbidden)
    return text


def _situation_output(values: list[float], ctx: PatchContext) -> ScoreIR:
    pr = _build_situation_problem(ctx.parameters, ctx.plan)
    names, pitches = pr.names, pr.pitches
    if values == [NO_SOLUTION]:
        raise ValueError(f"Situation found no solution for conditions: {_situation_conditions(pr)[0]}")
    _check_count(values, len(pitches))
    if unexpected := [v for v in values if v not in pitches]:
        raise ValueError(f"solution contains unrequested pitches: {unexpected[:5]}")
    k, chords = len(names), math.ceil(len(pitches) / len(names))
    parts: list[Part] = []
    for j, name in enumerate(names):
        inst = instrument(name)
        events: list[Event] = []
        for c in range(chords):
            s, eid = c * k + j, f"P{j + 1}V1E{c + 1}"
            if s < len(values):
                events.append(
                    Note(
                        id=eid,
                        start=Fraction(c),
                        duration=Fraction(1),
                        provenance=ctx.provenance,
                        pitches=[Pitch(midicents=int(values[s]))],
                    )
                )
            else:
                events.append(Rest(id=eid, start=Fraction(c), duration=Fraction(1), provenance=ctx.provenance))
        parts.append(
            Part(
                id=f"P{j + 1}",
                instrument=name.strip().capitalize(),
                clef=inst.clef,
                range=inst.range,
                voices=[Voice(id=f"P{j + 1}V1", events=events)],
            )
        )
    solution = ScoreIR(title="Constraint distribution (Situation)", measures=_measures(chords), parts=parts)
    violations = [str(v) for v in check_constraints(solution, [Constraint(kind="no_unison"), *pr.constraints])]
    seen: dict[float, int] = {}
    for s, v in enumerate(values):
        if v in seen:
            violations.append(
                f"repeated pitch ({names[s % k]} m.{s // k + 1}): {int(v)} already used in "
                f"{names[seen[v] % k]} m.{seen[v] // k + 1}"
            )
        seen.setdefault(v, s)
    if violations:
        raise ValueError(f"imported solution violates constraints: {'; '.join(violations[:6])}")
    return solution


# --- Cluster Engine: polyphonic voice-leading over fixed rhythm ------------------------------------------------------

@dataclass(frozen=True)
class _ClusterVoice:
    part: Part
    rhythm: list[Fraction]
    chains: list[list[Note]]
    domain: list[int]


def _cluster_voices(p: dict[str, float], current: ScoreIR | None, plan: Plan | None) -> list[_ClusterVoice]:
    base, first, last = _selection_range(current, plan)
    grid, sel = MeasureGrid(base), plan.selection  # type: ignore[union-attr]
    start_pos, end_pos = grid.starts[first - 1], grid.ends[last - 1]
    spectrum = sorted(set(partials(p["fundamental"], int(p["partials"]), int(p["microtone"]))))
    voices: list[_ClusterVoice] = []
    for part in base.parts:
        reg = part_range(part)
        if (sel.parts and part.id not in sel.parts) or (reg and reg[0] == reg[1]):
            continue
        for voice in part.voices:
            if sel.voices and voice.id not in sel.voices:
                continue
            events = [e for e in voice.events if isinstance(e, (Note, Rest)) and start_pos <= e.start < end_pos]
            if not any(isinstance(e, Note) for e in events):
                continue
            rhythm: list[Fraction] = []
            chains: list[list[Note]] = []
            cursor, prev = start_pos, None
            for e in events:
                if e.start > cursor:
                    rhythm.append(-(e.start - cursor))
                if isinstance(e, Note) and isinstance(prev, Note) and ties_to(prev, e):
                    chains[-1].append(e)
                    rhythm[-1] += e.duration
                elif isinstance(e, Note):
                    chains.append([e])
                    rhythm.append(e.duration)
                else:
                    rhythm.append(-e.duration)
                cursor, prev = e.end, e
            domain = [a for a in spectrum if reg is None or reg[0] <= a <= reg[1]]
            if not domain:
                raise ValueError(f"{part.instrument}: no partial within range; nothing sent to OpenMusic")
            voices.append(_ClusterVoice(part, rhythm, chains, domain))
    if not voices:
        raise ValueError("selection contains no notes for Cluster Engine")
    return voices


def _cluster_input(p: dict[str, float], current: ScoreIR | None, plan: Plan | None) -> str:
    voices = _cluster_voices(p, current, plan)
    if p["no_unison"] == 2 and len({a for v in voices for a in v.domain}) < len(voices):
        raise ValueError(f"no_unison requires at least {len(voices)} distinct pitches across voices; nothing sent")
    if (attacks := sum(len(v.chains) for v in voices)) > MAX_CE_ATTACKS:
        raise ValueError(f"{attacks} attacks in selection; limit is {MAX_CE_ATTACKS} (select fewer measures)")
    base, first, last = _selection_range(current, plan)
    m = base.measures[first - 1]
    rules = [int(p[k]) for k in ("max_leap", "no_unison", "no_voice_crossing", "common_tones")]
    variables = max(max(len(v.chains) for v in voices), last - first + 1) + 1
    text = f"{len(voices)} {' '.join(map(str, rules))} {variables}\n"
    text += f"{m.numerator} {m.denominator}\n"
    for v in voices:
        text += f"{len(v.rhythm)} " + " ".join(f"{d.numerator} {d.denominator}" for d in v.rhythm) + "\n"
        text += f"{len(v.domain)} {' '.join(map(str, v.domain))}\n"
    return text


def _cluster_output(values: list[float], ctx: PatchContext) -> ScoreIR:
    if values == [NO_SOLUTION]:
        raise ValueError(
            "no solution: Cluster Engine found no pitches satisfying these rules (relax max_leap or disable a rule)"
        )
    voices = _cluster_voices(ctx.parameters, ctx.current, ctx.plan)
    numbers = [int(x) for x in values]
    if not numbers or numbers[0] != len(voices):
        raise ValueError(f"output announces {numbers[:1]} voices; task had {len(voices)}")
    pos = 1
    updated: dict[str, Note] = {}
    for v in voices:
        if pos >= len(numbers) or len(numbers) < pos + 1 + numbers[pos]:
            raise ValueError(f"{v.part.instrument}: output ended before all announced pitches")
        k, pitches = numbers[pos], numbers[pos + 1 : pos + 1 + numbers[pos]]
        pos += 1 + k
        if k < len(v.chains):
            raise ValueError(f"{v.part.instrument}: solver returned {k} pitches for {len(v.chains)} notes")
        if outside := [a for a in pitches[: len(v.chains)] if a not in v.domain]:
            raise ValueError(f"{v.part.instrument}: {outside[:3]} outside the allowed pitches")
        updated |= {
            n.id: with_pitches(n, chord_over(n, a, v.domain)).model_copy(update={"provenance": ctx.provenance})
            for chain, a in zip(v.chains, pitches)
            for n in chain
        }
    return map_voices(
        require_score(ctx.current),
        lambda _part, voice: voice.model_copy(update={"events": [updated.get(e.id, e) for e in voice.events]}),
    )


# --- OMChroma + om2csound: additive synthesis ------------------------------------------------------------------------

def _seconds_per_whole(current: ScoreIR) -> float:
    bpm = next((m.tempo for m in current.measures if m.tempo), 60)
    return 4 * 60 / bpm


def _selected_notes(current: ScoreIR, plan: Plan | None) -> list[Note]:
    locs = locate(current)
    return sorted(
        (
            e
            for _, _, e in current.events()
            if isinstance(e, Note) and (plan is None or plan.selection.contains(*locs[e.id]))
        ),
        key=lambda e: e.start,
    )


def _partials_at(p: dict[str, float], start: Fraction, total: Fraction) -> int:
    t = float(start / total) if total else 0.0
    return max(1, round(p["initial_partials"] + (p["final_partials"] - p["initial_partials"]) * t))


def components(
    current: ScoreIR, plan: Plan | None, p: dict[str, float]
) -> list[tuple[float, float, float, float]]:
    """Return `(onset_s, duration_s, freq_hz, amp_db)` for each synthesized harmonic partial."""
    notes = _selected_notes(current, plan)
    if not notes:
        raise ValueError("additive synthesis requires notes in the selection")
    sps, total = _seconds_per_whole(current), sum((m.length for m in current.measures), Fraction(0))
    output: list[tuple[float, float, float, float]] = []
    for n in notes:
        k = _partials_at(p, n.start, total)
        for a in n.pitches:
            f0 = midicents_to_hz(a.midicents)
            for j in range(1, k + 1):
                if f0 * j < 20000:
                    output.append(
                        (
                            float(n.start) * sps,
                            float(n.duration) * sps,
                            f0 * j,
                            p["amplitude"] - 6 * math.log2(j),
                        )
                    )
    if len(output) > MAX_COMPONENTS:
        raise ValueError(f"{len(output)} partials exceed limit of {MAX_COMPONENTS}; reduce partials or selection")
    return output


def _chroma_input(p: dict[str, float], current: ScoreIR | None, plan: Plan | None) -> str:
    comps = components(require_score(current), plan, p)
    return f"{len(comps)}\n" + "".join(f"{t:.6f} {d:.6f} {f:.6f} {a:.6f}\n" for t, d, f, a in comps)


def _chroma_output(values: list[float], ctx: PatchContext) -> ScoreIR:
    current = require_score(ctx.current)
    expected = int(ctx.input_text.split(maxsplit=1)[0])
    if values != [float(expected)]:
        raise ValueError(f"output should equal the number of synthesized partials ({expected}); got {values[:3]}")
    notes = _selected_notes(current, ctx.plan)
    total = sum((m.length for m in current.measures), Fraction(0))
    p = ctx.parameters
    ids = IdGenerator(current)
    part_id = ids.new_id("ELE")
    cues: list[Event] = []
    for n, onset in enumerate(sorted({note.start for note in notes})):
        simultaneous = [x for x in notes if x.start == onset]
        desc = (
            f"additive synthesis: {len(simultaneous)} note(s), up to {_partials_at(p, onset, total)} partials "
            "(partials above 20 kHz omitted)"
        )
        cues.append(
            ElectronicEvent(
                id=ids.new_id(f"{part_id}C"),
                start=onset,
                duration=Fraction(0),
                provenance=ctx.provenance,
                description=desc,
            )
        )
    curve = Curve(
        id=ids.new_id(f"{part_id}K"),
        start=Fraction(0),
        duration=total,
        provenance=ctx.provenance,
        parameter="partials per note",
        points=[(Fraction(0), p["initial_partials"]), (total, p["final_partials"])],
    )
    electronics = Part(
        id=part_id,
        instrument="Electronics (additive synthesis)",
        clef="percussion",
        voices=[Voice(id=f"{part_id}V1", events=cues), Voice(id=f"{part_id}V2", events=[curve])],
    )
    return current.model_copy(update={"parts": [*current.parts, electronics]})


# --- OM-SuperVP: spectral sound analysis -----------------------------------------------------------------------------

def _supervp_input(p: dict[str, float], _current: ScoreIR | None, _plan: Plan | None) -> str:
    return f"{int(p['partials'])} {int(p['frames'])}\n"


def _supervp_output(values: list[float], ctx: PatchContext) -> ScoreIR:
    if not values or values[0] != int(values[0]) or len(values) != 1 + 3 * int(values[0]) or values[0] < 1:
        raise ValueError("analysis output must be N followed by N triples (time, frequency, amplitude)")
    triples = [values[1 + 3 * k : 4 + 3 * k] for k in range(int(values[0]))]
    if invalid := [t for t in triples if t[0] < 0 or not 0 < t[1] <= 30000 or t[2] < 0]:
        raise ValueError(f"triples out of range (time >= 0, 0 < Hz <= 30000, amplitude >= 0): {invalid[:3]}")
    frames_by_onset: dict[Fraction, list[Partial]] = {}
    for time_s, freq, amp in triples:
        frames_by_onset.setdefault(Fraction(round(time_s * 4), 16), []).append(Partial(frequency=freq, amplitude=amp))
    starts = sorted(frames_by_onset)
    ends = [*starts[1:], starts[-1] + Fraction(1, 4)]
    events: list[Event] = [
        SpectralFrame(
            id=f"A1V1E{k + 1}",
            start=s,
            duration=e - s,
            provenance=ctx.provenance,
            partials=sorted(frames_by_onset[s], key=lambda q: q.frequency),
        )
        for k, (s, e) in enumerate(zip(starts, ends))
    ]
    part = Part(
        id="A1",
        instrument="Spectral analysis (OM-SuperVP)",
        voices=[Voice(id="A1V1", events=events)],
    )
    return ScoreIR(
        title="Spectral analysis (OM-SuperVP)",
        measures=_measures(max(1, math.ceil(ends[-1]))),
        parts=[part],
    )


PATCHES: dict[str, ApprovedPatch] = {
    p.procedure: p
    for p in [
        ApprovedPatch(
            procedure="harmonic_series",
            library="esquisse",
            om_function="harm-series",
            description="Harmonic series via Esquisse (harm-series, unit = Midics): returns pitches in midicents.",
            input_format="one line: fundamental_midicents numer denom begin end (e.g. 3600 1 1 1 16)",
            output_format="saida.txt: space-separated list of midicents (parentheses accepted)",
            steps=_steps("harmonic_series.lisp", " Uses Midics (1) and Chord (1)."),
            build_input=_series_input,
            read_output=_series_output,
            author="OpenMusic/Esquisse",
            file="patches/harmonic_series.lisp",
            validated=True,
        ),
        *[
            ApprovedPatch(
                procedure=name,
                library="chaos",
                om_function="Verhulst",
                description=(
                    f"Verhulst logistic map (Chaos): one value per selected measure, normalized and mapped to {target} "
                    "between min_value and max_value."
                ),
                input_format="one line: x0 lambda iterations (iterations = measures - 1)",
                output_format="saida.txt: series values (iterations + 1), space-separated",
                steps=_steps("chaos_verhulst.lisp"),
                build_input=_chaos_input,
                read_output=_chaos_reader(apply_fn),
                author="OpenMusic/Chaos",
                file="patches/chaos_verhulst.lisp",
                validated=True,
            )
            for name, target, apply_fn in [
                ("chaotic_density", "attack density", _apply_chaos_density),
                ("chaotic_register", "register shift", _apply_chaos_register),
                ("chaotic_dynamics", "dynamics", _apply_chaos_dynamics),
                ("chaotic_durations", "rhythmic figure", _apply_chaos_durations),
            ]
        ],
        ApprovedPatch(
            procedure="distribute_by_constraints",
            library="situation",
            om_function="Csolver + generic-cnstr",
            description=(
                "Situation (Csolver with per-variable domains): each instrument slot receives a pitch within its "
                "range, all distinct (no unison, each pitch used once)."
            ),
            input_format="N; then one line per slot: domain size followed by candidate pitches (midicents)",
            output_format="saida.txt: N pitches in slot order, or -1 if no solution exists",
            steps=_steps("distribute_by_constraints.lisp"),
            build_input=_situation_input,
            read_output=_situation_output,
            author="OpenMusic/Situation",
            file="patches/distribute_by_constraints.lisp",
            validated=True,
        ),
        ApprovedPatch(
            procedure="additive_synthesis",
            library="omchroma",
            om_function="add-1 + synthesize (:run nil)",
            description=(
                "OMChroma: each note becomes additive-synthesis partials (class add-1); OMChroma writes "
                "muschat.orc/muschat.sco and Muschat renders audio with the detected Csound."
            ),
            input_format="N; then N lines: onset (s) duration (s) frequency (Hz) amplitude (dB)",
            output_format="saida.txt: N (written partials); muschat.orc and muschat.sco in the task directory",
            steps=_steps("additive_synthesis.lisp", " Also writes muschat.orc and muschat.sco."),
            build_input=_chroma_input,
            read_output=_chroma_output,
            author="OpenMusic/OMChroma",
            file="patches/additive_synthesis.lisp",
            validated=True,
        ),
        ApprovedPatch(
            procedure="supervp_analysis",
            library="om-supervp",
            om_function="supervp (partial analysis)",
            description=(
                "OM-SuperVP: partial analysis of a sound selected in OpenMusic; result is imported as SpectralFrame "
                "data, never as fake notes."
            ),
            input_format="one line: partials frames",
            output_format="saida.txt: N followed by N triples time (s) frequency (Hz) amplitude",
            steps=_steps("supervp_analysis.lisp"),
            build_input=_supervp_input,
            read_output=_supervp_output,
            author="OpenMusic/OM-SuperVP",
            executables=("supervp",),
        ),
        ApprovedPatch(
            procedure="pitches_by_constraints",
            library="cluster-engine",
            om_function="ClusterEngine + R-pitches-one-voice, R-pitch-pitch, HR-pitches-one-voice",
            description=(
                "Cluster Engine (Sandred; OM port by P. Raposo): preserves each voice's rhythm in the selection and "
                "solves for harmonic-partial pitches with bounded leaps, no unison, no voice crossing, and common tones."
            ),
            input_format="V S U C H M; NUM DEN; per voice: E and rhythm pairs n d (n < 0 = rest); D and domain pitches",
            output_format="saida.txt: V and per voice k followed by k note pitches; -1 if no solution exists",
            steps=_steps("pitches_by_constraints.lisp", " Search may take a few seconds."),
            build_input=_cluster_input,
            read_output=_cluster_output,
            author="OpenMusic/Cluster Engine",
            file="patches/pitches_by_constraints.lisp",
            validated=True,
        ),
    ]
}

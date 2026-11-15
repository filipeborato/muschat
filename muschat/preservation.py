"""In-memory structural validation and selection/provenance preservation."""

from bisect import bisect_right
from fractions import Fraction
from pydantic import ValidationError
from muschat.errors import RefusalError
from muschat.plan import Plan
from muschat.procedures import CATALOG, Procedure
from muschat.score_ir import (
    Note,
    Rest,
    Provenance,
    ScoreIR,
    Source,
    diff_scores,
    locate,
    ties_to,
    is_figure,
)


def verify_step(
    plan: Plan,
    before: ScoreIR | None,
    after: ScoreIR,
    *,
    source: Source,
    operation: str,
) -> ScoreIR:
    """Re-validate the step's ScoreIR, check the preservation invariant against `before`, and stamp provenance."""
    after = validate_ir(after)
    if before is None:
        return stamp_provenance(
            after, {e.id for _, _, e in after.events()}, source, operation
        )
    diff = diff_scores(before, after)
    loc_before, loc_after = locate(before), locate(after)
    for part in before.parts:
        for voice in part.voices:
            if diff.modified.get(voice.id) != ["tuplets"]:
                continue
            after_voice = next(
                v for p in after.parts for v in p.voices if v.id == voice.id
            )
            a, b = (
                {t.id: t for t in voice.tuplets},
                {t.id: t for t in after_voice.tuplets},
            )
            changed = {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}
            while (
                children := {
                    t.id for t in [*a.values(), *b.values()] if t.parent in changed
                }
                - changed
            ):
                changed |= children
            members = {
                i
                for key in changed
                for t in (a.get(key), b.get(key))
                if t
                for i in t.events
            }
            if all(
                plan.selection.contains(*loc)
                for i in members
                for loc in (loc_before.get(i), loc_after.get(i))
                if loc
            ):
                del diff.modified[voice.id]
    outside = sorted(
        {
            i
            for i in [*diff.added, *diff.removed, *diff.modified]
            for loc in (loc_before.get(i), loc_after.get(i))
            if loc and not plan.selection.contains(*loc)
        }
    )
    header_changed = (before.title, before.composer) != (after.title, after.composer)
    if outside or ((diff.measures or header_changed) and not plan.selection.is_full):
        where = outside or (
            f"measures {diff.measures}" if diff.measures else "title/composer"
        )
        raise RefusalError(
            f"procedure modified material outside the selection: {where}."
        )
    return stamp_provenance(
        after, set(diff.added) | set(diff.modified), source, operation
    )


def validate_ir(ir: ScoreIR) -> ScoreIR:
    """Re-validate ScoreIR (`model_copy` does not re-validate) and check structural invariants in memory."""
    try:
        ir = ScoreIR.model_validate(ir.model_dump())
        _check_structure(ir)
    except ValidationError as exc:
        raise RefusalError(
            exc.errors(include_input=False, include_url=False)[0]["msg"]
        ) from exc
    except ValueError as exc:
        raise RefusalError((str(exc) or type(exc).__name__).splitlines()[0]) from exc
    return ir


def stamp_provenance(
    ir: ScoreIR,
    ids: set[str],
    source: Source,
    operation: str,
    author: str | None = None,
) -> ScoreIR:
    """Stamp engine provenance + operation ID on created or modified events (preserving OpenMusic author)."""

    def prov(current_prov: Provenance) -> Provenance:
        return Provenance(
            source=source,
            operation=operation,
            author=author
            or (current_prov.author if current_prov.source == source else None),
        )

    return ir.model_copy(
        update={
            "parts": [
                p.model_copy(
                    update={
                        "voices": [
                            v.model_copy(
                                update={
                                    "events": [
                                        e.model_copy(
                                            update={"provenance": prov(e.provenance)}
                                        )
                                        if e.id in ids
                                        else e
                                        for e in v.events
                                    ]
                                }
                            )
                            for v in p.voices
                        ]
                    }
                )
                for p in ir.parts
            ]
        }
    )


def validate_selection(
    plan: Plan, current: ScoreIR | None, catalog: dict[str, Procedure] = CATALOG
) -> None:
    sel = plan.selection
    if current is None:
        return
    parts = {p.id for p in current.parts}
    creates_piece = any(
        op.procedure in catalog and not catalog[op.procedure].requires_score
        for op in plan.operations
    )
    if not creates_piece and (
        outside := sorted(
            {i for r in plan.constraints for i in r.parts if i not in parts}
        )
    ):
        raise RefusalError(
            f"constraints reference parts that do not exist in the composition: {outside}."
        )
    if sel.is_full:
        return
    voices = {v.id for p in current.parts for v in p.voices}
    if missing := [i for i in sel.parts if i not in parts] + [
        i for i in sel.voices if i not in voices
    ]:
        raise RefusalError(
            f"selection references IDs that do not exist in the composition: {missing}."
        )
    start, end = sel.start_measure or 1, sel.end_measure or len(current.measures)
    if not 1 <= start <= end <= len(current.measures):
        raise RefusalError(
            f"measures {start}–{end} outside the composition (1–{len(current.measures)})."
        )


def _check_structure(ir: ScoreIR) -> None:
    starts = ir.measure_starts()
    total = starts[-1] + ir.measures[-1].length
    if any(m.length <= 0 for m in ir.measures):
        raise ValueError("measure duration must be positive")
    for part in ir.parts:
        for voice in part.voices:
            groups = {t.id: t for t in voice.tuplets}
            notated_ids = [e.id for e in voice.events if isinstance(e, (Note, Rest))]
            positions = {event_id: i for i, event_id in enumerate(notated_ids)}
            for tuplet in voice.tuplets:
                descendants = {tuplet.id}
                while (
                    children := {t.id for t in voice.tuplets if t.parent in descendants}
                    - descendants
                ):
                    descendants |= children
                members = {
                    event_id
                    for t in voice.tuplets
                    if t.id in descendants
                    for event_id in t.events
                }
                indices = sorted(
                    positions[event_id] for event_id in members if event_id in positions
                )
                if len(indices) != len(members) or indices != list(
                    range(indices[0], indices[-1] + 1)
                ):
                    raise ValueError(
                        f"tuplet {tuplet.id} must contain contiguous notated events"
                    )
            ratios: dict[str, Fraction] = {}
            for tuplet in voice.tuplets:
                ratio = Fraction(tuplet.numerator, tuplet.denominator)
                parent = tuplet.parent
                while parent is not None:
                    group = groups[parent]
                    ratio *= Fraction(group.numerator, group.denominator)
                    parent = group.parent
                for event_id in tuplet.events:
                    ratios[event_id] = ratio
            for i, event in enumerate(voice.events):
                if event.end > total:
                    raise ValueError(f"event {event.id} extends after the last measure")
                if isinstance(event, (Note, Rest)):
                    measure = bisect_right(starts, event.start) - 1
                    end = starts[measure] + ir.measures[measure].length
                    if event.start >= total or event.end > end:
                        raise ValueError(
                            f"event {event.id} crosses the barline; split it with a tie"
                        )
                    if not is_figure(
                        event.duration * ratios.get(event.id, Fraction(1))
                    ):
                        raise ValueError(
                            f"event {event.id} has an invalid notated duration"
                        )
                if isinstance(event, Note) and event.tied:
                    next_event = (
                        voice.events[i + 1] if i + 1 < len(voice.events) else None
                    )
                    if not ties_to(event, next_event):
                        raise ValueError(f"invalid tie on event {event.id}")

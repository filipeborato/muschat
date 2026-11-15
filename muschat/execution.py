"""Uniform Python, NotaGen, and assisted OpenMusic step execution."""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from muschat.composition import PendingTask, OmTask
from muschat.errors import RefusalError
from muschat.instruments import instrument as get_instrument
from muschat.notagen import ComposerBackend
from muschat.notation.musicxml import from_musicxml
from muschat.notation.techniques import fits_instrument
from muschat.openmusic import create_task, import_output
from muschat.preservation import verify_step
from muschat.procedures import Procedure
from muschat.score_ir import Note, Part, Provenance, ScoreIR
from muschat.security import sanitize


@dataclass(frozen=True)
class StepCompleted:
    ir: ScoreIR
    external_output: str | None = None
    warnings: list[str] = field(default_factory=list)
    post_processors: list[Callable[[Path], dict[str, Any]]] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class StepPaused:
    task: OmTask


def execute_step(
    ctx: PendingTask,
    k: int,
    current: ScoreIR | None,
    catalog: dict[str, Procedure],
    tasks_dir: Path,
    composer: ComposerBackend | None,
    *,
    output: bytes | str | None = None,
) -> StepCompleted | StepPaused:
    step = ctx.decision.steps[k]
    target = ctx.plan.for_step(k)
    if step.engine == "notagen":
        return _notagen_step(ctx, k, current, composer)
    try:
        if step.engine == "openmusic":
            if output is None:
                return StepPaused(
                    create_task(
                        step.procedure,
                        ctx.parameters[k],
                        root=tasks_dir,
                        current=current,
                        plan=target,
                    )
                )
            if ctx.task is None:
                raise RefusalError("OpenMusic output requires a pending task")
            material = import_output(
                ctx.task,
                output,
                operation=ctx.operation,
                current=current,
                plan=target,
                rng=random.Random(f"{ctx.seed}/{k}"),
            )
            processors: list[Callable[[Path], dict[str, Any]]] = []
            if step.procedure == "additive_synthesis":
                from muschat.audio import synthesize_audio

                processors.append(synthesize_audio)
            return StepCompleted(
                verify_step(
                    target,
                    current,
                    material,
                    source="openmusic",
                    operation=ctx.operation,
                ),
                post_processors=processors,
            )
        fn = catalog[step.procedure].fn
        if fn is None:
            raise RefusalError(f"{step.procedure} has no Python implementation")
        material = fn(
            current, target, ctx.parameters[k], random.Random(f"{ctx.seed}/{k}")
        )
        return StepCompleted(
            verify_step(
                target, current, material, source="algorithm", operation=ctx.operation
            )
        )
    except OSError as exc:
        raise RefusalError(
            f"step {k + 1} ({step.procedure}, {step.engine}): filesystem operation failed ({exc})"
        ) from exc
    except ValueError as exc:
        raise RefusalError(
            f"step {k + 1} ({step.procedure}): {_first_line(exc)}"
        ) from exc


def _notagen_step(
    ctx: PendingTask,
    k: int,
    current: ScoreIR | None,
    composer: ComposerBackend | None,
) -> StepCompleted:
    """Read NotaGen output and adapt the requested instrumentation before the contemporary layer."""
    op = ctx.plan.operations[k]
    target = ctx.plan.for_step(k)
    requested_measures = int(ctx.parameters[k]["measures"])
    xml = ctx.external_outputs.get(k)
    if xml is None:
        if composer is None:
            raise RefusalError(
                "NotaGen is not enabled in this session (MUSCHAT_NOTAGEN=1; see README)."
            )
        try:
            xml = composer.generate(
                op.condition,
                requested_measures,
                random.Random(f"{ctx.seed}/{k}").randrange(2**31),
            ).decode("utf-8")
        except ValueError as exc:
            raise RefusalError(
                f"step {k + 1} ({op.procedure}): {_first_line(exc)}"
            ) from exc
    try:
        ir, warnings = from_musicxml(
            xml.encode("utf-8"), Provenance(source="external", author="NotaGen-X")
        )
    except ValueError as exc:
        raise RefusalError(
            f"step {k + 1} ({op.procedure}): unreadable output: {_first_line(exc)}"
        ) from exc
    if len(ir.measures) < requested_measures:
        warnings.append(
            f"NotaGen wrote {len(ir.measures)} of {requested_measures} measures"
        )
    adapted = False
    if len(ctx.plan.instruments) == len(ir.parts):
        ir = ir.model_copy(
            update={
                "parts": [
                    _with_instrument(p, name)
                    for p, name in zip(ir.parts, ctx.plan.instruments)
                ]
            }
        )
    elif ctx.plan.instruments:
        from muschat.orchestration import adapt_instrumentation

        try:
            ir, warning = adapt_instrumentation(ir, ctx.plan.instruments)
        except ValueError as exc:
            raise RefusalError(str(exc)) from exc
        warnings.append(warning)
        adapted = True
    if op.section:
        from muschat.sections import insert_section

        try:
            ir, current = insert_section(
                current, ir, target.selection, ctx.plan.sections
            )
        except ValueError as exc:
            raise RefusalError(str(exc)) from exc
    updated = verify_step(
        target,
        current,
        ir,
        source="algorithm" if adapted else "external",
        operation=ctx.operation,
    )
    return StepCompleted(
        updated, external_output=xml, warnings=[sanitize(w) for w in warnings]
    )


def _with_instrument(part: Part, name: str) -> Part:
    """Rename a NotaGen part (and attach its range when known) to the instrument requested in the plan."""
    try:
        inst = get_instrument(name)
    except ValueError:
        return part.model_copy(update={"instrument": name.strip().capitalize()})
    return part.model_copy(
        update={
            "instrument": name.strip().capitalize(),
            "range": inst.range,
            "clef": inst.clef,
            "voices": [
                v.model_copy(
                    update={
                        "events": [
                            e.model_copy(
                                update={
                                    "techniques": [
                                        t
                                        for t in e.techniques
                                        if fits_instrument(t, name)
                                    ]
                                }
                            )
                            if isinstance(e, Note)
                            else e
                            for e in v.events
                        ]
                    }
                )
                for v in part.voices
            ],
        }
    )


def _first_line(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]

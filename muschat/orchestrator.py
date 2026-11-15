"""Composition turn: request + Composition + mode -> validated Plan -> router -> step pipeline -> new Version,
or assisted step awaiting the composer, or explained refusal.

Each step runs in order; after each step ScoreIR is re-validated, the preservation invariant is checked against the
previous step, and created/modified events receive engine provenance. An OpenMusic step pauses the pipeline
(PendingStep on Composition); `resume` imports its output and continues from there. Failure at any step produces no
partial Version.
"""

import platform
import random
import shutil
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING


from muschat.execution import StepCompleted, StepPaused, execute_step
from muschat.errors import RefusalError
from muschat.planning import (
    plan_composition,
    resolve_parameters,
    validate_conditions,
    normalize_plan,
    summarize_score,
)
from muschat.preservation import validate_ir, stamp_provenance, validate_selection
from muschat.composition import Composition, PendingTask, Version
from muschat.constraints import check_constraints
from muschat.notagen import ComposerBackend
from muschat.notation.musicxml import from_musicxml, to_musicxml
from muschat.notation.techniques import unplayable_techniques
from muschat.openmusic import TASKS_DIR, OmTask
from muschat.plan import Parameter, Plan
from muschat.procedures import CATALOG, Procedure
from muschat.registry import ENGINE_NAME, Registry
from muschat.router import MODES, Mode, NoRouteError, route
from muschat.score_ir import Note, ScoreIR, Source
from muschat.security import sanitize

if TYPE_CHECKING:
    from muschat.reasoning import ChatSession, Generator

__all__ = [
    "RefusalError",
    "TurnResult",
    "compose",
    "discard_task",
    "open_musicxml",
    "open_score",
    "discard",
    "reapply",
    "resume",
    "runtime_dependencies",
    "summarize_score",
]


@dataclass(frozen=True)
class TurnResult:
    summary: str  # what was done, what awaits the composer, or why nothing was done (passed to synthesis)
    version: Version | None  # None = no new Version (refusal or step awaiting composer)
    task: OmTask | None = None  # assisted step awaiting composer


def compose(
    request: str,
    composition: Composition,
    *,
    generator: "Generator",
    chat: "ChatSession",
    model: str,
    mode: Mode = "auto",
    registry: Registry | None = None,
    catalog: dict[str, Procedure] = CATALOG,
    tasks_dir: Path = TASKS_DIR,
    composer: ComposerBackend | None = None,
) -> TurnResult:
    registry = registry or Registry.python_only(_native_procedures(catalog))
    if composition.pending:
        return _awaiting(composition.pending)
    current = composition.current.ir if composition.current else None
    try:
        plan_sections = (
            composition.current.experiment.get("plan", {}).get("sections", [])
            if composition.current
            else []
        )
        plan = plan_composition(
            request,
            current,
            generator=generator,
            chat=chat,
            model=model,
            catalog=catalog,
            registry=registry,
            mode=mode,
            sections=plan_sections,
        )
    except RefusalError as exc:
        return _refusal(exc)
    return _apply_plan(
        plan,
        composition,
        composition.index,
        request=request,
        model=model,
        mode=mode,
        registry=registry,
        catalog=catalog,
        tasks_dir=tasks_dir,
        composer=composer,
    )


def reapply(
    composition: Composition,
    parameters: list[dict[str, float]],
    *,
    registry: Registry | None = None,
    catalog: dict[str, Procedure] = CATALOG,
    tasks_dir: Path = TASKS_DIR,
    composer: ComposerBackend | None = None,
) -> TurnResult:
    """Re-run the current Version's operation from the same base Version, with the same seed and mode,
    swapping only the parameters (the composer adjusted the operational hypothesis). Does not call the LLM."""
    ver = composition.current
    if ver is None:
        return _refusal(RefusalError("there is no operation to reapply."))
    if composition.pending:
        return _awaiting(composition.pending)
    if not ver.reapplicable:
        return _refusal(
            RefusalError(
                "this Version was imported from a file; there is no operation to reapply."
            )
        )
    plan = Plan.model_validate(ver.experiment["plan"])
    plan = plan.model_copy(
        update={
            "seed": ver.experiment["seed"],
            "operations": [
                op.model_copy(
                    update={
                        "parameters": [
                            Parameter(name=k, value=v) for k, v in new_params.items()
                        ]
                    }
                )
                for op, new_params in zip(plan.operations, parameters, strict=True)
            ],
        }
    )
    reuse = {
        int(k): xml
        for k, xml in ver.experiment.get("external_outputs", {}).items()
        if parameters[int(k)] == ver.experiment["parameters"][int(k)]
    }
    return _apply_plan(
        plan,
        composition,
        ver.previous,
        request=f"{ver.experiment.get('prompt', '')} (reapplied)",
        model=ver.experiment.get("model", ""),
        mode=ver.experiment.get("mode", "auto"),
        registry=registry or Registry.python_only(_native_procedures(catalog)),
        catalog=catalog,
        tasks_dir=tasks_dir,
        composer=composer,
        reuse=reuse,
        plan_expanded=ver.experiment.plan_expanded,
    )


def resume(
    composition: Composition,
    output: bytes | str,
    *,
    catalog: dict[str, Procedure] = CATALOG,
    tasks_dir: Path = TASKS_DIR,
    composer: ComposerBackend | None = None,
) -> TurnResult:
    """Import the output of the OpenMusic step awaiting the composer and continue the remaining steps."""
    p = composition.pending
    if p is None or p.task is None:
        return _refusal(RefusalError("no OpenMusic step is awaiting output."))
    try:
        validate_selection(p.plan.for_step(p.step), p.current, catalog)
        result = execute_step(
            p, p.step, p.current, catalog, tasks_dir, composer, output=output
        )
        assert isinstance(result, StepCompleted)
    except (ValueError, RefusalError) as exc:
        return TurnResult(
            f"OpenMusic output was rejected and the task is still awaiting you: {exc}.",
            None,
            p.task,
        )
    composition.pending = None
    try:
        ctx = _completed_context(p, p.step, result)
        ctx = ctx.model_copy(
            update={
                "step": p.step + 1,
                "task": None,
                "intermediates": [*p.intermediates, result.ir],
            }
        )
        return _continue_pipeline(
            ctx, result.ir, composition, catalog, tasks_dir, composer
        )
    except RefusalError as exc:
        return _refusal(exc)


def open_musicxml(
    composition: Composition,
    data: bytes,
    name: str,
    *,
    source: Source = "composer",
    author: str | None = None,
) -> TurnResult:
    """Import a MusicXML file (`.musicxml`, `.xml`, or `.mxl`) as a new Version with provenance."""
    if composition.pending:
        return _awaiting(composition.pending)
    name = sanitize(name)
    try:
        ir, warnings = from_musicxml(data)
        operation = f"op{len(composition.versions) + 1}"
        ir = validate_ir(ir)
        to_musicxml(ir)
        ir = stamp_provenance(
            ir, {e.id for _, _, e in ir.events()}, source, operation, author
        )
    except (ValueError, RefusalError) as exc:
        return _refusal(RefusalError(f"{name}: {_first_line(exc)}"))
    warnings = [sanitize(w) for w in warnings]
    summary = (
        f"Version {len(composition.versions) + 1}: opened {name}; {len(ir.parts)} parts, {len(ir.measures)} measures"
        + (f"; discarded on import: {'; '.join(warnings)}" if warnings else "")
    )
    ver = composition.add(
        ir,
        summary,
        {
            "prompt": f"open {name}",
            "operation": operation,
            "file": name,
            "warnings": warnings,
            "dependencies": runtime_dependencies(),
        },
        previous=composition.index,
    )
    return TurnResult(summary, ver)


def discard_task(composition: Composition) -> None:
    """Abandon the pending assisted step and delete its task working directory."""
    if composition.pending and composition.pending.task:
        shutil.rmtree(composition.pending.task.directory, ignore_errors=True)
    composition.pending = None


def _refusal(exc: Exception) -> TurnResult:
    return TurnResult(
        f"Operation refused; the composition was not changed. Reason: {exc}", None
    )


def _awaiting(p: PendingTask) -> TurnResult:
    step = p.decision.steps[p.step]
    return TurnResult(
        f"Awaiting you: step {p.step + 1} of {len(p.decision.steps)} ({step.procedure} in OpenMusic) "
        "requires patch output before any other operation; submit the file or discard the task.",
        None,
        p.task,
    )


def _apply_plan(
    plan: Plan,
    composition: Composition,
    base: int | None,
    *,
    request: str,
    model: str,
    mode: Mode,
    registry: Registry,
    catalog: dict[str, Procedure],
    tasks_dir: Path,
    composer: ComposerBackend | None,
    reuse: dict[int, str] | None = None,
    plan_expanded: bool = False,
) -> TurnResult:
    current = None if base is None else composition.versions[base].ir
    try:
        plan = normalize_plan(plan, mode, catalog, current, plan_expanded=plan_expanded)
        try:
            decision = route(plan, mode, registry)
        except NoRouteError as exc:
            raise RefusalError(str(exc)) from exc
        parameters, exists = [], current is not None
        for k, op in enumerate(plan.operations):
            if op.section and op.procedure == "generate_notagen":
                try:
                    sel = plan.for_step(k).selection
                except ValueError as exc:
                    raise RefusalError(str(exc)) from exc
                assert sel.end_measure is not None and sel.start_measure is not None
                op = op.model_copy(
                    update={
                        "parameters": [p for p in op.parameters if p.name != "measures"]
                        + [
                            Parameter(
                                name="measures",
                                value=sel.end_measure - sel.start_measure + 1,
                            )
                        ]
                    }
                )
            parameters.append(resolve_parameters(op, catalog, exists))
            exists = exists or not catalog[op.procedure].requires_score
        for k in range(len(plan.operations)):
            try:
                target = plan.for_step(k)
            except ValueError as exc:
                raise RefusalError(str(exc)) from exc
            validate_selection(target, current, catalog)
        validate_conditions(plan, decision.steps, registry)
        ctx = PendingTask(
            prompt=request,
            model=model,
            plan=plan,
            decision=decision,
            parameters=parameters,
            seed=plan.seed if plan.seed is not None else random.randrange(2**31),
            operation=f"op{len(composition.versions) + 1}",
            base=base,
            step=0,
            current=current,
            intermediates=[],
            external_outputs=reuse or {},
        )
        return _continue_pipeline(
            ctx, current, composition, catalog, tasks_dir, composer
        )
    except RefusalError as exc:
        return _refusal(exc)


def _continue_pipeline(
    ctx: PendingTask,
    current: ScoreIR | None,
    composition: Composition,
    catalog: dict[str, Procedure],
    tasks_dir: Path,
    composer: ComposerBackend | None,
) -> TurnResult:
    intermediates = list(ctx.intermediates)
    for k in range(ctx.step, len(ctx.decision.steps)):
        result = execute_step(ctx, k, current, catalog, tasks_dir, composer)
        if isinstance(result, StepPaused):
            composition.pending = ctx.model_copy(
                update={
                    "step": k,
                    "current": current,
                    "task": result.task,
                    "intermediates": intermediates,
                }
            )
            return _awaiting(composition.pending)
        current = result.ir
        ctx = _completed_context(ctx, k, result)
        intermediates.append(current)
    if current is None:
        raise RefusalError("the plan produced no composition")
    return _record_version(
        ctx.model_copy(update={"intermediates": intermediates}),
        current,
        composition,
        catalog,
    )


def _record_version(
    ctx: PendingTask,
    updated: ScoreIR,
    composition: Composition,
    catalog: dict[str, Procedure],
) -> TurnResult:
    try:
        to_musicxml(updated)
    except ValueError as exc:
        raise RefusalError(_first_line(exc)) from exc
    violations = [str(v) for v in check_constraints(updated, ctx.plan.constraints)]
    violations += [f"unplayable technique: {t}" for t in unplayable_techniques(updated)]
    steps_desc = ", ".join(
        f"{s.procedure}[{ENGINE_NAME[s.engine]}]({_format_params(p, catalog[s.procedure])})"
        for s, p in zip(ctx.decision.steps, ctx.parameters)
    )
    plan = ctx.plan
    silent_parts = [
        f"{p.id} ({p.instrument})"
        for p in updated.parts
        if not any(isinstance(e, Note) for v in p.voices for e in v.events)
    ]
    summary = (
        f"Version {len(composition.versions) + 1}: {steps_desc}, seed {ctx.seed}, mode "
        f"{MODES[ctx.decision.mode]}; {len(updated.parts)} parts, {len(updated.measures)} measures"
        + (
            ""
            if plan.selection.is_full
            else f"; selection {plan.selection.model_dump(exclude_defaults=True)}"
        )
        + (f"; silent parts: {', '.join(silent_parts)}" if silent_parts else "")
        + "".join(f"; hypothesis: {sanitize(h)}" for h in plan.hypotheses)
        + (
            f"; violated constraints ({len(violations)}): {'; '.join(violations[:5])}"
            if violations
            else "; constraints satisfied"
        )
        + (f"; warnings: {'; '.join(ctx.warnings)}" if ctx.warnings else "")
        + f". Engine choice: {' '.join(ctx.decision.rationale)}"
    )
    ver = composition.add(
        updated,
        summary,
        {
            "prompt": ctx.prompt,
            "model": ctx.model,
            "mode": ctx.decision.mode,
            "plan": plan.model_dump(mode="json"),
            "plan_expanded": True,
            "decision": ctx.decision.model_dump(mode="json"),
            "operation": ctx.operation,
            "procedures": [op.procedure for op in plan.operations],
            "parameters": ctx.parameters,
            "seed": ctx.seed,
            "violations": violations,
            "dependencies": runtime_dependencies(),
            "steps": [{**s.model_dump(), "state": "done"} for s in ctx.decision.steps],
            "intermediates": [ir.model_dump(mode="json") for ir in ctx.intermediates],
            "external_outputs": {
                str(k): xml for k, xml in ctx.external_outputs.items()
            },
            "warnings": ctx.warnings,
            "audio": ctx.audio,
        },
        previous=ctx.base,
    )
    return TurnResult(summary, ver)


def runtime_dependencies() -> dict[str, str | None]:
    """Versions of Python packages that generate and engrave music (provenance for each Version)."""

    def pkg_version(pkg: str) -> str | None:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return None

    return {
        "python": platform.python_version(),
        **{p: pkg_version(p) for p in ("abjad", "verovio", "google-genai")},
    }


def _native_procedures(catalog: dict[str, Procedure]) -> tuple[str, ...]:
    return tuple(n for n, p in catalog.items() if p.fn is not None)


def _first_line(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]


def _format_params(parameters: dict[str, float], proc: Procedure) -> str:
    return ", ".join(
        f"{k}={v:g} {proc.parameters[k].unit}".rstrip() for k, v in parameters.items()
    )


def _completed_context(ctx: PendingTask, k: int, result: StepCompleted) -> PendingTask:
    audio = ctx.audio
    if result.post_processors:
        assert ctx.task is not None
        for processor in result.post_processors:
            audio = processor(Path(ctx.task.directory))
    return ctx.model_copy(
        update={
            "external_outputs": {
                **ctx.external_outputs,
                **(
                    {k: result.external_output}
                    if result.external_output is not None
                    else {}
                ),
            },
            "warnings": [*ctx.warnings, *result.warnings],
            "audio": audio,
        }
    )


# Public names used by the composition facade.
open_score = open_musicxml
discard = discard_task

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

from google.genai import types
from pydantic import ValidationError

from muschat import prompts
from muschat.composition import Composition, PendingTask, Version
from muschat.constraints import check_constraints
from muschat.instruments import FAMILIES, INSTRUMENTS, instrument as get_instrument
from muschat.notagen import ComposerBackend
from muschat.notation.musicxml import from_musicxml, to_musicxml
from muschat.notation.techniques import family_techniques, unplayable_techniques
from muschat.openmusic import TASKS_DIR, OmTask, create_task, import_output
from muschat.plan import Operation, Parameter, Plan
from muschat.procedures import CATALOG, Procedure
from muschat.recipes import documentation as recipes_in_prompt, expand_recipe
from muschat.registry import ENGINE_NAME, Registry
from muschat.router import MODES, Mode, NoRouteError, Step, route
from muschat.score_ir import Note, Part, Provenance, ScoreIR, Source, diff_scores, locate
from muschat.security import sanitize

if TYPE_CHECKING:
    from muschat.reasoning import ChatSession, Generator

__all__ = [
    "RefusalError",
    "TurnResult",
    "compose",
    "discard_task",
    "open_musicxml",
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


class RefusalError(Exception):
    pass


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
        plan = _plan_composition(
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
        return _refusal(RefusalError("this Version was imported from a file; there is no operation to reapply."))
    plan = Plan.model_validate(ver.experiment["plan"])
    plan = plan.model_copy(
        update={
            "seed": ver.experiment["seed"],
            "operations": [
                op.model_copy(update={"parameters": [Parameter(name=k, value=v) for k, v in new_params.items()]})
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
        material = import_output(
            p.task,
            output,
            operation=p.operation,
            current=p.current,
            plan=p.plan.for_step(p.step),
            rng=random.Random(f"{p.seed}/{p.step}"),
        )
    except ValueError as exc:
        return TurnResult(
            f"OpenMusic output was rejected and the task is still awaiting you: {exc}.",
            None,
            p.task,
        )
    composition.pending = None
    try:
        updated = _verify_step(
            p.plan.for_step(p.step),
            p.current,
            material,
            source="openmusic",
            operation=p.operation,
        )
        ctx = p.model_copy(
            update={
                "step": p.step + 1,
                "task": None,
                "intermediates": [*p.intermediates, updated],
            }
        )
        return _continue_pipeline(ctx, updated, composition, catalog, tasks_dir, composer)
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
        ir = _validate_ir(ir)
        ir = _stamp_provenance(ir, {e.id for _, _, e in ir.events()}, source, operation, author)
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
    return TurnResult(f"Operation refused; the composition was not changed. Reason: {exc}", None)


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
) -> TurnResult:
    current = None if base is None else composition.versions[base].ir
    try:
        try:
            plan = expand_recipe(plan)
        except ValueError as exc:
            raise RefusalError(str(exc)) from exc
        effective = plan.mode if mode == "auto" else mode
        if (
            effective in ("hybrid", "openmusic")
            and plan.instruments
            and any(o.procedure == "harmonic_series" for o in plan.operations)
        ):
            operations = []
            for k, op in enumerate(plan.operations):
                operations.append(op)
                if op.procedure != "harmonic_series":
                    continue
                remaining = plan.operations[k + 1 :]
                next_series = next(
                    (i for i, nxt in enumerate(remaining) if nxt.procedure == "harmonic_series"),
                    len(remaining),
                )
                if not any(o.procedure == "orchestrate_material" for o in remaining[:next_series]):
                    values = _resolve_parameters(op, catalog, current is not None)
                    operations.append(
                        Operation(
                            procedure="orchestrate_material",
                            section=op.section,
                            parameters=[Parameter(name=n, value=values[n]) for n in ("measures", "rests")],
                        )
                    )
            if plan.techniques and not any(
                o.procedure in ("apply_techniques", "gestural_techniques") for o in operations
            ):
                operations.append(Operation(procedure="gestural_techniques"))
            try:
                plan = Plan.model_validate(
                    {**plan.model_dump(), "operations": [o.model_dump() for o in operations]}
                )
            except ValidationError as exc:
                raise RefusalError(
                    "the expanded hybrid plan exceeds operation limits; reduce the number of operations"
                ) from exc
        if current is None and plan.sections:
            ordered = sorted(plan.sections, key=lambda s: s.start_measure)
            if ordered[0].start_measure != 1 or any(
                a.end_measure + 1 != b.start_measure for a, b in zip(ordered, ordered[1:])
            ):
                raise RefusalError("the formal plan must cover measures contiguously without gaps")
            if any(not op.section for op in plan.operations):
                raise RefusalError("every operation in a multi-section form must specify its section")
            for sec in plan.sections:
                ops = [op for op in plan.operations if op.section.casefold() == sec.name.casefold()]
                if (
                    len(ops) < 2
                    or ops[0].procedure != "generate_notagen"
                    or any(op.procedure == "generate_notagen" for op in ops[1:])
                ):
                    raise RefusalError(
                        "each section requires a single NotaGen generation followed by transformations"
                    )
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
                        + [Parameter(name="measures", value=sel.end_measure - sel.start_measure + 1)]
                    }
                )
            parameters.append(_resolve_parameters(op, catalog, exists))
            exists = exists or not catalog[op.procedure].requires_score
        for k in range(len(plan.operations)):
            try:
                target = plan.for_step(k)
            except ValueError as exc:
                raise RefusalError(str(exc)) from exc
            _validate_selection(target, current, catalog)
        _validate_conditions(plan, decision.steps, registry)
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
        return _continue_pipeline(ctx, current, composition, catalog, tasks_dir, composer)
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
        step = ctx.decision.steps[k]
        target = ctx.plan.for_step(k)
        if step.engine == "notagen":
            current, ctx = _notagen_step(ctx, k, current, composer)
            intermediates.append(current)
            continue
        if step.engine == "openmusic":
            try:
                task = create_task(
                    step.procedure,
                    ctx.parameters[k],
                    root=tasks_dir,
                    current=current,
                    plan=target,
                )
            except OSError as exc:
                raise RefusalError(f"could not create OpenMusic task directory ({exc})") from exc
            except ValueError as exc:
                raise RefusalError(f"step {k + 1} ({step.procedure}): {_first_line(exc)}") from exc
            composition.pending = ctx.model_copy(
                update={"step": k, "current": current, "task": task, "intermediates": intermediates}
            )
            return TurnResult(
                f"Awaiting you: step {k + 1} of {len(ctx.decision.steps)}, {step.procedure} in "
                f"OpenMusic (assisted). Download task {task.id}, run the patch, and submit saida.txt. "
                f"Engine choice: {' '.join(ctx.decision.rationale)}",
                None,
                task,
            )
        rng = random.Random(f"{ctx.seed}/{k}")
        fn = catalog[step.procedure].fn
        if fn is None:
            raise RefusalError(f"{step.procedure} has no Python implementation")
        try:
            updated = fn(current, target, ctx.parameters[k], rng)
        except ValueError as exc:
            raise RefusalError(f"step {k + 1} ({step.procedure}): {_first_line(exc)}") from exc
        current = _verify_step(target, current, updated, source="algorithm", operation=ctx.operation)
        intermediates.append(current)
    if current is None:
        raise RefusalError("the plan produced no composition")
    return _record_version(ctx.model_copy(update={"intermediates": intermediates}), current, composition, catalog)


def _record_version(
    ctx: PendingTask,
    updated: ScoreIR,
    composition: Composition,
    catalog: dict[str, Procedure],
) -> TurnResult:
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
        + ("" if plan.selection.is_full else f"; selection {plan.selection.model_dump(exclude_defaults=True)}")
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
            "decision": ctx.decision.model_dump(mode="json"),
            "operation": ctx.operation,
            "procedures": [op.procedure for op in plan.operations],
            "parameters": ctx.parameters,
            "seed": ctx.seed,
            "violations": violations,
            "dependencies": runtime_dependencies(),
            "steps": [{**s.model_dump(), "state": "done"} for s in ctx.decision.steps],
            "intermediates": [ir.model_dump(mode="json") for ir in ctx.intermediates],
            "external_outputs": {str(k): xml for k, xml in ctx.external_outputs.items()},
            "warnings": ctx.warnings,
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

    return {"python": platform.python_version(), **{p: pkg_version(p) for p in ("abjad", "verovio", "google-genai")}}


def _verify_step(
    plan: Plan,
    before: ScoreIR | None,
    after: ScoreIR,
    *,
    source: Source,
    operation: str,
) -> ScoreIR:
    """Re-validate the step's ScoreIR, check the preservation invariant against `before`, and stamp provenance."""
    after = _validate_ir(after)
    if before is None:
        return _stamp_provenance(after, {e.id for _, _, e in after.events()}, source, operation)
    diff = diff_scores(before, after)
    loc_before, loc_after = locate(before), locate(after)
    for part in before.parts:
        for voice in part.voices:
            if diff.modified.get(voice.id) != ["tuplets"]:
                continue
            after_voice = next(v for p in after.parts for v in p.voices if v.id == voice.id)
            a, b = {t.id: t for t in voice.tuplets}, {t.id: t for t in after_voice.tuplets}
            changed = {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}
            while children := {t.id for t in [*a.values(), *b.values()] if t.parent in changed} - changed:
                changed |= children
            members = {i for key in changed for t in (a.get(key), b.get(key)) if t for i in t.events}
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
        where = outside or (f"measures {diff.measures}" if diff.measures else "title/composer")
        raise RefusalError(f"procedure modified material outside the selection: {where}.")
    return _stamp_provenance(after, set(diff.added) | set(diff.modified), source, operation)


def _validate_conditions(plan: Plan, steps: list[Step], registry: Registry) -> None:
    """Validate NotaGen condition strings against the available conditions before running any step."""
    cap = registry.capability("NotaGen")
    for op, step in zip(plan.operations, steps):
        if step.engine == "notagen" and (cap is None or op.condition not in cap.options):
            raise RefusalError(f"{op.procedure}: condition {op.condition!r} is not accepted by NotaGen-X.")


def _notagen_step(
    ctx: PendingTask,
    k: int,
    current: ScoreIR | None,
    composer: ComposerBackend | None,
) -> tuple[ScoreIR, PendingTask]:
    """Read NotaGen output and adapt the requested instrumentation before the contemporary layer."""
    op = ctx.plan.operations[k]
    target = ctx.plan.for_step(k)
    requested_measures = int(ctx.parameters[k]["measures"])
    xml = ctx.external_outputs.get(k)
    if xml is None:
        if composer is None:
            raise RefusalError("NotaGen is not enabled in this session (MUSCHAT_NOTAGEN=1; see README).")
        try:
            xml = composer.generate(
                op.condition,
                requested_measures,
                random.Random(f"{ctx.seed}/{k}").randrange(2**31),
            ).decode("utf-8")
        except ValueError as exc:
            raise RefusalError(f"step {k + 1} ({op.procedure}): {_first_line(exc)}") from exc
    try:
        ir, warnings = from_musicxml(xml.encode("utf-8"), Provenance(source="external", author="NotaGen-X"))
    except ValueError as exc:
        raise RefusalError(f"step {k + 1} ({op.procedure}): unreadable output: {_first_line(exc)}") from exc
    if len(ir.measures) < requested_measures:
        warnings.append(f"NotaGen wrote {len(ir.measures)} of {requested_measures} measures")
    adapted = False
    if len(ctx.plan.instruments) == len(ir.parts):
        ir = ir.model_copy(
            update={"parts": [_with_instrument(p, name) for p, name in zip(ir.parts, ctx.plan.instruments)]}
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
            ir, current = insert_section(current, ir, target.selection, ctx.plan.sections)
        except ValueError as exc:
            raise RefusalError(str(exc)) from exc
    updated = _verify_step(
        target,
        current,
        ir,
        source="algorithm" if adapted else "external",
        operation=ctx.operation,
    )
    return updated, ctx.model_copy(
        update={
            "external_outputs": {**ctx.external_outputs, k: xml},
            "warnings": [*ctx.warnings, *(sanitize(w) for w in warnings)],
        }
    )


def _with_instrument(part: Part, name: str) -> Part:
    """Rename a NotaGen part (and attach its range when known) to the instrument requested in the plan."""
    try:
        rng = get_instrument(name).range
    except ValueError:
        rng = part.range
    return part.model_copy(update={"instrument": name.strip().capitalize(), "range": rng})


def _validate_ir(ir: ScoreIR) -> ScoreIR:
    """Re-validate ScoreIR (`model_copy` does not re-validate) and require valid MusicXML serialization."""
    try:
        ir = ScoreIR.model_validate(ir.model_dump())
        to_musicxml(ir)
    except ValueError as exc:
        raise RefusalError(_first_line(exc)) from exc
    return ir


def _stamp_provenance(
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
            author=author or (current_prov.author if current_prov.source == source else None),
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
                                        e.model_copy(update={"provenance": prov(e.provenance)})
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


def _native_procedures(catalog: dict[str, Procedure]) -> tuple[str, ...]:
    return tuple(n for n, p in catalog.items() if p.fn is not None)


def _first_line(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]


def _plan_composition(
    request: str,
    current: ScoreIR | None,
    *,
    generator: "Generator",
    chat: "ChatSession",
    model: str,
    catalog: dict[str, Procedure],
    registry: Registry,
    mode: Mode = "auto",
    sections: list | None = None,
    retrying_route: bool = False,
) -> Plan:
    text = (
        f"<user_question>\n{sanitize(request)}\n</user_question>\n"
        f"<sections>{sanitize(str(sections or []))}</sections>\n"
        f"<current_composition>\n{summarize_score(current)}\n</current_composition>"
    )
    response = generator.generate_content(
        model=model,
        contents=[*chat.get_history(), types.Content(role="user", parts=[types.Part(text=text)])],
        config=types.GenerateContentConfig(
            system_instruction=prompts.PLAN.format(
                catalog=_catalog_in_prompt(catalog, registry, mode) + ("\n" + recipes_in_prompt()),
                instruments="; ".join(
                    f"{f}: {', '.join(n for n, i in INSTRUMENTS.items() if i.family == f)}" for f in FAMILIES
                ),
                techniques="; ".join(f"{f}: {', '.join(family_techniques(f))}" for f in FAMILIES),
            )
            + (
                f"\nSelected engine mode in the UI: {mode}. This selection takes priority. "
                "Use only procedures from the catalog above, including for techniques. "
                "OpenMusic is assisted: it prepares a task for the composer to run the patch; "
                "it is neither a language model nor an automatic call to the application. "
                "Do not invent operations to satisfy unsupported parts of the request."
            )
            + (
                " Start mandatorily with generate_notagen using an available condition, followed by the "
                "requested Python transformations; do not substitute harmonic_series."
                if mode == "notagen"
                else ""
            )
            + (
                " OpenMusic/Hybrid requires at least one operation with an available OpenMusic patch in the catalog. "
                "For a new composition, use that material in orchestrating the requested instruments and "
                "then apply techniques in Python. Do not submit only Python transformations."
                if mode in ("openmusic", "hybrid")
                else ""
            )
            + (
                " CORRECTION: the previous plan omitted the OpenMusic step. Rebuild the entire chain with an "
                "available approved patch, without losing the instrumentation, measure count, and techniques "
                "from the request."
                if retrying_route
                else ""
            ),
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=Plan,
        ),
    )
    try:
        plan = Plan.model_validate_json(response.text or "")
        if sections:
            plan = Plan.model_validate({**plan.model_dump(), "sections": sections})
        effective = plan.mode if mode == "auto" else mode
        if effective in ("openmusic", "hybrid"):
            approved = {
                name
                for name in registry.available_procedures()
                if name in catalog
                and (cap := registry.implementations(name).get("openmusic"))
                and cap.state != "unavailable"
            }
            if approved and not any(op.procedure in approved for op in plan.operations):
                if retrying_route:
                    raise RefusalError(
                        "planning did not include an OpenMusic step even after correction; no engine was executed"
                    )
                corrected = _plan_composition(
                    request,
                    current,
                    generator=generator,
                    chat=chat,
                    model=model,
                    catalog=catalog,
                    registry=registry,
                    mode=effective,
                    sections=sections,
                    retrying_route=True,
                )
                return corrected.model_copy(update={"mode": effective})
        return plan
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)[:3]
        )
        raise RefusalError(f"invalid compositional plan: {fields}.") from exc


def _resolve_parameters(op: Operation, catalog: dict[str, Procedure], exists: bool) -> dict[str, float]:
    name, given = op.procedure, {p.name: p.value for p in op.parameters}
    if len(given) < len(op.parameters):
        raise RefusalError(f"{name}: duplicate parameter in plan.")
    if name not in catalog:
        raise RefusalError(f"procedure {name!r} does not exist in the catalog ({', '.join(catalog)}).")
    proc = catalog[name]
    if proc.requires_score and not exists:
        raise RefusalError(f"{name} transforms an existing composition, and none exists yet in this conversation.")
    if unknown := given.keys() - proc.parameters.keys():
        raise RefusalError(f"{name} does not have parameters {sorted(unknown)}.")
    for key, value in given.items():
        rng = proc.parameters[key]
        if not rng.min_value <= value <= rng.max_value or (rng.integer and value != int(value)):
            kind = "integer " if rng.integer else ""
            raise RefusalError(f"{name}: {key}={value:g} out of range ({kind}{rng.min_value:g} to {rng.max_value:g}).")
    return {key: given.get(key, rng.default) for key, rng in proc.parameters.items()}


def _validate_selection(plan: Plan, current: ScoreIR | None, catalog: dict[str, Procedure] = CATALOG) -> None:
    sel = plan.selection
    if current is None:
        return
    parts = {p.id for p in current.parts}
    creates_piece = any(
        op.procedure in catalog and not catalog[op.procedure].requires_score for op in plan.operations
    )
    if not creates_piece and (outside := sorted({i for r in plan.constraints for i in r.parts if i not in parts})):
        raise RefusalError(f"constraints reference parts that do not exist in the composition: {outside}.")
    if sel.is_full:
        return
    voices = {v.id for p in current.parts for v in p.voices}
    if missing := [i for i in sel.parts if i not in parts] + [i for i in sel.voices if i not in voices]:
        raise RefusalError(f"selection references IDs that do not exist in the composition: {missing}.")
    start, end = sel.start_measure or 1, sel.end_measure or len(current.measures)
    if not 1 <= start <= end <= len(current.measures):
        raise RefusalError(f"measures {start}–{end} outside the composition (1–{len(current.measures)}).")


def summarize_score(ir: ScoreIR | None) -> str:
    """Score summary for the prompt: lists part and voice IDs that selection may reference."""
    if ir is None:
        return "no composition yet"
    meters = sorted({f"{m.numerator}/{m.denominator}" for m in ir.measures})
    lines = [f"title: {ir.title}", f"measures: {len(ir.measures)} ({', '.join(meters)})", "parts:"]
    for p in ir.parts:
        voices = ", ".join(f"{v.id} ({len(v.events)} events)" for v in p.voices)
        notes = [e for v in p.voices for e in v.events if isinstance(e, Note)]
        techniques = sorted({t for e in notes for t in e.techniques})
        lines.append(
            f"- {p.id}: {p.instrument}; voices {voices}; {len(notes)} notes; "
            f"active techniques: {', '.join(techniques) or 'none'}"
        )
    return sanitize("\n".join(lines))


def _catalog_in_prompt(catalog: dict[str, Procedure], registry: Registry, mode: Mode = "auto") -> str:
    """List only procedures that `Registry` reports as runnable on this machine."""
    lines = []
    for name in registry.available_procedures():
        if name not in catalog:
            continue
        proc = catalog[name]
        engines = registry.implementations(name)
        if mode == "notagen" and not any(
            m in engines and engines[m].state != "unavailable" for m in ("notagen", "python")
        ):
            continue
        if mode == "python" and (mode not in engines or engines[mode].state == "unavailable"):
            continue
        if mode == "openmusic" and not any(
            m in engines and engines[m].state != "unavailable" for m in ("openmusic", "python")
        ):
            continue
        where = " Also in OpenMusic (assisted: the composer runs the patch)." if "openmusic" in engines else ""
        lines.append(f"- {proc.name}: {proc.description}{where}")
        for key, r in proc.parameters.items():
            kind = "integer" if r.integer else "float"
            lines.append(
                f"    - {key} ({kind} {r.unit}, {r.min_value:g} to {r.max_value:g}, default {r.default:g}): "
                f"{r.description}"
            )
        if options := [o for c in engines.values() for o in c.options]:
            lines.append(f"    - condition (exact string, one of): {' | '.join(options)}")
    return "\n".join(lines)


def _format_params(parameters: dict[str, float], proc: Procedure) -> str:
    return ", ".join(f"{k}={v:g} {proc.parameters[k].unit}".rstrip() for k, v in parameters.items())

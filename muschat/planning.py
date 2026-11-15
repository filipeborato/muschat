"""Structured composition planning, mode retry policy, and plan normalization."""

import logging
from typing import TYPE_CHECKING
from google.genai import types
from pydantic import ValidationError
from muschat import prompts
from muschat.history import recent_history, DEFAULT_HISTORY_MESSAGES
from muschat.errors import RefusalError
from muschat.instruments import FAMILIES, INSTRUMENTS
from muschat.notation.techniques import TECHNIQUES, family_techniques, fits_instrument
from muschat.plan import Operation, Parameter, Plan
from muschat.procedures import Procedure
from muschat.procedures.common import DEFAULT_QUARTET
from muschat.recipes import documentation as recipes_in_prompt, expand_recipe
from muschat.registry import Registry
from muschat.router import Mode, Step, effective_mode
from muschat.score_ir import Note, ScoreIR
from muschat.security import sanitize

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from muschat.reasoning import ChatSession, Generator


def plan_composition(
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
    history_messages: int = DEFAULT_HISTORY_MESSAGES,
    retrying_route: bool = False,
    previous_plan: Plan | None = None,
    required_procedures: tuple[str, ...] = (),
) -> Plan:
    text = (
        f"<user_question>\n{sanitize(request)}\n</user_question>\n"
        f"<sections>{sanitize(str(sections or []))}</sections>\n"
        f"<current_composition>\n{summarize_score(current)}\n</current_composition>"
    )
    if previous_plan is not None:
        text += f"\n<previous_plan>{sanitize(previous_plan.model_dump_json())}</previous_plan>"
        text += f"\n<required_procedures>{', '.join(required_procedures)}</required_procedures>"
        text += "\nThe previous plan omitted every required procedure. Include at least one listed procedure, then retain the requested instrumentation and transformations."
    response = generator.generate_content(
        model=model,
        contents=[
            *recent_history(chat, history_messages),
            types.Content(role="user", parts=[types.Part(text=text)]),
        ],
        config=types.GenerateContentConfig(
            system_instruction=prompts.PLAN.format(
                catalog=catalog_in_prompt(catalog, registry, mode)
                + ("\n" + recipes_in_prompt()),
                instruments="; ".join(
                    f"{f}: {', '.join(n for n, i in INSTRUMENTS.items() if i.family == f)}"
                    for f in FAMILIES
                ),
                techniques="; ".join(
                    f"{f}: {', '.join(family_techniques(f))}" for f in FAMILIES
                ),
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
                " CORRECTION: the previous plan omitted the required engine step. Rebuild the entire chain with an "
                "available operation for the selected mode, without losing the instrumentation, measure count, and techniques "
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
        if previous_plan is not None:
            preserved = {
                name: getattr(previous_plan, name)
                for name in ("instruments", "techniques", "constraints", "sections")
                if not getattr(plan, name)
            }
            if plan.selection.is_full and not previous_plan.selection.is_full:
                preserved["selection"] = previous_plan.selection
            plan = Plan.model_validate({**plan.model_dump(), **preserved})
        if sections and not plan.sections:
            plan = Plan.model_validate({**plan.model_dump(), "sections": sections})
        effective = effective_mode(plan, mode)
        if effective in ("openmusic", "hybrid", "notagen"):
            approved = {
                name
                for name in registry.available_procedures()
                if name in catalog
                and (cap := registry.implementations(name).get("openmusic"))
                and cap.state != "unavailable"
            }
            if effective == "notagen":
                approved = {"generate_notagen"}
            if approved and not any(op.procedure in approved for op in plan.operations):
                logger.warning(
                    "Planner omitted required engine: mode=%s operations=%s required=%s retry=%s",
                    effective,
                    [op.procedure for op in plan.operations],
                    sorted(approved),
                    retrying_route,
                )
                if retrying_route:
                    if (
                        effective in ("openmusic", "hybrid")
                        and current is None
                        and not plan.sections
                        and plan.selection.is_full
                        and "harmonic_series" in approved
                        and all(
                            op.procedure in catalog
                            and catalog[op.procedure].requires_score
                            for op in plan.operations
                        )
                    ):
                        # Repair only new pieces: inserting a generator into an edit would replace existing music.
                        operations = list(plan.operations)
                        if previous_plan is not None:
                            operations = [
                                op
                                for op in previous_plan.operations
                                if not any(
                                    (op.procedure, op.section)
                                    == (new.procedure, new.section)
                                    for new in operations
                                )
                            ] + operations
                        if any(
                            op.procedure not in catalog
                            or not catalog[op.procedure].requires_score
                            for op in operations
                        ):
                            raise RefusalError(
                                "Cannot safely repair a plan containing another material generator"
                            )
                        instruments = plan.instruments or list(DEFAULT_QUARTET)
                        techniques = list(plan.techniques)
                        if not techniques and any(
                            op.procedure in ("apply_techniques", "gestural_techniques")
                            for op in operations
                        ):
                            for instrument in instruments:
                                for name, technique in TECHNIQUES.items():
                                    if (
                                        fits_instrument(name, instrument)
                                        and technique.register == (0.0, 1.0)
                                        and technique.min_duration == 0
                                    ):
                                        if name not in techniques:
                                            techniques.append(name)
                                        break
                                else:
                                    raise RefusalError(
                                        f"No idiomatic extended technique is available for {instrument}"
                                    )
                        return Plan.model_validate(
                            {
                                **plan.model_dump(),
                                "instruments": instruments,
                                "techniques": techniques,
                                "operations": [
                                    Operation(procedure="harmonic_series").model_dump(),
                                    *[op.model_dump() for op in operations],
                                ],
                                "hypotheses": [
                                    *plan.hypotheses,
                                    "repaired missing generation: OpenMusic harmonic series supplies the new instrumental piece",
                                ],
                            }
                        )
                    raise RefusalError(
                        f"planning did not include a required {effective} step even after correction; no engine was executed"
                    )
                corrected = plan_composition(
                    request,
                    current,
                    generator=generator,
                    chat=chat,
                    model=model,
                    catalog=catalog,
                    registry=registry,
                    mode=effective,
                    sections=sections,
                    history_messages=history_messages,
                    retrying_route=True,
                    previous_plan=plan,
                    required_procedures=tuple(sorted(approved)),
                )
                return corrected.model_copy(update={"mode": effective})
        return plan
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)[:3]
        )
        raise RefusalError(f"invalid compositional plan: {fields}.") from exc


def resolve_parameters(
    op: Operation, catalog: dict[str, Procedure], exists: bool
) -> dict[str, float]:
    name, given = op.procedure, {p.name: p.value for p in op.parameters}
    if len(given) < len(op.parameters):
        raise RefusalError(f"{name}: duplicate parameter in plan.")
    if name not in catalog:
        raise RefusalError(
            f"procedure {name!r} does not exist in the catalog ({', '.join(catalog)})."
        )
    proc = catalog[name]
    if proc.requires_score and not exists:
        raise RefusalError(
            f"{name} transforms an existing composition, and none exists yet in this conversation."
        )
    if unknown := given.keys() - proc.parameters.keys():
        raise RefusalError(f"{name} does not have parameters {sorted(unknown)}.")
    for key, value in given.items():
        rng = proc.parameters[key]
        if not rng.min_value <= value <= rng.max_value or (
            rng.integer and value != int(value)
        ):
            kind = "integer " if rng.integer else ""
            raise RefusalError(
                f"{name}: {key}={value:g} out of range ({kind}{rng.min_value:g} to {rng.max_value:g})."
            )
    return {key: given.get(key, rng.default) for key, rng in proc.parameters.items()}


def validate_conditions(plan: Plan, steps: list[Step], registry: Registry) -> None:
    """Validate NotaGen condition strings against the available conditions before running any step."""
    cap = registry.capability("NotaGen")
    for op, step in zip(plan.operations, steps):
        if step.engine == "notagen" and (
            cap is None or op.condition not in cap.options
        ):
            raise RefusalError(
                f"{op.procedure}: condition {op.condition!r} is not accepted by NotaGen-X."
            )


def summarize_score(ir: ScoreIR | None) -> str:
    """Score summary for the prompt: lists part and voice IDs that selection may reference."""
    if ir is None:
        return "no composition yet"
    meters = sorted({f"{m.numerator}/{m.denominator}" for m in ir.measures})
    lines = [
        f"title: {ir.title}",
        f"measures: {len(ir.measures)} ({', '.join(meters)})",
        "parts:",
    ]
    for p in ir.parts:
        voices = ", ".join(f"{v.id} ({len(v.events)} events)" for v in p.voices)
        notes = [e for v in p.voices for e in v.events if isinstance(e, Note)]
        techniques = sorted({t for e in notes for t in e.techniques})
        lines.append(
            f"- {p.id}: {p.instrument}; voices {voices}; {len(notes)} notes; "
            f"active techniques: {', '.join(techniques) or 'none'}"
        )
    return sanitize("\n".join(lines))


def catalog_in_prompt(
    catalog: dict[str, Procedure], registry: Registry, mode: Mode = "auto"
) -> str:
    """List only procedures that `Registry` reports as runnable on this machine."""
    lines = []
    for name in registry.available_procedures():
        if name not in catalog:
            continue
        proc = catalog[name]
        engines = registry.implementations(name)
        if mode == "notagen" and not any(
            m in engines and engines[m].state != "unavailable"
            for m in ("notagen", "python")
        ):
            continue
        if mode == "python" and (
            mode not in engines or engines[mode].state == "unavailable"
        ):
            continue
        if mode == "openmusic" and not any(
            m in engines and engines[m].state != "unavailable"
            for m in ("openmusic", "python")
        ):
            continue
        where = (
            " Also in OpenMusic (assisted: the composer runs the patch)."
            if "openmusic" in engines
            else ""
        )
        lines.append(f"- {proc.name}: {proc.description}{where}")
        for key, r in proc.parameters.items():
            kind = "integer" if r.integer else "float"
            lines.append(
                f"    - {key} ({kind} {r.unit}, {r.min_value:g} to {r.max_value:g}, default {r.default:g}): "
                f"{r.description}"
            )
        if options := [o for c in engines.values() for o in c.options]:
            lines.append(
                f"    - condition (exact string, one of): {' | '.join(options)}"
            )
    return "\n".join(lines)


def normalize_plan(
    plan: Plan,
    mode: Mode,
    catalog: dict[str, Procedure],
    current: ScoreIR | None,
    *,
    plan_expanded: bool = False,
) -> Plan:
    try:
        if not plan_expanded:
            plan = expand_recipe(plan)
    except ValueError as exc:
        raise RefusalError(str(exc)) from exc
    effective = effective_mode(plan, mode)
    if (
        not plan_expanded
        and effective in ("hybrid", "openmusic")
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
                (
                    i
                    for i, nxt in enumerate(remaining)
                    if nxt.procedure == "harmonic_series"
                ),
                len(remaining),
            )
            if not any(
                o.procedure == "orchestrate_material" for o in remaining[:next_series]
            ):
                values = resolve_parameters(op, catalog, current is not None)
                operations.append(
                    Operation(
                        procedure="orchestrate_material",
                        section=op.section,
                        parameters=[
                            Parameter(name=n, value=values[n])
                            for n in ("measures", "rests")
                        ],
                    )
                )
        if plan.techniques and not any(
            o.procedure in ("apply_techniques", "gestural_techniques")
            for o in operations
        ):
            operations.append(Operation(procedure="gestural_techniques"))
        try:
            plan = Plan.model_validate(
                {
                    **plan.model_dump(),
                    "operations": [o.model_dump() for o in operations],
                }
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
            raise RefusalError(
                "the formal plan must cover measures contiguously without gaps"
            )
        if any(not op.section for op in plan.operations):
            raise RefusalError(
                "every operation in a multi-section form must specify its section"
            )
        for sec in plan.sections:
            ops = [
                op
                for op in plan.operations
                if op.section.casefold() == sec.name.casefold()
            ]
            if (
                len(ops) < 2
                or ops[0].procedure != "generate_notagen"
                or any(op.procedure == "generate_notagen" for op in ops[1:])
            ):
                raise RefusalError(
                    "each section requires a single NotaGen generation followed by transformations"
                )
    return plan

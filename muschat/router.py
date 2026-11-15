"""Router: Plan + Mode + Registry -> steps (engine and procedure) with rationale, or refusal with alternatives.

Explicit ordered rules (see docs/adr/0002-regras-do-roteador.md); no arbitrary "quality" scoring.
  R1 suitability: only engines that declare the procedure.
  R2 availability: engines unavailable on this machine are excluded (with reason recorded in rationale).
  R3 manual choice: Python stays exclusive; OpenMusic combined with Python-only operations becomes an explicit Hybrid
     chain while preserving at least one real OpenMusic step. If no route exists, refuse with alternatives.
  R4 hybrid: each operation goes to OpenMusic when available here, and the remainder to Python; if no OpenMusic step is
     possible, refuse.
  R5 automatic: least manual intervention first (automatic < file_exchange < manual), which is also the most
     reproducible.
  R6 fixed tie-breaker: Python (native ScoreIR format, lossless, fewer steps).
Constraint preservation does not break ties: the single constraint verifier runs after any engine.
"""

from pydantic import BaseModel, model_validator

from muschat.plan import Mode, Plan, normalize_mode, normalize_procedure_id
from muschat.registry import ENGINE_NAME, PREFERENCE, Capability, Engine, Registry

MODES: dict[Mode, str] = {
    "auto": "Automatic",
    "python": "Python/Abjad",
    "openmusic": "OpenMusic",
    "hybrid": "Hybrid",
    "notagen": "NotaGen + Python",
}


class Step(BaseModel):
    engine: Engine
    procedure: str

    @model_validator(mode="after")
    def _normalize_proc(self) -> "Step":
        object.__setattr__(self, "procedure", normalize_procedure_id(self.procedure))
        return self


class RoutingDecision(BaseModel):
    mode: Mode
    steps: list[Step]
    rationale: list[str]

    @model_validator(mode="before")
    @classmethod
    def _normalize_mode_field(cls, data: object) -> object:
        if isinstance(data, dict) and "mode" in data and isinstance(data["mode"], str):
            return {**data, "mode": normalize_mode(data["mode"])}
        return data


class NoRouteError(Exception):
    """Raised when the requested mode cannot execute the plan; message includes reason and alternatives."""


def effective_mode(plan: Plan, mode: Mode) -> Mode:
    """Resolve selector precedence before capability-dependent routing rules."""
    mode = normalize_mode(mode)
    return plan.mode if mode == "auto" else mode


def route(plan: Plan, mode: Mode, registry: Registry) -> RoutingDecision:
    mode = normalize_mode(mode)
    effective, reasons = effective_mode(plan, mode), []
    if mode == "auto" and plan.mode != "auto":
        reasons.append(f"Request selected engine {MODES[effective]}; treated as manual choice (R3).")
    elif plan.mode not in ("auto", mode):
        reasons.append(
            f"Request mentioned engine {MODES[plan.mode]}, but selector is set to {MODES[mode]}: selector wins (R3). "
            "Switch the selector to use the other engine."
        )
    if effective == "openmusic":
        om = [registry.implementations(op.procedure).get("openmusic") for op in plan.operations]
        if any(c and c.state != "unavailable" for c in om) and any(
            c is None or c.state == "unavailable" for c in om
        ):
            effective = "hybrid"
            reasons.append(
                "OpenMusic with Python layer: chain executed in Hybrid mode; "
                "approved patches run in OpenMusic and remaining transformations run in Python/Abjad."
            )
    steps, lines = [], list(reasons)
    for op in plan.operations:
        engine, line = _choose_engine(op.procedure, effective, registry)
        steps.append(Step(engine=engine, procedure=op.procedure))
        lines.append(line)
    if effective == "notagen" and not any(s.engine == "notagen" for s in steps):
        raise NoRouteError("NotaGen mode requires generate_notagen; plan did not invoke the local model")
    if effective == "hybrid" and not any(s.engine == "openmusic" for s in steps):
        raise NoRouteError(
            "Hybrid mode requires at least one OpenMusic step, and no operation in the plan runs in "
            f"OpenMusic on this machine. {_om_status(registry)} Alternatives: Automatic or Python/Abjad."
        )
    return RoutingDecision(mode=effective, steps=steps, rationale=lines)


def _choose_engine(procedure: str, mode: Mode, registry: Registry) -> tuple[Engine, str]:
    proc = normalize_procedure_id(procedure)
    impl = registry.implementations(proc)  # R1 + R2: only engines that declare and can run it here
    available: dict[Engine, Capability] = {m: c for m, c in impl.items() if c.state != "unavailable"}
    if mode == "notagen":
        engine: Engine = "notagen" if proc == "generate_notagen" else "python"
        if engine not in available:
            raise NoRouteError(f"{proc} is not available in {ENGINE_NAME[engine]} in this mode")
        return engine, f"{proc}: {ENGINE_NAME[engine]}, NotaGen + Python mode."
    if mode in ("python", "openmusic"):  # R3
        if mode in available:
            return mode, f"{proc}: {MODES[mode]}, as selected ({_state_text(available[mode].state)})."
        raise NoRouteError(
            f"engine {MODES[mode]} cannot execute {proc} on this machine. "
            f"{_why_not(proc, mode, registry)} Alternatives: {_alternatives(available)}"
        )
    if not available:
        raise NoRouteError(f"no engine executes {proc} on this machine.")
    if mode == "hybrid" and "openmusic" in available:  # R4
        return (
            "openmusic",
            f"{proc}: OpenMusic, Hybrid mode step ({_state_text(available['openmusic'].state)}).",
        )
    if mode == "hybrid" and "python" in available:
        return "python", f"{proc}: Python/Abjad, OpenMusic does not provide this procedure here (R4)."
    automatic: dict[Engine, Capability] = {m: c for m, c in available.items() if c.state == "automatic"}
    if not automatic:
        raise NoRouteError("this operation requires explicit assisted mode; select OpenMusic or Hybrid.")
    # R5 and R6: least manual intervention first; tie goes to Python
    chosen = min(automatic, key=lambda m: (PREFERENCE[available[m].state], m != "python"))
    others: list[Engine] = [m for m in available if m != chosen]
    if others:
        other = others[0]
        return chosen, (
            f"{proc}: {ENGINE_NAME[chosen]} ({_state_text(available[chosen].state)}). "
            f"{ENGINE_NAME[other]} also implements it, but here it is {_state_text(available[other].state)}; "
            "Automatic mode prefers less manual intervention (R5)."
        )
    reason = "" if registry.capability("OpenMusic") is None or chosen == "openmusic" else f" {_om_status(registry)}"
    return chosen, f"{proc}: {ENGINE_NAME[chosen]}, the only available engine here.{reason}"


def _state_text(state: str) -> str:
    return {
        "automatic": "automatic",
        "file_exchange": "via file exchange",
        "manual": "assisted: composer runs the patch",
        "unavailable": "unavailable",
    }[state]


def _om_status(registry: Registry) -> str:
    om = registry.capability("OpenMusic")
    if om is None or om.state == "unavailable":
        return f"OpenMusic unavailable: {om.reason if om else 'not detected'}."
    libs = [c for c in registry.capabilities if c.kind == "library"]
    return " ".join(f"{c.name}: {c.reason}." for c in libs if c.state == "unavailable") or "OpenMusic available."


def _why_not(procedure: str, engine: Engine, registry: Registry) -> str:
    if engine == "openmusic":
        om = registry.capability("OpenMusic")
        if om is None:
            return "Experimental integration disabled; set MUSCHAT_OPENMUSIC=1 to enable OpenMusic."
        if om.state == "unavailable":
            return _om_status(registry)
        if not any(procedure in c.procedures for c in registry.capabilities if c.kind == "library"):
            return f"No approved OpenMusic patch for {procedure}."
        return _om_status(registry)
    return f"{procedure} is not in the Python catalog."


def _alternatives(available: dict[Engine, Capability]) -> str:
    if not available:
        return "no other engine executes this here."
    return "; ".join(f"{ENGINE_NAME[m]} ({_state_text(c.state)})" for m, c in available.items()) + "."

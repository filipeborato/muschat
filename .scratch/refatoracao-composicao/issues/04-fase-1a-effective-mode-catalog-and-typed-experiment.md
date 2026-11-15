# 04: Fase 1A — Router effective_mode, injected catalog validation, and typed Experiment with idempotent reapply

**What to build:** Unify effective routing mode resolution in a single public router function, honor injected capability catalogs during selection validation, and replace the untyped experiment dictionary with a typed Pydantic `Experiment` model that makes `reapply` idempotent on already-expanded plans.

**Blocked by:** 03: Fase 0 (Migrate Batch 2 & Contract) — Notation, export, RAG, English LLM prompts, orchestrator, UI, and shim removal

**Status:** resolved

- [x] `effective_mode(plan: Plan, mode: Mode) -> Mode` is the single source of truth used across planning, plan normalization, and routing without altering ADR 0002 rules `R1`–`R6`
- [x] Selection validation in `compose`, `reapply`, and `resume` consults the injected `Registry`/catalog rather than falling back to the global catalog
- [x] `Version.experiment` uses the typed `Experiment` Pydantic model (`ConfigDict(extra="allow")`, `plan_expanded: bool = False`, `audio: dict[str, Any] | None = None`) and deserializes legacy JSON files (including string keys in `external_outputs` and missing `plan_expanded`) without breaking `Version.reapplicable`:

```python
# From spec: canonical Experiment schema
class Experiment(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt: str = ""
    model: str = ""
    mode: Mode = "auto"
    plan: Plan | None = None
    plan_expanded: bool = False
    decision: RoutingDecision | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, float]] = Field(default_factory=list)
    seed: int | None = None
    intermediates: list[ScoreIR] = Field(default_factory=list)
    external_outputs: dict[int, str] = Field(default_factory=dict)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    audio: dict[str, Any] | None = None
```

- [x] Calling `reapply` on a version with `plan_expanded=True` skips recipe expansion and hybrid auto-completion, producing identical deterministic results when reapplied with the same parameters

## Completion

Implemented in commit `6630a1e`. Full suite: 529 tests passed; targeted checks passed after final adjustments. Standards and specification reviews completed.

Real NotaGen/OpenMusic installations were not exercised. Five pre-existing targeted mypy errors are tracked separately in issue 12 (`12-fix-existing-typechecking-errors.md`).

"""Session composition state: immutable ScoreIR Versions with undo, checkout, diff, and assisted pending tasks.

Serializes to and from JSON (Versions + PendingTask), including transparent migration of legacy Portuguese v2 JSONs.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from muschat.plan import (
    PROCEDURE_ALIASES,
    Mode,
    Plan,
    normalize_instrument_name,
    normalize_mode,
    normalize_parameter_name,
    normalize_procedure_id,
    normalize_recipe,
)
from muschat.router import RoutingDecision
from muschat.score_ir import (
    ScoreDiff,
    ScoreIR,
    diff_scores,
    migrate_legacy_score_ir_dict,
    normalize_clef,
    normalize_source,
    normalize_technique,
)

_REVERSE_PROCEDURE_ALIASES: dict[str, str] = {v: k for k, v in PROCEDURE_ALIASES.items()}


def migrate_legacy_plan_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy Portuguese `Plano` dictionary into the canonical English `Plan` dictionary shape."""
    if "goal" in raw and "operations" in raw:
        return raw
    sel = raw.get("selection", raw.get("selecao", {})) or {}
    norm_sel = {
        "parts": sel.get("parts", sel.get("partes", [])),
        "voices": sel.get("voices", sel.get("vozes", [])),
        "start_measure": sel.get("start_measure", sel.get("compasso_inicial")),
        "end_measure": sel.get("end_measure", sel.get("compasso_final")),
    }
    sections = [
        {
            "name": s.get("name", s.get("nome", "")),
            "character": s.get("character", s.get("carater", "")),
            "start_measure": s.get("start_measure", s.get("compasso_inicial")),
            "end_measure": s.get("end_measure", s.get("compasso_final")),
            "material": s.get("material", ""),
            "techniques": [normalize_technique(t) for t in s.get("techniques", s.get("tecnicas", []))],
        }
        for s in raw.get("sections", raw.get("secoes", []))
    ]
    constraints = [
        {
            "kind": c.get("kind", c.get("tipo")),
            "value": c.get("value", c.get("valor")),
            "low": c.get("low", c.get("grave")),
            "high": c.get("high", c.get("agudo")),
            "parts": c.get("parts", c.get("partes", [])),
        }
        for c in raw.get("constraints", raw.get("restricoes", []))
    ]
    operations = []
    for op in raw.get("operations", raw.get("operacoes", [])):
        proc = normalize_procedure_id(op.get("procedure", op.get("procedimento", "")))
        params = [
            {
                "name": normalize_parameter_name(p.get("name", p.get("nome", "")), proc),
                "value": p.get("value", p.get("valor")),
            }
            for p in op.get("parameters", op.get("parametros", []))
        ]
        operations.append(
            {
                "section": op.get("section", op.get("secao", "")),
                "procedure": proc,
                "parameters": params,
                "condition": op.get("condition", op.get("condicao", "")),
            }
        )
    return {
        "recipe": normalize_recipe(raw.get("recipe", raw.get("receita", ""))),
        "recipe_rationale": raw.get("recipe_rationale", raw.get("motivo_receita", "")),
        "sections": sections,
        "goal": raw.get("goal", raw.get("objetivo", "")),
        "instruments": [normalize_instrument_name(i) for i in raw.get("instruments", raw.get("instrumentos", []))],
        "structure": raw.get("structure", raw.get("estrutura", "")),
        "techniques": [normalize_technique(t) for t in raw.get("techniques", raw.get("tecnicas", []))],
        "constraints": constraints,
        "hypotheses": raw.get("hypotheses", raw.get("hipoteses", [])),
        "operations": operations,
        "selection": norm_sel,
        "mode": normalize_mode(raw.get("mode", raw.get("motor", "auto"))),
        "criteria": raw.get("criteria", raw.get("criterios", [])),
        "seed": raw.get("seed", raw.get("semente")),
    }


def migrate_legacy_decision_dict(raw: dict[str, Any]) -> dict[str, Any]:
    if "steps" in raw and "rationale" in raw:
        return raw
    return {
        "mode": normalize_mode(raw.get("mode", raw.get("modo", "auto"))),
        "steps": [
            {
                "engine": s.get("engine", s.get("motor")),
                "procedure": normalize_procedure_id(s.get("procedure", s.get("procedimento", ""))),
            }
            for s in raw.get("steps", raw.get("etapas", []))
        ],
        "rationale": raw.get("rationale", raw.get("justificativa", [])),
    }


def _migrate_procedure_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("procedure must be a string")
    return normalize_procedure_id(value)


def _migrate_param_dict(params: dict[str, Any], procedure: str = "") -> dict[str, float]:
    return {normalize_parameter_name(k, procedure): float(v) for k, v in params.items()}


def _migrate_param_list(raw_params: list[Any], procedures: list[str]) -> list[dict[str, float]]:
    return [
        _migrate_param_dict(p, procedures[idx] if idx < len(procedures) else "")
        if isinstance(p, dict)
        else p
        for idx, p in enumerate(raw_params)
    ]


def migrate_legacy_experiment_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy Portuguese experiment dictionary to canonical English keys and values."""
    out: dict[str, Any] = {}
    key_map = {
        "pedido": "prompt",
        "modelo": "model",
        "modo": "mode",
        "plano": "plan",
        "decisao": "decision",
        "etapas": "steps",
        "parametros": "parameters",
        "semente": "seed",
        "intermediarios": "intermediates",
        "saidas_externas": "external_outputs",
        "violacoes": "violations",
        "operacao": "operation",
        "procedimentos": "procedures",
        "avisos": "warnings",
        "dependencias": "dependencies",
        "arquivo": "file",
    }
    for k, v in raw.items():
        out[key_map.get(k, k)] = v
    if "mode" in out and isinstance(out["mode"], str):
        out["mode"] = normalize_mode(out["mode"])
    if "plan" in out and isinstance(out["plan"], dict):
        out["plan"] = Plan.model_validate(migrate_legacy_plan_dict(out["plan"])).model_dump(mode="json")
    if "decision" in out and isinstance(out["decision"], dict):
        out["decision"] = RoutingDecision.model_validate(migrate_legacy_decision_dict(out["decision"])).model_dump(
            mode="json"
        )
    if "steps" in out and isinstance(out["steps"], list):
        step_state_map = {"feita": "done", "done": "done", "pendente": "pending", "pending": "pending"}
        migrated_steps = []
        for s in out["steps"]:
            if isinstance(s, dict):
                entry = {
                    k: v
                    for k, v in s.items()
                    if k not in ("motor", "procedimento", "estado")
                }
                entry["engine"] = s.get("engine", s.get("motor"))
                entry["procedure"] = _migrate_procedure_id(s.get("procedure", s.get("procedimento", "")))
                if "state" in s or "estado" in s:
                    raw_st = str(s.get("state", s.get("estado", "")))
                    entry["state"] = step_state_map.get(raw_st, raw_st)
                migrated_steps.append(entry)
            else:
                migrated_steps.append(s)
        out["steps"] = migrated_steps
    if "procedures" in out and isinstance(out["procedures"], list):
        out["procedures"] = [normalize_procedure_id(p) for p in out["procedures"]]
    if "parameters" in out and isinstance(out["parameters"], list):
        step_procs = [s.get("procedure", "") if isinstance(s, dict) else "" for s in out.get("steps", [])]
        plan_ops = out["plan"].get("operations", []) if isinstance(out.get("plan"), dict) else []
        plan_procs = [op.get("procedure", "") if isinstance(op, dict) else "" for op in plan_ops]
        procs = step_procs if len(step_procs) >= len(out["parameters"]) else plan_procs
        out["parameters"] = _migrate_param_list(out["parameters"], procs)
    if "intermediates" in out and isinstance(out["intermediates"], list):
        out["intermediates"] = [
            ScoreIR.model_validate(migrate_legacy_score_ir_dict(ir)).model_dump(mode="json")
            if isinstance(ir, dict)
            else ir
            for ir in out["intermediates"]
        ]
    if "external_outputs" in out and isinstance(out["external_outputs"], dict):
        out["external_outputs"] = {int(k): str(v) for k, v in out["external_outputs"].items()}
    return out


class OmTask(BaseModel):
    """Assisted OpenMusic step package persisted inside `PendingTask`."""

    id: str
    procedure: str
    parameters: dict[str, float]
    directory: str
    created_at: datetime = Field(default_factory=datetime.now)
    content: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        if isinstance(data, dict):
            proc = _migrate_procedure_id(data.get("procedure", data.get("procedimento", "")))
            raw_params = data.get("parameters", data.get("parametros", {}))
            params = _migrate_param_dict(raw_params, proc) if isinstance(raw_params, dict) else raw_params
            return {
                "id": data.get("id"),
                "procedure": proc,
                "parameters": params,
                "directory": data.get("directory", data.get("pasta")),
                "created_at": data.get("created_at", data.get("criada", datetime.now())),
                "content": data.get("content", data.get("conteudo")),
            }
        return data

    @model_validator(mode="after")
    def _validate_catalog_ranges(self) -> "OmTask":
        from muschat.om_patches import PATCHES
        from muschat.procedures import CATALOG

        if self.procedure not in PATCHES or self.procedure not in CATALOG:
            raise ValueError(f"task references procedure without approved patch: {self.procedure}")
        spec_ranges = CATALOG[self.procedure].parameters
        if set(self.parameters) != set(spec_ranges):
            raise ValueError(f"task parameters do not match {self.procedure}")
        for name, val in self.parameters.items():
            r = spec_ranges[name]
            if not r.min_value <= val <= r.max_value or (r.integer and val != int(val)):
                raise ValueError(f"task parameter {name}={val:g} out of range")
        return self


class Experiment(BaseModel):
    """Typed experiment state; extra fields preserve historical metadata."""

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

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: object) -> object:
        if isinstance(data, dict):
            data = migrate_legacy_experiment_dict(data)
            data["violations"] = [v if isinstance(v, dict) else {"message": v} for v in data.get("violations", [])]
        return data

    def __getitem__(self, key: str) -> Any:
        if key == "external_outputs":
            return self.external_outputs
        if key == "violations":
            return [v.get("message", str(v)) for v in self.violations]
        if key not in type(self).model_fields and key not in (self.model_extra or {}):
            raise KeyError(key)
        value = getattr(self, key)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if key == "intermediates":
            return [ir.model_dump(mode="json") for ir in self.intermediates]
        return value

    def get(self, key: str, default: Any = None) -> Any:
        value = self[key] if key in type(self).model_fields or key in (self.model_extra or {}) else default
        return default if value is None else value

    def items(self):
        return self.model_dump(mode="json").items()


@dataclass(frozen=True)
class Version:
    ir: ScoreIR
    summary: str
    experiment: Experiment
    previous: int | None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment", Experiment.model_validate(self.experiment))

    @property
    def reapplicable(self) -> bool:
        """Only versions produced by a planned operation can be reapplied (imported score versions cannot)."""
        return self.experiment.plan is not None


class PendingTask(BaseModel):
    """Pipeline state paused on an assisted OpenMusic step, holding everything required to resume."""

    prompt: str
    model: str
    plan: Plan
    decision: RoutingDecision
    parameters: list[dict[str, float]]
    seed: int
    operation: str
    base: int | None
    step: int
    current: ScoreIR | None
    intermediates: list[ScoreIR]
    task: OmTask | None = None
    external_outputs: dict[int, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    audio: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        if not isinstance(data, dict) or ("prompt" in data and "decision" in data and "step" in data):
            return data
        raw_plan = data.get("plan", data.get("plano", {}))
        plan_dict = migrate_legacy_plan_dict(raw_plan) if isinstance(raw_plan, dict) else raw_plan
        raw_dec = data.get("decision", data.get("decisao", {}))
        dec_dict = migrate_legacy_decision_dict(raw_dec) if isinstance(raw_dec, dict) else raw_dec
        step_procs = [
            s.get("procedure", "") if isinstance(s, dict) else ""
            for s in (dec_dict.get("steps", []) if isinstance(dec_dict, dict) else [])
        ]
        raw_params = data.get("parameters", data.get("parametros", []))
        if not isinstance(raw_params, list):
            raise ValueError("parameters must be a list")
        norm_params = _migrate_param_list(raw_params, step_procs)
        raw_current = data.get("current", data.get("atual"))
        norm_current = (
            migrate_legacy_score_ir_dict(raw_current) if isinstance(raw_current, dict) else raw_current
        )
        raw_inter = data.get("intermediates", data.get("intermediarios", []))
        if not isinstance(raw_inter, list):
            raise ValueError("intermediates must be a list")
        norm_inter = [
            migrate_legacy_score_ir_dict(ir) if isinstance(ir, dict) else ir for ir in raw_inter
        ]
        raw_ext = data.get("external_outputs", data.get("saidas_externas", {})) or {}
        return {
            "prompt": data.get("prompt", data.get("pedido", "")),
            "model": data.get("model", data.get("modelo", "")),
            "plan": plan_dict,
            "decision": dec_dict,
            "parameters": norm_params,
            "seed": data.get("seed", data.get("semente")),
            "operation": data.get("operation", data.get("operacao", "")),
            "base": data.get("base"),
            "step": data.get("step", data.get("etapa")),
            "current": norm_current,
            "intermediates": norm_inter,
            "task": data.get("task", data.get("tarefa")),
            "external_outputs": {int(k): str(v) for k, v in raw_ext.items()},
            "warnings": data.get("warnings", data.get("avisos", [])),
        }


class Composition:
    """Append-only version history: undo and checkout move `index`, never deleting a Version."""

    def __init__(self) -> None:
        self.versions: list[Version] = []
        self.index: int | None = None
        self.pending: PendingTask | None = None

    def refresh_schema(self) -> None:
        """Restore retained session objects after a module reload without losing history."""
        stale_versions = any(
            type(v) is not Version or type(v.ir) is not ScoreIR or type(v.experiment) is not Experiment
            for v in self.versions
        )
        stale_pending = self.pending is not None and (
            type(self.pending) is not PendingTask
            or (self.pending.current is not None and type(self.pending.current) is not ScoreIR)
        )
        if stale_versions or stale_pending:
            try:
                restored = Composition.from_json(self.to_json())
            except (AttributeError, TypeError) as exc:
                raise ValueError("Retained composition could not be migrated; its original history was preserved") from exc
            self.versions, self.index, self.pending = restored.versions, restored.index, restored.pending

    @property
    def current(self) -> Version | None:
        return None if self.index is None else self.versions[self.index]

    def add(self, ir: ScoreIR, summary: str, experiment: Experiment | dict[str, Any], *, previous: int | None) -> Version:
        version = Version(ir, summary, Experiment.model_validate(experiment), previous)
        self.versions.append(version)
        self.index = len(self.versions) - 1
        return version

    def undo(self) -> None:
        if self.current is not None and self.current.previous is not None:
            self.index = self.current.previous

    def checkout(self, index: int) -> None:
        if not 0 <= index < len(self.versions):
            raise IndexError(f"version {index} does not exist")
        self.index = index

    def diff(self, start: int, end: int) -> ScoreDiff:
        return diff_scores(self.versions[start].ir, self.versions[end].ir)

    def to_json(self) -> str:
        return json.dumps(
            {
                "index": self.index,
                "versions": [
                    {
                        "ir": v.ir.model_dump(mode="json"),
                        "summary": v.summary,
                        "experiment": (
                            v.experiment if isinstance(v.experiment, dict) else v.experiment.model_dump(mode="json")
                        ),
                        "previous": v.previous,
                        "created_at": v.created_at.isoformat(),
                    }
                    for v in self.versions
                ],
                "pending": self.pending.model_dump(mode="json") if self.pending else None,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "Composition":
        """Reconstruct and revalidate a Composition from canonical English or legacy Portuguese v2 JSON."""
        try:
            data = json.loads(text)
            raw_versions = data["versions"] if "versions" in data else data["versoes"]
            raw_index = data["index"] if "index" in data else data["indice"]
            raw_pending = data.get("pending") if "pending" in data else data.get("pendente")
            comp = cls()
            comp.versions = [
                Version(
                    ScoreIR.model_validate(
                        migrate_legacy_score_ir_dict(v["ir"]) if isinstance(v["ir"], dict) else v["ir"]
                    ),
                    str(v["summary"] if "summary" in v else v["resumo"]),
                    Experiment.model_validate(v["experiment"] if "experiment" in v else v["experimento"]),
                    v["previous"] if "previous" in v else v["anterior"],
                    datetime.fromisoformat(v["created_at"] if "created_at" in v else v["data"]),
                )
                for v in raw_versions
            ]
            comp.index = raw_index
            comp.pending = PendingTask.model_validate(raw_pending) if raw_pending else None
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"invalid or incomplete composition JSON: {exc}") from exc
        total = len(comp.versions)

        def valid_idx(idx: object) -> bool:
            return idx is None or (isinstance(idx, int) and 0 <= idx < total)

        if not valid_idx(comp.index) or (total and comp.index is None) or not all(
            valid_idx(v.previous) for v in comp.versions
        ):
            raise ValueError("composition JSON has an invalid version index")
        p = comp.pending
        if p and (
            not valid_idx(p.base)
            or not 0 <= p.step < len(p.decision.steps)
            or len(p.parameters) != len(p.decision.steps)
            or p.task is None
            or p.decision.steps[p.step].engine != "openmusic"
        ):
            raise ValueError("composition JSON has an incoherent pending step")
        return comp

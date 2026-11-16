"""Compositional plan: structured schema returned by the planner before any procedure executes."""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from muschat.constraints import Constraint
from muschat.instruments import identify
from muschat.score_ir import normalize_technique

Mode = Literal["auto", "python", "openmusic", "hybrid", "notagen"]

_MODE_ALIASES: dict[str, Mode] = {
    "auto": "auto",
    "automatic": "auto",
    "automatico": "auto",
    "python": "python",
    "openmusic": "openmusic",
    "hybrid": "hybrid",
    "hibrido": "hybrid",
    "notagen": "notagen",
}

PROCEDURE_ALIASES: dict[str, str] = {
    "desafinar": "detune",
    "substituir_timbre": "timbral_substitution",
    "rarefazer": "rarefy",
    "micropolifonia": "micropolyphony",
    "deformar_ritmo": "deform_rhythm",
    "serie_harmonica": "harmonic_series",
    "orquestrar_material": "orchestrate_material",
    "espectralizar": "spectralize",
    "transpor": "transpose",
    "inverter": "invert",
    "retrogradar": "retrograde",
    "rotacionar": "rotate",
    "intervalos": "scale_intervals",
    "aumentacao_diminuicao": "augment_diminish",
    "permutar_duracoes": "permute_durations",
    "subdividir": "subdivide",
    "densidade": "density",
    "registro_progressivo": "progressive_register",
    "dinamica_progressiva": "progressive_dynamics",
    "tecnicas_por_gesto": "gestural_techniques",
    "aplicar_tecnicas": "apply_techniques",
    "distribuir": "distribute",
    "eventos_do_espectro": "events_from_spectrum",
    "gerar_notagen": "generate_notagen",
    "densidade_caotica": "chaotic_density",
    "registro_caotico": "chaotic_register",
    "dinamica_caotica": "chaotic_dynamics",
    "duracoes_caoticas": "chaotic_durations",
    "distribuir_restricoes": "distribute_by_constraints",
    "alturas_por_restricoes": "pitches_by_constraints",
    "sintese_aditiva": "additive_synthesis",
    "analise_supervp": "supervp_analysis",
}

PARAMETER_ALIASES: dict[str, str] = {
    "desvio": "deviation",
    "resolucao": "resolution",
    "curva": "curve",
    "glissandos": "glissandos",
    "inicial": "start",
    "final": "end",
    "familia_ruido": "noise_family",
    "manter": "keep",
    "harmonicos": "harmonics",
    "dinamica": "dynamic",
    "acentos": "accents",
    "parte_origem": "source_part",
    "defasagem": "offset",
    "variacao": "variation",
    "espalhamento": "spread",
    "proporcao": "ratio",
    "quialteras": "tuplets",
    "minimo": "min_value",
    "maximo": "max_value",
    "fundamental": "fundamental",
    "parciais": "partials",
    "compassos": "measures",
    "microtom": "microtone",
    "pausas": "rests",
    "salto_maximo": "max_leap",
    "intervalo": "interval",
    "eixo": "axis",
    "passos": "steps",
    "fator": "factor",
    "expoente": "exponent",
    "divisao": "division",
    "alturas_por_quadro": "pitches_per_frame",
    "x0": "x0",
    "turbulencia": "turbulence",
    "alturas": "pitches",
    "base": "base",
    "passo": "step",
    "sem_unissono": "no_unison",
    "sem_cruzamento": "no_voice_crossing",
    "notas_comuns": "common_tones",
    "parciais_inicial": "initial_partials",
    "parciais_final": "final_partials",
    "amplitude": "amplitude",
    "quadros": "frames",
}

RECIPE_ALIASES: dict[str, str] = {
    "espectral": "spectral",
    "ritmo_elastico": "elastic_rhythm",
}


# User boundary: free-form PT/EN request text.
_MICROTONAL_TERMS = re.compile(
    r"micro-?to[nm]|quarter[- ]?tones?|eighth[- ]?tones?|(quartos?|oitavos?) de tom|1/[48][- ]?(de )?(tom|tone)"
    r"|half[- ](sharp|flat)|\bedo\b|\d+[.,]\d*[1-9]\s*semit",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(without|no|not|never|avoid|sem|nada de|n[aã]o|evite)\b(\W+\w+)?\W*$", re.IGNORECASE)
_CENTS = re.compile(r"(\d+)\s*cents?\b", re.IGNORECASE)


def requests_microtones(text: str) -> bool:
    """Explicit microtonal intent: microtones, quarter/eighth-tones, or a non-semitone offset in cents."""
    # shortcut: negation checks one word back only, so long negated clauses slip through; ask the planner LLM if needed.
    terms = [m for m in _MICROTONAL_TERMS.finditer(text) if not _NEGATED.search(text[: m.start()])]
    return bool(terms) or any(int(n) % 100 for n in _CENTS.findall(text))


def normalize_mode(value: str) -> Mode:
    return _MODE_ALIASES.get(str(value).strip().lower(), value)  # type: ignore[return-value]


def normalize_procedure_id(name: str) -> str:
    cleaned = str(name).strip()
    return PROCEDURE_ALIASES.get(cleaned, cleaned)


def normalize_parameter_name(name: str, procedure: str = "") -> str:
    cleaned = str(name).strip()
    proc = normalize_procedure_id(procedure)
    if proc == "deform_rhythm" and cleaned in ("minimo", "min_factor"):
        return "min_factor"
    if proc == "deform_rhythm" and cleaned in ("maximo", "max_factor"):
        return "max_factor"
    return PARAMETER_ALIASES.get(cleaned, cleaned)


def normalize_recipe(name: str) -> str:
    cleaned = str(name).strip()
    return RECIPE_ALIASES.get(cleaned, cleaned)


class Parameter(BaseModel):
    name: str
    value: float


class Operation(BaseModel):
    section: str = Field(default="", description="Target section name; empty uses the plan selection.")
    procedure: str = Field(description="Procedure ID from the catalog.")
    parameters: list[Parameter] = Field(default_factory=list, description="Omitted parameters use catalog defaults.")
    condition: str = Field(
        default="", description="Only for generate_notagen: one of the conditions listed in the catalog."
    )

    def as_dict(self) -> dict[str, float]:
        return {p.name: p.value for p in self.parameters}

    @model_validator(mode="after")
    def _normalize_procedure_and_params(self) -> "Operation":
        proc = normalize_procedure_id(self.procedure)
        params = [
            Parameter(name=normalize_parameter_name(p.name, proc), value=p.value)
            for p in self.parameters
        ]
        object.__setattr__(self, "procedure", proc)
        object.__setattr__(self, "parameters", params)
        return self


class Selection(BaseModel):
    """Target slice where the operation may modify events; empty = whole piece."""

    parts: list[str] = Field(default_factory=list)
    voices: list[str] = Field(default_factory=list)
    start_measure: int | None = None
    end_measure: int | None = None

    def contains(self, part: str, voice: str | None, measure: int | None) -> bool:
        """Parts/voices enter with `measure=None`: they are inside the selection only when measures are unrestricted."""
        unrestricted_measures = self.start_measure is None and self.end_measure is None
        if measure is None:
            in_span = unrestricted_measures
        else:
            in_span = (self.start_measure or 1) <= measure <= (self.end_measure or measure)
        return (not self.parts or part in self.parts) and (not self.voices or voice in self.voices) and in_span

    @property
    def is_full(self) -> bool:
        return self == Selection()


def normalize_instrument_name(name: str) -> str:
    try:
        return identify(name)
    except ValueError:
        return str(name).strip()


class Section(BaseModel):
    name: str = Field(min_length=1)
    character: str
    start_measure: int = Field(ge=1)
    end_measure: int = Field(ge=1)
    material: str
    techniques: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "Section":
        if self.end_measure < self.start_measure:
            raise ValueError("section has inverted measure bounds")
        object.__setattr__(self, "techniques", [normalize_technique(t) for t in self.techniques])
        return self


class Plan(BaseModel):
    recipe: str = Field(default="", description="Aesthetic recipe name from the catalog, or empty.")
    recipe_rationale: str = Field(default="", description="Why this recipe chain fits the request; not imitation.")
    sections: list[Section] = Field(default_factory=list)
    goal: str
    instruments: list[str] = Field(
        default_factory=list,
        max_length=16,
        description='One per part: ["violin", "violin", ...].',
    )
    structure: str = Field(default="", description="Temporal structure (measures, sections).")
    techniques: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list, description="Checked after the operation.")
    hypotheses: list[str] = Field(
        default_factory=list,
        description='Subjective request translated into parameters: "fragmented = density 1 -> 3 attacks/quarter in m. 7-8".',
    )
    operations: list[Operation] = Field(min_length=1, max_length=8)
    selection: Selection = Field(default_factory=Selection)
    mode: Mode = Field(
        default="auto",
        description="Only set when the user explicitly requests an engine; otherwise auto.",
    )
    criteria: list[str] = Field(default_factory=list, description="Validation criteria for the result.")
    seed: int | None = Field(default=None, description="Only set when the user requests reproducing a specific seed.")
    microtonal: bool = Field(
        default=False,
        description="Set by the application from the request text (explicit microtones); otherwise pitches stay on semitones.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            updated = dict(data)
            if "mode" in updated and isinstance(updated["mode"], str):
                updated["mode"] = normalize_mode(updated["mode"])
            if "recipe" in updated and isinstance(updated["recipe"], str):
                updated["recipe"] = normalize_recipe(updated["recipe"])
            if "recipe_reason" in updated and "recipe_rationale" not in updated:
                updated["recipe_rationale"] = updated.pop("recipe_reason")
            return updated
        return data

    @model_validator(mode="after")
    def _validate_sections(self) -> "Plan":
        object.__setattr__(self, "instruments", [normalize_instrument_name(i) for i in self.instruments])
        object.__setattr__(self, "techniques", [normalize_technique(t) for t in self.techniques])
        names = [s.name.casefold() for s in self.sections]
        if len(set(names)) != len(names):
            raise ValueError("duplicate section names")
        ordered = sorted(self.sections, key=lambda s: s.start_measure)
        if any(a.end_measure >= b.start_measure for a, b in zip(ordered, ordered[1:])):
            raise ValueError("overlapping sections")
        return self

    def grid(self, microtone: int) -> int:
        """Quantization for a pitch-changing step: the requested microtone, or semitones (0) unless microtonal."""
        return microtone if self.microtonal else 0

    def for_step(self, index: int) -> "Plan":
        name = self.operations[index].section
        if not name:
            return self
        section = next((s for s in self.sections if s.name.casefold() == name.casefold()), None)
        if section is None:
            raise ValueError(f"unknown section: {name}")
        return self.model_copy(
            update={
                "selection": self.selection.model_copy(
                    update={"start_measure": section.start_measure, "end_measure": section.end_measure}
                ),
                "techniques": section.techniques or self.techniques,
            }
        )

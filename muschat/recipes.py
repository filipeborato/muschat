"""Single source of aesthetic recipes for planning and documentation: `python -m muschat.recipes`."""

from dataclasses import dataclass

from muschat.plan import Operation, Parameter, Plan, normalize_procedure_id, normalize_recipe


@dataclass(frozen=True)
class Recipe:
    description: str
    chain: tuple[tuple[str, dict[str, float]], ...]


RECIPES: dict[str, Recipe] = {
    "scelsi": Recipe(
        "Microtonal pitch deviation and intra-gesture glissandos.",
        (("detune", {"deviation": 100, "resolution": 2, "curve": 1, "glissandos": 0.7}),),
    ),
    "lachenmann": Recipe(
        "Pitches progressively yield to playable instrumental noise techniques.",
        (("timbral_substitution", {"start": 0.2, "end": 0.9, "noise_family": 0}),),
    ),
    "sciarrino": Recipe(
        "Silences, prolongations, natural harmonics/whistle tones, and pppp.",
        (("rarefy", {"keep": 0.3, "harmonics": 0.7, "dynamic": 0, "accents": 0.03}),),
    ),
    "ligeti": Recipe(
        "Close canon with pitch spread and microtonal motion.",
        (
            ("micropolyphony", {"source_part": 1, "offset": 1, "variation": 0, "spread": 150}),
            ("detune", {"deviation": 50, "resolution": 2, "curve": 0, "glissandos": 0}),
        ),
    ),
    "spectral": Recipe(
        "Harmonic partials distributed by register, followed by microtonal drift.",
        (
            ("spectralize", {"fundamental": 3600, "partials": 16, "microtone": 1, "max_leap": 700}),
            ("detune", {"deviation": 50, "resolution": 2, "curve": 0, "glissandos": 0.4}),
        ),
    ),
    "elastic_rhythm": Recipe(
        "Gestures deformed into irregular tuplets and breath rests.",
        (("deform_rhythm", {"ratio": 0.6, "tuplets": 15, "min_factor": 0.5, "max_factor": 1}),),
    ),
}


def documentation() -> str:
    lines = [
        "# Aesthetic Recipes",
        "",
        "Starting points by aesthetic reference, not composer imitations.",
        "Source is `muschat/recipes.py`; regenerate with `uv run python -m muschat.recipes`.",
        "",
        "For new material, precede the chain with NotaGen generation (installed condition); existing material can be transformed directly.",
        "",
    ]
    for name, recipe in RECIPES.items():
        chain = " → ".join(
            f"{op}({', '.join(f'{k}={v:g}' for k, v in params.items())})" for op, params in recipe.chain
        )
        lines.append(f"- **{name}**: {recipe.description} {chain}")
    lines += [
        "",
        "## Notation Limits",
        "",
        "Micropolyphony quantizes onsets and endings to 1/64 of a whole note and clips the tail to the selection. "
        "Rhythmic deformation approximates scaling factors to representable note values, absorbs following rests to "
        "stretch gestures, and refuses gestures already inside tuplets or too short without altering the score. "
        "String harmonics use standard tuning and partials 2–8. Aesthetic evaluation rests with the composer's ear.",
    ]
    return "\n".join(lines) + "\n"


def expand_recipe(plan: Plan) -> Plan:
    if not plan.recipe:
        return plan
    recipe_key = normalize_recipe(plan.recipe)
    if recipe_key not in RECIPES:
        raise ValueError(f"unknown recipe: {plan.recipe}")
    recipe = RECIPES[recipe_key]
    names = [name for name, _ in recipe.chain]
    existing = [op for op in plan.operations if normalize_procedure_id(op.procedure) in names]
    if [normalize_procedure_id(o.procedure) for o in existing] == names:
        operations = plan.operations
    else:
        if existing:
            raise ValueError("partial recipe chain: provide the full chain or only the generation operation")
        chain = [
            Operation(procedure=name, parameters=[Parameter(name=k, value=v) for k, v in params.items()])
            for name, params in recipe.chain
        ]
        operations = [*plan.operations, *chain]
    hypothesis = f"Recipe {recipe_key}: {plan.recipe_rationale or recipe.description}"
    return Plan.model_validate(
        {
            **plan.model_dump(),
            "recipe": recipe_key,
            "operations": [o.model_dump() for o in operations],
            "hypotheses": plan.hypotheses if hypothesis in plan.hypotheses else [*plan.hypotheses, hypothesis],
        }
    )


if __name__ == "__main__":
    from pathlib import Path

    Path("docs/receitas.md").write_text(documentation(), encoding="utf-8")

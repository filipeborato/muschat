"""Contract test enforcing canonical English codebase, removed shims, and English LLM prompts."""

import ast
from pathlib import Path

from muschat import prompts
from muschat.procedures import CATALOG

ROOT = Path(__file__).resolve().parents[1]

REMOVED_LEGACY_MODULES = {
    "muschat/composicao.py",
    "muschat/erros.py",
    "muschat/exportacao.py",
    "muschat/instrumentos.py",
    "muschat/musicir.py",
    "muschat/notacao",
    "muschat/orquestracao.py",
    "muschat/orquestrador.py",
    "muschat/patches_om.py",
    "muschat/plano.py",
    "muschat/procedimentos",
    "muschat/rag/busca.py",
    "muschat/rag/indice.py",
    "muschat/rag/leitores.py",
    "muschat/receitas.py",
    "muschat/referencias.py",
    "muschat/registro.py",
    "muschat/restricoes.py",
    "muschat/roteador.py",
    "muschat/secoes.py",
    "muschat/seguranca.py",
    "servicos",
}

FORBIDDEN_IMPORT_STEMS = {
    "servicos",
    "muschat.composicao",
    "muschat.erros",
    "muschat.exportacao",
    "muschat.instrumentos",
    "muschat.musicir",
    "muschat.notacao",
    "muschat.orquestracao",
    "muschat.orquestrador",
    "muschat.patches_om",
    "muschat.plano",
    "muschat.procedimentos",
    "muschat.rag.busca",
    "muschat.rag.indice",
    "muschat.rag.leitores",
    "muschat.receitas",
    "muschat.referencias",
    "muschat.registro",
    "muschat.restricoes",
    "muschat.roteador",
    "muschat.secoes",
    "muschat.seguranca",
}

FORBIDDEN_PROMPT_MARKERS = (
    "pergunta_usuario",
    "analise_interna",
    "<trechos>",
    "<trecho ",
    "composicao_atual",
    "<operacao>",
    "<secoes>",
    "no_escopo",
    "tecnicas_por_gesto",
    "serie_harmonica",
    "gerar_notagen",
)


def _python_files():
    for folder in (ROOT / "muschat", ROOT / "services", ROOT / "tests"):
        if folder.exists():
            yield from sorted(folder.rglob("*.py"))
    yield ROOT / "app.py"


def test_legacy_portuguese_modules_and_shims_are_removed():
    still_present = [rel for rel in sorted(REMOVED_LEGACY_MODULES) if (ROOT / rel).exists()]
    assert still_present == []


def test_no_source_or_test_imports_legacy_portuguese_modules():
    offenders: list[tuple[str, str]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == stem or node.module.startswith(f"{stem}.") for stem in FORBIDDEN_IMPORT_STEMS):
                    offenders.append((path.relative_to(ROOT).as_posix(), node.module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == stem or alias.name.startswith(f"{stem}.") for stem in FORBIDDEN_IMPORT_STEMS):
                        offenders.append((path.relative_to(ROOT).as_posix(), alias.name))
    assert offenders == []


def test_llm_prompts_and_catalog_use_canonical_english_contract():
    all_prompts = "\n".join(
        [
            prompts.SCOPE,
            prompts.SYSTEM,
            prompts.ANALYSIS,
            prompts.TURN_CONTEXT,
            prompts.NOTATION,
            prompts.PLAN,
            prompts.EXCERPT,
            prompts.REFUSAL,
        ]
    )
    for tag in ("<user_question>", "<internal_analysis>", "<excerpt", "<excerpts>", "<operation>"):
        assert tag in all_prompts
    for marker in FORBIDDEN_PROMPT_MARKERS:
        assert marker not in all_prompts, marker
    assert "gestural_techniques" in prompts.PLAN
    assert "gestural_techniques" in CATALOG
    assert "harmonic_series" in CATALOG
    assert "Reply in the user's language" in prompts.SYSTEM


def test_approved_openmusic_patches_use_canonical_english_paths():
    from muschat.om_patches import PATCHES

    for patch in PATCHES.values():
        if patch.file is None:
            continue
        path = ROOT / patch.file
        assert path.is_file(), f"missing approved patch: {patch.file}"
        assert patch.file in {
            "patches/harmonic_series.lisp",
            "patches/chaos_verhulst.lisp",
            "patches/distribute_by_constraints.lisp",
            "patches/additive_synthesis.lisp",
            "patches/pitches_by_constraints.lisp",
        }

"""Turn orchestration: analysis (CoT) -> retrieval -> notation (on demand) -> synthesis."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx2
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from muschat import prompts
from muschat.composition import Composition, Version
from muschat.notagen import ComposerBackend
from muschat.notation.lilypond import to_lilypond
from muschat.notation.model import ScoreExample, to_score_ir
from muschat.notation.musicxml import to_musicxml
from muschat.notation.techniques import TECHNIQUES
from muschat.orchestrator import compose, resume, summarize_score
from muschat.registry import Registry
from muschat.router import Mode
from muschat.score_ir import ScoreIR
from muschat.security import sanitize as _sanitize

__all__ = [
    "GENERATED_CAPTION",
    "MAX_CORPUS_SCORES",
    "MAX_GENERATED_MEASURES",
    "Analysis",
    "ChatSession",
    "Excerpt",
    "Generator",
    "NullRetriever",
    "Response",
    "Retriever",
    "ScoreView",
    "respond",
    "version_score_view",
]


class Analysis(BaseModel):
    in_scope: bool
    instruments: list[str]
    techniques: list[str]
    plan: str
    queries: list[str]
    notation: Literal["none", "corpus", "generate", "compose"] = "none"


@dataclass(frozen=True)
class Excerpt:
    text: str
    source: str
    location: str = ""  # "p. 12" | "m.7–9" | heading trail | example label
    id: str = ""
    score: float = 0.0
    origin: tuple[str, ...] = ()  # dense | lexical | work
    composer: str = ""
    musicxml: str = ""  # standalone document when the excerpt is a corpus score


@dataclass(frozen=True)
class ScoreView:
    """Score displayed below the reply: real corpus excerpt, illustrative generated example, or composition."""

    title: str
    musicxml: str
    caption: str
    origin: Literal["corpus", "generated", "composition"]
    lilypond: dict[str, str] = field(default_factory=dict)  # score and parts (.ly), for compositions


@dataclass(frozen=True)
class Response:
    final: str
    analysis: dict
    excerpts: list[Excerpt]
    out_of_scope: bool
    scores: list[ScoreView] = field(default_factory=list)


MAX_CORPUS_SCORES = 2
MAX_GENERATED_MEASURES = 8
GENERATED_CAPTION = "Exemplo ilustrativo gerado — não é trecho de obra publicada"


class Generator(Protocol):
    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any: ...


class ChatSession(Protocol):
    def get_history(self) -> list: ...
    def send_message(self, message: str, config: Any = None) -> Any: ...


class Retriever(Protocol):
    def search(self, queries: list[str], k: int, instruments: Sequence[str] = ()) -> list[Excerpt]: ...


class NullRetriever:
    """No-op retriever used when no usable index exists (synthesis declares general knowledge)."""

    def search(self, queries: list[str], k: int, instruments: Sequence[str] = ()) -> list[Excerpt]:
        return []


# =============================================================================
# REASONING STRATEGY: Chained and grounded Chain-of-Thought (RAG)
#
#   1. ANALYSIS (hidden): atomic `generate_content` call, OUTSIDE chat history,
#      using history as context. The model reasons step by step (scope ->
#      instruments/techniques -> Least-to-Most plan -> queries) and returns
#      schema-validated JSON. Invalid JSON -> safe fallback.
#   2. SCOPE: out of domain -> standard refusal without running stages 3-4.
#   3. RETRIEVAL: analysis queries search the corpus via hybrid dense + BM25
#      (RRF), boosted by instrument family.
#   4. NOTATION (only when requested by analysis): real score from the corpus
#      (MusicXML from excerpts) or, if none, an illustrative generated example
#      validated by JSON schema and converted to MusicXML by a deterministic
#      builder. Composition request: orchestrator plans (Plan validated against
#      catalog), executes procedures, and records a new Version.
#   5. SYNTHESIS: `chat.send_message(question)` (native memory); analysis,
#      excerpts, and score list go into the turn's `system_instruction`,
#      delimited by XML tags treated as data, and are not stored in history.
# =============================================================================
def respond(
    question: str,
    *,
    generator: Generator,
    chat: ChatSession,
    retriever: Retriever,
    model: str,
    k: int = 6,
    composition: Composition | None = None,
    mode: Mode = "auto",
    registry: Registry | None = None,
    composer: ComposerBackend | None = None,
) -> Response:
    if composition is not None and composition.pending is not None:
        pending = composition.pending
        task = pending.task
        text = question.strip()
        if re.match(r"^[\d(+\-.]", text):
            result = resume(composition, text, composer=composer)
            unit_note = (
                " Harmonic series values are in midicents (3600 = C2, 6000 = C4), not Hz."
                if task and task.procedure == "harmonic_series"
                else ""
            )
            return Response(
                result.summary + unit_note,
                {},
                [],
                False,
                [version_score_view(result.version)] if result.version else [],
            )
        expected_fmt = task.patch.output_format if task else "numeric patch output"
        return Response(
            f"The composition is awaiting output for task {task.id if task else ''}. "
            f"Expected format: {expected_fmt}. Paste only the numbers here in the chat (with or without parentheses), "
            "or upload saida.txt in the sidebar and click Import and continue. "
            "No new composition was generated in this turn. To change your request, discard the task in the sidebar.",
            {},
            [],
            False,
        )
    safe_question = _sanitize(question)

    analysis = _analyze(safe_question, generator=generator, chat=chat, model=model)
    if not analysis.in_scope:
        return Response(prompts.REFUSAL, analysis.model_dump(), [], out_of_scope=True)

    excerpts = retriever.search(analysis.queries, k, analysis.instruments)
    operation = "none"
    if analysis.notation == "compose":
        composition = composition if composition is not None else Composition()
        result = compose(
            safe_question,
            composition,
            generator=generator,
            chat=chat,
            model=model,
            mode=mode,
            registry=registry,
            composer=composer,
        )
        operation = result.summary
        scores = [version_score_view(result.version)] if result.version else []
        if result.task is not None:
            return Response(
                result.summary + f" Expected output: {result.task.patch.output_format}. "
                "Run the patch and paste only the numbers here in the chat, or import saida.txt via the sidebar. "
                "The composition has not yet been completed.",
                analysis.model_dump(),
                excerpts,
                False,
            )
        if result.version is None:
            return Response(result.summary, analysis.model_dump(), excerpts, False)
        operation += "\nInventory of the score actually generated:\n" + summarize_score(result.version.ir)
    else:
        scores = _resolve_scores(safe_question, analysis, excerpts, generator=generator, chat=chat, model=model)

    context = prompts.TURN_CONTEXT.format(
        analysis=_sanitize(analysis.model_dump_json(indent=2)),
        excerpts="\n\n".join(_format_excerpt(e) for e in excerpts) or "none",
        scores="\n".join(f"- {_sanitize(s.caption)}: {_sanitize(s.title)}" for s in scores) or "none",
        operation=_sanitize(operation),
    )
    try:
        reply = chat.send_message(
            safe_question,
            config=types.GenerateContentConfig(system_instruction=prompts.SYSTEM + context, temperature=0.5),
        )
        text = (reply.text or "").strip()
    except (errors.APIError, httpx2.TransportError):
        if analysis.notation != "compose":
            raise
        text = ""
    if not text and analysis.notation == "compose":
        text = operation + " The Gemini explanation was unavailable; this is the actual operation result."
    return Response(text, analysis.model_dump(), excerpts, False, scores)


def _resolve_scores(
    question: str,
    analysis: Analysis,
    excerpts: list[Excerpt],
    *,
    generator: Generator,
    chat: ChatSession,
    model: str,
) -> list[ScoreView]:
    if analysis.notation == "none":
        return []
    if analysis.notation == "corpus":
        with_score = sorted((e for e in excerpts if e.musicxml), key=lambda e: -e.score)
        real_scores = [
            ScoreView(f"{e.source} — {e.location}", e.musicxml, _corpus_caption(e), "corpus")
            for e in with_score[:MAX_CORPUS_SCORES]
        ]
        if real_scores:
            return real_scores
    generated = _generate_score(question, analysis, generator=generator, chat=chat, model=model)
    return [generated] if generated else []


def _corpus_caption(e: Excerpt) -> str:
    return f"Do acervo: {e.source}, {e.location}" + (f" — {e.composer}" if e.composer else "")


def _generate_score(
    question: str,
    analysis: Analysis,
    *,
    generator: Generator,
    chat: ChatSession,
    model: str,
) -> ScoreView | None:
    request = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    f"<user_question>\n{question}\n</user_question>\n"
                    f"<internal_analysis>\n{_sanitize(analysis.model_dump_json())}\n</internal_analysis>"
                )
            )
        ],
    )
    try:
        reply = generator.generate_content(
            model=model,
            contents=[*chat.get_history(), request],
            config=types.GenerateContentConfig(
                system_instruction=prompts.NOTATION.format(techniques=", ".join(TECHNIQUES)),
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=ScoreExample,
            ),
        )
        example = ScoreExample.model_validate_json(reply.text or "")
    except (ValidationError, errors.APIError, httpx2.TransportError):
        return None
    example.measures = example.measures[:MAX_GENERATED_MEASURES]
    if not example.measures:
        return None
    return ScoreView(example.title, to_musicxml(to_score_ir(example)), GENERATED_CAPTION, "generated")


def _analyze(question: str, *, generator: Generator, chat: ChatSession, model: str) -> Analysis:
    request = types.Content(
        role="user",
        parts=[types.Part(text=f"<user_question>\n{question}\n</user_question>")],
    )
    reply = generator.generate_content(
        model=model,
        contents=[*chat.get_history(), request],
        config=types.GenerateContentConfig(
            system_instruction=prompts.ANALYSIS,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=Analysis,
        ),
    )
    try:
        return Analysis.model_validate_json(reply.text or "")
    except ValidationError:
        return Analysis(in_scope=True, instruments=[], techniques=[], plan="", queries=[question])


def _format_excerpt(e: Excerpt) -> str:
    return prompts.EXCERPT.format(
        source=_attr(e.source),
        location=_attr(e.location) or "?",
        author=f' author="{_attr(e.composer)}"' if e.composer else "",
        text=_sanitize(e.text),
    )


def _attr(value: str) -> str:
    return _sanitize(value).replace('"', "'")


def version_score_view(v: Version) -> ScoreView:
    """ScoreView for a composition Version in chat (MusicXML always; LilyPond when Abjad succeeds)."""
    return ScoreView(
        v.ir.title,
        to_musicxml(v.ir),
        f"Composição da conversa — {v.summary}",
        "composition",
        _lilypond_or_empty(v.ir),
    )


def _lilypond_or_empty(ir: ScoreIR) -> dict[str, str]:
    try:
        return to_lilypond(ir)
    except Exception:
        return {}

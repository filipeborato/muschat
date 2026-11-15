import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from google.genai import errors

from muschat.composition import Composition
from muschat.notation.model import ScoreExample
from muschat.plan import Plan
from muschat.reasoning import Excerpt, respond
from tests.test_reasoning import ANALYSIS_OK, FakeChat, FakeRetriever

EXAMPLE_SCORE = {
    "title": "Alternation",
    "instrument": "Violin",
    "clef": "treble",
    "meter": "4/4",
    "measures": [
        {
            "notes": [
                {"pitches": ["D5"], "duration": "half", "techniques": ["sul ponticello"]},
                {"pitches": ["D5"], "duration": "half", "techniques": ["col legno battuto"]},
            ]
        }
    ],
}
CORPUS_XML = "<score-partwise><part-list/></score-partwise>"


class SequentialGenerator:
    """Returns one text payload per call, in order."""

    def __init__(self, log, *texts):
        self.log, self.texts, self.calls = log, list(texts), []

    def generate_content(self, *, model, contents, config):
        self.log.append("generator")
        self.calls.append(SimpleNamespace(contents=contents, config=config))
        return SimpleNamespace(text=self.texts.pop(0))


def _run(notation, *extras, excerpts=()):
    log = []
    analysis = json.dumps({**ANALYSIS_OK, "notation": notation})
    g = SequentialGenerator(log, analysis, *extras)
    c, r = FakeChat(log), FakeRetriever(log, excerpts)
    resp = respond("question", generator=g, chat=c, retriever=r, model="m")
    return resp, g, c


def _system(c):
    return c.messages[0].config.system_instruction


def test_none_notation_makes_no_extra_call_and_returns_no_score():
    resp, g, c = _run("none")
    assert len(g.calls) == 1 and resp.scores == []


def test_analysis_without_notation_field_remains_valid():
    log = []
    g = SequentialGenerator(log, json.dumps(ANALYSIS_OK))
    resp = respond("q", generator=g, chat=FakeChat(log), retriever=FakeRetriever(log), model="m")
    assert resp.out_of_scope is False and resp.scores == []


def test_corpus_uses_real_excerpts_without_generating():
    excerpts = [
        Excerpt(text="t1", source="Quartet", location="m.7–9", composer="Filipe Borato", musicxml=CORPUS_XML),
        Excerpt(text="t2", source="Grimoire", location="Sul pont."),
        Excerpt(text="t3", source="Snippets", location="2. col legno batt.", musicxml=CORPUS_XML),
        Excerpt(text="t4", source="Snippets", location="3. c.l. tratto", musicxml=CORPUS_XML),
    ]
    resp, g, c = _run("corpus", excerpts=excerpts)
    assert len(g.calls) == 1
    assert [s.origin for s in resp.scores] == ["corpus", "corpus"]
    assert resp.scores[0].caption == "Do acervo: Quartet, m.7–9 — Filipe Borato"
    assert "Do acervo: Quartet, m.7–9 — Filipe Borato" in _system(c)


def test_corpus_without_score_in_excerpts_generates_illustrative_example():
    resp, g, c = _run("corpus", json.dumps(EXAMPLE_SCORE))
    assert len(g.calls) == 2
    assert [s.origin for s in resp.scores] == ["generated"]


def test_generate_makes_schema_call_and_returns_musicxml():
    resp, g, c = _run("generate", json.dumps(EXAMPLE_SCORE))
    call = g.calls[1]
    assert call.config.response_schema is ScoreExample
    assert call.config.response_mime_type == "application/json"
    [s] = resp.scores
    assert s.origin == "generated" and s.title == "Alternation"
    assert s.caption.startswith("Exemplo ilustrativo gerado")
    assert ET.fromstring(s.musicxml).findtext(".//part-name") == "Violin"
    assert "Exemplo ilustrativo gerado" in _system(c)


def test_order_analysis_search_notation_synthesis():
    log = []
    g = SequentialGenerator(log, json.dumps({**ANALYSIS_OK, "notation": "generate"}), json.dumps(EXAMPLE_SCORE))
    respond("q", generator=g, chat=FakeChat(log), retriever=FakeRetriever(log), model="m")
    assert log == ["generator", "search", "generator", "synthesis"]


def test_invalid_score_example_does_not_break_turn():
    resp, g, c = _run("generate", "{this is not a score")
    assert resp.scores == [] and resp.final == "final reply"


def test_excerpt_includes_author_in_context():
    excerpt = Excerpt(text="x", source="Etude II", location="m.1–3", composer="Filipe Borato")
    resp, g, c = _run("none", excerpts=[excerpt])
    assert '<excerpt source="Etude II" location="m.1–3" author="Filipe Borato">' in _system(c)


def test_corpus_selects_highest_scoring_excerpts():
    excerpts = [
        Excerpt(text="a", source="Quartet", location="m.3–5", score=0.1, musicxml=CORPUS_XML),
        Excerpt(text="b", source="Quartet", location="m.7–9", score=0.9, musicxml=CORPUS_XML),
    ]
    resp, g, c = _run("corpus", excerpts=excerpts)
    assert [s.title for s in resp.scores] == ["Quartet — m.7–9", "Quartet — m.3–5"]


def test_api_failure_during_notation_does_not_fail_turn():
    class FailingNotationGenerator(SequentialGenerator):
        def generate_content(self, *, model, contents, config):
            if self.calls:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "unstable"}})
            return super().generate_content(model=model, contents=contents, config=config)

    log = []
    g = FailingNotationGenerator(log, json.dumps({**ANALYSIS_OK, "notation": "generate"}))
    resp = respond("q", generator=g, chat=FakeChat(log), retriever=FakeRetriever(log), model="m")
    assert resp.scores == [] and resp.final == "final reply"


def test_generated_example_is_capped_at_8_measures():
    long = {**EXAMPLE_SCORE, "measures": EXAMPLE_SCORE["measures"] * 12}
    resp, g, c = _run("generate", json.dumps(long))
    assert len(list(ET.fromstring(resp.scores[0].musicxml).iter("measure"))) == 8


PLAN = {
    "goal": "quartet",
    "instruments": ["violin", "violin", "viola", "cello"],
    "operations": [{"procedure": "harmonic_series", "parameters": [{"name": "measures", "value": 8}]}],
    "seed": 1,
}


def _compose_turn(analysis_notation, *extras, composition=None):
    log = []
    g = SequentialGenerator(log, json.dumps({**ANALYSIS_OK, "notation": analysis_notation}), *extras)
    c = FakeChat(log)
    resp = respond("create eight measures", generator=g, chat=c, retriever=FakeRetriever(log), model="m", composition=composition)
    return resp, g, c


def test_composition_request_records_version_and_shows_score_with_summary():
    composition = Composition()
    resp, g, c = _compose_turn("compose", json.dumps(PLAN), composition=composition)
    assert len(g.calls) == 2 and g.calls[1].config.response_schema is Plan
    assert composition.current is not None and len(composition.current.ir.parts) == 4
    [score] = resp.scores
    assert score.origin == "composition" and score.musicxml.count("<score-part ") == 4
    assert "harmonic_series" in _system(c) and composition.current.summary in _system(c)


@pytest.mark.parametrize("failure", [False, True])
def test_composition_reaches_chat_even_without_synthesis(failure):
    class ChatWithoutSynthesis(FakeChat):
        def send_message(self, message, config=None):
            if failure:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "unavailable"}})
            return SimpleNamespace(text=" ")

    log = []
    c = Composition()
    g = SequentialGenerator(log, json.dumps({**ANALYSIS_OK, "notation": "compose"}), json.dumps(PLAN))
    r = respond("compose", generator=g, chat=ChatWithoutSynthesis(log), retriever=FakeRetriever(log), model="m", composition=c)
    assert len(c.versions) == 1 and c.current is not None
    assert c.current.summary in r.final
    assert len(r.scores) == 1 and r.scores[0].origin == "composition"


def test_refused_plan_shows_reason_without_synthesis_substituting_proposal():
    composition = Composition()
    resp, g, c = _compose_turn(
        "compose", json.dumps({**PLAN, "operations": [{"procedure": "rm -rf"}]}), composition=composition
    )
    assert resp.scores == [] and composition.current is None
    assert "refused" in resp.final and "no engine executes rm -rf" in resp.final
    assert c.messages == []


def test_invalid_plan_displays_offending_field():
    resp, _, chat = _compose_turn("compose", json.dumps({**PLAN, "operations": []}))
    assert "operations" in resp.final and "invalid" in resp.final
    assert not resp.scores and chat.messages == []


def test_ordinary_question_skips_orchestrator():
    composition = Composition()
    resp, g, c = _compose_turn("none", composition=composition)
    assert len(g.calls) == 1 and composition.versions == []
    assert "<operation>\nnone\n</operation>" in _system(c)

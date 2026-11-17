from fractions import Fraction as F

import httpx2
import pytest
from google.genai import errors as genai_errors
from pydantic import ValidationError

from muschat.config import DEFAULT_EMBED_MODEL, DEFAULT_MODEL, MissingApiKeyError, load_config
from muschat.errors import error_message
from muschat.score_ir import (
     ElectronicEvent,
    Curve,
    Measure,
    Note,
    Part,
    Partial,
    Pitch,
    Provenance,
    Rest,
    ScoreIR,
    SpectralFrame,
    Tuplet,
    Voice,
    diff_scores,
    figures,
    gestures,
    is_figure,
    locate,
    slices,
    ties_to,
)
from muschat.security import sanitize

LLM_PROV = Provenance(source="llm")


def sample_quartet() -> ScoreIR:
    violin = Part(
        id="vln",
        instrument="violin",
        voices=[
            Voice(
                id="vln1",
                events=[
                    Note(
                        id="a",
                        start=F(0),
                        duration=F(1, 2),
                        pitches=[Pitch(midicents=6950, spelling="A")],
                        techniques=["sul ponticello"],
                        dynamic="pp",
                        provenance=LLM_PROV,
                    ),
                    Rest(id="b", start=F(1, 2), duration=F(1, 2), provenance=LLM_PROV),
                ],
            )
        ],
    )
    cello = Part(
        id="vc",
        instrument="cello",
        clef="bass",
        range=(3600, 7600),
        voices=[
            Voice(
                id="vc1",
                events=[
                    Note(id="c", start=F(0), duration=F(1, 6), pitches=[Pitch(midicents=3600)], provenance=LLM_PROV),
                    Note(id="d", start=F(1, 6), duration=F(1, 6), pitches=[Pitch(midicents=4300)], provenance=LLM_PROV),
                    Note(id="e", start=F(1, 3), duration=F(1, 6), pitches=[Pitch(midicents=4800)], provenance=LLM_PROV),
                    Rest(id="f", start=F(1, 2), duration=F(1, 2), provenance=LLM_PROV),
                ],
                tuplets=[Tuplet(id="q1", numerator=3, denominator=2, events=["c", "d", "e"])],
            ),
            Voice(
                id="vc2",
                events=[
                    Curve(
                        id="g",
                        start=F(0),
                        duration=F(1),
                        parameter="filter",
                        points=[(F(0), 0.0), (F(1), 1.0)],
                        provenance=Provenance(source="algorithm", operation="op1"),
                    ),
                ],
            ),
            Voice(
                id="vc3",
                events=[
                    SpectralFrame(
                        id="h",
                        start=F(0),
                        duration=F(1),
                        partials=[Partial(frequency=65.4, amplitude=1.0)],
                        provenance=Provenance(source="openmusic"),
                    ),
                    ElectronicEvent(
                        id="i",
                        start=F(1),
                        duration=F(0),
                        description="cue 1",
                        provenance=Provenance(source="composer"),
                    ),
                ],
            ),
        ],
    )
    return ScoreIR(
        title="Quartet",
        measures=[Measure(numerator=4, denominator=4, tempo=60)],
        parts=[violin, cello],
    )


def test_score_ir_json_roundtrip_and_invariants():
    ir = sample_quartet()
    dumped = ir.model_dump_json()
    assert '"measures"' in dumped and '"parts"' in dumped and '"pitches"' in dumped
    assert ScoreIR.model_validate_json(dumped) == ir
    assert locate(ir)["a"] == ("vln", "vln1", 1)
    assert is_figure(F(3, 8)) and figures(F(5, 8)) == [F(1, 2), F(1, 8)]
    assert slices([F(0), F(1)], F(1, 2), F(1)) == [(F(1, 2), F(1, 2)), (F(1), F(1, 2))]


def test_score_ir_rejects_overlapping_events_duplicate_ids_and_invalid_tuplets():
    with pytest.raises(ValidationError):
        Voice(
            id="v",
            events=[
                Rest(id="a", start=F(0), duration=F(1, 2), provenance=LLM_PROV),
                Rest(id="b", start=F(1, 4), duration=F(1, 2), provenance=LLM_PROV),
            ],
        )
    with pytest.raises(ValidationError):
        Part(id="p", instrument="cello", range=(7600, 3600), voices=[Voice(id="v")])
    with pytest.raises(ValidationError):
        Voice(
            id="v",
            events=[Rest(id="a", start=F(0), duration=F(1, 6), provenance=LLM_PROV)],
            tuplets=[Tuplet(id="q1", numerator=3, denominator=2, events=["missing"])],
        )


def test_diff_scores_tracks_added_removed_modified_and_measures():
    before = sample_quartet()
    assert not diff_scores(before, sample_quartet())
    after = before.model_copy(deep=True)
    note_d = after.parts[1].voices[0].events[1]
    assert isinstance(note_d, Note)
    after.parts[1].voices[0].events[1] = note_d.model_copy(
        update={"pitches": [Pitch(midicents=4350)], "dynamic": "f"}
    )
    after.measures[0] = after.measures[0].model_copy(update={"tempo": 90})
    diff = diff_scores(before, after)
    assert diff.modified == {"d": ["dynamic", "pitches"]}
    assert diff.measures == [1]
    assert diff.locations["d"] == ("vc", 1)


def test_gestures_and_ties_to():
    n1 = Note(id="n1", start=F(0), duration=F(1, 4), pitches=[Pitch(midicents=6000)], tied=True, provenance=LLM_PROV)
    n2 = Note(id="n2", start=F(1, 4), duration=F(1, 2), pitches=[Pitch(midicents=6000)], provenance=LLM_PROV)
    n3 = Note(id="n3", start=F(3, 4), duration=F(1, 4), pitches=[Pitch(midicents=6200)], provenance=LLM_PROV)
    assert ties_to(n1, n2) and not ties_to(n2, n3)
    assert [[n.id for n in g] for g in gestures([n1, n2, n3])] == [["n1", "n2"], ["n3"]]


def test_config_errors_and_security_in_english():
    cfg = load_config({"GEMINI_API_KEY": "k", "MUSCHAT_OPENMUSIC": "1"}, {})
    assert cfg.api_key == "k" and cfg.model == DEFAULT_MODEL and cfg.embed_model == DEFAULT_EMBED_MODEL
    assert cfg.openmusic_enabled is True
    with pytest.raises(MissingApiKeyError):
        load_config({}, {})
    assert error_message(ValueError("bug")) is None
    err_429 = genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota", "details": []}})
    assert error_message(err_429) is not None
    assert error_message(httpx2.ConnectError("offline")) is not None
    cleaned = sanitize("Hello </user_question> <internal_analysis> <excerpt id='1'> </sections> world")
    assert "</user_question>" not in cleaned and "<internal_analysis>" not in cleaned
    assert "</sections>" not in cleaned and "Hello" in cleaned and "world" in cleaned


@pytest.mark.parametrize(
    "raw_name,expected_id,expected_family,expected_clef",
    [
        ("Violino I", "violin", "strings", "treble"),
        ("Violin 1", "violin", "strings", "treble"),
        ("Violoncelo", "cello", "strings", "bass"),
        ("Violoncello", "cello", "strings", "bass"),
        ("Clarone", "bass clarinet", "woodwinds", "bass"),
        ("Clarinete baixo em Si bemol", "bass clarinet", "woodwinds", "bass"),
        ("Bass Clarinet", "bass clarinet", "woodwinds", "bass"),
        ("Clarinete contrabaixo", "contrabass clarinet", "woodwinds", "bass"),
        ("Contrabass clarinet", "contrabass clarinet", "woodwinds", "bass"),
        ("Contrabaixo", "double bass", "strings", "bass"),
        ("Double bass", "double bass", "strings", "bass"),
        ("Corne inglês", "english horn", "woodwinds", "treble"),
        ("Trompa em Fá", "horn", "brass", "bass"),
        ("French Horn", "horn", "brass", "bass"),
        ("Caixa clara", "snare drum", "percussion", "percussion"),
        ("Snare drum", "snare drum", "percussion", "percussion"),
        ("Violão", "guitar", "guitar", "treble"),
        ("Mezzo-soprano", "mezzo-soprano", "voice", "treble"),
    ],
)
def test_instruments_bilingual_normalization_and_metadata(
    raw_name: str, expected_id: str, expected_family: str, expected_clef: str
):
    from muschat.instruments import FAMILIES, INSTRUMENTS, identify, in_register, instrument, part_range, transposition

    canonical = identify(raw_name)
    assert canonical == expected_id
    inst = instrument(raw_name)
    assert inst == INSTRUMENTS[expected_id]
    assert inst.family == expected_family and inst.family in FAMILIES
    assert inst.clef == expected_clef
    assert in_register(1200, (3600, 6000)) == 3600
    assert transposition("bass clarinet") == (-1, -2, -1)
    assert transposition("unknown instrument") == (0, 0, 0)
    sample = sample_quartet()
    assert part_range(sample.parts[0]) == INSTRUMENTS["violin"].range


def test_constraints_bilingual_normalization_and_violation_checks():
    from muschat.constraints import Constraint, check_constraints

    ir = sample_quartet()
    # Bilingual kind normalization ("densidade_maxima" -> "max_density", "intervalo_proibido" -> "forbidden_interval")
    c_density = Constraint(kind="densidade_maxima", value=0.5, parts=["vc"])  # type: ignore[arg-type]
    assert c_density.kind == "max_density"
    c_interval = Constraint(kind="forbidden_interval", value=700, parts=["vc"])
    violations = check_constraints(ir, [c_density, c_interval])
    kinds = {v.constraint for v in violations}
    assert kinds == {"max_density", "forbidden_interval"}
    assert all("vc" in str(v) for v in violations)


def test_plan_sections_registry_and_router_in_english():
    from muschat.plan import Operation, Parameter, Plan, Section, Selection
    from muschat.registry import Capability, Registry
    from muschat.router import NoRouteError, route
    from muschat.sections import insert_section

    plan = Plan(
        goal="Spectral duo",
        instruments=["flute", "cello"],
        sections=[
            Section(name="A", character="sparse", start_measure=1, end_measure=1, material="spectral"),
            Section(name="B", character="dense", start_measure=2, end_measure=2, material="spectral"),
        ],
        operations=[
            Operation(
                section="A",
                procedure="serie_harmonica",  # normalized to canonical "harmonic_series"
                parameters=[Parameter(name="compassos", value=1)],
            ),
            Operation(
                section="B",
                procedure="densidade",  # normalized to canonical "density"
                parameters=[Parameter(name="inicial", value=1), Parameter(name="final", value=3)],
            ),
        ],
        mode="automatico",  # type: ignore[arg-type]
    )
    assert plan.mode == "auto"
    assert [op.procedure for op in plan.operations] == ["harmonic_series", "density"]
    assert plan.operations[0].parameters[0].name == "measures"
    assert [p.name for p in plan.operations[1].parameters] == ["start", "end"]
    step_a = plan.for_step(0)
    assert step_a.selection == Selection(start_measure=1, end_measure=1)

    # Insert section into skeleton
    mat = sample_quartet()
    merged, skeleton = insert_section(None, mat, step_a.selection, plan.sections)
    assert len(skeleton.measures) == 2 and len(merged.measures) == 2
    assert len(merged.parts[0].voices[0].events) == 2

    # Routing rules R1-R6
    reg_no_om = Registry(
        (
            Capability("Python", "engine", "automatic", "native", procedures=("harmonic_series", "density")),
            Capability("OpenMusic", "engine", "unavailable", "not installed"),
        )
    )
    reg_with_om = Registry(
        (
            Capability("Python", "engine", "automatic", "native", procedures=("harmonic_series", "density")),
            Capability("OpenMusic", "engine", "manual", "installed"),
            Capability("Esquisse", "library", "manual", "installed", procedures=("harmonic_series",)),
        )
    )
    dec_auto = route(plan, "auto", reg_with_om)
    assert [s.engine for s in dec_auto.steps] == ["python", "python"]
    assert any("R5" in line for line in dec_auto.rationale)

    dec_om_hybrid = route(plan, "openmusic", reg_with_om)
    assert dec_om_hybrid.mode == "hybrid"
    assert [s.engine for s in dec_om_hybrid.steps] == ["openmusic", "python"]

    with pytest.raises(NoRouteError):
        route(plan, "hybrid", reg_no_om)


def test_composition_english_roundtrip_and_legacy_portuguese_json_migration():
    import json
    from muschat.composition import Composition, OmTask, PendingTask

    # 1. Pre-Fase-0 Portuguese v2 Composition JSON payload with a Version + Pending OpenMusic task
    legacy_ir_dict = {
        "titulo": "Quarteto",
        "compassos": [{"numerador": 4, "denominador": 4, "andamento": 60}],
        "partes": [
            {
                "id": "vln",
                "instrumento": "violino",
                "vozes": [
                    {
                        "id": "vln1",
                        "eventos": [
                            {
                                "tipo": "nota",
                                "id": "a",
                                "inicio": "0",
                                "duracao": "1/2",
                                "alturas": [{"midicents": 6950, "grafia": "A"}],
                                "tecnicas": ["sul ponticello"],
                                "dinamica": "pp",
                                "proveniencia": {"origem": "llm"},
                            },
                            {"tipo": "pausa", "id": "b", "inicio": "1/2", "duracao": "1/2", "proveniencia": {"origem": "llm"}},
                        ],
                    }
                ],
            },
            {
                "id": "vc",
                "instrumento": "violoncelo",
                "clave": "fa",
                "tessitura": [3600, 7600],
                "vozes": [{"id": "vc1", "eventos": []}],
            },
        ],
    }
    legacy_plan_dict = {
        "receita": "espectral",
        "motivo_receita": "teste",
        "objetivo": "Quarteto espectral",
        "instrumentos": ["violino", "violoncelo"],
        "tecnicas": ["sul ponticello"],
        "restricoes": [{"tipo": "densidade_maxima", "valor": 2}],
        "operacoes": [
            {"procedimento": "serie_harmonica", "parametros": [{"nome": "compassos", "valor": 4}]},
            {"procedimento": "desafinar", "parametros": [{"nome": "desvio", "valor": 50}]},
        ],
        "selecao": {"partes": ["vc"], "compasso_inicial": 1, "compasso_final": 1},
        "motor": "hibrido",
        "semente": 42,
    }
    legacy_decision_dict = {
        "modo": "hibrido",
        "etapas": [
            {"motor": "openmusic", "procedimento": "serie_harmonica"},
            {"motor": "python", "procedimento": "desafinar"},
        ],
        "justificativa": ["etapa 1 OM", "etapa 2 Python"],
    }
    legacy_json = json.dumps(
        {
            "versao_formato": 2,
            "versoes": [
                {
                    "ir": legacy_ir_dict,
                    "resumo": "Versão 1",
                    "data": "2026-01-01T12:00:00",
                    "experimento": {
                        "pedido": "componha quarteto",
                        "modelo": "gemini-3.1-flash-lite",
                        "modo": "hibrido",
                        "plano": legacy_plan_dict,
                        "decisao": legacy_decision_dict,
                        "etapas": [{"motor": "openmusic", "procedimento": "serie_harmonica", "estado": "feita"}],
                        "parametros": [
                            {"fundamental": 3600, "parciais": 16, "compassos": 4, "microtom": 1, "pausas": 0.15},
                            {"desvio": 50, "resolucao": 1, "curva": 0, "glissandos": 0.5},
                        ],
                        "semente": 42,
                        "intermediarios": [legacy_ir_dict],
                        "saidas_externas": {"0": "<score-partwise/>"},
                        "violacoes": ["densidade_maxima (vc c.1): 3 ataques"],
                        "avisos": ["aviso legado"],
                    },
                    "anterior": None,
                }
            ],
            "indice": 0,
            "pendente": {
                "pedido": "componha",
                "modelo": "gemini-3.1-flash-lite",
                "plano": legacy_plan_dict,
                "decisao": legacy_decision_dict,
                "parametros": [
                    {"fundamental": 3600, "parciais": 16, "compassos": 4, "microtom": 1, "pausas": 0.15},
                    {"desvio": 50, "resolucao": 1, "curva": 0, "glissandos": 0.5},
                ],
                "semente": 42,
                "operacao": "op-1",
                "base": 0,
                "etapa": 0,
                "atual": legacy_ir_dict,
                "intermediarios": [legacy_ir_dict],
                "tarefa": {
                    "id": "om-legacy-1",
                    "procedimento": "serie_harmonica",
                    "parametros": {"fundamental": 3600, "parciais": 16, "compassos": 4, "microtom": 1, "pausas": 0.15},
                    "pasta": ".muschat/openmusic/om-legacy-1",
                    "conteudo": "3600 1 1 1 16\n",
                },
                "saidas_externas": {"0": "<score-partwise/>"},
                "avisos": ["aviso 1"],
            },
        }
    )

    # 2. Load legacy Portuguese JSON into canonical English Composition
    migrated = Composition.from_json(legacy_json)
    assert len(migrated.versions) == 1 and migrated.index == 0
    v0 = migrated.current
    assert v0 is not None and v0.reapplicable
    assert v0.ir.title == "Quarteto"
    assert len(v0.ir.measures) == 1 and v0.ir.measures[0].tempo == 60
    assert [p.id for p in v0.ir.parts] == ["vln", "vc"]
    assert v0.ir.parts[1].clef == "bass" and v0.ir.parts[1].range == (3600, 7600)
    assert v0.experiment["seed"] == 42
    assert v0.experiment["mode"] == "hybrid"
    assert v0.experiment["plan"]["recipe"] == "spectral"
    assert v0.experiment["plan"]["instruments"] == ["violin", "cello"]
    assert [op["procedure"] for op in v0.experiment["plan"]["operations"]] == ["harmonic_series", "detune"]
    assert v0.experiment["steps"][0]["state"] == "done"
    assert v0.experiment["parameters"][1]["deviation"] == 50
    assert v0.experiment["warnings"] == ["aviso legado"]
    assert migrated.pending is not None
    assert isinstance(migrated.pending, PendingTask)
    assert isinstance(migrated.pending.task, OmTask)
    assert migrated.pending.task.procedure == "harmonic_series"
    assert migrated.pending.task.parameters["measures"] == 4
    assert migrated.pending.parameters[1]["deviation"] == 50
    assert [s.procedure for s in migrated.pending.decision.steps] == ["harmonic_series", "detune"]

    # 3. Round-trip in canonical English JSON and undo/checkout/diff
    english_json = migrated.to_json()
    raw = json.loads(english_json)
    assert "versions" in raw and "index" in raw and "pending" in raw
    reloaded = Composition.from_json(english_json)
    assert reloaded.versions[0].ir == migrated.versions[0].ir
    assert reloaded.pending == migrated.pending

    reloaded.add(sample_quartet(), "Version 2", {"plan": v0.experiment["plan"], "seed": 7}, previous=0)
    assert reloaded.index == 1 and len(reloaded.versions) == 2
    assert reloaded.diff(0, 1)
    reloaded.undo()
    assert reloaded.index == 0
    reloaded.checkout(1)
    assert reloaded.index == 1

    # 4. Reject invalid JSON indices
    bad = json.loads(english_json)
    bad["index"] = 99
    with pytest.raises(ValueError):
        Composition.from_json(json.dumps(bad))





@pytest.mark.parametrize("value", [None, 12, {}, []])
def test_legacy_procedure_identifiers_reject_non_strings(value):
    from muschat.composition import Experiment, OmTask

    with pytest.raises(ValidationError, match="procedure must be a string"):
        Experiment.model_validate({"etapas": [{"procedimento": value}]})
    with pytest.raises(ValidationError, match="procedure must be a string"):
        OmTask.model_validate({"procedimento": value})


def _legacy_pending():
    return {
        "pedido": "test", "modelo": "test", "plano": {"goal": "test", "operations": [{"procedure": "harmonic_series"}]},
        "decisao": {"steps": [], "rationale": [], "mode": "auto"},
        "semente": 1, "operacao": "op1", "base": None, "etapa": 0, "atual": None,
    }


def test_legacy_pending_missing_collections_default_to_empty():
    from muschat.composition import PendingTask

    pending = PendingTask.model_validate(_legacy_pending())
    assert pending.parameters == [] and pending.intermediates == []


@pytest.mark.parametrize("field", ["parametros", "intermediarios"])
@pytest.mark.parametrize("value", [None, 12, "invalid", {}])
def test_legacy_pending_rejects_malformed_collections(field, value):
    from muschat.composition import PendingTask

    with pytest.raises(ValidationError, match="must be a list"):
        PendingTask.model_validate({**_legacy_pending(), field: value})

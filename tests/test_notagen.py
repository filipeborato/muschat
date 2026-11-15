"""Optional NotaGen engine tests: installation detection, Registry/router integration, and composition steps."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from muschat.composicao import Composicao
from muschat.notacao.musicxml import para_musicxml
from muschat.notagen import (
    WEIGHTS_FILE,
    LocalNotaGen,
    NotaGenInstallation,
    detect_notagen,
)
from muschat.orquestrador import compor, reaplicar
from muschat.plan import Operation, Plan
from muschat.plano import Plano
from muschat.registry import detect_capabilities
from muschat.registro import detectar
from muschat.roteador import rotear
from muschat.router import route
from tests.test_composicao import ChatFake, GeradorFake, _plano
from tests.test_leitura_musicxml import ATRIBUTOS, _nota, _xml

CONDITIONS = ["Romantic_Ravel, Maurice_Chamber", "Classical_Mozart, Wolfgang Amadeus_Keyboard"]


def _installation(tmp_path: Path, *, weights: bool = True):
    python = tmp_path / "venv" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    gradio = tmp_path / "NotaGen" / "gradio"
    gradio.mkdir(parents=True, exist_ok=True)
    (gradio / "prompts.txt").write_text("\n".join(CONDITIONS) + "\n", encoding="utf-8")
    if weights:
        (gradio / WEIGHTS_FILE).write_bytes(b"")
    return detect_notagen(str(python), str(tmp_path / "NotaGen"))


def _registry(notagen):
    return detectar(lilypond=None, openmusic=None, abjad_versao="3.31", which=lambda e: None, notagen=notagen)


def test_detection_reads_conditions_or_reports_missing_item(tmp_path):
    ok = _installation(tmp_path)
    assert isinstance(ok, NotaGenInstallation) and ok.conditions == tuple(CONDITIONS)
    assert "weights" in detect_notagen(ok.python, str(tmp_path / "empty"))
    assert "NOTAGEN_PYTHON" in detect_notagen(str(tmp_path / "missing.exe"), str(tmp_path / "NotaGen"))


def test_detected_notagen_is_automatic_engine_with_conditions(tmp_path):
    inst = _installation(tmp_path)
    reg_en = detect_capabilities(
        lilypond=None, openmusic=None, abjad_version="3.31", which=lambda e: None, notagen=inst
    )
    cap_en = reg_en.capability("NotaGen")
    assert cap_en is not None and cap_en.state == "automatic"
    assert cap_en.procedures == ("generate_notagen",) and cap_en.options == tuple(CONDITIONS)
    assert "generate_notagen" in reg_en.available_procedures()
    plan_en = Plan(goal="test", operations=[Operation(procedure="generate_notagen", condition=CONDITIONS[0])])
    decision_en = route(plan_en, "auto", reg_en)
    assert [s.engine for s in decision_en.steps] == ["notagen"]

    reg = _registry(inst)
    cap = reg.capacidade("NotaGen")
    assert cap.estado == "automatico" and cap.procedimentos == ("gerar_notagen",) and cap.opcoes == tuple(CONDITIONS)
    assert "gerar_notagen" in reg.disponiveis()
    plano = Plano.model_validate(_plano("gerar_notagen"))
    decisao = rotear(plano, "automatico", reg)
    assert [e.motor for e in decisao.etapas] == ["notagen"]


def test_missing_notagen_is_unavailable_with_reason_and_disabled_is_absent(tmp_path):
    reg = _registry(_installation(tmp_path, weights=False))
    cap = reg.capacidade("NotaGen")
    assert cap.estado == "indisponivel" and "weights" in cap.motivo and "gerar_notagen" not in reg.disponiveis()
    assert _registry(None).capacidade("NotaGen") is None


# --- Composition step integration -------------------------------------------------------------------------------------


class FakeComposer:
    def __init__(self, failure: str | None = None):
        self.calls, self.failure = [], failure

    def generate(self, condition: str, measures: int, seed: int) -> bytes:
        self.calls.append((condition, measures, seed))
        if self.failure:
            raise ValueError(self.failure)
        bars = "".join(
            f'<measure number="{n}">{ATRIBUTOS if n == 1 else ""}{_nota("C", 4, 6, "half", extra="<dot/>")}</measure>'
            for n in range(1, measures + 1)
        )
        return _xml(
            f'<part id="P1">{bars}</part><part id="P2">{bars.replace("C</step>", "E</step>")}</part>',
            '<score-part id="P1"><part-name>Violin</part-name></score-part>'
            '<score-part id="P2"><part-name>Cello</part-name></score-part>',
        )

    def gerar(self, condicao: str, compassos: int, semente: int) -> bytes:
        return self.generate(condicao, compassos, semente)


def _generate(tmp_path, composer, *extras, condition=CONDITIONS[0], instruments=("violino", "violoncelo"), comp=None):
    plan = _plano("gerar_notagen", parametros={"compassos": 2}, instrumentos=list(instruments))
    plan["operacoes"][0]["condicao"] = condition
    plan["operacoes"] += list(extras)
    comp = comp or Composicao()
    result = compor(
        "request",
        comp,
        gerador=GeradorFake(plan),
        chat=ChatFake(),
        modelo="m",
        registro=_registry(_installation(tmp_path)),
        compositor=composer,
    )
    return comp, result


def test_notagen_generates_external_version_with_plan_instruments(tmp_path):
    fake = FakeComposer()
    _, result = _generate(tmp_path, fake)
    assert result.versao is not None, result.resumo
    assert fake.calls == [(CONDITIONS[0], 2, fake.calls[0][2])] and isinstance(fake.calls[0][2], int)
    events = [e for _, _, e in result.versao.ir.eventos()]
    assert {(e.proveniencia.origem, e.proveniencia.autor) for e in events} == {("externo", "NotaGen-X")}
    assert [p.instrumento for p in result.versao.ir.partes] == ["Violino", "Violoncelo"]
    assert result.versao.experimento["etapas"][0]["motor"] == "notagen"
    assert list(result.versao.experimento["saidas_externas"]) == ["0"]


def test_notagen_material_flows_into_python_transformations(tmp_path):
    transpose_op = {"procedimento": "transpor", "parametros": [{"nome": "intervalo", "valor": 50}]}
    _, result = _generate(tmp_path, FakeComposer(), transpose_op)
    assert [e["motor"] for e in result.versao.experimento["etapas"]] == ["notagen", "python"]
    assert result.versao.ir.partes[0].vozes[0].eventos[0].alturas[0].midicents == 6050


def test_notagen_mode_requires_local_call_and_executes_transformation(tmp_path):
    reg = _registry(_installation(tmp_path))
    fake = FakeComposer()
    plan = _plano("gerar_notagen", parametros={"compassos": 2}, instrumentos=["violino", "violoncelo"])
    plan["operacoes"][0]["condicao"] = CONDITIONS[0]
    plan["operacoes"].append({"procedimento": "transpor", "parametros": [{"nome": "intervalo", "valor": 50}]})
    comp = Composicao()
    gen = GeradorFake(plan)
    result = compor(
        "componha", comp, gerador=gen, chat=ChatFake(), modelo="m", modo="notagen", registro=reg, compositor=fake
    )
    assert result.versao is not None, result.resumo
    assert len(fake.calls) == 1
    assert [e["motor"] for e in result.versao.experimento["etapas"]] == ["notagen", "python"]
    assert "Comece obrigatoriamente por gerar_notagen" in gen.chamadas[0].config.system_instruction
    result = compor(
        "componha",
        comp,
        gerador=GeradorFake(_plano()),
        chat=ChatFake(),
        modelo="m",
        modo="notagen",
        registro=reg,
        compositor=fake,
    )
    assert result.versao is None and "não chamou" in result.resumo and len(comp.versoes) == 1


def test_unknown_condition_is_refused_without_running_model(tmp_path):
    fake = FakeComposer()
    _, result = _generate(tmp_path, fake, condition="Contemporary_Lachenmann_Chamber")
    assert result.versao is None and "condição" in result.resumo and fake.calls == []


def test_model_failure_becomes_refusal_and_nothing_is_saved(tmp_path):
    comp, result = _generate(tmp_path, FakeComposer(failure="GPU out of memory"))
    assert result.versao is None and "GPU out of memory" in result.resumo and comp.versoes == []
    comp, result = _generate(tmp_path, None)
    assert result.versao is None and "NotaGen" in result.resumo and comp.versoes == []


def test_reapply_reuses_stored_musicxml_and_regenerates_only_if_parameter_changes(tmp_path):
    fake = FakeComposer()
    comp, result = _generate(tmp_path, fake)
    same = reaplicar(comp, [{"compassos": 2}], registro=_registry(_installation(tmp_path / "b")), compositor=fake)
    assert same.versao is not None and len(fake.calls) == 1
    assert para_musicxml(same.versao.ir) == para_musicxml(result.versao.ir)
    longer = reaplicar(comp, [{"compassos": 3}], registro=_registry(_installation(tmp_path / "c")), compositor=fake)
    assert len(fake.calls) == 2 and len(longer.versao.ir.compassos) == 3


def test_planner_prompt_lists_conditions_only_when_notagen_is_available(tmp_path):
    gen = GeradorFake(_plano())
    compor(
        "pedido", Composicao(), gerador=gen, chat=ChatFake(), modelo="m", registro=_registry(_installation(tmp_path))
    )
    system = gen.chamadas[0].config.system_instruction
    assert "gerar_notagen" in system and CONDITIONS[0] in system
    gen = GeradorFake(_plano())
    compor("pedido", Composicao(), gerador=gen, chat=ChatFake(), modelo="m", registro=_registry("no GPU"))
    assert "gerar_notagen" not in gen.chamadas[0].config.system_instruction


def test_installation_without_prompts_file_returns_reason(tmp_path):
    inst = _installation(tmp_path)
    (Path(inst.directory) / "gradio" / "prompts.txt").unlink()
    assert "prompts.txt" in detect_notagen(inst.python, inst.directory)


def test_manual_python_mode_with_generate_notagen_refuses_with_alternative(tmp_path):
    plan = _plano("gerar_notagen", parametros={"compassos": 2})
    plan["operacoes"][0]["condicao"] = CONDITIONS[0]
    fake = FakeComposer()
    result = compor(
        "pedido",
        Composicao(),
        gerador=GeradorFake(plan),
        chat=ChatFake(),
        modelo="m",
        modo="python",
        registro=_registry(_installation(tmp_path)),
        compositor=fake,
    )
    assert result.versao is None and "NotaGen (automático)" in result.resumo and fake.calls == []


def test_different_part_count_is_adapted_and_documented(tmp_path):
    fake = FakeComposer()
    comp, result = _generate(tmp_path, fake, instruments=("flauta", "oboe", "fagote"))
    assert result.versao is not None and len(comp.versoes) == 1
    assert [p.instrumento for p in result.versao.ir.partes] == ["Flauta", "Oboe", "Fagote"]
    assert "orquestração adaptada de 2 para 3 partes" in result.resumo
    redone = reaplicar(
        comp,
        result.versao.experimento["parametros"],
        registro=_registry(_installation(tmp_path / "reuse")),
        compositor=fake,
    )
    assert para_musicxml(redone.versao.ir) == para_musicxml(result.versao.ir)
    assert len(fake.calls) == 1


def test_fewer_measures_returned_produces_warning(tmp_path):
    fake = FakeComposer()
    fake.gerar = lambda condicao, compassos, semente: FakeComposer().gerar(condicao, 1, semente)
    _, result = _generate(tmp_path, fake)
    assert "1 de 2 compassos" in result.resumo


def test_duo_request_with_solo_output_adapts_before_contemporary_layer(tmp_path):
    from muschat.reasoning import responder
    from tests.test_reasoning import ANALISE_OK, FakeChat, FakeRetriever

    comp, previous = _generate(tmp_path / "base", FakeComposer())

    class SoloComposer:
        def gerar(self, condition, measures, seed):
            root = ET.fromstring(FakeComposer().gerar(condition, measures, seed))
            root.remove(root.find("part"))
            part_list = root.find("part-list")
            part_list.remove(part_list.find("score-part"))
            part_list.find("score-part/part-name").text = "Violoncello"
            return ET.tostring(root)

    plan = _plano("gerar_notagen", parametros={"compassos": 8}, instrumentos=["flauta", "violoncelo"])
    plan["operacoes"][0]["condicao"] = CONDITIONS[0]
    plan["operacoes"].append({"procedimento": "rarefazer"})
    chat = FakeChat([])
    response = responder(
        "crie oito compassos para flauta e cello",
        composicao=comp,
        gerador=GeradorFake({**ANALISE_OK, "notacao": "compor"}, plan),
        chat=chat,
        retriever=FakeRetriever([]),
        modelo="m",
        modo="notagen",
        registro=_registry(_installation(tmp_path / "duo")),
        compositor=SoloComposer(),
    )
    assert comp.versoes[0] is previous.versao and len(comp.versoes) == 2
    assert response.partituras and chat.mensagens
    assert [p.instrumento for p in comp.atual.ir.partes] == ["Flauta", "Violoncelo"]
    assert len(comp.atual.ir.compassos) == 8
    assert "orquestração adaptada de 1 para 2 partes" in chat.mensagens[0].config.system_instruction


def test_subprocess_failure_becomes_message_instead_of_raw_exception(tmp_path):
    gradio = tmp_path / "gradio"
    gradio.mkdir()
    local = LocalNotaGen(NotaGenInstallation(sys.executable, str(tmp_path), ("A_B_C",)))
    with pytest.raises(ValueError, match="NotaGen failed"):
        local.generate("A_B_C", 1, 1)
    with pytest.raises(ValueError, match="not accepted"):
        local.generate("X_Y_Z", 1, 1)


def test_two_section_form_and_section_edit_preserves_first_section(tmp_path):
    class WithTempo(FakeComposer):
        def gerar(self, *args):
            return super().gerar(*args).replace(
                b"</attributes>", b'</attributes><direction><sound tempo="120"/></direction>'
            )

    fake = WithTempo()
    reg = _registry(_installation(tmp_path))
    plan = _plano("gerar_notagen", instrumentos=["violino", "violoncelo"])
    plan["secoes"] = [
        dict(nome=n, carater="calmo", material="notas", tecnicas=[], compasso_inicial=i, compasso_final=i + 1)
        for n, i in [("A", 1), ("B", 3)]
    ]
    plan["operacoes"] = [
        op
        for n in ["A", "B"]
        for op in [
            dict(procedimento="gerar_notagen", condicao=CONDITIONS[0], secao=n),
            dict(procedimento="transpor", secao=n, parametros=[dict(nome="intervalo", valor=50)]),
        ]
    ]
    comp = Composicao()

    def run_plan(p):
        return compor(
            "edit B", comp, gerador=GeradorFake(p), chat=ChatFake(), modelo="m", registro=reg, compositor=fake
        )

    huge = {
        **plan,
        "secoes": [
            dict(nome="A", carater="calmo", material="notas", compasso_inicial=1, compasso_final=1000000)
        ],
        "operacoes": plan["operacoes"][:2],
    }
    assert run_plan(huge).versao is None
    incomplete = {**plan, "operacoes": plan["operacoes"][:2]}
    assert run_plan(incomplete).versao is None
    assert fake.calls == []
    result = run_plan(plan)
    assert result.versao is not None, result.resumo
    assert [v[1] for v in fake.calls] == [2, 2]
    before = result.versao.ir
    edit = _plano("transpor", parametros={"intervalo": 100})
    edit["operacoes"][0]["secao"] = "B"
    result = run_plan(edit)
    assert result.versao is not None, result.resumo
    assert result.versao.experimento["plano"]["secoes"] == plan["secoes"]
    for pa, pn in zip(before.partes, result.versao.ir.partes):
        assert pa.vozes[0].eventos[:2] == pn.vozes[0].eventos[:2]
        assert pn.vozes[0].eventos[2].alturas[0].midicents == pa.vozes[0].eventos[2].alturas[0].midicents + 100
    edit["operacoes"][0]["secao"] = "missing"
    assert run_plan(edit).versao is None
    assert len(comp.versoes) == 2

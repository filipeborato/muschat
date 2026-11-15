"""Motor NotaGen opcional (ticket 04 da composição musical): detecção, Registro/roteador e etapa na Composição."""

import sys
from pathlib import Path

import pytest

from muschat.composicao import Composicao
from muschat.notacao.musicxml import para_musicxml
from muschat.notagen import PESOS, InstalacaoNotaGen, NotaGenLocal, detectar_notagen
from muschat.orquestrador import compor, reaplicar
from muschat.registro import detectar
from muschat.roteador import rotear
from muschat.plano import Plano
from tests.test_composicao import ChatFake, GeradorFake, _plano
from tests.test_leitura_musicxml import ATRIBUTOS, _nota, _xml

CONDICOES = ["Romantic_Ravel, Maurice_Chamber", "Classical_Mozart, Wolfgang Amadeus_Keyboard"]


def _instalacao(tmp_path: Path, *, pesos: bool = True):
    python = tmp_path / "venv" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    gradio = tmp_path / "NotaGen" / "gradio"
    gradio.mkdir(parents=True, exist_ok=True)
    (gradio / "prompts.txt").write_text("\n".join(CONDICOES) + "\n", encoding="utf-8")
    if pesos:
        (gradio / PESOS).write_bytes(b"")
    return detectar_notagen(str(python), str(tmp_path / "NotaGen"))


def _registro(notagen):
    return detectar(lilypond=None, openmusic=None, abjad_versao="3.31", which=lambda e: None, notagen=notagen)


def test_deteccao_le_as_condicoes_ou_diz_o_que_falta(tmp_path):
    ok = _instalacao(tmp_path)
    assert not isinstance(ok, str) and ok.condicoes == tuple(CONDICOES)
    assert "pesos" in detectar_notagen(ok.python, str(tmp_path / "vazio"))
    assert "NOTAGEN_PYTHON" in detectar_notagen(str(tmp_path / "nao-existe.exe"), str(tmp_path / "NotaGen"))


def test_notagen_detectado_e_motor_automatico_com_as_condicoes(tmp_path):
    reg = _registro(_instalacao(tmp_path))
    cap = reg.capacidade("NotaGen")
    assert cap.estado == "automatico" and cap.procedimentos == ("gerar_notagen",) and cap.opcoes == tuple(CONDICOES)
    assert "gerar_notagen" in reg.disponiveis()
    plano = Plano.model_validate(_plano("gerar_notagen"))
    decisao = rotear(plano, "automatico", reg)
    assert [e.motor for e in decisao.etapas] == ["notagen"]


def test_notagen_ausente_fica_indisponivel_com_motivo_e_desligado_nao_aparece(tmp_path):
    reg = _registro(_instalacao(tmp_path, pesos=False))
    cap = reg.capacidade("NotaGen")
    assert cap.estado == "indisponivel" and "pesos" in cap.motivo and "gerar_notagen" not in reg.disponiveis()
    assert _registro(None).capacidade("NotaGen") is None


# --- etapa na Composição -----------------------------------------------------------------------------------------------

class CompositorFake:
    def __init__(self, falha: str | None = None):
        self.chamadas, self.falha = [], falha

    def gerar(self, condicao: str, compassos: int, semente: int) -> bytes:
        self.chamadas.append((condicao, compassos, semente))
        if self.falha:
            raise ValueError(self.falha)
        c = "".join(f'<measure number="{n}">{ATRIBUTOS if n == 1 else ""}{_nota("C", 4, 6, "half", extra="<dot/>")}'
                    "</measure>" for n in range(1, compassos + 1))
        return _xml(f'<part id="P1">{c}</part><part id="P2">{c.replace("C</step>", "E</step>")}</part>',
                    '<score-part id="P1"><part-name>Violin</part-name></score-part>'
                    '<score-part id="P2"><part-name>Cello</part-name></score-part>')


def _gerar(tmp_path, compositor, *extras, condicao=CONDICOES[0], instrumentos=("violino", "violoncelo"), c=None):
    plano = _plano("gerar_notagen", parametros={"compassos": 2}, instrumentos=list(instrumentos))
    plano["operacoes"][0]["condicao"] = condicao
    plano["operacoes"] += list(extras)
    c = c or Composicao()
    r = compor("pedido", c, gerador=GeradorFake(plano), chat=ChatFake(), modelo="m",
               registro=_registro(_instalacao(tmp_path)), compositor=compositor)
    return c, r


def test_notagen_gera_versao_externa_com_os_instrumentos_do_plano(tmp_path):
    fake = CompositorFake()
    c, r = _gerar(tmp_path, fake)
    assert r.versao is not None, r.resumo
    assert fake.chamadas == [(CONDICOES[0], 2, fake.chamadas[0][2])] and isinstance(fake.chamadas[0][2], int)
    eventos = [e for _, _, e in r.versao.ir.eventos()]
    assert {(e.proveniencia.origem, e.proveniencia.autor) for e in eventos} == {("externo", "NotaGen-X")}
    assert [p.instrumento for p in r.versao.ir.partes] == ["Violino", "Violoncelo"]
    assert r.versao.experimento["etapas"][0]["motor"] == "notagen"
    assert list(r.versao.experimento["saidas_externas"]) == ["0"]


def test_material_do_notagen_segue_para_as_transformacoes_do_python(tmp_path):
    transpor = {"procedimento": "transpor", "parametros": [{"nome": "intervalo", "valor": 50}]}
    _, r = _gerar(tmp_path, CompositorFake(), transpor)
    assert [e["motor"] for e in r.versao.experimento["etapas"]] == ["notagen", "python"]
    assert r.versao.ir.partes[0].vozes[0].eventos[0].alturas[0].midicents == 6050


def test_condicao_desconhecida_e_recusada_sem_rodar_o_modelo(tmp_path):
    fake = CompositorFake()
    _, r = _gerar(tmp_path, fake, condicao="Contemporary_Lachenmann_Chamber")
    assert r.versao is None and "condição" in r.resumo and fake.chamadas == []


def test_falha_do_modelo_vira_recusa_e_nada_e_gravado(tmp_path):
    c, r = _gerar(tmp_path, CompositorFake(falha="a GPU ficou sem memória"))
    assert r.versao is None and "GPU ficou sem memória" in r.resumo and c.versoes == []
    c, r = _gerar(tmp_path, None)
    assert r.versao is None and "NotaGen" in r.resumo and c.versoes == []


def test_reaplicar_reusa_o_musicxml_guardado_e_so_gera_de_novo_se_o_parametro_mudar(tmp_path):
    fake = CompositorFake()
    c, r = _gerar(tmp_path, fake)
    igual = reaplicar(c, [{"compassos": 2}], registro=_registro(_instalacao(tmp_path / "b")), compositor=fake)
    assert igual.versao is not None and len(fake.chamadas) == 1
    assert para_musicxml(igual.versao.ir) == para_musicxml(r.versao.ir)
    maior = reaplicar(c, [{"compassos": 3}], registro=_registro(_instalacao(tmp_path / "c")), compositor=fake)
    assert len(fake.chamadas) == 2 and len(maior.versao.ir.compassos) == 3


def test_prompt_do_plano_lista_as_condicoes_so_quando_o_notagen_esta_disponivel(tmp_path):
    g = GeradorFake(_plano())
    compor("pedido", Composicao(), gerador=g, chat=ChatFake(), modelo="m", registro=_registro(_instalacao(tmp_path)))
    sistema = g.chamadas[0].config.system_instruction
    assert "gerar_notagen" in sistema and CONDICOES[0] in sistema
    g = GeradorFake(_plano())
    compor("pedido", Composicao(), gerador=g, chat=ChatFake(), modelo="m", registro=_registro("sem GPU"))
    assert "gerar_notagen" not in g.chamadas[0].config.system_instruction


def test_instalacao_sem_lista_de_condicoes_vira_motivo(tmp_path):
    inst = _instalacao(tmp_path)
    (Path(inst.pasta) / "gradio" / "prompts.txt").unlink()
    assert "prompts.txt" in detectar_notagen(inst.python, inst.pasta)


def test_modo_manual_python_com_gerar_notagen_recusa_com_a_alternativa(tmp_path):
    plano = _plano("gerar_notagen", parametros={"compassos": 2})
    plano["operacoes"][0]["condicao"] = CONDICOES[0]
    fake = CompositorFake()
    r = compor("pedido", Composicao(), gerador=GeradorFake(plano), chat=ChatFake(), modelo="m", modo="python",
               registro=_registro(_instalacao(tmp_path)), compositor=fake)
    assert r.versao is None and "NotaGen (automático)" in r.resumo and fake.chamadas == []


def test_partes_e_compassos_diferentes_do_pedido_viram_aviso(tmp_path):
    fake = CompositorFake()
    _, r = _gerar(tmp_path, fake, instrumentos=("flauta", "oboe", "fagote"))
    assert [p.instrumento for p in r.versao.ir.partes] == ["Violin", "Cello"]
    assert "2 partes para 3 instrumentos pedidos" in r.resumo
    fake.gerar = lambda condicao, compassos, semente: CompositorFake().gerar(condicao, 1, semente)
    _, r = _gerar(tmp_path, fake)
    assert "1 de 2 compassos" in r.resumo


def test_falha_do_processo_vira_mensagem_e_nao_excecao(tmp_path):
    gradio = tmp_path / "gradio"
    gradio.mkdir()
    local = NotaGenLocal(InstalacaoNotaGen(sys.executable, str(tmp_path), ("A_B_C",)))
    with pytest.raises(ValueError, match="o NotaGen falhou"):  # sem torch neste ambiente: o script sai com erro
        local.gerar("A_B_C", 1, 1)
    with pytest.raises(ValueError, match="não é aceita"):
        local.gerar("X_Y_Z", 1, 1)

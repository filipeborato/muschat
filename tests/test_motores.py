"""Registro, roteador, bridge OpenMusic (assistido) e lista de etapas com pausa/retomada."""

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from muschat.composicao import Composicao
from muschat.openmusic import PATCHES, InstalacaoOM, detectar_openmusic
from muschat.orquestrador import compor, descartar, retomar
from muschat.procedimentos import CATALOGO, Procedimento
from muschat.registro import Registro, detectar
from tests.test_composicao import QUARTETO, ChatFake, GeradorFake, _plano

SEM_OM = detectar(lilypond=None, openmusic=None, abjad_versao="3.31", which=lambda e: None)
OM = InstalacaoOM(Path("C:/OM/OM 8.0.exe"), "8.0", {"esquisse": Path("C:/OM/Libraries/Esquisse")})
PATCH_DE_TESTE = {"serie_harmonica": replace(PATCHES["serie_harmonica"], arquivo=__file__)}  # um .omp que existe
COM_OM = detectar(lilypond=None, openmusic=OM, abjad_versao="3.31", which=lambda e: None, patches=PATCH_DE_TESTE)
SAIDA_16 = "(3600 4800 5502 6000 6386 6702 6969 7200 7404 7586 7751 7902 8041 8169 8288 8400)"


def _rodar(plano, *, modo="automatico", registro=SEM_OM, composicao=None, catalogo=CATALOGO, pasta=None):
    c = composicao or Composicao()
    g = GeradorFake(plano)
    r = compor("pedido", c, gerador=g, chat=ChatFake(), modelo="m", modo=modo, registro=registro,
               catalogo=catalogo, pasta_tarefas=pasta or Path("nao-usar"))
    return c, r, g


def _hibrido(*extras, instrumentos=QUARTETO):
    plano = _plano(parametros={"parciais": 16}, instrumentos=instrumentos)
    plano["operacoes"] += [{"procedimento": "orquestrar_material", "parametros": [{"nome": "compassos",
                                                                                     "valor": 4}]}, *extras]
    return plano


# --- ticket 07: registro e roteador ---------------------------------------------------------------------------------

def test_manual_openmusic_sem_openmusic_recusa_com_alternativas_e_nao_chama_motor():
    chamado = []
    espiao = {**CATALOGO, "serie_harmonica": Procedimento(
        "serie_harmonica", "x", CATALOGO["serie_harmonica"].parametros,
        lambda *a: chamado.append(1) or CATALOGO["serie_harmonica"].funcao(*a), requer_composicao=False)}
    c, r, _ = _rodar(_plano(), modo="openmusic", catalogo=espiao)
    assert r.versao is None and r.tarefa is None and not chamado and c.pendente is None
    assert "OpenMusic" in r.resumo and "não encontrado" in r.resumo and "Python/Abjad (automático)" in r.resumo


def test_automatico_escolhe_python_para_serie_harmonica_e_diz_por_que():
    c, r, _ = _rodar(_plano())
    assert r.versao.experimento["decisao"]["etapas"] == [{"motor": "python", "procedimento": "serie_harmonica"}]
    assert "OpenMusic indisponível" in r.resumo


def test_automatico_com_openmusic_disponivel_prefere_o_automatico_e_justifica():
    c, r, _ = _rodar(_plano(), registro=COM_OM)
    justificativa = " ".join(r.versao.experimento["decisao"]["justificativa"])
    assert r.versao.experimento["decisao"]["etapas"][0]["motor"] == "python"
    assert "OpenMusic também faz" in justificativa and "R5" in justificativa


def test_procedimento_fora_do_registro_nunca_entra_no_prompt():
    _, _, g = _rodar(_plano(), registro=Registro.so_python(("serie_harmonica",)))
    sistema = g.chamadas[0].config.system_instruction
    assert "serie_harmonica" in sistema and "- transpor" not in sistema and "- densidade" not in sistema


def test_pedido_que_escolhe_o_motor_vale_como_escolha_manual():
    c, r, _ = _rodar({**_plano(), "motor": "openmusic"})
    assert r.versao is None and "OpenMusic" in r.resumo


@pytest.mark.parametrize("modo,registro,operacoes,esperado", [  # Experimento D: bateria de decisões
    ("automatico", SEM_OM, ["serie_harmonica"], ["python"]),
    ("automatico", COM_OM, ["serie_harmonica"], ["python"]),
    ("python", COM_OM, ["serie_harmonica", "densidade"], ["python", "python"]),
    ("openmusic", COM_OM, ["serie_harmonica"], ["openmusic"]),
    ("openmusic", COM_OM, ["serie_harmonica", "densidade"], None),  # densidade não existe no OpenMusic
    ("hibrido", COM_OM, ["serie_harmonica", "orquestrar_material"], ["openmusic", "python"]),
    ("hibrido", SEM_OM, ["serie_harmonica", "orquestrar_material"], None),
    ("hibrido", COM_OM, ["transpor"], None),  # nenhuma etapa possível no OpenMusic
])
def test_experimento_d_decisao_e_justificativa_registradas(modo, registro, operacoes, esperado, tmp_path):
    from muschat.plano import Plano
    from muschat.roteador import SemRota, rotear
    plano = Plano(objetivo="d", operacoes=[{"procedimento": o} for o in operacoes])
    if esperado is None:
        with pytest.raises(SemRota, match="Alternativas|nenhuma"):
            rotear(plano, modo, registro)
        return
    decisao = rotear(plano, modo, registro)
    assert [e.motor for e in decisao.etapas] == esperado
    assert len(decisao.justificativa) == len(operacoes) and all(decisao.justificativa)


def test_registro_detecta_bibliotecas_e_dependencias_externas(tmp_path):
    raiz = tmp_path / "OpenMusic 8.0"
    raiz.mkdir()
    (raiz / "OM 8.0.exe").write_bytes(b"")
    libs = tmp_path / "Libraries"
    for nome in ("Esquisse 2.1", "Chaos", "OMChroma-6.2"):
        (libs / nome).mkdir(parents=True)
    om = detectar_openmusic(raizes=[raiz], pasta_usuario=libs)
    assert om is not None and om.versao == "8.0" and set(om.bibliotecas) == {"esquisse", "chaos", "omchroma"}
    reg = detectar(lilypond=None, openmusic=om, abjad_versao="3.31", which=lambda e: None, patches=PATCH_DE_TESTE)
    estado = {c.nome: (c.estado, c.motivo) for c in reg.capacidades}
    assert estado["OpenMusic"][0] == "manual" and estado["Esquisse"][0] == "manual"
    sem_omp = detectar(lilypond=None, openmusic=om, abjad_versao="3.31", which=lambda e: None, patches={})
    assert sem_omp.capacidade("Esquisse").estado == "indisponivel"  # instalada, mas sem o .omp aprovado
    assert estado["Chaos"] == ("indisponivel", "instalada, mas ainda sem patch aprovado no projeto")
    assert estado["OMChroma"] == ("indisponivel", "instalada, mas falta o executável: csound")
    assert estado["OM-SuperVP"] == ("indisponivel", "não instalada em ~/OM/Libraries")
    assert estado["LilyPond"][0] == "indisponivel" and estado["Python"][0] == "automatico"


def test_sem_openmusic_tudo_que_depende_dele_fica_indisponivel_com_motivo():
    estado = {c.nome: (c.estado, c.motivo) for c in SEM_OM.capacidades}
    assert estado["OpenMusic"][0] == "indisponivel" and "OPENMUSIC" in estado["OpenMusic"][1]
    assert all(estado[n] == ("indisponivel", "precisa do OpenMusic") for n in ("Esquisse", "Chaos", "OM-SuperVP"))
    assert SEM_OM.disponiveis() == list(CATALOGO)


# --- ticket 08: OpenMusic assistido -------------------------------------------------------------------------------------

def _om(tmp_path, plano=None, composicao=None):
    return _rodar(plano or _plano(parametros={"parciais": 16}), modo="openmusic", registro=COM_OM,
                  composicao=composicao, pasta=tmp_path)


def test_etapa_openmusic_cria_tarefa_e_aguarda_o_compositor(tmp_path):
    c, r, _ = _om(tmp_path)
    assert r.versao is None and r.tarefa is not None and c.pendente is not None and "Aguardando você" in r.resumo
    pasta = Path(r.tarefa.pasta)
    assert (pasta / "entrada.txt").read_text() == "3600 1 1 1 16\n"
    assert "harm-series" in (pasta / "instrucoes.md").read_text(encoding="utf-8")
    assert b"entrada.txt" in r.tarefa.pacote()
    patch = Path(PATCHES["serie_harmonica"].arquivo)
    assert (pasta / patch.name).read_bytes() == patch.read_bytes()
    with zipfile.ZipFile(io.BytesIO(r.tarefa.pacote())) as pacote:
        assert pacote.read(patch.name) == patch.read_bytes()
        assert pacote.read("entrada.txt") == b"3600 1 1 1 16\n"


def test_saida_valida_vira_versao_com_proveniencia_openmusic(tmp_path):
    c, r, _ = _om(tmp_path)
    r2 = retomar(c, SAIDA_16)
    assert r2.versao is not None and c.pendente is None
    eventos = [e for _, _, e in r2.versao.ir.eventos()]
    assert {e.proveniencia.origem for e in eventos} == {"openmusic"}
    assert {e.proveniencia.autor for e in eventos} == {"OpenMusic/Esquisse"}
    alturas = [a.midicents for e in eventos if hasattr(e, "alturas") for a in e.alturas]
    assert alturas[:3] == [3600, 4800, 5500]  # 5502 aproximado ao quarto de tom


@pytest.mark.parametrize("saida,trecho", [
    ("(defun x () (shell \"rm\"))", "além de números"),
    ("3600,5 4800", "além de números"),
    ("3600 4800", "2 valores"),
    (SAIDA_16.replace("8400", "99999"), "fora de 0–12700"),
    ("1 " * 40_000, "limite"),
    (b"\xff\xfe\x00", "não é texto"),
])
def test_saida_malformada_e_recusada_e_a_tarefa_continua_aguardando(tmp_path, saida, trecho):
    c, r, _ = _om(tmp_path)
    r2 = retomar(c, saida)
    assert r2.versao is None and trecho in r2.resumo and c.pendente is not None and c.versoes == []


def test_tarefa_pendente_sobrevive_a_serializar_e_restaurar(tmp_path):
    c, r, _ = _om(tmp_path)
    restaurada = Composicao.de_json(c.para_json())
    assert restaurada.pendente.tarefa.id == r.tarefa.id
    assert retomar(restaurada, SAIDA_16).versao is not None


def test_com_etapa_aguardando_novo_pedido_nao_chama_o_modelo(tmp_path):
    c, _, _ = _om(tmp_path)
    _, r2, g = _rodar(_plano(), composicao=c)
    assert r2.versao is None and "Aguardando você" in r2.resumo and g.chamadas == []
    descartar(c)
    assert c.pendente is None


_OM_REAL = detectar_openmusic()


@pytest.mark.skipif(_OM_REAL is None, reason="OpenMusic não instalado")
def test_openmusic_real_detectado():
    assert _OM_REAL.executavel.is_file() and _OM_REAL.versao


# --- ticket 09: híbrido ------------------------------------------------------------------------------------------------

def test_hibrido_pausa_no_openmusic_e_a_retomada_completa_as_outras_etapas(tmp_path):
    plano = _hibrido({"procedimento": "aplicar_tecnicas"}) | {"tecnicas": ["harmonico"]}
    c, r, _ = _rodar(plano, modo="hibrido", registro=COM_OM, pasta=tmp_path)
    assert r.tarefa is not None and c.pendente.etapa == 0 and c.versoes == []
    r2 = retomar(c, SAIDA_16)
    v = r2.versao
    assert v is not None and [p.instrumento for p in v.ir.partes] == ["Violino", "Violino", "Viola", "Violoncelo"]
    assert [e["motor"] for e in v.experimento["etapas"]] == ["openmusic", "python", "python"]
    assert len(v.experimento["intermediarios"]) == 3
    assert v.experimento["intermediarios"][0]["partes"][0]["instrumento"] == "Material espectral"  # guardado
    usadas = {a.midicents % 1200 for _, _, e in v.ir.eventos() if hasattr(e, "alturas") for a in e.alturas}
    espectro = {round(int(x) / 50) * 50 % 1200 for x in SAIDA_16.strip("()").split()}  # quartos de tom
    assert usadas <= espectro  # o Python orquestrou só as classes de altura vindas do OpenMusic
    assert {e.proveniencia.origem for _, _, e in v.ir.eventos()} == {"algoritmo"}


def test_falha_numa_etapa_depois_da_retomada_nao_grava_versao_parcial(tmp_path):
    plano = _hibrido({"procedimento": "aplicar_tecnicas"}) | {"tecnicas": ["whistle tone"]}  # não cabe em cordas
    c, r, _ = _rodar(plano, modo="hibrido", registro=COM_OM, pasta=tmp_path)
    r2 = retomar(c, SAIDA_16)
    assert r2.versao is None and "etapa 3" in r2.resumo and c.versoes == [] and c.pendente is None


def test_invariante_de_preservacao_vale_na_etapa_do_openmusic(tmp_path):
    from tests.test_composicao import _quarteto
    c = _quarteto()
    plano = _plano(parametros={"parciais": 16}, selecao={"partes": ["P4"]})
    c, r, _ = _rodar(plano, modo="openmusic", registro=COM_OM, composicao=c, pasta=tmp_path)
    r2 = retomar(c, SAIDA_16)
    assert r2.versao is None and "fora da seleção" in r2.resumo and len(c.versoes) == 1


@pytest.mark.parametrize("estragar", [
    lambda d: d["pendente"].update(etapa=7),
    lambda d: d["pendente"]["tarefa"]["parametros"].update(microtom=99),
    lambda d: d["pendente"]["tarefa"].update(procedimento="executar_lisp"),
    lambda d: d.update(indice=5),
    lambda d: d.pop("versoes"),
])
def test_json_importado_incoerente_e_recusado(tmp_path, estragar):
    import json
    c, _, _ = _om(tmp_path)
    dados = json.loads(c.para_json())
    estragar(dados)
    with pytest.raises(ValueError):
        Composicao.de_json(json.dumps(dados))


def test_descartar_apaga_a_pasta_da_tarefa(tmp_path):
    c, r, _ = _om(tmp_path)
    descartar(c)
    assert not Path(r.tarefa.pasta).exists() and c.pendente is None

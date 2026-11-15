"""Abrir um MusicXML na Composição (ticket 03 da composição musical)."""

from muschat.notacao.musicxml import para_musicxml
from muschat.orquestrador import abrir, reaplicar
from tests.test_composicao import _quarteto
from tests.test_leitura_musicxml import ATRIBUTOS, _nota, _xml
from tests.test_motores import _om

APOJATURA = '<note><grace/><pitch><step>B</step><octave>3</octave></pitch><voice>1</voice><type>eighth</type></note>'
ARQUIVO = _xml(f'<part id="P1"><measure number="1">{ATRIBUTOS}{APOJATURA}{_nota("C", 4, 6, "half", extra="<dot/>")}'
               "</measure></part>")


def test_arquivo_aberto_vira_versao_do_compositor_e_desfazer_volta_a_anterior():
    c = _quarteto()
    r = abrir(c, ARQUIVO, "peca.musicxml")
    assert r.versao is not None and c.indice == 1 and c.atual.anterior == 0
    eventos = [e for _, _, e in r.versao.ir.eventos()]
    assert {(e.proveniencia.origem, e.proveniencia.operacao) for e in eventos} == {("compositor", "op2")}
    assert "peca.musicxml" in r.resumo and "1 apojatura(s) descartada(s)" in r.resumo
    assert r.versao.experimento["avisos"] == ["Violin: 1 apojatura(s) descartada(s)"]
    para_musicxml(r.versao.ir)
    c.desfazer()
    assert c.indice == 0


def test_versao_aberta_de_arquivo_nao_tem_operacao_para_reaplicar():
    c = _quarteto()
    abrir(c, ARQUIVO, "peca.musicxml")
    r = reaplicar(c, [])
    assert r.versao is None and "arquivo" in r.resumo and c.indice == 1


def test_arquivo_invalido_e_recusado_e_a_composicao_nao_muda():
    c = _quarteto()
    r = abrir(c, b"nada", "x.musicxml")
    assert r.versao is None and "não é um MusicXML válido" in r.resumo and len(c.versoes) == 1


def test_com_tarefa_do_openmusic_aguardando_o_arquivo_espera(tmp_path):
    c, _, _ = _om(tmp_path)
    r = abrir(c, ARQUIVO, "peca.musicxml")
    assert r.versao is None and "Aguardando você" in r.resumo and c.versoes == []


def test_motor_externo_abre_com_a_propria_origem_e_autor():
    c = _quarteto()
    r = abrir(c, ARQUIVO, "notagen.xml", origem="llm", autor="NotaGen-X")
    assert {(e.proveniencia.origem, e.proveniencia.autor) for _, _, e in r.versao.ir.eventos()} == {
        ("llm", "NotaGen-X")}

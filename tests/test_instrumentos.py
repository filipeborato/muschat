import random

import pytest

from muschat.instrumentos import FAMILIAS, INSTRUMENTOS, instrumento
from muschat.musicir import Nota
from muschat.notacao.lilypond import para_lilypond
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.tecnicas import TECNICAS, tecnicas_da_familia
from muschat.plano import Plano
from muschat.procedimentos import CATALOGO


def _peca(instrumentos, tecnicas=(), semente=1):
    plano = Plano(objetivo="t", operacoes=[{"procedimento": "serie_harmonica"}], instrumentos=list(instrumentos),
                  tecnicas=list(tecnicas))
    gerar = CATALOGO["serie_harmonica"]
    p = {k: f.padrao for k, f in gerar.parametros.items()} | {"compassos": 2}
    ir = gerar.funcao(None, plano, p, random.Random(semente))
    if tecnicas:
        ir = CATALOGO["aplicar_tecnicas"].funcao(ir, plano, {"proporcao": 1}, random.Random(semente))
    return ir


def test_a_orquestra_inteira_esta_na_tabela():
    essenciais = ["piccolo", "flauta", "oboe", "corne ingles", "clarinete", "clarone", "fagote", "contrafagote",
                  "trompa", "trompete", "trombone", "trombone baixo", "tuba", "timpanos", "caixa", "bumbo",
                  "pratos", "tam-tam", "marimba", "vibrafone", "xilofone", "glockenspiel", "celesta", "harpa",
                  "piano", "violino", "viola", "violoncelo", "contrabaixo", "soprano", "baixo"]
    assert [n for n in essenciais if n not in INSTRUMENTOS] == []
    assert {i.familia for i in INSTRUMENTOS.values()} == set(FAMILIAS)


@pytest.mark.parametrize("nome", sorted(INSTRUMENTOS))
def test_todo_instrumento_gera_e_se_escreve(nome):
    ir = _peca(["flauta", nome])
    parte = ir.partes[1]
    grave, agudo = parte.tessitura
    assert all(grave <= a.midicents <= agudo for e in parte.vozes[0].eventos if isinstance(e, Nota)
               for a in e.alturas)
    assert para_musicxml(ir) and para_lilypond(ir)["grade"]


@pytest.mark.parametrize("nome,familia", [
    ("Clarinete baixo", "madeiras"), ("clarone", "madeiras"), ("Violino I", "cordas"), ("Trompa em Fá", "metais"),
    ("Saxofone alto", "madeiras"), ("prato suspenso", "percussao"), ("mezzo-soprano", "voz"), ("Violão", "violao"),
])
def test_nomes_variados_casam_com_o_instrumento_certo(nome, familia):
    assert instrumento(nome).familia == familia


def test_percussao_sem_altura_definida_usa_a_linha_central():
    caixa = _peca(["caixa"]).partes[0]
    assert caixa.clave == "percussao"
    assert {a.midicents for e in caixa.vozes[0].eventos if isinstance(e, Nota) for a in e.alturas} == {7100}


def test_toda_familia_tem_tecnicas_e_toda_tecnica_tem_familia_valida():
    for familia in FAMILIAS:
        assert len(tecnicas_da_familia(familia)) >= 4, familia
    assert all(t.familias <= set(FAMILIAS) for t in TECNICAS.values())


def test_aplicar_tecnicas_so_usa_as_que_cabem_em_cada_instrumento():
    ir = _peca(["flauta", "violoncelo", "trompa"], tecnicas=["whistle tone", "sul ponticello", "gestopft"])
    usadas = {p.instrumento: {t for e in p.vozes[0].eventos if isinstance(e, Nota) for t in e.tecnicas}
              for p in ir.partes}
    assert usadas == {"Flauta": {"whistle tone"}, "Violoncelo": {"sul ponticello"}, "Trompa": {"gestopft"}}


def test_tecnica_que_nao_cabe_em_nenhum_instrumento_e_recusada():
    with pytest.raises(ValueError, match="nenhuma"):
        _peca(["violino"], tecnicas=["whistle tone"])


def test_tecnicas_de_um_instrumento_so_nao_vao_para_os_vizinhos_de_familia():
    from muschat.notacao.tecnicas import cabe
    assert cabe("whistle tone", "Flauta") and not cabe("whistle tone", "Clarone")
    assert cabe("una corda", "Piano") and not cabe("una corda", "Celesta")
    assert cabe("rim shot", "Caixa") and not cabe("rim shot", "Triângulo")
    assert all(i in INSTRUMENTOS for t in TECNICAS.values() for i in t.so)

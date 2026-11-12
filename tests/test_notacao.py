import xml.etree.ElementTree as ET

import pytest

from muschat.notacao.modelo import Compasso, Nota, Partitura, para_ir
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.render import svg_de
from tests.exemplos import quarteto


def _partitura(*compassos, **kw):
    base = dict(titulo="Exemplo", instrumento="Violino", clave="sol", metrica="4/4", andamento=60)
    return para_ir(Partitura(**{**base, **kw}, compassos=[Compasso(notas=list(c)) for c in compassos]))


def _notas(xml: str) -> list[ET.Element]:
    return list(ET.fromstring(xml).iter("note"))


def _palavras(xml: str) -> list[str]:
    return [w.text or "" for w in ET.fromstring(xml).iter("words")]


def test_cabecalho_titulo_parte_metrica_clave_andamento():
    raiz = ET.fromstring(para_musicxml(_partitura([Nota(alturas=["C4"], duracao="inteira")])))
    assert raiz.tag == "score-partwise"
    assert raiz.findtext("work/work-title") == "Exemplo"
    assert raiz.findtext("part-list/score-part/part-name") == "Violino"
    attrs = raiz.find("part/measure/attributes")
    assert attrs is not None
    assert (attrs.findtext("time/beats"), attrs.findtext("time/beat-type")) == ("4", "4")
    assert (attrs.findtext("clef/sign"), attrs.findtext("clef/line")) == ("G", "2")
    assert raiz.findtext(".//metronome/per-minute") == "60"


@pytest.mark.parametrize("altura,step,alter,octave,acidente", [
    ("E5", "E", None, "5", None),
    ("F#4", "F", "1", "4", "sharp"),
    ("Bb3", "B", "-1", "3", "flat"),
    ("A4+", "A", "0.5", "4", "quarter-sharp"),
    ("A4-", "A", "-0.5", "4", "quarter-flat"),
    ("C#+5", "C", "1.5", "5", "three-quarters-sharp"),
])
def test_alturas_com_acidentes_e_microtons(altura, step, alter, octave, acidente):
    nota = _notas(para_musicxml(_partitura([Nota(alturas=[altura], duracao="inteira")])))[0]
    assert nota.findtext("pitch/step") == step
    assert nota.findtext("pitch/alter") == alter
    assert nota.findtext("pitch/octave") == octave
    assert nota.findtext("accidental") == acidente


def test_duracoes_pontuacao_e_pausa():
    xml = para_musicxml(_partitura([
        Nota(alturas=["C4"], duracao="minima", pontuada=True),
        Nota(alturas=[], duracao="seminima"),
    ]))
    a, b = _notas(xml)
    assert (a.findtext("type"), a.find("dot") is not None, a.findtext("duration")) == ("half", True, "24")
    assert b.find("rest") is not None and b.findtext("type") == "quarter"


def test_acorde_marca_notas_seguintes_com_chord():
    notas = _notas(para_musicxml(_partitura([Nota(alturas=["G4", "C5", "E5"], duracao="inteira")])))
    assert [n.find("chord") is not None for n in notas] == [False, True, True]


def test_tecnicas_do_grimorio_viram_notehead_texto_e_technical():
    xml = para_musicxml(_partitura([
        Nota(alturas=["D5"], duracao="seminima", tecnicas=["col legno battuto"]),
        Nota(alturas=["E6"], duracao="seminima", tecnicas=["harmonico"]),
        Nota(alturas=["A4"], duracao="seminima", tecnicas=["sul ponticello", "tremolo"]),
        Nota(alturas=["G3"], duracao="seminima", tecnicas=["pizz. Bartók"]),
    ]))
    clb, harm, sp, bartok = _notas(xml)
    assert clb.findtext("notehead") == "x"
    assert harm.findtext("notehead") == "diamond" and harm.find("notations/technical/harmonic") is None
    assert sp.find("notations/ornaments/tremolo") is not None
    assert bartok.find("notations/technical/snap-pizzicato") is not None
    assert {"c.l. batt.", "sul pont."} <= set(_palavras(xml))


def test_texto_so_aparece_quando_a_tecnica_muda():
    xml = para_musicxml(_partitura([
        Nota(alturas=["A4"], duracao="seminima", tecnicas=["sul ponticello"]),
        Nota(alturas=["B4"], duracao="seminima", tecnicas=["sul ponticello"]),
        Nota(alturas=["C5"], duracao="seminima"),
    ]))
    assert _palavras(xml) == ["sul pont.", "ord."]


def test_tecnica_desconhecida_vira_texto_sem_falhar():
    xml = para_musicxml(_partitura([Nota(alturas=["C5"], duracao="inteira", tecnicas=["sopro com chave"])]))
    assert "sopro com chave" in _palavras(xml)


def test_glissando_liga_nota_com_a_seguinte():
    a, b = _notas(para_musicxml(_partitura([
        Nota(alturas=["A5"], duracao="minima", glissando=True),
        Nota(alturas=["A5-"], duracao="minima"),
    ])))
    ga, gb = a.find("notations/glissando"), b.find("notations/glissando")
    assert ga is not None and gb is not None
    assert (ga.get("type"), gb.get("type")) == ("start", "stop")


def test_dinamica_e_texto_livre():
    xml = para_musicxml(_partitura([Nota(alturas=["C5"], duracao="inteira", dinamica="pppp", texto="come un respiro")]))
    raiz = ET.fromstring(xml)
    assert raiz.find(".//direction/direction-type/dynamics/pppp") is not None
    assert "come un respiro" in _palavras(xml)


def test_altura_invalida_vira_pausa_em_vez_de_quebrar():
    nota = _notas(para_musicxml(_partitura([Nota(alturas=["H9"], duracao="inteira")])))[0]
    assert nota.find("rest") is not None


def test_varios_compassos_numerados():
    raiz = ET.fromstring(para_musicxml(_partitura(
        [Nota(alturas=["C4"], duracao="inteira")], [Nota(alturas=["D4"], duracao="inteira")]
    )))
    assert [m.get("number") for m in raiz.iter("measure")] == ["1", "2"]


def test_resultado_renderiza_no_verovio():
    xml = para_musicxml(_partitura(
        [Nota(alturas=["D5"], duracao="minima", tecnicas=["col legno battuto"], dinamica="mf"),
         Nota(alturas=["A4+"], duracao="minima", tecnicas=["sul ponticello", "tremolo"])],
    ))
    svg = svg_de(xml)
    assert svg is not None and svg.lstrip().startswith("<svg")


def test_render_de_xml_invalido_devolve_none():
    assert svg_de("<isto-nao-e-musicxml/>") is None


def test_semicolcheia_pontuada_tem_duracao_inteira():
    nota = _notas(para_musicxml(_partitura([Nota(alturas=["C5"], duracao="semicolcheia", pontuada=True)])))[0]
    assert nota.findtext("duration") == "3"


def test_bequadro_quando_acidente_e_cancelado_no_compasso():
    a, b, c = _notas(para_musicxml(_partitura(
        [Nota(alturas=["F#4"], duracao="seminima"), Nota(alturas=["F4"], duracao="seminima"),
         Nota(alturas=["G4"], duracao="minima")],
    )))
    assert (a.findtext("accidental"), b.findtext("accidental"), c.findtext("accidental")) == ("sharp", "natural", None)


def test_microtom_duplicado_e_rejeitado():
    nota = _notas(para_musicxml(_partitura([Nota(alturas=["A#+4+"], duracao="inteira")])))[0]
    assert nota.find("rest") is not None


def test_glissando_para_pausa_ou_de_altura_invalida_nao_fica_pendurado():
    xml = para_musicxml(_partitura([
        Nota(alturas=["A5"], duracao="seminima", glissando=True),
        Nota(alturas=[], duracao="seminima"),
        Nota(alturas=["H9"], duracao="seminima", glissando=True),
        Nota(alturas=["C5"], duracao="seminima"),
    ]))
    assert ET.fromstring(xml).find(".//glissando") is None


def test_saida_do_pizzicato_escreve_arco():
    xml = para_musicxml(_partitura([
        Nota(alturas=["G3"], duracao="minima", tecnicas=["pizzicato"]),
        Nota(alturas=["G3"], duracao="minima"),
    ]))
    assert _palavras(xml) == ["pizz.", "arco"]


def test_pausa_nao_conta_como_mudanca_de_tecnica():
    xml = para_musicxml(_partitura([
        Nota(alturas=["C6"], duracao="seminima", tecnicas=["frullato"]),
        Nota(alturas=[], duracao="seminima"),
        Nota(alturas=["D6"], duracao="minima", tecnicas=["frullato"]),
    ]))
    assert _palavras(xml) == ["flz."]


def test_texto_livre_igual_a_tecnica_nao_se_repete():
    xml = para_musicxml(_partitura([Nota(alturas=["E5"], duracao="inteira", tecnicas=["aria"], texto="Aria")]))
    assert _palavras(xml) == ["aria"]


def test_svg_tem_fundo_branco_para_tema_escuro():
    svg = svg_de(para_musicxml(_partitura([Nota(alturas=["C5"], duracao="inteira")])))
    assert svg is not None and 'fill="white"' in svg[:2000]


def test_dinamica_repetida_nao_e_reescrita():
    xml = para_musicxml(_partitura(
        [Nota(alturas=["C5"], duracao="minima", dinamica="p"), Nota(alturas=["D5"], duracao="minima", dinamica="p")],
        [Nota(alturas=["E5"], duracao="minima", dinamica="p"), Nota(alturas=["F5"], duracao="minima", dinamica="f")],
    ))
    assert [d[0].tag for d in ET.fromstring(xml).iter("dynamics")] == ["p", "f"]


def test_musicir_de_duas_partes_com_vozes_e_tercina_renderiza_no_verovio():
    raiz = ET.fromstring(xml := para_musicxml(quarteto()))
    assert [p.findtext("part-name") for p in raiz.iter("score-part")] == ["Violino", "Violoncelo"]
    vln, vc = raiz.findall("part")
    assert vln.findtext(".//pitch/step") == "A" and vln.findtext(".//pitch/alter") == "0.5"
    assert vc.findtext("measure/attributes/clef/sign") == "F"
    tercina = [n for n in vc.iter("note") if n.find("time-modification") is not None]
    assert [n.findtext("type") for n in tercina] == ["quarter"] * 3
    assert {n.findtext("time-modification/actual-notes") for n in tercina} == {"3"}
    assert [t.get("type") for n in tercina for t in n.iter("tuplet")] == ["start", "stop"]
    assert {n.findtext("voice") for n in vc.iter("note")} == {"1"}  # curva/espectral não viram notas falsas
    svg = svg_de(xml)
    assert svg is not None and svg.count("<g") > 10


def test_notas_sem_grafia_usam_sustenido_e_grafia_forca_a_letra():
    from fractions import Fraction as F

    from muschat.musicir import Altura, Compasso as C, MusicIR, Nota as N, Parte, Proveniencia, Voz

    def nota(i, mc, grafia=None):
        return N(id=f"n{i}", inicio=F(i, 4), duracao=F(1, 4), alturas=[Altura(midicents=mc, grafia=grafia)],
                 proveniencia=Proveniencia(origem="algoritmo"))

    ir = MusicIR(titulo="t", compassos=[C(numerador=4, denominador=4)], partes=[Parte(
        id="P1", instrumento="Flauta", vozes=[Voz(id="v", eventos=[
            nota(0, 6100), nota(1, 6100, "D"), nota(2, 6150), nota(3, 6025)])])])
    passos = [(n.findtext("pitch/step"), n.findtext("pitch/alter"), n.findtext("accidental"))
              for n in _notas(para_musicxml(ir))]
    assert passos == [("C", "1", "sharp"), ("D", "-1", "flat"), ("C", "1.5", "three-quarters-sharp"),
                      ("C", "0.25", "natural-up")]

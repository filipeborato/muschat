import io
import zipfile
from fractions import Fraction as F

import pytest

from muschat.musicir import Altura, Compasso, MusicIR, Nota, Parte, Pausa, Proveniencia, Quialtera, Voz
from muschat.notacao.musicxml import LIMITE_MUSICXML, de_musicxml, para_musicxml


def _xml(partes: str, lista: str = '<score-part id="P1"><part-name>Violin</part-name></score-part>') -> bytes:
    return (f'<?xml version="1.0"?><score-partwise version="4.0"><work><work-title>Teste</work-title></work>'
            f"<part-list>{lista}</part-list>{partes}</score-partwise>").encode()


def _nota(step: str, oitava: int, duracao: int, tipo: str, alter: str | None = None, extra: str = "") -> str:
    acidente = f"<alter>{alter}</alter>" if alter else ""
    return (f"<note><pitch><step>{step}</step>{acidente}<octave>{oitava}</octave></pitch>"
            f"<duration>{duracao}</duration><voice>1</voice><type>{tipo}</type>{extra}</note>")


ATRIBUTOS = ("<attributes><divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time>"
             "<clef><sign>F</sign><line>4</line></clef></attributes>")


def test_le_uma_parte_com_notas_pausa_acidente_dinamica_metrica_e_andamento():
    compasso1 = (ATRIBUTOS + '<direction><direction-type><metronome><beat-unit>quarter</beat-unit>'
                 "<per-minute>72</per-minute></metronome></direction-type><sound tempo=\"72\"/></direction>"
                 "<direction><direction-type><dynamics><pp/></dynamics></direction-type></direction>"
                 + _nota("C", 3, 2, "quarter") + _nota("F", 3, 3, "quarter", "1", "<dot/>")
                 + "<note><rest/><duration>1</duration><voice>1</voice><type>eighth</type></note>")
    compasso2 = _nota("A", 3, 6, "half", "-0.5", "<dot/>")
    ir, avisos = de_musicxml(_xml(f'<part id="P1"><measure number="1">{compasso1}</measure>'
                                  f'<measure number="2">{compasso2}</measure></part>'))
    assert avisos == []
    assert ir.titulo == "Teste"
    assert [(c.numerador, c.denominador, c.andamento) for c in ir.compassos] == [(3, 4, 72), (3, 4, None)]
    parte = ir.partes[0]
    assert (parte.instrumento, parte.clave) == ("Violin", "fa")
    eventos = parte.vozes[0].eventos
    assert [(type(e), e.inicio, e.duracao) for e in eventos] == [
        (Nota, F(0), F(1, 4)), (Nota, F(1, 4), F(3, 8)), (Pausa, F(5, 8), F(1, 8)), (Nota, F(3, 4), F(3, 4))]
    assert [a.midicents for e in eventos if isinstance(e, Nota) for a in e.alturas] == [4800, 5400, 5650]
    assert [a.grafia for e in eventos if isinstance(e, Nota) for a in e.alturas] == ["C", "F", "A"]
    assert eventos[0].dinamica == "pp" and eventos[1].dinamica is None
    assert eventos[0].proveniencia.origem == "compositor"


def _conteudo(ir) -> list:
    """O que a ida e volta precisa preservar (os ids são renumerados na leitura)."""
    return [(p.instrumento, p.clave, [[(e.tipo, e.inicio, e.duracao, [a.midicents for a in getattr(e, "alturas", [])],
                                        e.dinamica, getattr(e, "ligada", False)) for e in v.eventos]
                                      for v in p.vozes]) for p in ir.partes]


def test_ligadura_atraves_da_barra_e_lida_e_reescrita():
    ligada = '<tie type="start"/>', '<notations><tied type="start"/></notations>'
    chega = '<tie type="stop"/>', '<notations><tied type="stop"/></notations>'

    def com(nota: str, ligadura: tuple[str, str]) -> str:  # <tie> vem depois de <duration>, <tied> no fim
        return nota.replace("</duration>", "</duration>" + ligadura[0]).replace("</note>", ligadura[1] + "</note>")

    c1 = ATRIBUTOS + _nota("C", 3, 4, "half") + com(_nota("D", 3, 2, "quarter"), ligada)
    c2 = com(_nota("D", 3, 2, "quarter"), chega) + _nota("E", 3, 4, "half")
    ir, _ = de_musicxml(_xml(f'<part id="P1"><measure number="1">{c1}</measure>'
                             f'<measure number="2">{c2}</measure></part>'))
    assert [e.ligada for e in ir.partes[0].vozes[0].eventos] == [False, True, False, False]
    xml = para_musicxml(ir)
    assert xml.count('<tie type="start"') == 1 and xml.count('<tie type="stop"') == 1
    assert xml.count('<tied type="start"') == 1 and xml.count('<tied type="stop"') == 1
    assert _conteudo(de_musicxml(xml.encode())[0]) == _conteudo(ir)


def test_ida_e_volta_preserva_partes_vozes_acordes_pausas_e_dinamicas():
    prov = Proveniencia(origem="algoritmo")

    def nota(i, inicio, dur, *mc, din=None):
        return Nota(id=i, inicio=inicio, duracao=dur, alturas=[Altura(midicents=m) for m in mc], dinamica=din,
                    proveniencia=prov)

    violino = Parte(id="a", instrumento="Violino", vozes=[Voz(id="a1", eventos=[
        nota("n1", F(0), F(1, 4), 7600, 8100, din="mf"), Pausa(id="n2", inicio=F(1, 4), duracao=F(1, 4), proveniencia=prov),
        nota("n3", F(1, 2), F(1, 2), 6950), nota("n4", F(1), F(1), 7200, din="pp")])])
    piano = Parte(id="b", instrumento="Piano", clave="fa", vozes=[
        Voz(id="b1", eventos=[nota("m1", F(0), F(1), 4800), nota("m2", F(1), F(1, 2), 5000)]),
        Voz(id="b2", eventos=[nota("m3", F(1, 2), F(1, 2), 3600, 4300), nota("m4", F(3, 2), F(1, 2), 3800)])])
    ir = MusicIR(titulo="Ida e volta", compassos=[Compasso(numerador=4, denominador=4, andamento=60),
                                                  Compasso(numerador=4, denominador=4)], partes=[violino, piano])
    lida, avisos = de_musicxml(para_musicxml(ir).encode())
    assert avisos == []
    assert lida.titulo == "Ida e volta"
    assert [(c.numerador, c.denominador, c.andamento) for c in lida.compassos] == [(4, 4, 60), (4, 4, None)]
    assert _conteudo(lida) == _conteudo(ir)


def test_quialteras_simples_voltam_como_grupos():
    prov = Proveniencia(origem="algoritmo")
    eventos = [Nota(id=f"t{k}", inicio=F(k, 6), duracao=F(1, 6), alturas=[Altura(midicents=6000 + 200 * k)],
                    proveniencia=prov) for k in range(3)]
    eventos.append(Nota(id="fim", inicio=F(1, 2), duracao=F(1, 2), alturas=[Altura(midicents=6700)], proveniencia=prov))
    ir = MusicIR(titulo="q", compassos=[Compasso(numerador=4, denominador=4)], partes=[Parte(
        id="p", instrumento="Flauta", vozes=[Voz(id="v", eventos=eventos, quialteras=[
            Quialtera(id="q1", numerador=3, denominador=2, eventos=["t0", "t1", "t2"])])])])
    lida, avisos = de_musicxml(para_musicxml(ir).encode())
    voz = lida.partes[0].vozes[0]
    assert avisos == [] and _conteudo(lida) == _conteudo(ir)
    assert [(q.numerador, q.denominador, q.eventos) for q in voz.quialteras] == [
        (3, 2, [e.id for e in voz.eventos[:3]])]


def test_o_que_a_musicir_nao_representa_vira_aviso_e_palavras_viram_texto():
    apojatura = '<note><grace/><pitch><step>B</step><octave>3</octave></pitch><voice>1</voice><type>eighth</type></note>'
    palavras = '<direction><direction-type><words>sul pont.</words></direction-type></direction>'
    rfz = "<direction><direction-type><dynamics><rfz/></dynamics></direction-type></direction>"
    duas_pautas = ("<attributes><divisions>2</divisions><staves>2</staves><clef number=\"1\"><sign>G</sign>"
                   "<line>2</line></clef><clef number=\"2\"><sign>F</sign><line>4</line></clef></attributes>")
    c1 = duas_pautas + apojatura + palavras + rfz + _nota("C", 4, 8, "whole")
    ir, avisos = de_musicxml(_xml(f'<part id="P1"><measure number="1">{c1}</measure></part>'))
    nota = ir.partes[0].vozes[0].eventos[0]
    assert (nota.texto, nota.dinamica, nota.duracao) == ("sul pont.", None, F(1))
    assert avisos == ["Violin: 1 apojatura(s) descartada(s)", "Violin: dinâmica 'rfz' descartada",
                      "Violin: 2 pautas lidas numa só, na clave da primeira"]


@pytest.mark.parametrize("dados, trecho", [
    (b"isto nao e xml", "não é um MusicXML válido"),
    (b'<?xml version="1.0"?><score-timewise/>', "score-partwise"),
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><score-partwise/>', "entidade"),
    (b'<?xml version="1.0"?><score-partwise><part-list/></score-partwise>', "nenhuma parte"),
    (b"<" * (LIMITE_MUSICXML + 1), "limite"),
])
def test_arquivo_que_nao_serve_e_recusado_com_mensagem(dados, trecho):
    with pytest.raises(ValueError, match=trecho):
        de_musicxml(dados)


def test_le_mxl_comprimido_pelo_container():
    xml = _xml('<part id="P1"><measure number="1">' + ATRIBUTOS + _nota("C", 4, 6, "half", extra="<dot/>")
               + "</measure></part>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="peca/score.xml"/>'
                                             "</rootfiles></container>")
        z.writestr("peca/score.xml", xml)
    ir, _ = de_musicxml(buffer.getvalue())
    assert ir.partes[0].vozes[0].eventos[0].alturas[0].midicents == 6000


def test_anacruse_vira_compasso_curto():
    c0 = ATRIBUTOS + _nota("G", 3, 2, "quarter")
    c1 = _nota("C", 4, 6, "half", extra="<dot/>")
    ir, _ = de_musicxml(_xml(f'<part id="P1"><measure number="0" implicit="yes">{c0}</measure>'
                             f'<measure number="1">{c1}</measure></part>'))
    assert [c.comprimento for c in ir.compassos] == [F(1, 4), F(3, 4)]
    assert [e.inicio for e in ir.partes[0].vozes[0].eventos] == [F(0), F(1, 4)]
    para_musicxml(ir)  # continua notável


def test_articulacoes_conhecidas_viram_tecnica_e_o_resto_das_notacoes_vira_aviso():
    marcas = ('<notations><articulations><staccato/><strong-accent/></articulations><slur type="start"/>'
              "<fermata/></notations>")
    c1 = ATRIBUTOS + _nota("C", 4, 6, "half", extra="<dot/><notehead>diamond</notehead>" + marcas)
    ir, avisos = de_musicxml(_xml(f'<part id="P1"><measure number="1">{c1}</measure></part>'))
    assert ir.partes[0].vozes[0].eventos[0].tecnicas == ["staccato", "marcato"]
    assert avisos == ["Violin: notação descartada: cabeça diamond (1), fermata (1), slur (1)"]


def test_percussao_sem_altura_usa_a_posicao_escrita_e_acorde_orfao_e_recusado():
    tambor = ('<note><unpitched><display-step>E</display-step><display-octave>4</display-octave></unpitched>'
              "<duration>8</duration><voice>1</voice><type>whole</type></note>")
    ir, _ = de_musicxml(_xml(f'<part id="P1"><measure number="1">{ATRIBUTOS}{tambor}</measure></part>'))
    assert ir.partes[0].vozes[0].eventos[0].alturas[0].midicents == 6400
    orfao = _nota("C", 4, 8, "whole", extra="").replace("<note>", "<note><chord/>")
    with pytest.raises(ValueError, match="acorde"):
        de_musicxml(_xml(f'<part id="P1"><measure number="1">{ATRIBUTOS}{orfao}</measure></part>'))


def test_entidade_em_qualquer_codificacao_e_recusada():
    xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]><score-partwise/>'
    with pytest.raises(ValueError, match="entidade"):
        de_musicxml(xml.encode("utf-16"))


def test_container_do_mxl_tambem_tem_limite():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", b" " * (LIMITE_MUSICXML + 1))
    with pytest.raises(ValueError, match="limite"):
        de_musicxml(buffer.getvalue())


def test_transpositor_vira_som_real_e_mudanca_de_clave_vira_aviso():
    clarinete = ("<attributes><divisions>2</divisions><time><beats>4</beats><beat-type>4</beat-type></time>"
                 "<clef><sign>G</sign><line>2</line></clef><transpose><diatonic>-1</diatonic>"
                 "<chromatic>-2</chromatic></transpose></attributes>")
    nova_clave = "<attributes><clef><sign>F</sign><line>4</line></clef></attributes>"
    andamento = '<sound tempo="90"/>'
    c1 = clarinete + andamento + _nota("D", 5, 8, "whole")
    c2 = nova_clave + _nota("D", 5, 8, "whole")
    ir, avisos = de_musicxml(_xml(f'<part id="P1"><measure number="1">{c1}</measure>'
                                  f'<measure number="2">{c2}</measure></part>'))
    assert [e.alturas[0].midicents for e in ir.partes[0].vozes[0].eventos] == [7200, 7200]  # Ré 5 escrito = Dó 5
    assert ir.compassos[0].andamento == 90
    assert avisos == ["Violin: mudança de clave depois do primeiro compasso descartada"]

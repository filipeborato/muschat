import subprocess
from fractions import Fraction as F
from pathlib import Path

import pytest

from muschat.musicir import Altura, Compasso, MusicIR, Nota, Parte, Pausa, Quialtera, Voz
from muschat.notacao.lilypond import AVISO_SEM_LILYPOND, Lilypond, detectar, gravar, para_lilypond
from tests.exemplos import LLM, quarteto


def _solo(eventos, quialteras=(), compassos=1):
    return MusicIR(titulo='Solo "1"', compassos=[Compasso(numerador=4, denominador=4)] * compassos, partes=[
        Parte(id="P1", instrumento="Violoncelo", clave="fa", vozes=[Voz(id="V", eventos=eventos,
                                                                         quialteras=list(quialteras))])])


def _nota(i, inicio, duracao, mc, **kw):
    return Nota(id=i, inicio=inicio, duracao=duracao, alturas=[Altura(midicents=mc)], proveniencia=LLM, **kw)


def test_quialtera_quarto_de_tom_harmonico_e_glissando_no_texto_lilypond():
    ly = para_lilypond(_solo([
        _nota("a", F(0), F(1, 6), 4850, tecnicas=["harmonico"], glissando=True, dinamica="pp"),
        _nota("b", F(1, 6), F(1, 6), 5500),
        _nota("c", F(1, 3), F(1, 6), 4825),  # oitavo de tom
        _nota("d", F(1, 2), F(1, 2), 4800, tecnicas=["sul ponticello", "staccato"], texto="come un respiro"),
    ], [Quialtera(id="q", numerador=3, denominador=2, eventos=["a", "b", "c"])]))["grade"]
    assert r"\tuplet 3/2" in ly
    assert "cqs4" in ly or "cqs8" in ly  # 4850 = Dó 3 + quarto de tom; colcheia de tercina
    assert "NoteHead.style = #'harmonic" in ly and r"\glissando" in ly and r"\pp" in ly
    assert r"\arrow-head #Y #UP" in ly  # 4825: quarto de tom abaixo + seta
    assert r'\italic "sul pont."' in ly and r"\staccato" in ly and '"come un respiro"' in ly
    assert r'\clef "bass"' in ly and 'title = "Solo \\"1\\""' in ly


def test_grade_e_uma_parte_por_instrumento_com_polifonia():
    fontes = para_lilypond(quarteto())
    assert set(fontes) == {"grade", "Violino (vln)", "Violoncelo (vc)"}
    assert r"\new StaffGroup" in fontes["grade"] and fontes["grade"].count(r"\context Staff") == 2
    cello = fontes["Violoncelo (vc)"]
    assert r"\voiceOne" not in cello  # curva e espectral não viram voz notada
    assert 'subtitle = "Violoncelo"' in cello and "Violino" not in cello
    assert r"\tempo 4=60" in fontes["grade"] and r"\time 4/4" in fontes["grade"]


def test_lacunas_viram_pausas_cortadas_na_barra():
    ly = para_lilypond(_solo([_nota("a", F(3, 4), F(1, 4), 4800), _nota("b", F(3, 2), F(1, 2), 4800)],
                             compassos=2))["grade"]
    assert ly.index("r2.") < ly.index("c4") < ly.index("r2\n")  # 0–3/4: mínima pontuada; 1–3/2: mínima


def test_duracao_sem_figura_vira_erro_de_valor():
    with pytest.raises(ValueError, match="figura"):
        para_lilypond(_solo([_nota("a", F(0), F(5, 8), 4800), Pausa(id="b", inicio=F(5, 8), duracao=F(3, 8),
                                                                      proveniencia=LLM)]))


# --- gravação ----------------------------------------------------------------------------------------------------------

LP = Lilypond("lilypond", (2, 26, 0))
FONTES = {"grade": "{ c'4 }", "P1": "{ c'4 }"}


class ExecutorFake:
    def __init__(self, erro=None, escreve=True):
        self.erro, self.escreve, self.chamadas = erro, escreve, []

    def __call__(self, args, cwd, timeout):
        self.chamadas.append(args)
        if self.erro:
            raise self.erro
        if self.escreve:
            for a in args:
                if a.endswith(".ly"):
                    destino = Path(cwd, a[:-3] + (".svg" if "--svg" in args else ".pdf"))
                    destino.write_bytes(b"<svg/>" if "--svg" in args else b"%PDF-1.7")
        return ""


def test_gravacao_devolve_pdf_de_cada_fonte_e_svg_da_grade():
    ex = ExecutorFake()
    g = gravar(FONTES, LP, executor=ex)
    assert g.aviso is None and g.pdfs == {"grade": b"%PDF-1.7", "P1": b"%PDF-1.7"} and g.svgs == ["<svg/>"]
    assert ex.chamadas[0][-2:] == ["f0.ly", "f1.ly"] and "--svg" in ex.chamadas[1]


def test_sem_lilypond_cai_para_o_verovio_com_aviso_de_instalacao():
    g = gravar(FONTES, None)
    assert g.svgs == [] and g.aviso == AVISO_SEM_LILYPOND and "instale o LilyPond" in g.aviso


@pytest.mark.parametrize("erro,trecho", [
    (subprocess.TimeoutExpired("lilypond", 120), "passou de 120 s"),
    (subprocess.CalledProcessError(1, "lilypond", stderr="erro de sintaxe na linha 3"), "linha 3"),
    (FileNotFoundError("lilypond"), "Não foi possível executar"),
])
def test_falha_do_processo_externo_vira_aviso_amigavel(erro, trecho):
    g = gravar(FONTES, LP, executor=ExecutorFake(erro))
    assert g.svgs == [] and trecho in g.aviso


def test_saida_ausente_vira_aviso():
    assert "PDF válido" in gravar(FONTES, LP, executor=ExecutorFake(escreve=False)).aviso


def test_detecta_versao_compativel_e_recusa_antiga():
    def versao(texto):
        return lambda args, cwd, timeout: texto
    assert detectar("lp", executor=versao("GNU LilyPond 2.26.0 (running Guile 3.0)")).versao == (2, 26, 0)
    assert detectar("lp", executor=versao("GNU LilyPond 2.24.4")) is None  # todo candidato responde 2.24
    assert detectar("lp", executor=versao("não é o lilypond")) is None


def test_parte_com_duas_vozes_usa_voice_one_e_voice_two():
    ir = _solo([_nota("a", F(0), F(1), 6000)])
    segunda = Voz(id="V2", eventos=[_nota("b", F(1, 2), F(1, 2), 5500)])
    ir = ir.model_copy(update={"partes": [ir.partes[0].model_copy(update={"vozes": [*ir.partes[0].vozes, segunda]})]})
    ly = para_lilypond(ir)["grade"]
    assert "voiceOne" in ly and "voiceTwo" in ly and "s2" in ly  # lacuna da 2ª voz é espaço, não pausa


_REAL = detectar()


@pytest.mark.skipif(_REAL is None, reason="LilyPond >= 2.25.26 não detectado")
def test_compilacao_real_da_grade_e_das_partes():
    g = gravar(para_lilypond(quarteto()), _REAL)
    assert g.aviso is None, g.aviso
    assert set(g.pdfs) == {"grade", "Violino (vln)", "Violoncelo (vc)"} and all(p.startswith(b"%PDF") for p in g.pdfs.values())
    assert g.svgs and "<svg" in g.svgs[0]

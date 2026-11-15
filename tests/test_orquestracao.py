import pytest

from muschat import musicir as ir
from muschat.orquestracao import adaptar
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.lilypond import para_lilypond
from muschat.notacao.tecnicas import tecnicas_intocaveis
from tests.test_composicao import _quarteto
from tests.test_contemporaneos import material, rodar


def test_quarteto_vira_duo_com_registro_claves_e_linhas_ativas():
    fonte = _quarteto(compassos=8).atual.ir
    antes = fonte.model_dump_json()
    duo, aviso = adaptar(fonte, ["flauta", "violoncelo"])
    assert [p.instrumento for p in duo.partes] == ["Flauta", "Violoncelo"]
    assert [p.clave for p in duo.partes] == ["sol", "fa"]
    assert duo.compassos == fonte.compassos
    assert "4 para 2" in aviso and "violoncelo ← Violoncelo" in aviso
    for parte in duo.partes:
        notas = [e for e in parte.vozes[0].eventos if isinstance(e, ir.Nota)]
        assert notas
        assert all(len(n.alturas) == 1 and parte.tessitura[0] <= n.alturas[0].midicents <= parte.tessitura[1] for n in notas)
    assert not tecnicas_intocaveis(duo)
    assert fonte.model_dump_json() == antes
    assert para_musicxml(duo) and para_lilypond(duo)


def test_expansao_renomeia_ids_e_preserva_quialteras():
    fonte = rodar("deformar_ritmo", material(), proporcao=1)
    fonte.partes = fonte.partes[:1]
    duo, _ = adaptar(fonte, ["flauta", "violoncelo"])
    ir.MusicIR.model_validate(duo.model_dump())
    for parte in duo.partes:
        assert [(e.inicio, e.duracao) for e in parte.vozes[0].eventos] == [
            (e.inicio, e.duracao) for e in fonte.partes[0].vozes[0].eventos]
        assert len(parte.vozes[0].quialteras) == len(fonte.partes[0].vozes[0].quialteras)
    assert para_musicxml(duo) and para_lilypond(duo)


def test_fonte_inteiramente_silenciosa_nao_inventa_notas():
    fonte = material()
    for parte in fonte.partes:
        for voz in parte.vozes:
            voz.eventos = []
    with pytest.raises(ValueError, match="nenhuma linha com notas"):
        adaptar(fonte, ["flauta", "violoncelo"])

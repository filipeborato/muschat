from fractions import Fraction as F

import pytest
from pydantic import ValidationError

from muschat.musicir import Altura, MusicIR, Nota, Parte, Pausa, Proveniencia, Quialtera, Voz, comparar
from tests.exemplos import LLM, quarteto


def test_ida_e_volta_json_preserva_tudo():
    ir = quarteto()
    assert MusicIR.model_validate_json(ir.model_dump_json()) == ir


@pytest.mark.parametrize("campos", [
    {"duracao": F(0)},
    {"duracao": F(-1, 4)},
    {"alturas": [Altura(midicents=6900)], "inicio": F(-1, 4)},
])
def test_schema_rejeita_duracao_ou_posicao_invalida(campos):
    base = {"id": "x", "inicio": F(0), "duracao": F(1, 4), "alturas": [Altura(midicents=6900)], "proveniencia": LLM}
    with pytest.raises(ValidationError):
        Nota(**{**base, **campos})


@pytest.mark.parametrize("midicents", [-100, 12800])
def test_schema_rejeita_altura_fora_da_faixa(midicents):
    with pytest.raises(ValidationError):
        Altura(midicents=midicents)


def test_schema_rejeita_eventos_sobrepostos_na_mesma_voz():
    with pytest.raises(ValidationError):
        Voz(id="v", eventos=[
            Pausa(id="a", inicio=F(0), duracao=F(1, 2), proveniencia=LLM),
            Pausa(id="b", inicio=F(1, 4), duracao=F(1, 2), proveniencia=LLM),
        ])


def test_schema_rejeita_id_repetido():
    ir = quarteto()
    dados = ir.model_dump()
    dados["partes"][1]["vozes"][0]["eventos"][0]["id"] = "a"
    with pytest.raises(ValidationError):
        MusicIR.model_validate(dados)


def _trocar_evento(ir: MusicIR, id_evento: str, **mudancas) -> MusicIR:
    novo = ir.model_copy(deep=True)
    for _, voz, evento in novo.eventos():
        if evento.id == id_evento:
            voz.eventos[voz.eventos.index(evento)] = evento.model_copy(update=mudancas)
    return novo


def test_diff_aponta_exatamente_o_evento_alterado_e_os_campos():
    antes = quarteto()
    depois = _trocar_evento(antes, "d", alturas=[Altura(midicents=4350)], dinamica="f")
    d = comparar(antes, depois)
    assert (d.adicionados, d.removidos, d.alterados) == ([], [], {"d": ["alturas", "dinamica"]})


def test_diff_de_versoes_iguais_e_vazio():
    assert not comparar(quarteto(), quarteto())


def test_diff_lista_adicionados_e_removidos():
    antes = quarteto()
    depois = antes.model_copy(deep=True)
    voz = depois.partes[0].vozes[0]
    voz.eventos[1] = Nota(id="novo", inicio=F(1, 2), duracao=F(1, 2), alturas=[Altura(midicents=7200)],
                          proveniencia=Proveniencia(origem="compositor"))
    d = comparar(antes, depois)
    assert (d.adicionados, d.removidos, d.alterados) == (["novo"], ["b"], {})


def test_diff_detecta_evento_que_mudou_de_voz():
    antes = quarteto()
    depois = antes.model_copy(deep=True)
    pausa = depois.partes[1].vozes[0].eventos.pop()
    depois.partes[1].vozes.append(Voz(id="vc4", eventos=[pausa]))
    assert comparar(antes, depois).alterados == {"f": ["voz"]}


@pytest.mark.parametrize("quialteras", [
    [Quialtera(id="q1", numerador=3, denominador=2, eventos=["a"], pai="q2"),
     Quialtera(id="q2", numerador=3, denominador=2, eventos=["b"], pai="q1")],
    [Quialtera(id="q1", numerador=3, denominador=2, eventos=["a"]),
     Quialtera(id="q2", numerador=5, denominador=4, eventos=["a"])],
    [Quialtera(id="q1", numerador=3, denominador=2, eventos=["inexistente"])],
])
def test_schema_rejeita_quialteras_incoerentes(quialteras):
    eventos = [Pausa(id="a", inicio=F(0), duracao=F(1, 6), proveniencia=LLM),
               Pausa(id="b", inicio=F(1, 6), duracao=F(1, 6), proveniencia=LLM)]
    with pytest.raises(ValidationError):
        Voz(id="v", eventos=eventos, quialteras=quialteras)


def test_diff_cobre_partes_vozes_e_compassos():
    antes = quarteto()
    depois = antes.model_copy(deep=True)
    depois.partes[1].clave = "do4"
    depois.partes[1].vozes[0].quialteras = []
    depois.compassos[0].andamento = 90
    d = comparar(antes, depois)
    assert d.alterados == {"vc": ["clave"], "vc1": ["quialteras"]}
    assert d.compassos == [1]


@pytest.mark.parametrize("tessitura", [(7600, 3600), (-100, 3600), (3600, 12800)])
def test_schema_rejeita_tessitura_invalida(tessitura):
    with pytest.raises(ValidationError):
        Parte(id="p", instrumento="Violoncelo", tessitura=tessitura, vozes=[Voz(id="v")])

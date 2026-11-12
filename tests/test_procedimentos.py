import random
from fractions import Fraction as F

import pytest

from muschat.musicir import Nota, Pausa, localizar
from muschat.notacao.musicxml import para_musicxml
from muschat.plano import Plano, Selecao
from muschat.procedimentos import CATALOGO
from muschat.restricoes import Restricao, verificar


def _rodar(nome, ir=None, selecao=None, semente=1, instrumentos=None, **params):
    proc = CATALOGO[nome]
    completos = {k: params.get(k, f.padrao) for k, f in proc.parametros.items()}
    plano = Plano(objetivo="t", operacoes=[{"procedimento": nome}], selecao=selecao or Selecao(),
                  instrumentos=instrumentos or [], tecnicas=["harmonico"])
    novo = proc.funcao(ir, plano, completos, random.Random(semente))
    para_musicxml(novo)  # todo resultado continua notável (nada atravessa a barra, figuras válidas)
    return novo


@pytest.fixture(scope="module")
def quarteto():
    return _rodar("serie_harmonica", compassos=4)


def _notas(ir, parte=None):
    return [e for p, _, e in ir.eventos() if isinstance(e, Nota) and (parte is None or p.id == parte)]


def _ritmo(ir):
    return [(e.id, e.inicio, e.duracao, type(e)) for _, _, e in ir.eventos()]


def _alturas(ir, parte=None):
    return [[a.midicents for a in e.alturas] for e in _notas(ir, parte)]


# --- melódicos ---------------------------------------------------------------------------------------------------------

def test_transposicao_preserva_o_ritmo_e_soma_o_intervalo(quarteto):
    t = _rodar("transpor", quarteto, intervalo=150)
    assert _ritmo(t) == _ritmo(quarteto)
    assert _alturas(t) == [[a + 150 for a in acorde] for acorde in _alturas(quarteto)]


def test_inversao_duas_vezes_volta_ao_original(quarteto):
    duas = _rodar("inverter", _rodar("inverter", quarteto, eixo=6000), eixo=6000)
    assert _alturas(duas) == _alturas(quarteto)
    assert _alturas(_rodar("inverter", quarteto, eixo=6000), "P1")[0][0] == 12000 - _alturas(quarteto, "P1")[0][0]


def test_inversao_sem_eixo_espelha_na_primeira_altura_de_cada_voz(quarteto):
    inv = _rodar("inverter", quarteto)
    for parte in ("P1", "P4"):
        primeira = _alturas(quarteto, parte)[0][0]
        assert [a for [a] in _alturas(inv, parte)] == [2 * primeira - a for [a] in _alturas(quarteto, parte)]


def test_retrogradacao_inverte_a_ordem_das_alturas_e_mantem_o_ritmo(quarteto):
    r = _rodar("retrogradar", quarteto)
    assert _ritmo(r) == _ritmo(quarteto)
    assert _alturas(r, "P2") == _alturas(quarteto, "P2")[::-1]


def test_rotacao_desloca_as_alturas(quarteto):
    original = _alturas(quarteto, "P3")
    assert _alturas(_rodar("rotacionar", quarteto, passos=2), "P3") == original[2:] + original[:2]


def test_compressao_intervalar_reduz_a_distancia_da_primeira_altura(quarteto):
    sel = Selecao(partes=["P4"])
    e = _rodar("intervalos", quarteto, selecao=sel, fator=0.5)
    base = _alturas(quarteto, "P4")[0][0]
    esperado = [base + round((a - base) / 2 / 50) * 50 for [a] in _alturas(quarteto, "P4")]  # em quartos de tom
    assert [a for [a] in _alturas(e, "P4")] == esperado
    assert _alturas(e, "P1") == _alturas(quarteto, "P1")


def test_operacao_melodica_respeita_a_selecao_de_compassos(quarteto):
    t = _rodar("transpor", quarteto, selecao=Selecao(compasso_inicial=3), intervalo=100)
    locais = localizar(quarteto)
    for antes, depois in zip(_notas(quarteto), _notas(t)):
        mudou = antes.alturas != depois.alturas
        assert mudou == (locais[antes.id][2] >= 3)


# --- rítmicos ----------------------------------------------------------------------------------------------------------

def _duracao_total(ir, parte):
    return sum((e.duracao for p, _, e in ir.eventos() if p.id == parte), F(0))


def test_aumentacao_dobra_as_duracoes_dentro_do_trecho(quarteto):
    a = _rodar("aumentacao_diminuicao", quarteto, selecao=Selecao(partes=["P1"]), expoente=1)
    assert _duracao_total(a, "P1") <= _duracao_total(quarteto, "P1")
    primeira_antes, primeira_depois = _notas(quarteto, "P1")[0], _notas(a, "P1")[0]
    if primeira_antes.duracao <= F(1, 4):  # sem corte na barra
        assert primeira_depois.duracao == 2 * primeira_antes.duracao
    assert _ritmo(a)[-1][1] < 4  # nada passa do fim


def test_diminuicao_repete_o_material_para_preencher_o_trecho(quarteto):
    d = _rodar("aumentacao_diminuicao", quarteto, selecao=Selecao(partes=["P2"]), expoente=-1)
    assert len(_notas(d, "P2")) == 2 * len(_notas(quarteto, "P2"))
    assert _alturas(d, "P2") == _alturas(quarteto, "P2") * 2


def test_subdivisao_cria_quialteras_com_a_mesma_altura(quarteto):
    s = _rodar("subdividir", quarteto, selecao=Selecao(partes=["P4"]), divisao=3, proporcao=1)
    [voz] = s.partes[3].vozes
    assert voz.quialteras and all(q.numerador == 3 and q.denominador == 2 for q in voz.quialteras)
    eventos = {e.id: e for e in voz.eventos}
    for q in voz.quialteras:
        membros = [eventos[i] for i in q.eventos]
        assert len({tuple(a.midicents for a in m.alturas) for m in membros}) == 1
        assert sum((m.duracao for m in membros), F(0)) in {F(1, 2), F(1, 4), F(1, 8), F(3, 8)}
    assert s.partes[0] == quarteto.partes[0]


def test_permutacao_mantem_as_duracoes_de_cada_compasso(quarteto):
    p = _rodar("permutar_duracoes", quarteto, semente=4)
    locais = localizar(quarteto)
    for parte in ("P1", "P4"):
        por_compasso = lambda ir: sorted((locais[e.id][2], e.duracao) for q, _, e in ir.eventos() if q.id == parte)
        assert por_compasso(p) == por_compasso(quarteto)


# --- densidade e progressões --------------------------------------------------------------------------------------------

def test_densidade_atinge_o_alvo_de_ataques_por_compasso(quarteto):
    d = _rodar("densidade", quarteto, inicial=0.5, final=2)
    locais = localizar(d)
    for parte in ("P1", "P2", "P3", "P4"):
        ataques = [sum(1 for e in _notas(d, parte) if locais[e.id][2] == n) for n in range(1, 5)]
        assert ataques == [2, 4, 6, 8]  # 0.5 → 2 ataques por semínima, interpolado, × 4 semínimas


def test_registro_progressivo_desloca_mais_no_fim(quarteto):
    r = _rodar("registro_progressivo", quarteto, selecao=Selecao(partes=["P1"]), inicial=0, final=1200)
    deslocamentos = [b[0] - a[0] for a, b in zip(_alturas(quarteto, "P1"), _alturas(r, "P1"))]
    assert deslocamentos[0] == 0 and deslocamentos == sorted(deslocamentos) and deslocamentos[-1] > 900


def test_dinamica_progressiva_marca_o_primeiro_ataque_de_cada_compasso(quarteto):
    d = _rodar("dinamica_progressiva", quarteto, inicial=2, final=8)
    marcadas = [e.dinamica for e in _notas(d, "P4") if e.dinamica]
    assert marcadas[0] == "pp" and marcadas[-1] == "fff" and len(marcadas) == 4


def test_distribuir_espalha_a_origem_respeitando_tessituras(quarteto):
    d = _rodar("distribuir", quarteto, parte_origem=1)
    origem = sorted(a for [a] in _alturas(quarteto, "P1"))
    recebidas = sorted(a for p in ("P1", "P2", "P3", "P4") for acorde in _alturas(d, p) for a in acorde)
    assert len(recebidas) == len(origem)
    assert [v for v in verificar(d, []) if v.restricao == "tessitura"] == []


# --- verificador e semente -----------------------------------------------------------------------------------------------

def test_verificador_aponta_a_violacao_exata(quarteto):
    abaixo = _rodar("transpor", quarteto, selecao=Selecao(partes=["P4"], compasso_inicial=2, compasso_final=2),
                    intervalo=-2400)
    [v, *_] = [v for v in verificar(abaixo, []) if v.restricao == "tessitura"]
    assert v.parte == "P4" and v.compasso == 2 and v.evento.startswith("P4V1E")
    assert verificar(quarteto, []) == []


def test_verificador_intervalo_proibido_unissono_e_densidade(quarteto):
    primeiro = _notas(quarteto, "P1")[:2]
    intervalo = abs(primeiro[1].alturas[0].midicents - primeiro[0].alturas[0].midicents)
    violacoes = verificar(quarteto, [Restricao(tipo="intervalo_proibido", valor=intervalo, partes=["P1"]),
                                     Restricao(tipo="densidade_maxima", valor=0.25)])
    tipos = {v.restricao for v in violacoes}
    assert "intervalo_proibido" in tipos and "densidade_maxima" in tipos
    p1 = quarteto.partes[0]
    copia = p1.vozes[0].model_copy(update={"id": "XV1", "eventos": [
        e.model_copy(update={"id": "X" + e.id}) for e in p1.vozes[0].eventos]})
    dobro = quarteto.model_copy(update={"partes": [*quarteto.partes, p1.model_copy(update={"id": "X", "vozes": [copia]})]})
    unissonos = verificar(dobro, [Restricao(tipo="sem_unissono", partes=["P1", "X"])])
    assert unissonos and all(v.parte == "X" and v.evento == "X" + v.detalhe.split(" com ")[1].split(" ")[0]
                             for v in unissonos)


@pytest.mark.parametrize("nome,params", [
    ("densidade", {"inicial": 3, "final": 1}), ("subdividir", {"proporcao": 0.5}), ("distribuir", {}),
    ("aumentacao_diminuicao", {"expoente": -2}), ("permutar_duracoes", {}),
])
def test_mesma_semente_mesmo_resultado(quarteto, nome, params):
    assert _rodar(nome, quarteto, semente=9, **params) == _rodar(nome, quarteto, semente=9, **params)


def test_todo_procedimento_preserva_ou_cria_pausas_validas(quarteto):
    for nome, proc in CATALOGO.items():
        if proc.requer_composicao:
            novo = _rodar(nome, quarteto)
            assert all(isinstance(e, (Nota, Pausa)) for _, _, e in novo.eventos())


def test_aplicar_tecnicas_marca_as_notas_da_selecao(quarteto):
    proc = CATALOGO["aplicar_tecnicas"]
    plano = Plano(objetivo="t", operacoes=[{"procedimento": "aplicar_tecnicas"}], tecnicas=["harmonico"],
                  selecao=Selecao(partes=["P2"]))
    novo = proc.funcao(quarteto, plano, {"proporcao": 1}, random.Random(1))
    assert all(e.tecnicas == ["harmonico"] for e in _notas(novo, "P2"))
    assert all(e.tecnicas == [] for e in _notas(novo, "P1"))
    with pytest.raises(ValueError):
        proc.funcao(quarteto, plano.model_copy(update={"tecnicas": []}), {"proporcao": 1}, random.Random(1))


def test_restricao_de_tessitura_pedida_usa_os_limites_dados(quarteto):
    violacoes = verificar(quarteto, [Restricao(tipo="tessitura", grave=6000, agudo=6100, partes=["P1"])])
    assert violacoes and {v.parte for v in violacoes} == {"P1"} and all("6000–6100" in v.detalhe for v in violacoes)
    with pytest.raises(ValueError):
        Restricao(tipo="tessitura", grave=7000, agudo=6000)


def test_aplicar_tecnicas_sorteia_uma_tecnica_por_nota(quarteto):
    plano = Plano(objetivo="t", operacoes=[{"procedimento": "aplicar_tecnicas"}],
                  tecnicas=["sul ponticello", "pizzicato", "col legno battuto"])
    novo = CATALOGO["aplicar_tecnicas"].funcao(quarteto, plano, {"proporcao": 1}, random.Random(3))
    usadas = [e.tecnicas for e in _notas(novo)]
    assert all(len(t) == 1 for t in usadas) and len({t[0] for t in usadas}) == 3

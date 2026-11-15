import math
import random
from fractions import Fraction as F

import pytest

from muschat.musicir import Nota, Pausa, gestos, localizar
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
        if proc.requer_composicao and proc.funcao and nome != "eventos_do_espectro":  # este pede dado espectral
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


# --- espectralizar (ticket 05 da composição musical) -------------------------------------------------------------------

def _sinais(seq):
    return [(b > a) - (b < a) for a, b in zip(seq, seq[1:])]


def _melodias(ir):
    return {(p.id, v.id): [e.alturas[0].midicents for e in v.eventos if isinstance(e, Nota)]
            for p in ir.partes for v in p.vozes}


def test_espectralizar_usa_so_parciais_preserva_contorno_e_limita_o_salto(quarteto):
    novo = _rodar("espectralizar", quarteto, fundamental=3600, parciais=16, microtom=1, salto_maximo=1200)
    parciais = {round((3600 + 1200 * math.log2(n)) / 50) * 50 for n in range(1, 17)}
    assert {a for alturas in _alturas(novo) for a in alturas} <= parciais
    antes, depois = _melodias(quarteto), _melodias(novo)
    iguais = invertidos = 0
    for chave, seq in depois.items():
        for s0, s1 in zip(_sinais(antes[chave]), _sinais(seq)):
            iguais += s0 == s1
            invertidos += s0 * s1 < 0  # subir onde descia: nunca
        assert all(abs(b - a) <= 1200 for a, b in zip(seq, seq[1:]))
    assert invertidos == 0 and iguais >= 0.8 * sum(len(s) - 1 for s in depois.values())
    for p in novo.partes:
        assert all(p.tessitura[0] <= a <= p.tessitura[1] for alturas in _alturas(novo, p.id) for a in alturas)
    assert _ritmo(novo) == _ritmo(quarteto)


def test_espectralizar_planeja_registro_do_agudo_ao_grave_e_respeita_a_selecao(quarteto):
    novo = _rodar("espectralizar", quarteto)
    media = {p.id: sum(a for al in _alturas(novo, p.id) for a in al) / len(_alturas(novo, p.id)) for p in novo.partes}
    assert media["P1"] > media["P3"] > media["P4"] and media["P2"] > media["P4"]
    so_p2 = _rodar("espectralizar", quarteto, selecao=Selecao(partes=["P2"]))
    assert _alturas(so_p2, "P1") == _alturas(quarteto, "P1") and _alturas(so_p2, "P2") != _alturas(quarteto, "P2")


# --- técnica idiomática por gesto (ticket 06) --------------------------------------------------------------------------

def _por_gesto(ir, tecnicas, selecao=None, semente=1, **params):
    plano = Plano(objetivo="t", operacoes=[{"procedimento": "tecnicas_por_gesto"}], tecnicas=tecnicas,
                  selecao=selecao or Selecao())
    completos = {k: params.get(k, f.padrao) for k, f in CATALOGO["tecnicas_por_gesto"].parametros.items()}
    novo = CATALOGO["tecnicas_por_gesto"].funcao(ir, plano, completos, random.Random(semente))
    return novo, para_musicxml(novo)


def test_tecnica_vale_para_o_gesto_inteiro_e_so_onde_se_toca(quarteto):
    novo, xml = _por_gesto(quarteto, ["sul ponticello", "harmonico"], proporcao=1)
    com_tecnica = 0
    for p in novo.partes:
        for gesto in gestos(p.vozes[0].eventos):
            tecnicas = {tuple(n.tecnicas) for n in gesto}
            assert len(tecnicas) == 1  # o gesto inteiro com a mesma técnica (ou nenhuma)
            com_tecnica += tecnicas != {()}
    assert com_tecnica and xml.count(">sul pont.<") <= com_tecnica
    from muschat.exportacao import metricas
    assert metricas(novo)["tecnicas_intocaveis"] == []
    assert _por_gesto(quarteto, ["sul ponticello"], semente=4)[0] == _por_gesto(quarteto, ["sul ponticello"],
                                                                                   semente=4)[0]
    so_p3 = _por_gesto(quarteto, ["sul ponticello"], selecao=Selecao(partes=["P3"]), proporcao=1)[0]
    assert all(n.tecnicas == [] for n in _notas(so_p3, "P1")) and any(n.tecnicas for n in _notas(so_p3, "P3"))


def test_rotulo_no_comeco_do_gesto_e_ord_so_na_volta(quarteto):
    novo, xml = _por_gesto(quarteto, ["sul ponticello"], proporcao=1, semente=2)
    marcados = [g for p in novo.partes for g in gestos(p.vozes[0].eventos) if g[0].tecnicas]
    assert xml.count(">sul pont.<") <= len(marcados)  # um rótulo por trecho, não por nota
    assert xml.count(">sul pont.<") < sum(len(g) for g in marcados)


def test_tecnica_que_nao_se_toca_em_nenhum_gesto_e_recusada(quarteto):
    with pytest.raises(ValueError, match="nenhum gesto"):
        _por_gesto(quarteto, ["whistle tone"])  # só flautas


def test_espectralizar_so_exige_parciais_nas_partes_selecionadas():
    duo = _rodar("serie_harmonica", instrumentos=["piccolo", "violoncelo"], compassos=2)
    novo = _rodar("espectralizar", duo, selecao=Selecao(partes=["P2"]), fundamental=3600, parciais=4)
    assert _alturas(novo, "P1") == _alturas(duo, "P1")
    with pytest.raises(ValueError, match="Piccolo"):  # o piccolo não alcança Dó 2..Dó 4
        _rodar("espectralizar", duo, fundamental=3600, parciais=4)


def test_tecnicas_por_gesto_mantem_as_articulacoes(quarteto):
    marcado = quarteto.model_copy(deep=True)
    for _, _, e in marcado.eventos():
        if isinstance(e, Nota):
            e.tecnicas = ["staccato"]
    novo, _ = _por_gesto(marcado, ["sul ponticello"], proporcao=1)
    assert all("staccato" in n.tecnicas for n in _notas(novo))
    assert any("sul ponticello" in n.tecnicas for n in _notas(novo))

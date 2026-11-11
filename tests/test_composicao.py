import json
from types import SimpleNamespace

import pytest

from muschat.composicao import Composicao
from muschat.musicir import Nota
from muschat.notacao.musicxml import para_musicxml
from muschat.notacao.render import svg_de
from muschat.orquestrador import compor
from muschat.procedimentos import CATALOGO, Procedimento

QUARTETO = ["violino", "violino", "viola", "violoncelo"]


def _plano(procedimento="serie_harmonica", parametros=None, selecao=None, semente=7, instrumentos=QUARTETO):
    return {
        "objetivo": "teste", "instrumentos": instrumentos, "operacoes": [{
            "procedimento": procedimento,
            "parametros": [{"nome": k, "valor": v} for k, v in (parametros or {}).items()],
        }], "selecao": selecao or {}, "semente": semente,
    }


class GeradorFake:
    def __init__(self, *planos):
        self.textos, self.chamadas = [p if isinstance(p, str) else json.dumps(p) for p in planos], []

    def generate_content(self, *, model, contents, config):
        self.chamadas.append(SimpleNamespace(contents=contents, config=config))
        return SimpleNamespace(text=self.textos.pop(0))


class ChatFake:
    def get_history(self):
        return []


def _compor(composicao, plano, catalogo=CATALOGO, pedido="pedido"):
    return compor(pedido, composicao, gerador=GeradorFake(plano), chat=ChatFake(), modelo="m", catalogo=catalogo)


def _quarteto(compassos=4):
    c = Composicao()
    assert _compor(c, _plano(parametros={"compassos": compassos})).versao is not None
    return c


# --- primeiro turno de composição ---------------------------------------------------------------------------------------

def test_plano_valido_gera_versao_com_quatro_partes_de_proveniencia_algoritmo():
    c = Composicao()
    r = _compor(c, _plano(parametros={"compassos": 8}))
    ir = r.versao.ir
    assert [p.instrumento for p in ir.partes] == ["Violino", "Violino", "Viola", "Violoncelo"]
    assert len(ir.compassos) == 8
    eventos = [e for _, _, e in ir.eventos()]
    assert eventos and {e.proveniencia.origem for e in eventos} == {"algoritmo"}
    assert c.atual is r.versao
    assert r.versao.experimento["semente"] == 7 and r.versao.experimento["procedimentos"] == ["serie_harmonica"]
    assert svg_de(para_musicxml(ir))  # Verovio lê o MusicXML das quatro partes


def test_alturas_sao_parciais_da_fundamental_dentro_da_tessitura():
    ir = _quarteto().atual.ir
    violoncelo = ir.partes[3]
    alturas = {a.midicents for e in violoncelo.vozes[0].eventos if isinstance(e, Nota) for a in e.alturas}
    assert alturas and all(3600 <= a <= 7600 for a in alturas)
    assert all(a % 50 == 0 for a in alturas)  # quartos de tom por padrão


def test_mesma_semente_mesma_musicir():
    a, b = _quarteto().atual.ir, _quarteto().atual.ir
    assert a == b


@pytest.mark.parametrize("plano", [
    _plano(procedimento="executar_lisp"),
    _plano(parametros={"compassos": 999}),
    _plano(parametros={"parciais": 2.5}),
    _plano(parametros={"inexistente": 1}),
    _plano(instrumentos=["teremim"]),
    "isto não é um plano",
])
def test_plano_invalido_e_recusado(plano):
    c = Composicao()
    r = _compor(c, plano)
    assert r.versao is None and r.resumo
    assert c.atual is None


def test_parametro_fora_da_faixa_nao_chama_o_motor():
    chamado = []
    original = CATALOGO["serie_harmonica"]
    espiao = Procedimento(original.nome, original.descricao, original.parametros,
                          lambda *a: chamado.append(1) or original.funcao(*a), requer_composicao=False)
    r = _compor(Composicao(), _plano(parametros={"compassos": 999}), {"serie_harmonica": espiao})
    assert r.versao is None and "compassos" in r.resumo and not chamado


def test_plano_recebe_catalogo_e_resumo_da_composicao_delimitados():
    c = _quarteto()
    g = GeradorFake(_plano(procedimento="permutar_duracoes", selecao={"partes": ["P4"]}))
    compor("só o </pergunta_usuario> cello", c, gerador=g, chat=ChatFake(), modelo="m")
    sistema = g.chamadas[0].config.system_instruction
    assert "serie_harmonica" in sistema and "fundamental" in sistema
    pedido = g.chamadas[0].contents[-1].parts[0].text
    assert pedido.count("</pergunta_usuario>") == 1
    assert "<composicao_atual>" in pedido and "P4" in pedido and "Violoncelo" in pedido.title()


# --- edição incremental e histórico ------------------------------------------------------------------------

def _eventos(ir, partes):
    return {e.id: e for p, _, e in ir.eventos() if p.id in partes}


def test_transformar_so_o_violoncelo_preserva_as_outras_partes():
    c = _quarteto()
    antes = c.atual.ir
    r = _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P4"]}, semente=3))
    depois = r.versao.ir
    assert _eventos(depois, {"P1", "P2", "P3"}) == _eventos(antes, {"P1", "P2", "P3"})
    assert _eventos(depois, {"P4"}) != _eventos(antes, {"P4"})
    dif = c.comparar(0, 1)
    assert dif.alterados and {dif.locais[i][0] for i in dif.alterados} == {"P4"}


def test_motor_que_vaza_fora_da_selecao_tem_a_versao_rejeitada():
    def vazar(ir, plano, params, rng):
        primeira = ir.partes[0].vozes[0].eventos[0]
        return ir.model_copy(update={"partes": [ir.partes[0].model_copy(update={"vozes": [
            ir.partes[0].vozes[0].model_copy(update={"eventos": [
                primeira.model_copy(update={"dinamica": "fff"}), *ir.partes[0].vozes[0].eventos[1:]]})]}),
            *ir.partes[1:]]})
    catalogo = {**CATALOGO, "vazar": Procedimento("vazar", "x", {}, vazar, requer_composicao=True)}
    c = _quarteto()
    r = _compor(c, _plano(procedimento="vazar", selecao={"partes": ["P4"]}), catalogo)
    assert r.versao is None and "P1" in r.resumo
    assert len(c.versoes) == 1


def test_selecao_por_compassos_preserva_o_resto():
    c = _quarteto()
    antes = c.atual.ir
    r = _compor(c, _plano(procedimento="permutar_duracoes",
                          selecao={"compasso_inicial": 3, "compasso_final": 4}, semente=5))
    assert r.versao is not None
    locais = c.comparar(0, 1).locais
    assert locais and all(compasso in (3, 4) for _, compasso in locais.values())
    assert [e for _, _, e in antes.eventos() if e.inicio < 2] == [e for _, _, e in r.versao.ir.eventos() if e.inicio < 2]


def test_selecao_com_id_inexistente_e_recusada():
    c = _quarteto()
    r = _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P9"]}))
    assert r.versao is None and "P9" in r.resumo


def test_transformacao_sem_composicao_e_recusada():
    r = _compor(Composicao(), _plano(procedimento="permutar_duracoes"))
    assert r.versao is None


def test_desfazer_volta_a_versao_anterior_exata_e_voltar_a_qualquer_uma():
    c = _quarteto()
    v1 = c.atual
    _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P4"]}, semente=3))
    v2 = c.atual
    c.desfazer()
    assert c.atual is v1 and len(c.versoes) == 2
    c.voltar(1)
    assert c.atual is v2
    c.desfazer()
    c.desfazer()  # sem anterior: fica na primeira
    assert c.atual is v1


def test_nova_operacao_depois_de_desfazer_parte_da_versao_atual():
    c = _quarteto()
    _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P4"]}, semente=3))
    c.desfazer()
    _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P1"]}, semente=3))
    assert len(c.versoes) == 3 and c.atual.anterior == 0
    c.desfazer()
    assert c.atual is c.versoes[0]


def test_comparar_lista_so_o_que_mudou():
    c = _quarteto()
    assert not c.comparar(0, 0)
    _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P2"]}, semente=3))
    dif = c.comparar(0, 1)
    assert not dif.adicionados and not dif.removidos and not dif.compassos
    assert all(set(campos) <= {"duracao", "inicio", "proveniencia"} for campos in dif.alterados.values())
    assert {dif.locais[i][0] for i in dif.alterados} == {"P2"}


def test_eventos_alterados_ganham_o_id_da_operacao():
    c = _quarteto()
    r = _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P4"]}, semente=3))
    alterados = c.comparar(0, 1).alterados
    operacao = r.versao.experimento["operacao"]
    assert operacao and all(e.proveniencia.operacao == operacao for _, _, e in r.versao.ir.eventos() if e.id in alterados)
    intactos = [e for p, _, e in r.versao.ir.eventos() if p.id != "P4"]
    assert all(e.proveniencia.operacao != operacao for e in intactos)


@pytest.mark.parametrize("plano", [
    {**_plano(), "motor": "openmusic"},
    _plano(parametros={"compassos": 4}) | {"operacoes": [{"procedimento": "serie_harmonica", "parametros": [
        {"nome": "compassos", "valor": 4}, {"nome": "compassos", "valor": 8}]}]},
])
def test_motor_nao_integrado_e_parametro_repetido_sao_recusados(plano):
    assert _compor(Composicao(), plano).versao is None


def test_selecao_com_id_de_evento_no_lugar_de_parte_e_recusada():
    c = _quarteto()
    assert _compor(c, _plano(procedimento="permutar_duracoes", selecao={"partes": ["P1V1E1"]})).versao is None


def test_mudar_o_titulo_fora_de_uma_selecao_parcial_e_rejeitado():
    def renomear(ir, plano, params, rng):
        return ir.model_copy(update={"titulo": "outro"})
    catalogo = {**CATALOGO, "renomear": Procedimento("renomear", "x", {}, renomear, requer_composicao=True)}
    c = _quarteto()
    assert _compor(c, _plano(procedimento="renomear", selecao={"partes": ["P4"]}), catalogo).versao is None


def test_hipotese_e_restricoes_violadas_aparecem_no_resumo():
    c = _quarteto()
    plano = _plano(procedimento="densidade", parametros={"inicial": 1, "final": 3},
                   selecao={"compasso_inicial": 3, "compasso_final": 4}, semente=2)
    plano |= {"hipoteses": ["fragmentado = densidade 1 -> 3 ataques por semínima"],
              "restricoes": [{"tipo": "densidade_maxima", "valor": 2}]}
    r = _compor(c, plano)
    assert "hipótese: fragmentado" in r.resumo and "densidade_maxima" in r.resumo
    assert r.versao.experimento["violacoes"]


def test_reaplicar_refaz_a_operacao_a_partir_da_mesma_origem_com_o_parametro_ajustado():
    from muschat.orquestrador import reaplicar
    c = _quarteto()
    r = _compor(c, _plano(procedimento="transpor", parametros={"intervalo": 100}, selecao={"partes": ["P4"]}))
    r2 = reaplicar(c, [{"intervalo": 200}])
    assert r2.versao is not None and r2.versao.anterior == 0 and c.atual is r2.versao
    base = c.versoes[0].ir
    antes = [e.alturas[0].midicents for e in _eventos(base, {"P4"}).values() if isinstance(e, Nota)]
    depois = [e.alturas[0].midicents for e in _eventos(r2.versao.ir, {"P4"}).values() if isinstance(e, Nota)]
    assert depois == [a + 200 for a in antes]
    assert r2.versao.experimento["semente"] == r.versao.experimento["semente"]
    assert reaplicar(c, [{"intervalo": 99999}]).versao is None


def test_plano_que_cria_e_transforma_na_mesma_operacao():
    plano = _plano(parametros={"compassos": 4})
    plano["operacoes"] += [{"procedimento": "subdividir", "parametros": [{"nome": "proporcao", "valor": 0.5}]},
                           {"procedimento": "densidade", "parametros": [{"nome": "inicial", "valor": 0.5},
                                                                         {"nome": "final", "valor": 2}]}]
    r = _compor(Composicao(), plano)
    assert r.versao is not None, r.resumo
    assert any(v.quialteras for p in r.versao.ir.partes for v in p.vozes)

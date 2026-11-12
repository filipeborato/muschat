import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from muschat.composicao import Composicao
from muschat.notacao.modelo import Partitura
from muschat.plano import Plano
from muschat.reasoning import Trecho, responder
from tests.test_reasoning import ANALISE_OK, FakeChat, FakeRetriever

PARTITURA = {
    "titulo": "Alternância",
    "instrumento": "Violino",
    "clave": "sol",
    "metrica": "4/4",
    "compassos": [{"notas": [
        {"alturas": ["D5"], "duracao": "minima", "tecnicas": ["sul ponticello"]},
        {"alturas": ["D5"], "duracao": "minima", "tecnicas": ["col legno battuto"]},
    ]}],
}
XML_ACERVO = "<score-partwise><part-list/></score-partwise>"


class GeradorSequencial:
    """Devolve um texto por chamada, na ordem."""

    def __init__(self, log, *textos):
        self.log, self.textos, self.chamadas = log, list(textos), []

    def generate_content(self, *, model, contents, config):
        self.log.append("gerador")
        self.chamadas.append(SimpleNamespace(contents=contents, config=config))
        return SimpleNamespace(text=self.textos.pop(0))


def _rodar(notacao, *extras, trechos=()):
    log = []
    analise = json.dumps({**ANALISE_OK, "notacao": notacao})
    g = GeradorSequencial(log, analise, *extras)
    c, r = FakeChat(log), FakeRetriever(log, trechos)
    resp = responder("pergunta", gerador=g, chat=c, retriever=r, modelo="m")
    return resp, g, c


def _sistema(c):
    return c.mensagens[0].config.system_instruction


def test_sem_notacao_nao_ha_chamada_extra_nem_partitura():
    resp, g, c = _rodar("nenhuma")
    assert len(g.chamadas) == 1 and resp.partituras == []


def test_analise_antiga_sem_campo_notacao_continua_valida():
    log = []
    g = GeradorSequencial(log, json.dumps(ANALISE_OK))
    resp = responder("p", gerador=g, chat=FakeChat(log), retriever=FakeRetriever(log), modelo="m")
    assert resp.fora_do_escopo is False and resp.partituras == []


def test_acervo_usa_trechos_reais_sem_gerar():
    trechos = [
        Trecho(texto="t1", fonte="Quarteto", local="m.7–9", compositor="Filipe Borato", musicxml=XML_ACERVO),
        Trecho(texto="t2", fonte="Grimório", local="Sul pont."),  # sem partitura
        Trecho(texto="t3", fonte="Snippets", local="2. col legno batt.", musicxml=XML_ACERVO),
        Trecho(texto="t4", fonte="Snippets", local="3. c.l. tratto", musicxml=XML_ACERVO),
    ]
    resp, g, c = _rodar("acervo", trechos=trechos)
    assert len(g.chamadas) == 1
    assert [p.origem for p in resp.partituras] == ["acervo", "acervo"]  # no máximo 2
    assert resp.partituras[0].legenda == "Do acervo: Quarteto, m.7–9 — Filipe Borato"
    assert "Do acervo: Quarteto, m.7–9 — Filipe Borato" in _sistema(c)


def test_acervo_sem_partitura_nos_trechos_gera_uma():
    resp, g, c = _rodar("acervo", json.dumps(PARTITURA))
    assert len(g.chamadas) == 2
    assert [p.origem for p in resp.partituras] == ["gerado"]


def test_gerar_faz_uma_chamada_com_schema_e_devolve_musicxml():
    resp, g, c = _rodar("gerar", json.dumps(PARTITURA))
    chamada = g.chamadas[1]
    assert chamada.config.response_schema is Partitura
    assert chamada.config.response_mime_type == "application/json"
    [p] = resp.partituras
    assert p.origem == "gerado" and p.titulo == "Alternância"
    assert p.legenda.startswith("Exemplo ilustrativo gerado")
    assert ET.fromstring(p.musicxml).findtext(".//part-name") == "Violino"
    assert "Exemplo ilustrativo gerado" in _sistema(c)


def test_ordem_analise_busca_notacao_sintese():
    log = []
    g = GeradorSequencial(log, json.dumps({**ANALISE_OK, "notacao": "gerar"}), json.dumps(PARTITURA))
    responder("p", gerador=g, chat=FakeChat(log), retriever=FakeRetriever(log), modelo="m")
    assert log == ["gerador", "busca", "gerador", "sintese"]


def test_partitura_invalida_nao_quebra_o_turno():
    resp, g, c = _rodar("gerar", "{isto não é partitura")
    assert resp.partituras == [] and resp.final == "resposta final"


def test_trecho_leva_o_autor_no_contexto():
    trecho = Trecho(texto="x", fonte="Estudo II", local="m.1–3", compositor="Filipe Borato")
    resp, g, c = _rodar("nenhuma", trechos=[trecho])
    assert '<trecho fonte="Estudo II" local="m.1–3" autor="Filipe Borato">' in _sistema(c)


def test_acervo_escolhe_as_partituras_mais_relevantes():
    trechos = [
        Trecho(texto="a", fonte="Quarteto", local="m.3–5", score=0.1, musicxml=XML_ACERVO),
        Trecho(texto="b", fonte="Quarteto", local="m.7–9", score=0.9, musicxml=XML_ACERVO),
    ]
    resp, g, c = _rodar("acervo", trechos=trechos)
    assert [p.titulo for p in resp.partituras] == ["Quarteto — m.7–9", "Quarteto — m.3–5"]


def test_falha_da_api_na_geracao_nao_derruba_o_turno():
    from google.genai import errors

    class GeradorQueFalhaNaNotacao(GeradorSequencial):
        def generate_content(self, *, model, contents, config):
            if self.chamadas:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "instável"}})
            return super().generate_content(model=model, contents=contents, config=config)

    log = []
    g = GeradorQueFalhaNaNotacao(log, json.dumps({**ANALISE_OK, "notacao": "gerar"}))
    resp = responder("p", gerador=g, chat=FakeChat(log), retriever=FakeRetriever(log), modelo="m")
    assert resp.partituras == [] and resp.final == "resposta final"


def test_partitura_gerada_e_limitada_a_8_compassos():
    longa = {**PARTITURA, "compassos": PARTITURA["compassos"] * 12}
    resp, g, c = _rodar("gerar", json.dumps(longa))
    assert len(list(ET.fromstring(resp.partituras[0].musicxml).iter("measure"))) == 8


PLANO = {"objetivo": "quarteto", "instrumentos": ["violino", "violino", "viola", "violoncelo"],
         "operacoes": [{"procedimento": "serie_harmonica", "parametros": [{"nome": "compassos", "valor": 8}]}],
         "semente": 1}


def _compor(analise_notacao, *extras, composicao=None):
    log = []
    g = GeradorSequencial(log, json.dumps({**ANALISE_OK, "notacao": analise_notacao}), *extras)
    c = FakeChat(log)
    resp = responder("crie oito compassos", gerador=g, chat=c, retriever=FakeRetriever(log), modelo="m",
                     composicao=composicao)
    return resp, g, c


def test_pedido_de_composicao_grava_versao_e_mostra_a_partitura_com_o_resumo():
    composicao = Composicao()
    resp, g, c = _compor("compor", json.dumps(PLANO), composicao=composicao)
    assert len(g.chamadas) == 2 and g.chamadas[1].config.response_schema is Plano
    assert composicao.atual is not None and len(composicao.atual.ir.partes) == 4
    [partitura] = resp.partituras
    assert partitura.origem == "composicao" and partitura.musicxml.count("<score-part ") == 4
    assert "serie_harmonica" in _sistema(c) and composicao.atual.resumo in _sistema(c)


def test_plano_recusado_chega_a_sintese_sem_partitura():
    composicao = Composicao()
    resp, g, c = _compor("compor", json.dumps({**PLANO, "operacoes": [{"procedimento": "rm -rf"}]}),
                         composicao=composicao)
    assert resp.partituras == [] and composicao.atual is None
    assert "recusada" in _sistema(c)


def test_pergunta_comum_nao_passa_pelo_orquestrador():
    composicao = Composicao()
    resp, g, c = _compor("nenhuma", composicao=composicao)
    assert len(g.chamadas) == 1 and composicao.versoes == []
    assert "<operacao>\nnenhuma\n</operacao>" in _sistema(c)

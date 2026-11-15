from fractions import Fraction as F
from pathlib import Path
import random

import pytest

from muschat import musicir as ir
from muschat.composicao import Composicao
from muschat.instrumentos import instrumento
from muschat.notacao.lilypond import para_lilypond
from muschat.notacao.musicxml import para_musicxml, de_musicxml
from muschat.notacao.tecnicas import tecnicas_intocaveis
from muschat.orquestrador import compor, reaplicar
from muschat.plano import Plano, Selecao
from muschat.procedimentos import CATALOGO
from muschat.receitas import RECEITAS, documentacao
from tests.test_composicao import GeradorFake, ChatFake


def material():
    partes = []
    for k, (nome, altura) in enumerate((("flauta", 7600), ("violino", 6900), ("violoncelo", 4800)), 1):
        inst = instrumento(nome)
        eventos = [ir.Nota(id=f"P{k}E{n}", inicio=F(n, 8), duracao=F(1, 8),
                          alturas=[ir.Altura(midicents=altura + (n % 3) * 100)], dinamica="mf",
                          proveniencia=ir.Proveniencia(origem="compositor")) for n in range(16)]
        eventos[7] = ir.Pausa(id=f"P{k}E7", inicio=F(7, 8), duracao=F(1, 8),
                             proveniencia=ir.Proveniencia(origem="compositor"))
        partes.append(ir.Parte(id=f"P{k}", instrumento=nome, tessitura=inst.tessitura, clave=inst.clave,
                               vozes=[ir.Voz(id=f"P{k}V", eventos=eventos)]))
    return ir.MusicIR(titulo="Referência", compassos=[ir.Compasso(numerador=4, denominador=4)] * 2, partes=partes)


def rodar(nome, atual=None, selecao=None, tecnicas=(), **params):
    atual = atual or material()
    proc = CATALOGO[nome]
    plano = Plano(objetivo="t", operacoes=[{"procedimento": nome}], selecao=selecao or Selecao(), tecnicas=list(tecnicas))
    completos = {n: f.padrao for n, f in proc.parametros.items()} | params
    nova = proc.funcao(atual, plano, completos, random.Random(17))
    nova = ir.MusicIR.model_validate(nova.model_dump())
    xml = para_musicxml(nova)
    assert de_musicxml(xml.encode())[0]
    assert para_lilypond(nova)
    return nova


def notas(peca, parte=0):
    return [e for e in peca.partes[parte].vozes[0].eventos if isinstance(e, ir.Nota)]


@pytest.mark.parametrize("nome", ["desafinar", "substituir_timbre", "rarefazer", "micropolifonia", "deformar_ritmo"])
def test_procedimentos_notaveis_deterministicos_e_preservam_fora_da_selecao(nome):
    antes = material()
    sel = Selecao(partes=["P2"], compasso_inicial=2, compasso_final=2)
    a = rodar(nome, antes, sel)
    assert a == rodar(nome, antes, sel)
    assert a.partes[0] == antes.partes[0] and a.partes[2] == antes.partes[2]
    assert [e for e in a.partes[1].vozes[0].eventos if e.inicio < 1] == antes.partes[1].vozes[0].eventos[:8]
    c = Composicao()
    c.adicionar(antes, "aberta", {}, anterior=None)
    plano = {"objetivo": "t", "operacoes": [{"procedimento": nome}], "selecao": sel.model_dump(), "semente": 17}
    r = compor("transforme", c, gerador=GeradorFake(plano), chat=ChatFake(), modelo="m")
    assert r.versao is not None, r.resumo


def test_desafinar_curvas_e_glissandos_sem_cruzar_pausas():
    antes = material()
    nova = rodar("desafinar", antes, desvio=100, resolucao=2, curva=0, glissandos=1)
    ns, orig = notas(nova), notas(antes)
    desvios = [b.alturas[0].midicents - a.alturas[0].midicents for a, b in zip(orig, ns)]
    assert desvios[0] == 0 and desvios[-1] == 100 and desvios == sorted(desvios)
    assert 25 in desvios and all(d % 25 == 0 for d in desvios)
    assert not ns[6].glissando and not ns[-1].glissando and ns[0].glissando
    assert [(n.inicio, n.duracao, n.dinamica, n.tecnicas) for n in ns] == [
        (n.inicio, n.duracao, n.dinamica, n.tecnicas) for n in orig]
    oscila = rodar("desafinar", antes, curva=1, resolucao=2)
    ds = [b.alturas[0].midicents - a.alturas[0].midicents for a, b in zip(orig, notas(oscila))]
    assert min(ds) < 0 < max(ds)
    assert "glissando" in para_musicxml(nova) and "glissando" in para_lilypond(nova)["grade"]


def test_ruido_idiomatico_preserva_ritmo_dinamicas_e_filtra_familia():
    antes = material()
    nova = rodar("substituir_timbre", antes, inicial=1, final=1, familia_ruido=1)
    assert notas(nova, 0) == notas(antes, 0)
    assert all(n.tecnicas for n in notas(nova, 1))
    assert not tecnicas_intocaveis(nova)
    assert [(e.id, e.inicio, e.duracao, getattr(e, "dinamica", None)) for _, _, e in nova.eventos()] == [
        (e.id, e.inicio, e.duracao, getattr(e, "dinamica", None)) for _, _, e in antes.eventos()]
    assert '<notehead>x</notehead>' in para_musicxml(nova)


def test_rarefazer_remove_ataques_prolonga_e_produz_harmonicos_tocaveis():
    antes = material()
    nova = rodar("rarefazer", antes, manter=0.3, harmonicos=1, acentos=0)
    assert len(notas(nova, 1)) < len(notas(antes, 1))
    assert any(n.duracao > F(1, 8) for n in notas(nova, 1))
    assert all(n.dinamica in (None, "pppp") for n in notas(nova, 1))
    assert all("harmonico" in n.tecnicas for n in notas(nova, 1))
    assert not tecnicas_intocaveis(nova)
    assert all(not notas(rodar("rarefazer", antes, manter=0), k) for k in range(3))


def test_micropolifonia_defasa_partes_sem_sair_da_tessitura():
    nova = rodar("micropolifonia", variacao=0, defasagem=1, espalhamento=100)
    assert [notas(nova, k)[0].inicio for k in range(3)] == [F(0), F(1, 16), F(1, 8)]
    for parte in nova.partes:
        assert all(parte.tessitura[0] <= a.midicents <= parte.tessitura[1]
                   for e in parte.vozes[0].eventos if isinstance(e, ir.Nota) for a in e.alturas)


@pytest.mark.parametrize("mascara,razao", [(1, (5, 4)), (2, (7, 4)), (4, (6, 4)), (8, (5, 6))])
def test_deformacao_preserva_notas_e_escreve_razoes(mascara, razao):
    antes = material()
    nova = rodar("deformar_ritmo", antes, proporcao=1, quialteras=mascara)
    assert [(n.id, n.alturas, n.dinamica, n.tecnicas) for n in notas(nova)] == [
        (n.id, n.alturas, n.dinamica, n.tecnicas) for n in notas(antes)]
    assert {(q.numerador, q.denominador) for p in nova.partes for v in p.vozes for q in v.quialteras} == {razao}


@pytest.mark.parametrize("receita", list(RECEITAS))
def test_receitas_expandem_persistem_e_reaplicam(receita):
    c = Composicao()
    plano = {"objetivo": "receita", "receita": receita, "motivo_receita": "transformar material", "semente": 17,
             "operacoes": [{"procedimento": "serie_harmonica", "parametros": [{"nome": "compassos", "valor": 2}]}]}
    r = compor("componha", c, gerador=GeradorFake(plano), chat=ChatFake(), modelo="m")
    assert r.versao is not None, r.resumo
    assert r.versao.experimento["plano"]["receita"] == receita
    assert receita in r.resumo
    assert para_lilypond(r.versao.ir)
    novamente = reaplicar(c, r.versao.experimento["parametros"])
    assert novamente.versao is not None, novamente.resumo
    # O carimbo da operação muda de versão; a música deve ser idêntica.
    assert para_musicxml(novamente.versao.ir) == para_musicxml(r.versao.ir)


def test_documentacao_das_receitas_vem_da_tabela():
    assert Path("docs/receitas.md").read_text(encoding="utf-8") == documentacao()


def test_desafinar_preserva_sustentacao_e_ligaduras_na_fronteira():
    antes = material()
    voz = antes.partes[1].vozes[0]
    voz.eventos[1].alturas = voz.eventos[0].alturas
    voz.eventos[0].ligada = True
    nova = rodar("desafinar", antes, desvio=200, resolucao=2)
    assert ir.ligam(*nova.partes[1].vozes[0].eventos[:2])
    voz.eventos[7] = voz.eventos[6].model_copy(update={"id": "P2E7", "inicio": F(7, 8), "ligada": True})
    voz.eventos[8].alturas = voz.eventos[7].alturas
    nova = rodar("desafinar", antes, Selecao(compasso_inicial=2), desvio=200, curva=1)
    assert ir.ligam(*nova.partes[1].vozes[0].eventos[7:9])


def test_canon_preenche_destino_vazio_importado_dentro_da_tessitura():
    antes = material()
    antes.partes[2].tessitura = None
    antes.partes[2].vozes[0].eventos = []
    nova = rodar("micropolifonia", antes, variacao=0)
    assert notas(nova, 2)
    grave, agudo = instrumento("violoncelo").tessitura
    assert all(grave <= a.midicents <= agudo for n in notas(nova, 2) for a in n.alturas)


def test_canon_recusa_evento_que_desapareceria_na_quantizacao():
    antes = material()
    voz = antes.partes[0].vozes[0]
    voz.eventos[0].duracao = F(1, 192)
    voz.quialteras = [ir.Quialtera(id="curta", numerador=3, denominador=1, eventos=[voz.eventos[0].id])]
    with pytest.raises(ValueError, match="curto demais"):
        rodar("micropolifonia", antes, variacao=0)


def test_desvio_arredondado_nao_ultrapassa_maximo():
    antes = material()
    nova = rodar("desafinar", antes, desvio=75, resolucao=1)
    assert all(abs(a.alturas[0].midicents - b.alturas[0].midicents) <= 75
               for a, b in zip(notas(antes), notas(nova)))


def test_resposta_recebe_instrumentos_notas_e_tecnicas_reais():
    from muschat.reasoning import responder
    from tests.test_reasoning import FakeChat, FakeRetriever, ANALISE_OK
    plano = {"objetivo": "t", "instrumentos": ["violino"], "semente": 17,
             "operacoes": [{"procedimento": "serie_harmonica"}]}
    gerador = GeradorFake({**ANALISE_OK, "notacao": "compor"}, plano)
    chat = FakeChat([])
    resposta = responder("componha", gerador=gerador, chat=chat, retriever=FakeRetriever([]), modelo="m")
    assert resposta.partituras
    contexto = chat.mensagens[0].config.system_instruction
    assert "Inventário da partitura efetivamente gerada" in contexto
    assert "P1: Violino" in contexto and "técnicas efetivas: nenhuma" in contexto


def test_deformacao_alonga_gesto_usando_respiracao_disponivel():
    antes = material()
    voz = antes.partes[0].vozes[0]
    voz.eventos = [voz.eventos[0], ir.Pausa(id="resp", inicio=F(1, 8), duracao=F(7, 8), proveniencia=voz.eventos[0].proveniencia),
                   *voz.eventos[8:]]
    nova = rodar("deformar_ritmo", antes, proporcao=1, minimo=2, maximo=2, quialteras=8)
    assert notas(nova)[0].duracao > F(1, 8)


def test_rarefacao_absorve_notas_dentro_da_quialtera():
    antes = material()
    voz = antes.partes[1].vozes[0]
    voz.eventos = [voz.eventos[k].model_copy(update={"inicio": F(k, 12), "duracao": F(1, 12)}) for k in range(3)]
    voz.eventos += [ir.Pausa(id="resto", inicio=F(1, 4), duracao=F(3, 4), proveniencia=voz.eventos[0].proveniencia),
                    ir.Pausa(id="resto2", inicio=F(1), duracao=F(1), proveniencia=voz.eventos[0].proveniencia)]
    voz.quialteras = [ir.Quialtera(id="q", numerador=3, denominador=2, eventos=[e.id for e in voz.eventos[:3]])]
    nova = rodar("rarefazer", antes, Selecao(partes=["P2"]), manter=0.6, harmonicos=0, acentos=0)
    assert any(n.duracao > F(1, 12) for n in notas(nova, 1))


def test_preservacao_recusa_mudanca_em_quialtera_que_alcanca_fora():
    from muschat.orquestrador import _conferir, Recusa
    antes = material()
    voz = antes.partes[1].vozes[0]
    voz.quialteras = [ir.Quialtera(id="q", numerador=3, denominador=2, eventos=["P2E0", "P2E8"])]
    nova = antes.model_copy(deep=True)
    nova.partes[1].vozes[0].quialteras[0].numerador = 4
    plano = Plano(objetivo="t", selecao=Selecao(compasso_inicial=2), operacoes=[{"procedimento": "deformar_ritmo"}])
    with pytest.raises(Recusa, match="fora da seleção"):
        _conferir(plano, antes, nova, origem="algoritmo", operacao="teste")


def test_receitas_nas_quatro_referencias_com_metricas(tmp_path):
    import json
    from muschat.referencias import gerar_receitas
    pasta = gerar_receitas("receitas", raiz=tmp_path)
    dados = json.loads((pasta / "metricas.json").read_text(encoding="utf-8"))
    assert len(dados) == 24
    assert len(list(pasta.glob("*.musicxml"))) == 24
    for nome, medidas in dados.items():
        if nome.endswith("sciarrino"):
            assert medidas["depois"]["ataques_por_seminima_somando_partes"] < medidas["antes"]["ataques_por_seminima_somando_partes"]
        if nome.endswith("lachenmann"):
            assert not medidas["depois"]["tecnicas_intocaveis"]

"""Inserção de material externo num trecho, preservando ids fora dele."""
from muschat.musicir import MusicIR
from muschat.plano import Selecao, Secao
from muschat.procedimentos.comum import Ids


def inserir(atual: MusicIR | None, material: MusicIR, sel: Selecao,
            secoes: list[Secao]) -> tuple[MusicIR, MusicIR]:
    primeiro, ultimo = sel.compasso_inicial, sel.compasso_final
    if primeiro is None or ultimo is None or len(material.compassos) != ultimo - primeiro + 1:
        raise ValueError("NotaGen devolveu quantidade incompatível de compassos para a seção")
    if atual is None:
        total = max(s.compasso_final for s in secoes)
        compassos = [material.compassos[0].model_copy(update={"andamento": None}) for _ in range(total)]
        compassos[primeiro - 1:ultimo] = material.compassos
        atual = material.model_copy(update={
            "compassos": compassos,
            "partes": [p.model_copy(update={"vozes": [v.model_copy(update={"eventos": [], "quialteras": []})
                       for v in p.vozes]}) for p in material.partes]})
    if any((a.numerador, a.denominador, a.comprimento) != (b.numerador, b.denominador, b.comprimento)
           for a, b in zip(material.compassos, atual.compassos[primeiro - 1:ultimo])):
        raise ValueError("métrica ou andamento do NotaGen incompatível com a seção")
    # shortcut: o trecho conserva o andamento da peca; importar mudancas de andamento exige politica de mesclagem.
    if len(material.partes) != len(atual.partes):
        raise ValueError("número de partes incompatível com a seção")
    inicio = atual.inicios()[primeiro - 1]
    fim = inicio + sum(c.comprimento for c in material.compassos)
    ids, partes = Ids(atual), []
    for parte, fonte in zip(atual.partes, material.partes):
        if len(parte.vozes) != len(fonte.vozes):
            raise ValueError("número de vozes incompatível com a seção")
        vozes = []
        for voz, nova in zip(parte.vozes, fonte.vozes):
            if not sel.contem(parte.id, voz.id, primeiro):
                vozes.append(voz)
                continue
            if any(e.inicio < inicio < e.fim or e.inicio < fim < e.fim for e in voz.eventos):
                raise ValueError("evento atravessa a fronteira da seção")
            removidos = {e.id for e in voz.eventos if inicio <= e.inicio < fim}
            if any(set(q.eventos) & removidos and not set(q.eventos) <= removidos for q in voz.quialteras):
                raise ValueError("quiáltera atravessa a fronteira da seção")
            mapa = {e.id: ids.novo(e.id) for e in nova.eventos}
            mapa.update({q.id: ids.novo(q.id) for q in nova.quialteras})
            eventos = [e for e in voz.eventos if e.id not in removidos] + [
                e.model_copy(update={"id": mapa[e.id], "inicio": inicio + e.inicio}) for e in nova.eventos]
            grupos = [q for q in voz.quialteras if not set(q.eventos) & removidos] + [
                q.model_copy(update={"id": mapa[q.id], "eventos": [mapa[e] for e in q.eventos],
                                     "pai": mapa[q.pai] if q.pai else None}) for q in nova.quialteras]
            vozes.append(voz.model_copy(update={"eventos": sorted(eventos, key=lambda e: e.inicio),
                                                "quialteras": grupos}))
        partes.append(parte.model_copy(update={"vozes": vozes}))
    return atual.model_copy(update={"partes": partes}), atual

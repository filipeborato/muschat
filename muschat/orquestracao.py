"""Adaptação explícita de linhas do NotaGen à formação pedida, antes da camada contemporânea."""

from statistics import median

from muschat import musicir as ir
from muschat.instrumentos import identificar, instrumento, no_registro
from muschat.notacao.tecnicas import problema


def adaptar(material: ir.MusicIR, nomes: list[str]) -> tuple[ir.MusicIR, str]:
    fontes = [(p, v) for p in material.partes for v in p.vozes
              if any(isinstance(e, ir.Nota) for e in v.eventos)]
    if not fontes:
        raise ValueError("o NotaGen não produziu nenhuma linha com notas para orquestrar")
    partes, usados, mapas = [], set(), []
    for k, nome in enumerate(nomes, 1):
        inst = instrumento(nome)
        registro = inst.registro_inicial or inst.tessitura
        centro = sum(registro) / 2

        def custo(fonte):
            p, v = fonte
            try:
                igual = identificar(p.instrumento) == identificar(nome)
            except ValueError:
                igual = False
            alturas = [a.midicents for e in v.eventos if isinstance(e, ir.Nota) for a in e.alturas]
            return (not igual, abs(median(alturas) - centro))

        disponiveis = [f for f in fontes if f[1].id not in usados] or fontes
        origem, voz = min(disponiveis, key=custo)
        usados.add(voz.id)
        parte = ir.Parte(id=f"P{k}", instrumento=nome.capitalize(), clave=inst.clave,
                         tessitura=inst.tessitura, vozes=[ir.Voz(id=f"P{k}V1")])
        ids = {e.id: f"P{k}V1E{n}" for n, e in enumerate(voz.eventos, 1)}
        grupos = {q.id: f"P{k}V1Q{n}" for n, q in enumerate(voz.quialteras, 1)}
        eventos = []
        for e in voz.eventos:
            copia = e.model_copy(update={"id": ids[e.id]})
            if isinstance(copia, (ir.Nota, ir.Pausa)):
                copia = copia.model_copy(update={"texto": None})
            if isinstance(copia, ir.Nota):
                # Uma linha tocável por intérprete; a voz superior representa acordes da fonte.
                altura = no_registro(max(a.midicents for a in copia.alturas), registro)
                copia = copia.model_copy(update={"alturas": [ir.Altura(midicents=altura)]})
                copia.tecnicas = [t for t in copia.tecnicas if problema(t, parte, copia) is None]
            eventos.append(copia)
        quialteras = [q.model_copy(update={"id": grupos[q.id], "eventos": [ids[i] for i in q.eventos],
                                          "pai": grupos[q.pai] if q.pai else None}) for q in voz.quialteras]
        parte.vozes = [ir.Voz(id=f"P{k}V1", eventos=eventos, quialteras=quialteras)]
        partes.append(parte)
        mapas.append(f"{nome} ← {origem.instrumento}/{voz.id}")
    aviso = (f"orquestração adaptada de {len(material.partes)} para {len(partes)} partes: "
             + "; ".join(mapas) + ". Linhas monofônicas, registro ajustado por oitavas; "
             "ritmo, pausas e quiálteras da fonte preservados. Linhas reutilizadas quando necessário; "
             "as demais vozes da fonte não entram no arranjo.")
    return material.model_copy(update={"partes": partes}), aviso

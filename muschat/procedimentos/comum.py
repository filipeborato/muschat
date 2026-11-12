"""Ferramentas dos procedimentos: seleção por voz/compasso, ids novos e figuras notáveis."""

from collections.abc import Callable
from fractions import Fraction

from muschat import musicir as ir
from muschat.musicir import localizar, pedacos
from muschat.plano import Selecao

QUARTETO = ["violino", "violino", "viola", "violoncelo"]
DINAMICAS: list[ir.Dinamica] = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff"]


def exigir(atual: ir.MusicIR | None) -> ir.MusicIR:
    if atual is None:
        raise ValueError("este procedimento transforma uma composição, e ainda não há nenhuma")
    return atual


class Ids:
    """Ids novos e únicos na peça: base.1, base.2..."""

    def __init__(self, atual: ir.MusicIR):
        self.usados = set(localizar(atual)) | {q.id for p in atual.partes for v in p.vozes for q in v.quialteras}

    def novo(self, base: str) -> str:
        n = 1
        while f"{base}.{n}" in self.usados:
            n += 1
        self.usados.add(f"{base}.{n}")
        return f"{base}.{n}"


class Grade:
    """Compassos da peça: onde cada um começa e termina."""

    def __init__(self, atual: ir.MusicIR):
        self.inicios = atual.inicios()
        self.fins = [*self.inicios[1:], self.inicios[-1] + atual.compassos[-1].comprimento]

    def trecho(self, sel: Selecao) -> tuple[int, int]:
        """Primeiro e último compasso da seleção (1..n)."""
        return sel.compasso_inicial or 1, sel.compasso_final or len(self.inicios)

    def pedacos(self, inicio: Fraction, duracao: Fraction) -> list[tuple[Fraction, Fraction]]:
        return pedacos(self.inicios, inicio, duracao)


# shortcut: a MusicIR não tem ligadura; um evento cortado na barra vira dois ataques. Evoluir com `ligada` na Nota.
def repartir(e: ir.Evento, inicio: Fraction, duracao: Fraction, grade: Grade, ids: Ids) -> list[ir.Evento]:
    """Evento movido/redimensionado em figuras notáveis; o primeiro pedaço mantém o id, a dinâmica e o texto."""
    pedacos = grade.pedacos(inicio, duracao)
    saida: list[ir.Evento] = []
    for k, (i, d) in enumerate(pedacos):
        campos: dict = {"id": e.id if k == 0 else ids.novo(e.id), "inicio": i, "duracao": d}
        if k > 0 and isinstance(e, (ir.Nota, ir.Pausa)):
            campos |= {"dinamica": None, "texto": None}
        if k < len(pedacos) - 1 and isinstance(e, ir.Nota):
            campos["glissando"] = False
        saida.append(e.model_copy(update=campos))
    return saida


def pausa(e: ir.Evento) -> ir.Pausa:
    return ir.Pausa(id=e.id, inicio=e.inicio, duracao=e.duracao, proveniencia=e.proveniencia,
                    dinamica=getattr(e, "dinamica", None), texto=getattr(e, "texto", None))


def com_alturas(n: ir.Nota, alturas: list[int]) -> ir.Nota:
    return n.model_copy(update={"alturas": [ir.Altura(midicents=a) for a in alturas]})  # Altura valida a faixa


def trocar_vozes(atual: ir.MusicIR, f: Callable[[ir.Parte, ir.Voz], ir.Voz]) -> ir.MusicIR:
    return atual.model_copy(update={"partes": [
        p.model_copy(update={"vozes": [f(p, v) for v in p.vozes]}) for p in atual.partes]})


TrechoDaVoz = Callable[[ir.Parte, ir.Voz, list[ir.Evento]], list[ir.Evento]]


def por_voz(atual: ir.MusicIR, sel: Selecao, f: TrechoDaVoz) -> ir.MusicIR:
    """Aplica `f` aos eventos selecionados de cada voz; o que `f` devolve substitui só esses eventos."""
    locais = localizar(atual)

    def voz(parte: ir.Parte, v: ir.Voz) -> ir.Voz:
        alvo = [e for e in v.eventos if sel.contem(parte.id, v.id, locais[e.id][2])]
        if not alvo:
            return v
        trocados = {e.id for e in alvo}
        eventos = [e for e in v.eventos if e.id not in trocados] + f(parte, v, alvo)
        return v.model_copy(update={"eventos": sorted(eventos, key=lambda e: e.inicio)})

    return trocar_vozes(atual, voz)


CompassoDaVoz = Callable[[ir.Parte, ir.Voz, list[ir.Evento], int], list[ir.Evento]]


def por_compasso(atual: ir.MusicIR, sel: Selecao, f: CompassoDaVoz) -> ir.MusicIR:
    """Como `por_voz`, compasso a compasso; compassos com quiálteras ficam como estão (mexer quebraria o grupo)."""
    locais = localizar(atual)

    def trecho(parte: ir.Parte, v: ir.Voz, alvo: list[ir.Evento]) -> list[ir.Evento]:
        em_quialtera = {i for q in v.quialteras for i in q.eventos}
        grupos: dict[int, list[ir.Evento]] = {}
        for e in alvo:
            grupos.setdefault(locais[e.id][2] or 0, []).append(e)
        saida: list[ir.Evento] = []
        for n, grupo in grupos.items():
            saida += grupo if em_quialtera & {e.id for e in grupo} else f(parte, v, grupo, n)
        return saida

    return por_voz(atual, sel, trecho)


def interpolar(inicial: float, final: float, n: int, primeiro: int, ultimo: int) -> float:
    """Valor no compasso n de uma rampa linear entre o primeiro e o último compasso do trecho."""
    return inicial if ultimo == primeiro else inicial + (final - inicial) * (n - primeiro) / (ultimo - primeiro)

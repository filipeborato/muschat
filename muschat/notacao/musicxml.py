"""Builder determinístico MusicIR -> MusicXML (score-partwise): várias partes e vozes, quiálteras, microtons e as
técnicas do grimório. Eventos eletrônicos, curvas de controle e dados espectrais não têm grafia e ficam de fora."""

import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from itertools import accumulate

from muschat.musicir import CLASSE_DA_LETRA, Altura, Compasso, MusicIR, Nota, Parte, Pausa, Quialtera, Voz
from muschat.notacao.tecnicas import Indicacoes, Tecnica, tecnica

_FIGURAS = {Fraction(1, 2**k): nome for k, nome in enumerate(["whole", "half", "quarter", "eighth", "16th", "32nd", "64th"])}
_CLAVE = {"sol": ("G", "2"), "fa": ("F", "4"), "do3": ("C", "3"), "do4": ("C", "4"), "percussao": ("percussion", None)}
_ACIDENTE = {Fraction(n, 4): nome for n, nome in [
    (4, "sharp"), (-4, "flat"), (2, "quarter-sharp"), (-2, "quarter-flat"), (6, "three-quarters-sharp"),
    (-6, "three-quarters-flat"), (8, "double-sharp"), (-8, "flat-flat"),
    (1, "natural-up"), (-1, "natural-down"), (3, "sharp-down"), (-3, "flat-up"), (5, "sharp-up"), (-5, "flat-down"),
]}
_LETRA_PADRAO = "C C D D E F F G G A A B".split()  # sem dica de grafia: sustenidos


def para_musicxml(ir: MusicIR) -> str:
    raiz = ET.Element("score-partwise", version="4.0")
    _sub(_sub(raiz, "work"), "work-title", ir.titulo)
    lista = _sub(raiz, "part-list")
    for parte in ir.partes:
        _sub(_sub(lista, "score-part", id=parte.id), "part-name", parte.instrumento)
    divisoes = _divisoes(ir)
    for k, parte in enumerate(ir.partes):
        _parte(_sub(raiz, "part", id=parte.id), parte, ir.compassos, divisoes, com_andamento=k == 0)
    return ET.tostring(raiz, encoding="unicode", xml_declaration=True)


def _divisoes(ir: MusicIR) -> int:
    """Divisões por semínima: 8 no mínimo (semicolcheia pontuada inteira), múltiplas de toda quiáltera."""
    tempos = [c.comprimento for c in ir.compassos]
    tempos += [t for _, _, e in ir.eventos() if isinstance(e, (Nota, Pausa)) for t in (e.inicio, e.duracao)]
    return math.lcm(8, *((t * 4).denominator for t in tempos))


def _parte(el: ET.Element, parte: Parte, compassos: list[Compasso], divisoes: int, *, com_andamento: bool) -> None:
    vozes = [v for v in parte.vozes if any(isinstance(e, (Nota, Pausa)) for e in v.eventos)]
    escritas = [_EscritaVoz(v, divisoes) for v in vozes]
    inicios = list(accumulate((c.comprimento for c in compassos), initial=Fraction(0)))
    for n, compasso in enumerate(compassos, 1):
        medida = _sub(el, "measure", number=str(n))
        _atributos(medida, compasso, compassos[n - 2] if n > 1 else None, parte, divisoes)
        if com_andamento and compasso.andamento:
            _andamento(medida, compasso.andamento)
        acidentes: dict[tuple[str, int], Fraction] = {}  # (step, oitava) -> alteração vigente no compasso
        for k, escrita in enumerate(escritas, 1):
            escrita.escrever(medida, k, inicios[n - 1], inicios[n], acidentes, polifonica=len(escritas) > 1)
    for escrita in escritas:
        if escrita.pendentes():
            raise ValueError(f"voz {escrita.voz.id}: eventos depois do último compasso ({escrita.pendentes()})")


class _EscritaVoz:
    """Escreve uma voz compasso a compasso, mantendo técnica, dinâmica e glissando em curso."""

    def __init__(self, voz: Voz, divisoes: int):
        self.voz, self.divisoes, self.indicacoes, self.glissando_aberto = voz, divisoes, Indicacoes(), False
        self.eventos = [e for e in voz.eventos if isinstance(e, (Nota, Pausa))]
        self.i = 0
        grupos = {q.id: q for q in voz.quialteras}
        self.cadeia: dict[str, list[str]] = {}  # evento -> grupos, do mais externo ao mais interno
        for q in voz.quialteras:
            for id_evento in q.eventos:
                cadeia: list[str] = []
                g: Quialtera | None = q
                while g is not None:
                    cadeia.insert(0, g.id)
                    g = grupos.get(g.pai) if g.pai else None
                self.cadeia[id_evento] = cadeia
        membros: dict[str, list[str]] = {}
        for e in self.eventos:
            for gid in self.cadeia.get(e.id, []):
                membros.setdefault(gid, []).append(e.id)
        self.grupos, self.membros = grupos, membros

    def pendentes(self) -> list[str]:
        return [e.id for e in self.eventos[self.i:]]

    def escrever(self, medida: ET.Element, numero: int, inicio: Fraction, fim: Fraction,
                 acidentes: dict[tuple[str, int], Fraction], *, polifonica: bool) -> None:
        """Em parte polifônica toda voz preenche o compasso, e as seguintes voltam ao início com <backup>."""
        cursor = inicio
        if numero > 1 and fim > inicio:
            _sub(_sub(medida, "backup"), "duration", str(self._div(fim - inicio)))
        while self.i < len(self.eventos) and self.eventos[self.i].inicio < fim:
            e = self.eventos[self.i]
            if e.fim > fim:  # shortcut: sem ligaduras através da barra; evoluir quando um motor gerar isso
                raise ValueError(f"evento {e.id} atravessa a barra do compasso; divida-o com ligadura")
            if e.inicio > cursor:
                _avanco(medida, "forward", self._div(e.inicio - cursor), numero)
            seguinte = self.eventos[self.i + 1] if self.i + 1 < len(self.eventos) else None
            self._evento(medida, e, seguinte, numero, acidentes)
            cursor = e.fim
            self.i += 1
        if polifonica and cursor < fim:
            _avanco(medida, "forward", self._div(fim - cursor), numero)

    def _div(self, t: Fraction) -> int:
        return int(t * 4 * self.divisoes)

    def _evento(self, medida: ET.Element, e: Nota | Pausa, seguinte: Nota | Pausa | None, numero: int,
                acidentes: dict[tuple[str, int], Fraction]) -> None:
        tecnicas = [tecnica(t) for t in e.tecnicas] if isinstance(e, Nota) else []
        italicos, livres, dinamica = self.indicacoes.proximo(tecnicas, isinstance(e, Nota), e.texto, e.dinamica)
        for texto in italicos:
            _direcao_texto(medida, texto, italico=True)
        for texto in livres:
            _direcao_texto(medida, texto)
        if dinamica:
            _dinamica(medida, dinamica)
        # glissando só entre duas notas: nunca para/de pausa
        abre = isinstance(e, Nota) and e.glissando and isinstance(seguinte, Nota)
        cadeia = self.cadeia.get(e.id, [])
        razao = math.prod((Fraction(self.grupos[g].numerador, self.grupos[g].denominador) for g in cadeia),
                          start=Fraction(1))
        tuplets = [(n, "start") for n, g in enumerate(cadeia, 1) if self.membros[g][0] == e.id]
        tuplets += [(n, "stop") for n, g in reversed(list(enumerate(cadeia, 1))) if self.membros[g][-1] == e.id]
        _nota(medida, e, tecnicas, numero=numero, duracao=self._div(e.duracao), razao=razao, tuplets=tuplets,
              fecha_glissando=self.glissando_aberto and isinstance(e, Nota), abre_glissando=abre,
              acidentes=acidentes)
        self.glissando_aberto = abre


def _sub(pai: ET.Element, tag: str, texto: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(pai, tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if texto is not None:
        el.text = texto
    return el


def _avanco(medida: ET.Element, tag: str, duracao: int, voz: int) -> None:
    el = _sub(medida, tag)
    _sub(el, "duration", str(duracao))
    _sub(el, "voice", str(voz))


def _atributos(medida: ET.Element, c: Compasso, anterior: Compasso | None, parte: Parte, divisoes: int) -> None:
    nova_metrica = anterior is None or (c.numerador, c.denominador) != (anterior.numerador, anterior.denominador)
    if anterior is not None and not nova_metrica:
        return
    attrs = _sub(medida, "attributes")
    if anterior is None:
        _sub(attrs, "divisions", str(divisoes))
        _sub(_sub(attrs, "key"), "fifths", "0")
    tempo = _sub(attrs, "time")
    _sub(tempo, "beats", str(c.numerador))
    _sub(tempo, "beat-type", str(c.denominador))
    if anterior is None:
        sign, line = _CLAVE[parte.clave]
        clave = _sub(attrs, "clef")
        _sub(clave, "sign", sign)
        if line:
            _sub(clave, "line", line)


def _andamento(medida: ET.Element, andamento: int) -> None:
    direcao = _sub(medida, "direction", placement="above")
    metronomo = _sub(_sub(direcao, "direction-type"), "metronome")
    _sub(metronomo, "beat-unit", "quarter")
    _sub(metronomo, "per-minute", str(andamento))
    _sub(direcao, "sound", tempo=str(andamento))


def _direcao_texto(medida: ET.Element, texto: str, italico: bool = False) -> None:
    tipo = _sub(_sub(medida, "direction", placement="above"), "direction-type")
    _sub(tipo, "words", texto, **({"font_style": "italic"} if italico else {}))


def _dinamica(medida: ET.Element, dinamica: str) -> None:
    tipo = _sub(_sub(medida, "direction", placement="below"), "direction-type")
    _sub(_sub(tipo, "dynamics"), dinamica)


def grafia(a: Altura) -> tuple[str, Fraction, int]:
    """(step, alteração em semitons, oitava); a dica de grafia vale se der alteração de até 2 semitons."""
    n = Fraction(a.midicents, 100)
    letra: str | None = a.grafia
    if letra is not None:
        oitava = round((n - CLASSE_DA_LETRA[letra]) / 12) - 1
        if abs(n - 12 * (oitava + 1) - CLASSE_DA_LETRA[letra]) > 2:
            letra = None
    if letra is None:
        semitom = round(Fraction(a.midicents, 50)) * 50 // 100  # quarto de tom mais próximo, abaixo do semitom
        letra, oitava = _LETRA_PADRAO[semitom % 12], semitom // 12 - 1
    return letra, n - 12 * (oitava + 1) - CLASSE_DA_LETRA[letra], oitava


def _figura(notada: Fraction, id_evento: str) -> tuple[str, bool]:
    for base, nome in _FIGURAS.items():
        if notada in (base, base * Fraction(3, 2)):
            return nome, notada != base
    raise ValueError(f"evento {id_evento}: duração notada {notada} não é figura simples nem pontuada")


def _nota(
    medida: ET.Element,
    e: Nota | Pausa,
    tecnicas: list[Tecnica],
    *,
    numero: int,
    duracao: int,
    razao: Fraction,
    tuplets: list[tuple[int, str]],
    fecha_glissando: bool,
    abre_glissando: bool,
    acidentes: dict[tuple[str, int], Fraction],
) -> None:
    tipo, pontuada = _figura(e.duracao * razao, e.id)
    alturas = [grafia(a) for a in e.alturas] if isinstance(e, Nota) else [None]
    notehead = next((t.notehead for t in tecnicas if t.notehead), None)
    for i, altura in enumerate(alturas):
        el = _sub(medida, "note")
        if i > 0:
            _sub(el, "chord")
        if altura is None:
            _sub(el, "rest")
        else:
            step, alter, oitava = altura
            pitch = _sub(el, "pitch")
            _sub(pitch, "step", step)
            if alter:
                _sub(pitch, "alter", f"{float(alter):g}")
            _sub(pitch, "octave", str(oitava))
        _sub(el, "duration", str(duracao))
        _sub(el, "voice", str(numero))
        _sub(el, "type", tipo)
        if pontuada:
            _sub(el, "dot")
        if altura is not None:
            step, alter, oitava = altura
            if alter:
                _sub(el, "accidental", _ACIDENTE.get(Fraction(round(alter * 4), 4), "natural"))
            elif acidentes.get((step, oitava)):
                _sub(el, "accidental", "natural")  # cancela acidente anterior no mesmo compasso
            acidentes[(step, oitava)] = alter
        if razao != 1:
            modificacao = _sub(el, "time-modification")
            _sub(modificacao, "actual-notes", str(razao.numerator))
            _sub(modificacao, "normal-notes", str(razao.denominator))
        if altura is not None and notehead:
            _sub(el, "notehead", notehead)
        if i == 0:
            _notacoes(el, tecnicas, tuplets, fecha_glissando, abre_glissando)


def _notacoes(el: ET.Element, tecnicas: list[Tecnica], tuplets: list[tuple[int, str]], fecha_glissando: bool,
              abre_glissando: bool) -> None:
    filhos: list[tuple[str, dict[str, str]]] = [("tuplet", {"type": t, "number": str(n)}) for n, t in tuplets]
    if fecha_glissando:
        filhos.append(("glissando", {"type": "stop", "number": "1"}))
    if abre_glissando:
        filhos.append(("glissando", {"type": "start", "number": "1", "line-type": "solid"}))
    if not filhos and not any(t.technical or t.tremolo or t.articulacao for t in tecnicas):
        return
    notacoes = _sub(el, "notations")
    for tag, attrs in filhos:
        ET.SubElement(notacoes, tag, attrs)
    if any(t.tremolo for t in tecnicas):
        _sub(_sub(notacoes, "ornaments"), "tremolo", "3", type="single")
    articulacoes = [t.articulacao for t in tecnicas if t.articulacao]
    if articulacoes:
        bloco = _sub(notacoes, "articulations")
        for tag in articulacoes:
            _sub(bloco, tag)
    tecnicos = [t.technical for t in tecnicas if t.technical]
    if tecnicos:
        tecnico = _sub(notacoes, "technical")
        for tag in tecnicos:
            _sub(tecnico, tag)

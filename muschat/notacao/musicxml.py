"""Builder determinístico Partitura -> MusicXML (score-partwise), com as técnicas do grimório."""

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from muschat.notacao.modelo import Nota, Partitura

DIVISOES = 8  # por semínima (8 deixa semicolcheia pontuada inteira)
_DURACAO = {"inteira": (32, "whole"), "minima": (16, "half"), "seminima": (8, "quarter"),
            "colcheia": (4, "eighth"), "semicolcheia": (2, "16th")}
_CLAVE = {"sol": ("G", "2"), "fa": ("F", "4"), "do3": ("C", "3"), "do4": ("C", "4"), "percussao": ("percussion", None)}
_ACIDENTE = {1.0: "sharp", -1.0: "flat", 0.5: "quarter-sharp", -0.5: "quarter-flat",
             1.5: "three-quarters-sharp", -1.5: "three-quarters-flat", 2.0: "double-sharp", -2.0: "flat-flat"}
_ALTER = {"#": 1, "##": 2, "b": -1, "bb": -2, "+": 0.5, "-": -0.5, "#+": 1.5, "b-": -1.5}
_ALTURA = re.compile(r"^([A-G])(##|#\+|#|bb|b-|b|\+|-)?(\d)([+-])?$")


@dataclass(frozen=True)
class Tecnica:
    texto: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False


TECNICAS = {
    "sul ponticello": Tecnica("sul pont."),
    "sul ponticello estremo": Tecnica("sul pont. estr."),
    "sul tasto": Tecnica("sul tasto"),
    "flautando": Tecnica("flautando"),
    "col legno battuto": Tecnica("c.l. batt.", "x"),
    "col legno tratto": Tecnica("c.l. tratto", "x"),
    "dietro il ponticello": Tecnica("dietro il pont.", "x"),
    "sulla cordiera": Tecnica("sulla cordiera", "x"),
    "scratch": Tecnica("scratch", "x"),
    "pizzicato": Tecnica("pizz."),
    "pizz. bartok": Tecnica(technical="snap-pizzicato"),
    "harmonico": Tecnica(notehead="diamond"),  # grimório: losango, sem o círculo redundante
    "fischio": Tecnica("fischio", "diamond"),
    "whistle tone": Tecnica("whistle tone", "diamond"),
    "tremolo": Tecnica(tremolo=True),
    "frullato": Tecnica("flz.", tremolo=True),
    "multifonico": Tecnica("multif."),
    "slap": Tecnica("slap", "x"),
    "key click": Tecnica("key click", "x"),
    "aria": Tecnica("aria", "circle-x"),
}
_ALIASES = {"harmonic": "harmonico", "armonico": "harmonico", "arm.": "harmonico", "arm. naturale": "harmonico",
            "flatterzunge": "frullato", "flz.": "frullato", "snap pizzicato": "pizz. bartok",
            "bartok pizzicato": "pizz. bartok", "c.l. batt.": "col legno battuto", "c.l.b.": "col legno battuto",
            "c.l. tratto": "col legno tratto", "c.l.t.": "col legno tratto", "s.p.e.": "sul ponticello estremo",
            "sul pont.": "sul ponticello", "sul pont. estr.": "sul ponticello estremo", "s.t.": "sul tasto",
            "multiphonic": "multifonico", "suono eolico": "aria", "chiavi": "key click"}


def _tecnica(nome: str) -> Tecnica:
    chave = unicodedata.normalize("NFKD", nome.strip().lower()).encode("ascii", "ignore").decode()
    chave = _ALIASES.get(chave, chave)
    return TECNICAS.get(chave) or Tecnica(texto=nome.strip())  # desconhecida: vira texto, nunca falha


def _sub(pai: ET.Element, tag: str, texto: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(pai, tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if texto is not None:
        el.text = texto
    return el


def _alter(altura: str) -> tuple[str, float, str] | None:
    m = _ALTURA.match(altura.strip())
    if not m:
        return None
    step, acidente, oitava, quarto = m.groups()
    if quarto and acidente and acidente[-1] in "+-":
        return None  # quarto de tom escrito duas vezes ("A#+4+"): ambíguo, vira pausa
    return step, _ALTER.get(acidente or "", 0) + _ALTER.get(quarto or "", 0), oitava


def para_musicxml(p: Partitura) -> str:
    raiz = ET.Element("score-partwise", version="4.0")
    _sub(_sub(raiz, "work"), "work-title", p.titulo)
    _sub(_sub(_sub(raiz, "part-list"), "score-part", id="P1"), "part-name", p.instrumento)
    parte = _sub(raiz, "part", id="P1")

    sequencia = [nota for compasso in p.compassos for nota in compasso.notas]
    soa = [any(_alter(a) for a in nota.alturas) for nota in sequencia]  # nota com altura válida?
    textos_anteriores: tuple[str, ...] = ()
    dinamica_atual: str | None = None
    glissando_aberto = False
    i = 0
    for n, compasso in enumerate(p.compassos, 1):
        medida = _sub(parte, "measure", number=str(n))
        if n == 1:
            _atributos(medida, p)
        acidentes: dict[tuple[str, str], float] = {}  # (step, oitava) -> alteração vigente no compasso
        for nota in compasso.notas:
            tecnicas = [_tecnica(t) for t in nota.tecnicas]
            textos = tuple(t.texto for t in tecnicas if t.texto)
            if soa[i] and textos != textos_anteriores:  # pausa não muda a técnica em curso
                retorno = "arco" if "pizz." in textos_anteriores else "ord."
                for texto in textos or (retorno,):
                    _direcao_texto(medida, texto, italico=True)
                textos_anteriores = textos
            if nota.texto and _tecnica(nota.texto).texto not in textos:  # não repete o que a técnica já escreveu
                _direcao_texto(medida, nota.texto)
            if nota.dinamica and nota.dinamica != dinamica_atual:  # dinâmica vale até a próxima mudança
                _dinamica(medida, nota.dinamica)
                dinamica_atual = nota.dinamica
            # glissando só entre duas notas que soam: nunca para/de pausa ou altura inválida
            abre = nota.glissando and soa[i] and i + 1 < len(sequencia) and soa[i + 1]
            _nota(medida, nota, tecnicas, fecha_glissando=glissando_aberto and soa[i], abre_glissando=abre,
                  acidentes=acidentes)
            glissando_aberto = abre
            i += 1
    return ET.tostring(raiz, encoding="unicode", xml_declaration=True)


def _atributos(medida: ET.Element, p: Partitura) -> None:
    attrs = _sub(medida, "attributes")
    _sub(attrs, "divisions", str(DIVISOES))
    _sub(_sub(attrs, "key"), "fifths", "0")
    beats, _, beat_type = p.metrica.partition("/")
    if not (beats.isdigit() and beat_type.isdigit()):
        beats, beat_type = "4", "4"
    tempo = _sub(attrs, "time")
    _sub(tempo, "beats", beats)
    _sub(tempo, "beat-type", beat_type)
    sign, line = _CLAVE[p.clave]
    clave = _sub(attrs, "clef")
    _sub(clave, "sign", sign)
    if line:
        _sub(clave, "line", line)
    if p.andamento:
        direcao = _sub(medida, "direction", placement="above")
        metronomo = _sub(_sub(direcao, "direction-type"), "metronome")
        _sub(metronomo, "beat-unit", "quarter")
        _sub(metronomo, "per-minute", str(p.andamento))
        _sub(direcao, "sound", tempo=str(p.andamento))


def _direcao_texto(medida: ET.Element, texto: str, italico: bool = False) -> None:
    tipo = _sub(_sub(medida, "direction", placement="above"), "direction-type")
    _sub(tipo, "words", texto, **({"font_style": "italic"} if italico else {}))


def _dinamica(medida: ET.Element, dinamica: str) -> None:
    tipo = _sub(_sub(medida, "direction", placement="below"), "direction-type")
    _sub(_sub(tipo, "dynamics"), dinamica)


def _nota(
    medida: ET.Element,
    nota: Nota,
    tecnicas: list[Tecnica],
    *,
    fecha_glissando: bool,
    abre_glissando: bool,
    acidentes: dict[tuple[str, str], float],
) -> None:
    duracao, tipo = _DURACAO[nota.duracao]
    if nota.pontuada:
        duracao = duracao * 3 // 2  # exato: DIVISOES=8 torna toda duração par
    alturas = [a for a in (_alter(x) for x in nota.alturas) if a is not None]
    notehead = next((t.notehead for t in tecnicas if t.notehead), None)
    for i, altura in enumerate(alturas or [None]):
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
                _sub(pitch, "alter", f"{alter:g}")
            _sub(pitch, "octave", oitava)
        _sub(el, "duration", str(duracao))
        _sub(el, "voice", "1")
        _sub(el, "type", tipo)
        if nota.pontuada:
            _sub(el, "dot")
        if altura is not None:
            step, alter, oitava = altura
            vigente = acidentes.get((step, oitava), 0.0)
            if alter:
                _sub(el, "accidental", _ACIDENTE.get(alter, "natural"))
            elif vigente:
                _sub(el, "accidental", "natural")  # cancela acidente anterior no mesmo compasso
            acidentes[(step, oitava)] = alter
        if altura is not None and notehead:
            _sub(el, "notehead", notehead)
        if altura is not None and i == 0:
            _notacoes(el, tecnicas, fecha_glissando, abre_glissando)


def _notacoes(el: ET.Element, tecnicas: list[Tecnica], fecha_glissando: bool, abre_glissando: bool) -> None:
    filhos: list[tuple[str, dict, str | None]] = []
    if fecha_glissando:
        filhos.append(("glissando", {"type": "stop", "number": "1"}, None))
    if abre_glissando:
        filhos.append(("glissando", {"type": "start", "number": "1", "line-type": "solid"}, None))
    if not filhos and not any(t.technical or t.tremolo for t in tecnicas):
        return
    notacoes = _sub(el, "notations")
    for tag, attrs, texto in filhos:
        ET.SubElement(notacoes, tag, attrs).text = texto
    if any(t.tremolo for t in tecnicas):
        _sub(_sub(notacoes, "ornaments"), "tremolo", "3", type="single")
    tecnicos = [t.technical for t in tecnicas if t.technical]
    if tecnicos:
        tecnico = _sub(notacoes, "technical")
        for tag in tecnicos:
            _sub(tecnico, tag)

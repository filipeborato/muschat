"""Builder determinístico MusicIR -> MusicXML (score-partwise): várias partes e vozes, quiálteras, microtons e as
técnicas do grimório. Eventos eletrônicos, curvas de controle e dados espectrais não têm grafia e ficam de fora."""

import io
import math
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile
from collections import Counter
from fractions import Fraction
from itertools import accumulate
from typing import get_args

from muschat.instrumentos import instrumento, transposicao
from muschat.musicir import (
    CLASSE_DA_LETRA, Altura, Compasso, Dinamica, MusicIR, Nota, Parte, Pausa, Proveniencia, Quialtera, Voz, ligam,
)
from muschat.notacao.tecnicas import TECNICAS, Indicacoes, Tecnica, tecnica

_FIGURAS = {Fraction(1, 2**k): nome for k, nome in enumerate(["whole", "half", "quarter", "eighth", "16th", "32nd", "64th"])}
_CLAVE = {"sol": ("G", "2"), "fa": ("F", "4"), "do3": ("C", "3"), "do4": ("C", "4"), "percussao": ("percussion", None)}
_ACIDENTE = {Fraction(n, 4): nome for n, nome in [
    (4, "sharp"), (-4, "flat"), (2, "quarter-sharp"), (-2, "quarter-flat"), (6, "three-quarters-sharp"),
    (-6, "three-quarters-flat"), (8, "double-sharp"), (-8, "flat-flat"),
    (1, "natural-up"), (-1, "natural-down"), (3, "sharp-down"), (-3, "flat-up"), (5, "sharp-up"), (-5, "flat-down"),
]}
_LETRA_PADRAO = "C C D D E F F G G A A B".split()  # sem dica de grafia: sustenidos


def para_musicxml(ir: MusicIR, *, transposta: bool = False) -> str:
    """Grade em som real por padrão; transposta=True escreve e declara os intervalos instrumentais."""
    raiz = ET.Element("score-partwise", version="4.0")
    _sub(_sub(raiz, "work"), "work-title", ir.titulo)
    lista = _sub(raiz, "part-list")
    for parte in ir.partes:
        _sub(_sub(lista, "score-part", id=parte.id), "part-name", parte.instrumento)
    divisoes = _divisoes(ir)
    for k, parte in enumerate(ir.partes):
        _parte(_sub(raiz, "part", id=parte.id), parte, ir.compassos, divisoes, com_andamento=k == 0, transposta=transposta)
    return ET.tostring(raiz, encoding="unicode", xml_declaration=True)


# --- leitura: MusicXML -> MusicIR ------------------------------------------------------------------------------------

LIMITE_MUSICXML = 5_000_000  # bytes; uma partitura orquestral longa cabe com folga
_CLAVE_LIDA = {sinal: clave for clave, sinal in _CLAVE.items() if sinal[1]}  # percussão fica "sol"
_DINAMICAS = set(get_args(Dinamica))
# marca MusicXML (<articulations>/<technical>) -> nome no grimório; as notações tied/tuplet são lidas à parte
_MARCAS = {(g, getattr(t, a)): nome for nome, t in TECNICAS.items()
           for g, a in (("articulations", "articulacao"), ("technical", "technical")) if getattr(t, a)}


def de_musicxml(dados: bytes, proveniencia: Proveniencia | None = None) -> tuple[MusicIR, list[str]]:
    """score-partwise -> (MusicIR, avisos). O que a MusicIR não representa é descartado e dito nos avisos, nunca em
    silêncio. Levanta ValueError (mensagem ao usuário) quando o arquivo não serve."""
    prov = proveniencia or Proveniencia(origem="compositor")
    raiz = _raiz(dados)
    try:
        return _ler(raiz, prov)
    # elemento obrigatório ausente, número inválido ou estrutura fora do esperado: recusa, nunca exceção crua
    except (TypeError, KeyError, AttributeError, IndexError, ZeroDivisionError) as exc:
        raise ValueError(f"MusicXML incompleto ou fora do padrão ({type(exc).__name__}: {exc})") from exc


def _raiz(dados: bytes) -> ET.Element:
    """Documento validado antes de ler: tamanho, .mxl (zip) pelo container, sem entidades, score-partwise."""
    if dados[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(dados)) as z:
                container = _xml_seguro(_ler_do_zip(z, "META-INF/container.xml"))
                dados = _ler_do_zip(z, container.find(".//rootfile").get("full-path"))
        except (zipfile.BadZipFile, KeyError, AttributeError, ET.ParseError) as exc:
            raise ValueError("arquivo .mxl sem um MusicXML legível dentro") from exc
    if len(dados) > LIMITE_MUSICXML:
        raise ValueError(f"o arquivo passa do limite de {LIMITE_MUSICXML} bytes")
    raiz = _xml_seguro(dados)
    if raiz.tag != "score-partwise":
        raise ValueError(f"esperado score-partwise, veio {raiz.tag} (exporte como MusicXML partwise)")
    if raiz.find("part") is None:
        raise ValueError("o MusicXML não tem nenhuma parte")
    return raiz


def _ler_do_zip(z: zipfile.ZipFile, caminho: str) -> bytes:
    if z.getinfo(caminho).file_size > LIMITE_MUSICXML:
        raise ValueError(f"{caminho} descomprimido passa do limite de {LIMITE_MUSICXML} bytes")
    return z.read(caminho)


def _xml_seguro(dados: bytes) -> ET.Element:
    """Parse sem declaração de entidade (expansão em cascata), em qualquer codificação: o expat avisa a declaração."""
    def recusar(*_):
        raise ValueError("DOCTYPE com declaração de entidade não é aceito; exporte de novo sem ela")

    varredura = expat.ParserCreate()
    varredura.EntityDeclHandler = recusar
    try:
        varredura.Parse(dados, True)  # para na declaração, antes de qualquer expansão
        return ET.fromstring(dados)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError(f"não é um MusicXML válido ({exc})") from exc


def _ler(raiz: ET.Element, prov: Proveniencia) -> tuple[MusicIR, list[str]]:
    nomes = {p.get("id"): (p.findtext("part-name") or "").strip() for p in raiz.iter("score-part")}
    compassos: list[Compasso] = []
    partes, avisos = [], []
    for k, el in enumerate(raiz.findall("part"), 1):
        nome = nomes.get(el.get("id")) or f"Parte {k}"
        leitura = _LeituraParte(f"P{k}", prov)
        for n, medida in enumerate(el.findall("measure")):
            leitura.compasso(medida, compassos if k == 1 else None, n)
        avisos += [f"{nome}: {a}" for a in leitura.avisos()]
        partes.append(Parte(id=f"P{k}", instrumento=nome, clave=leitura.clave,
                            vozes=[Voz(id=v, eventos=e, quialteras=leitura.grupos.get(v, []))
                                   for v, e in leitura.vozes.items()]))
    titulo = (raiz.findtext("work/work-title") or raiz.findtext("movement-title") or "Sem título").strip()
    return MusicIR(titulo=titulo, compassos=compassos, partes=partes), avisos


class _LeituraParte:
    def __init__(self, pid: str, prov: Proveniencia):
        self.pid, self.prov, self.clave = pid, prov, "sol"
        self.divisoes, self.metrica, self.inicio = 1, (4, 4), Fraction(0)
        self.vozes: dict[str, list] = {}
        self.grupos: dict[str, list[Quialtera]] = {}
        self.aberta: dict[str, Quialtera] = {}
        self.dinamica: str | None = None
        self.texto: str | None = None
        self.apojaturas, self.pautas, self.descartadas, self.notacoes = 0, 1, [], Counter()
        self.transposicao, self.claves_descartadas = 0, False  # transposição: semitons do escrito ao som real

    def avisos(self) -> list[str]:
        return ([f"{self.apojaturas} apojatura(s) descartada(s)"] if self.apojaturas else []) + [
            f"dinâmica '{d}' descartada" for d in self.descartadas] + (
            [f"notação descartada: {', '.join(f'{k} ({n})' for k, n in sorted(self.notacoes.items()))}"]
            if self.notacoes else []) + (
            [f"{self.pautas} pautas lidas numa só, na clave da primeira"] if self.pautas > 1 else []) + (
            ["mudança de clave depois do primeiro compasso descartada"] if self.claves_descartadas else [])

    def compasso(self, medida: ET.Element, compassos: list[Compasso] | None, n: int) -> None:
        andamento, cursor, maximo = None, Fraction(0), Fraction(0)
        for el in medida:
            if el.tag == "attributes":
                self.divisoes = int(el.findtext("divisions") or self.divisoes)
                if (tempo := el.find("time")) is not None:
                    self.metrica = (int(tempo.findtext("beats")), int(tempo.findtext("beat-type")))
                self.pautas = max(self.pautas, int(el.findtext("staves") or 1))
                if (transpor := el.find("transpose")) is not None:  # instrumento transpositor: MusicIR em som real
                    self.transposicao = (int(transpor.findtext("chromatic") or 0)
                                         + 12 * int(transpor.findtext("octave-change") or 0))
                # shortcut: uma clave por parte (a MusicIR não guarda troca de clave); evoluir junto com Parte
                if (clave := el.find("clef")) is not None:
                    if n == 0:
                        self.clave = _CLAVE_LIDA.get((clave.findtext("sign"), clave.findtext("line")), "sol")
                    else:
                        self.claves_descartadas = True
            elif el.tag == "sound" and el.get("tempo"):
                andamento = round(float(el.get("tempo")))
            elif el.tag == "direction":
                if (som := el.find("sound")) is not None and som.get("tempo"):
                    andamento = round(float(som.get("tempo")))
                for d in el.iterfind("direction-type/dynamics/*"):
                    if d.tag in _DINAMICAS:
                        self.dinamica = d.tag
                    elif d.tag not in self.descartadas:
                        self.descartadas.append(d.tag)
                palavras = " ".join(w.text.strip() for w in el.iterfind("direction-type/words") if w.text)
                if palavras:
                    self.texto = f"{self.texto}; {palavras}" if self.texto else palavras
            elif el.tag in ("backup", "forward"):
                passo = self._tempo(el)
                cursor += passo if el.tag == "forward" else -passo
                maximo = max(maximo, cursor)
            elif el.tag == "note":
                if el.find("grace") is not None:
                    self.apojaturas += 1
                    continue
                duracao = self._tempo(el)
                voz = f"{self.pid}V{el.findtext('voice') or '1'}"
                eventos = self.vozes.setdefault(voz, [])
                if el.find("chord") is not None:  # mais uma altura no acorde anterior da voz: o cursor não anda
                    if not eventos or not isinstance(eventos[-1], Nota):
                        raise ValueError(f"{voz}: nota de acorde sem a nota principal antes")
                    eventos[-1].alturas.append(self._altura(el))
                    continue
                comum = dict(id=f"{voz}E{len(eventos) + 1}", inicio=self.inicio + cursor, duracao=duracao,
                             proveniencia=self.prov, dinamica=self.dinamica, texto=self.texto)
                self.dinamica = self.texto = None
                self._quialtera(el, voz, comum["id"])
                if el.find("rest") is not None:
                    eventos.append(Pausa(**comum))
                else:
                    eventos.append(Nota(**comum, alturas=[self._altura(el)],
                                        ligada=el.find("tie[@type='start']") is not None,
                                        tecnicas=self._marcas(el)))
                cursor += duracao
                maximo = max(maximo, cursor)
        # anacruse (implicit): o compasso dura o que foi escrito nele, não a métrica
        comprimento = maximo if medida.get("implicit") == "yes" and maximo else Fraction(*self.metrica)
        if compassos is not None:
            compassos.append(Compasso(numerador=self.metrica[0], denominador=self.metrica[1], andamento=andamento,
                                      duracao=None if comprimento == Fraction(*self.metrica) else comprimento))
        self.inicio += comprimento

    def _marcas(self, el: ET.Element) -> list[str]:
        """Articulações e técnicas do grimório viram `tecnicas`; outra notação conta para o aviso."""
        tecnicas = []
        if (cabeca := el.findtext("notehead")) and cabeca != "normal":
            self.notacoes[f"cabeça {cabeca}"] += 1
        for notacao in el.iterfind("notations/*"):
            if notacao.tag in ("tied", "tuplet"):
                continue
            grupo = list(notacao) if notacao.tag in ("articulations", "technical") else [notacao]
            for marca in grupo:
                nome = _MARCAS.get((notacao.tag, marca.tag))
                if nome:
                    tecnicas.append(nome)
                else:
                    self.notacoes[marca.tag] += 1
        return tecnicas

    def _quialtera(self, el: ET.Element, voz: str, eid: str) -> None:
        """Grupos pela marcação <tuplet> start/stop, um nível só (aninhadas ainda não são lidas)."""
        tuplets = {t.get("type") for t in el.iterfind("notations/tuplet")}
        aberto = self.aberta.get(voz)
        if "start" in tuplets:
            if aberto:
                raise ValueError(f"{voz}: quiálteras aninhadas ainda não são importadas")
            mod = el.find("time-modification")
            if mod is None:
                raise ValueError(f"{voz}: quiáltera sem time-modification")
            aberto = self.aberta[voz] = Quialtera(id=f"{voz}Q{len(self.grupos.setdefault(voz, [])) + 1}",
                                                  numerador=int(mod.findtext("actual-notes")),
                                                  denominador=int(mod.findtext("normal-notes")), eventos=[eid])
        elif aberto:
            aberto.eventos.append(eid)
        elif el.find("time-modification") is not None:
            raise ValueError(f"{voz}: nota de quiáltera sem a marcação de início do grupo")
        if aberto and "stop" in tuplets:
            self.grupos.setdefault(voz, []).append(self.aberta.pop(voz))

    def _altura(self, el: ET.Element) -> Altura:
        """Som real: altura escrita + transposição; percussão sem altura usa a posição escrita na pauta."""
        if (pitch := el.find("pitch")) is not None:
            altura = _altura_lida(pitch.findtext("step"), pitch.findtext("octave"), pitch.findtext("alter"))
            if self.transposicao:
                altura = Altura(midicents=altura.midicents + 100 * self.transposicao)  # grafia escrita não vale
            return altura
        sem_altura = el.find("unpitched")
        return _altura_lida(sem_altura.findtext("display-step"), sem_altura.findtext("display-octave"), None)

    def _tempo(self, el: ET.Element) -> Fraction:
        return Fraction(int(el.findtext("duration")), 4 * self.divisoes)


def _altura_lida(step: str, oitava: str, alter: str | None) -> Altura:
    return Altura(midicents=round(((int(oitava) + 1) * 12 + CLASSE_DA_LETRA[step] + Fraction(alter or "0")) * 100),
                  grafia=step)


def _divisoes(ir: MusicIR) -> int:
    """Divisões por semínima: 8 no mínimo (semicolcheia pontuada inteira), múltiplas de toda quiáltera."""
    tempos = [c.comprimento for c in ir.compassos]
    tempos += [t for _, _, e in ir.eventos() if isinstance(e, (Nota, Pausa)) for t in (e.inicio, e.duracao)]
    return math.lcm(8, *((t * 4).denominator for t in tempos))


def _parte(el: ET.Element, parte: Parte, compassos: list[Compasso], divisoes: int, *, com_andamento: bool, transposta: bool) -> None:
    intervalo = transposicao(parte.instrumento) if transposta else (0, 0, 0)
    vozes = [v for v in parte.vozes if any(isinstance(e, (Nota, Pausa)) for e in v.eventos)]
    escritas = [_EscritaVoz(v, divisoes, intervalo) for v in vozes]
    inicios = list(accumulate((c.comprimento for c in compassos), initial=Fraction(0)))
    for n, compasso in enumerate(compassos, 1):
        medida = _sub(el, "measure", number=str(n))
        _atributos(medida, compasso, compassos[n - 2] if n > 1 else None, parte, divisoes, intervalo)
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

    def __init__(self, voz: Voz, divisoes: int, intervalo: tuple[int, int, int]):
        self.intervalo = intervalo
        self.voz, self.divisoes, self.indicacoes, self.glissando_aberto = voz, divisoes, Indicacoes(), False
        self.ligada_aberta = False
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
        liga = isinstance(e, Nota) and ligam(e, seguinte)
        _nota(medida, e, tecnicas, intervalo=self.intervalo, numero=numero, duracao=self._div(e.duracao), razao=razao, tuplets=tuplets,
              fecha_glissando=self.glissando_aberto and isinstance(e, Nota), abre_glissando=abre,
              acidentes=acidentes, ligaduras=[t for t, sim in (("stop", self.ligada_aberta), ("start", liga)) if sim])
        self.glissando_aberto, self.ligada_aberta = abre, liga


def _sub(pai: ET.Element, tag: str, texto: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(pai, tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if texto is not None:
        el.text = texto
    return el


def _avanco(medida: ET.Element, tag: str, duracao: int, voz: int) -> None:
    el = _sub(medida, tag)
    _sub(el, "duration", str(duracao))
    _sub(el, "voice", str(voz))


def _atributos(medida: ET.Element, c: Compasso, anterior: Compasso | None, parte: Parte, divisoes: int,
               intervalo: tuple[int, int, int]) -> None:
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
        clave_escrita = instrumento(parte.instrumento).clave_escrita if any(intervalo) else None
        sign, line = _CLAVE[clave_escrita or parte.clave]
        clave = _sub(attrs, "clef")
        _sub(clave, "sign", sign)
        if line:
            _sub(clave, "line", line)
        if any(intervalo):
            transpor = _sub(attrs, "transpose")
            _sub(transpor, "diatonic", str(intervalo[0]))
            _sub(transpor, "chromatic", str(intervalo[1]))
            if intervalo[2]:
                _sub(transpor, "octave-change", str(intervalo[2]))


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


def grafia(a: Altura, intervalo: tuple[int, int, int] = (0, 0, 0)) -> tuple[str, Fraction, int]:
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
    diatonica, cromatica, oitavas = intervalo
    oitava, indice = divmod(7 * oitava + "CDEFGAB".index(letra) - diatonica - 7 * oitavas, 7)
    letra = "CDEFGAB"[indice]
    n -= cromatica + 12 * oitavas
    if abs(n - 12 * (oitava + 1) - CLASSE_DA_LETRA[letra]) > 2:
        semitom = round(n * 2) // 2
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
    intervalo: tuple[int, int, int],
    numero: int,
    duracao: int,
    razao: Fraction,
    tuplets: list[tuple[int, str]],
    fecha_glissando: bool,
    abre_glissando: bool,
    acidentes: dict[tuple[str, int], Fraction],
    ligaduras: list[str],
) -> None:
    tipo, pontuada = _figura(e.duracao * razao, e.id)
    alturas = [grafia(a, intervalo) for a in e.alturas] if isinstance(e, Nota) else [None]
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
        for tipo_ligadura in ligaduras:
            _sub(el, "tie", type=tipo_ligadura)
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
        _notacoes(el, tecnicas if i == 0 else [], tuplets if i == 0 else [], i == 0 and fecha_glissando,
                  i == 0 and abre_glissando, ligaduras)


def _notacoes(el: ET.Element, tecnicas: list[Tecnica], tuplets: list[tuple[int, str]], fecha_glissando: bool,
              abre_glissando: bool, ligaduras: list[str]) -> None:
    filhos: list[tuple[str, dict[str, str]]] = [("tied", {"type": t}) for t in ligaduras]
    filhos += [("tuplet", {"type": t, "number": str(n)}) for n, t in tuplets]
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

"""MusicIR -> Abjad -> texto LilyPond (grade e partes); LilyPond externo -> PDF/SVG, com timeout e pasta temporária.

O texto sai do Abjad sem chamar o LilyPond; só `gravar` executa o binário. Eventos eletrônicos, curvas e dados
espectrais não têm grafia e ficam de fora, como no MusicXML.
"""

import glob
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Protocol

import abjad

from muschat.musicir import Compasso, MusicIR, Nota, Parte, Pausa, Quialtera, Voz, pedacos
from muschat.notacao.musicxml import grafia
from muschat.notacao.tecnicas import Indicacoes, tecnica

VERSAO_MINIMA = (2, 25, 26)  # exigida pelo Abjad 3.31 (README do Abjad)
_CLAVE = {"sol": "treble", "fa": "bass", "do3": "alto", "do4": "tenor", "percussao": "percussion"}
_ACIDENTE = {Fraction(0): "", Fraction(1, 2): "qs", Fraction(1): "s", Fraction(3, 2): "tqs", Fraction(2): "ss",
             Fraction(-1, 2): "qf", Fraction(-1): "f", Fraction(-3, 2): "tqf", Fraction(-2): "ff"}
_NOTEHEAD = {"x": "cross", "diamond": "harmonic", "circle-x": "xcircle"}
_ARTICULACAO = {"accent": "accent", "strong-accent": "marcato", "staccato": "staccato",
                "staccatissimo": "staccatissimo", "tenuto": "tenuto"}
_TECHNICAL = {"snap-pizzicato": "snappizzicato"}
AVISO_SEM_LILYPOND = (
    "Partitura pelo Verovio (MusicXML): LilyPond não encontrado, então não há PDF da grade nem das partes, e "
    "oitavos de tom saem como quartos de tom com seta. Para a gravação profissional, instale o LilyPond "
    f"{'.'.join(map(str, VERSAO_MINIMA))} ou mais novo (lilypond.org) e coloque-o no PATH, ou defina LILYPOND com "
    "o caminho do executável."
)


# --- MusicIR -> texto LilyPond ----------------------------------------------------------------------------------------

def para_lilypond(ir: MusicIR) -> dict[str, str]:
    """{"grade": partitura completa, "<instrumento> (<id>)": parte individual} como arquivos .ly autônomos."""
    fontes = {"grade": _arquivo(ir, ir.partes, ir.titulo)}
    for parte in ir.partes:
        fontes[f"{parte.instrumento} ({parte.id})"] = _arquivo(ir, [parte], ir.titulo, subtitulo=parte.instrumento)
    return fontes


def _arquivo(ir: MusicIR, partes: list[Parte], titulo: str, subtitulo: str | None = None) -> str:
    pautas = [_pauta(ir, p, com_andamento=k == 0) for k, p in enumerate(partes)]
    score = abjad.Score([abjad.StaffGroup(pautas)] if len(pautas) > 1 else pautas)
    cabecalho = f"  title = {_texto(titulo)}\n" + (f"  subtitle = {_texto(subtitulo)}\n" if subtitulo else "")
    return (f'\\version "{".".join(map(str, VERSAO_MINIMA))}"\n\\language "english"\n\n'
            f"\\header {{\n{cabecalho}  tagline = ##f\n}}\n\n"
            f"\\score {{\n{abjad.lilypond(score)}\n  \\layout {{ }}\n}}\n")


def _pauta(ir: MusicIR, parte: Parte, *, com_andamento: bool) -> abjad.Staff:
    vozes = [v for v in parte.vozes if any(isinstance(e, (Nota, Pausa)) for e in v.eventos)] or parte.vozes[:1]
    inicios = ir.inicios()
    fim = inicios[-1] + ir.compassos[-1].comprimento
    escritas = [_voz(v, inicios, fim, preencher_com_pausa=k == 0) for k, v in enumerate(vozes)]
    pauta = abjad.Staff([voz for voz, _ in escritas], name=parte.id, simultaneous=len(escritas) > 1)
    if len(escritas) > 1:
        for k, (voz, _) in enumerate(escritas):
            abjad.attach(abjad.LilyPondLiteral(rf"\voice{['One', 'Two', 'Three', 'Four'][min(k, 3)]}"),
                         abjad.select.leaf(voz, 0))
    primeiras = escritas[0][1]  # início -> primeira figura da voz principal (que preenche a peça toda)
    folha = primeiras[Fraction(0)]
    abjad.attach(abjad.Clef(_CLAVE[parte.clave]), folha)
    abjad.attach(abjad.InstrumentName(rf"\markup {_texto(parte.instrumento)}"), folha)
    anterior: Compasso | None = None
    for inicio, c in zip(inicios, ir.compassos):
        folha = primeiras[inicio]
        if anterior is None or (c.numerador, c.denominador, c.duracao) != (anterior.numerador, anterior.denominador,
                                                                          anterior.duracao):
            abjad.attach(abjad.TimeSignature(_metrica(c)), folha)
        if com_andamento and c.andamento:
            abjad.attach(abjad.MetronomeMark(abjad.Duration(1, 4), c.andamento), folha)
        anterior = c
    return pauta


def _metrica(c: Compasso) -> tuple[int, int]:
    if c.duracao is None:
        return c.numerador, c.denominador
    d = c.duracao if c.duracao.denominator >= c.denominador else c.duracao.limit_denominator(c.denominador)
    escala = max(1, c.denominador // d.denominator)  # 1/2 em 4/4 -> 2/4
    return d.numerator * escala, d.denominator * escala


def _voz(voz: Voz, inicios: list[Fraction], fim: Fraction, *,
         preencher_com_pausa: bool) -> tuple[abjad.Voice, dict[Fraction, abjad.Leaf]]:
    """Voz com quiálteras aninhadas; lacunas viram pausas (voz principal) ou espaços. Devolve também a primeira
    figura escrita em cada posição, para métrica e andamento."""
    eventos = [e for e in voz.eventos if isinstance(e, (Nota, Pausa))]
    grupos = {q.id: q for q in voz.quialteras}
    cadeias: dict[str, list[str]] = {}
    for q in voz.quialteras:
        cadeia: list[str] = []
        g: Quialtera | None = q
        while g is not None:
            cadeia.insert(0, g.id)
            g = grupos[g.pai] if g.pai else None
        for i in q.eventos:
            cadeias[i] = cadeia
    abjad_voz = abjad.Voice(name=voz.id)
    pilha: list[tuple[str, abjad.Tuplet]] = []
    folhas: dict[Fraction, abjad.Leaf] = {}
    indicacoes = Indicacoes()

    def colocar(folha: abjad.Leaf, inicio: Fraction, cadeia: list[str]) -> None:
        while pilha and [g for g, _ in pilha] != cadeia[:len(pilha)]:
            pilha.pop()
        for g in cadeia[len(pilha):]:
            tupla = abjad.Tuplet(f"{grupos[g].numerador}:{grupos[g].denominador}", [])
            (pilha[-1][1] if pilha else abjad_voz).append(tupla)
            pilha.append((g, tupla))
        (pilha[-1][1] if pilha else abjad_voz).append(folha)
        folhas.setdefault(inicio, folha)

    def lacuna(de: Fraction, ate: Fraction) -> None:
        for inicio, d in pedacos(inicios, de, ate - de) if ate > de else []:
            duracao = abjad.Duration(d.numerator, d.denominator)
            colocar(abjad.Rest.from_duration(duracao) if preencher_com_pausa else abjad.Skip.from_duration(duracao),
                    inicio, [])

    cursor = Fraction(0)
    for k, e in enumerate(eventos):
        lacuna(cursor, e.inicio)
        cadeia = cadeias.get(e.id, [])
        razao = math.prod((Fraction(grupos[g].numerador, grupos[g].denominador) for g in cadeia), start=Fraction(1))
        seguinte = eventos[k + 1] if k + 1 < len(eventos) else None
        colocar(_folha(e, e.duracao * razao, indicacoes, glissando=isinstance(seguinte, Nota)), e.inicio, cadeia)
        cursor = e.fim
    lacuna(cursor, fim)
    return abjad_voz, folhas


def _folha(e: Nota | Pausa, notada: Fraction, indicacoes: Indicacoes, *, glissando: bool) -> abjad.Leaf:
    duracao = abjad.Duration(notada.numerator, notada.denominator)
    tecnicas = [tecnica(t) for t in e.tecnicas] if isinstance(e, Nota) else []
    try:
        if isinstance(e, Pausa):
            folha: abjad.Leaf = abjad.Rest.from_duration(duracao)
        else:
            nomes, setas = zip(*(_altura(a) for a in e.alturas))
            nomes = tuple(abjad.NamedPitch(n) for n in nomes)
            folha = (abjad.Note.from_duration_and_pitch(duracao, nomes[0]) if len(nomes) == 1
                     else abjad.Chord.from_duration_and_pitches(duracao, list(nomes)))
            for seta in filter(None, setas):
                # shortcut: oitavo de tom como seta acima da nota; evoluir para acidente com seta (Ekmelily)
                abjad.attach(abjad.Markup(rf"\markup \tiny {seta}"), folha, direction=abjad.UP)
    except abjad.AssignabilityError as exc:
        raise ValueError(f"evento {e.id}: duração notada {notada} não é figura simples nem pontuada") from exc
    italicos, livres, dinamica = indicacoes.proximo(tecnicas, isinstance(e, Nota), e.texto, e.dinamica)
    for texto in italicos:
        abjad.attach(abjad.Markup(rf"\markup \italic {_texto(texto)}"), folha, direction=abjad.UP)
    for texto in livres:
        abjad.attach(abjad.Markup(rf"\markup {_texto(texto)}"), folha, direction=abjad.UP)
    if dinamica:
        abjad.attach(abjad.Dynamic(dinamica), folha)
    if isinstance(e, Nota):
        if notehead := next((_NOTEHEAD.get(t.notehead) for t in tecnicas if t.notehead), None):
            abjad.override(folha).NoteHead.style = f"#'{notehead}"
        if any(t.tremolo for t in tecnicas):
            abjad.attach(abjad.StemTremolo(32), folha)
        for t in tecnicas:
            nome = _ARTICULACAO.get(t.articulacao or "") or _TECHNICAL.get(t.technical or "")
            if nome:
                abjad.attach(abjad.Articulation(nome), folha)
        if e.glissando and glissando:  # glissando só entre duas notas
            abjad.attach(abjad.Glissando(), folha)
    return folha


def _altura(a) -> tuple[str, str | None]:
    """Nome LilyPond (inglês) e seta de oitavo de tom, se houver."""
    letra, alter, oitava = grafia(a)
    seta = None
    if (alter * 2).denominator != 1:  # oitavo de tom: escreve o quarto de tom abaixo + seta para cima
        alter -= Fraction(1, 4)
        seta = r"\arrow-head #Y #UP ##f"
    marcas = "'" * (oitava - 3) if oitava >= 3 else "," * (3 - oitava)
    return f"{letra.lower()}{_ACIDENTE[alter]}{marcas}", seta


def _texto(s: str) -> str:
    """String LilyPond entre aspas, sem quebrar a sintaxe."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


# --- LilyPond externo --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Lilypond:
    executavel: str
    versao: tuple[int, int, int]


class Executor(Protocol):
    """Roda um processo; levanta subprocess.TimeoutExpired / CalledProcessError / OSError."""

    def __call__(self, args: list[str], cwd: str, timeout: float) -> str: ...


def executar(args: list[str], cwd: str, timeout: float) -> str:
    feito = subprocess.run(args, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=True)
    return feito.stdout + feito.stderr


def detectar(configurado: str | None = None, executor: Executor = executar) -> Lilypond | None:
    """LilyPond em LILYPOND, no PATH ou na instalação padrão do Windows, com versão compatível com o Abjad."""
    padrao = sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\lilypond-*\bin\lilypond.exe")))
    candidatos = [configurado, shutil.which("lilypond"), *reversed(padrao)]
    for caminho in filter(None, candidatos):
        try:
            saida = executor([caminho, "--version"], tempfile.gettempdir(), 30)
        except (OSError, subprocess.SubprocessError):
            continue
        if m := re.search(r"GNU LilyPond (\d+)\.(\d+)\.(\d+)", saida):
            versao = (int(m[1]), int(m[2]), int(m[3]))
            if versao >= VERSAO_MINIMA:
                return Lilypond(caminho, versao)
    return None


@dataclass(frozen=True)
class Gravacao:
    svgs: list[str] = field(default_factory=list)  # páginas da grade
    pdfs: dict[str, bytes] = field(default_factory=dict)  # mesma chave de `para_lilypond`
    aviso: str | None = None  # por que não houve gravação (a UI cai para o Verovio)


LIMITE_BYTES = 20_000_000


def gravar(fontes: dict[str, str], lilypond: Lilypond | None, *, executor: Executor = executar,
           timeout: float = 120) -> Gravacao:
    """PDF de cada fonte e SVG da grade, numa pasta temporária apagada no fim. Nunca levanta: falha vira aviso."""
    if lilypond is None:
        return Gravacao(aviso=AVISO_SEM_LILYPOND)
    # ignore_cleanup_errors: no Windows um filho do LilyPond morto por timeout pode segurar arquivos por um instante
    with tempfile.TemporaryDirectory(prefix="muschat-ly-", ignore_cleanup_errors=True) as pasta:
        nomes = {chave: f"f{k}" for k, chave in enumerate(fontes)}  # nomes seguros: nada vem do usuário
        # sem -dsafe (removido no 2.26): o .ly é gerado pelo projeto e todo texto livre vai entre aspas escapadas
        base = [lilypond.executavel, "-dno-point-and-click"]
        try:
            for chave, texto in fontes.items():
                Path(pasta, f"{nomes[chave]}.ly").write_text(texto, encoding="utf-8")
            executor([*base, "--pdf", *(f"{n}.ly" for n in nomes.values())], pasta, timeout)
            executor([*base, "-dbackend=svg", "--svg", f"{nomes['grade']}.ly"], pasta, timeout)
        except subprocess.TimeoutExpired:
            return Gravacao(aviso=f"O LilyPond passou de {timeout:g} s e foi interrompido; partitura pelo Verovio.")
        except subprocess.CalledProcessError as exc:
            detalhe = " ".join(str(exc.stderr or exc.output or "").strip().splitlines()[-2:])
            return Gravacao(aviso=f"O LilyPond falhou ({detalhe or exc.returncode}); partitura pelo Verovio.")
        except OSError as exc:
            return Gravacao(aviso=f"Não foi possível executar o LilyPond ({exc}); partitura pelo Verovio.")
        pdfs, saidas = {}, sorted(Path(pasta).glob(f"{nomes['grade']}*.svg"),  # f0-2 antes de f0-10
                                  key=lambda s: int(m[1]) if (m := re.search(r"-(\d+)$", s.stem)) else 0)
        for chave, nome in nomes.items():
            pdf = Path(pasta, f"{nome}.pdf")
            if not pdf.is_file() or pdf.stat().st_size > LIMITE_BYTES:
                return Gravacao(aviso="O LilyPond não produziu um PDF válido; partitura pelo Verovio.")
            pdfs[chave] = pdf.read_bytes()
        svgs = [s.read_text(encoding="utf-8") for s in saidas if s.stat().st_size <= LIMITE_BYTES]
        if not svgs:
            return Gravacao(aviso="O LilyPond não produziu o SVG da grade; partitura pelo Verovio.")
        return Gravacao(svgs, pdfs)

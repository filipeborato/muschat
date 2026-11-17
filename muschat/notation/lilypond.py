"""ScoreIR -> Abjad -> LilyPond text (score and parts); external LilyPond -> PDF/SVG, with timeout and temporary directory.

Text is produced by Abjad without invoking LilyPond; only `engrave` runs the binary. Electronic events, control curves,
and spectral data have no staff notation and are omitted, just as in MusicXML.
"""

import glob
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

from muschat.instruments import instrument as get_instrument, transposition
from muschat.notation.spelling import spell_pitch
from muschat.notation.timeline import timeline
from muschat.notation.techniques import VoiceDirections, get_technique
from muschat.score_ir import Measure, Note, Part, Pitch, Rest, ScoreIR, Voice

MIN_VERSION = (2, 25, 26)  # required by Abjad 3.31
_CLEFS = {"treble": "treble", "bass": "bass", "alto": "alto", "tenor": "tenor", "percussion": "percussion"}
_ACCIDENTALS = {
    Fraction(0): "",
    Fraction(1, 2): "qs",
    Fraction(1): "s",
    Fraction(3, 2): "tqs",
    Fraction(2): "ss",
    Fraction(-1, 2): "qf",
    Fraction(-1): "f",
    Fraction(-3, 2): "tqf",
    Fraction(-2): "ff",
}
_NOTEHEADS = {"x": "cross", "diamond": "harmonic", "circle-x": "xcircle", "triangle": "triangle"}
_ARTICULATIONS = {
    "accent": "accent",
    "strong-accent": "marcato",
    "staccato": "staccato",
    "staccatissimo": "staccatissimo",
    "tenuto": "tenuto",
}
_TECHNICAL = {"snap-pizzicato": "snappizzicato", "stopped": "stopped", "open": "open"}
MISSING_LILYPOND_WARNING = (
    "Partitura pelo Verovio (MusicXML): LilyPond não encontrado, então não há PDF da grade nem das partes, e "
    "oitavos de tom saem como quartos de tom com seta. Para a gravação profissional, instale o LilyPond "
    f"{'.'.join(map(str, MIN_VERSION))} ou mais novo (lilypond.org) e coloque-o no PATH, ou defina LILYPOND com "
    "o caminho do executável."
)


# --- ScoreIR -> LilyPond text -----------------------------------------------------------------------------------------


def to_lilypond(ir: ScoreIR, *, transposed: bool = False) -> dict[str, str]:
    """Standalone `.ly` sources: full score in concert pitch by default and individual parts always transposed."""
    sources = {"score": _file(ir, ir.parts, ir.title, transposed=transposed)}
    for part in ir.parts:
        sources[f"{part.instrument} ({part.id})"] = _file(
            ir, [part], ir.title, subtitle=part.instrument, transposed=True
        )
    return sources


def _file(
    ir: ScoreIR,
    parts: list[Part],
    title: str,
    subtitle: str | None = None,
    *,
    transposed: bool = False,
) -> str:
    staves = [_staff(ir, p, with_tempo=k == 0, transposed=transposed) for k, p in enumerate(parts)]
    score = abjad.Score([abjad.StaffGroup(staves)] if len(staves) > 1 else staves)
    header = f"  title = {_quote(title)}\n" + (f"  subtitle = {_quote(subtitle)}\n" if subtitle else "")
    return (
        f'\\version "{".".join(map(str, MIN_VERSION))}"\n\\language "english"\n\n'
        f"\\header {{\n{header}  tagline = ##f\n}}\n\n"
        f"\\score {{\n{abjad.lilypond(score)}\n  \\layout {{ }}\n}}\n"
    )


def _staff(ir: ScoreIR, part: Part, *, with_tempo: bool, transposed: bool) -> abjad.Staff:
    interval = transposition(part.instrument) if transposed else (0, 0, 0)
    voices = [v for v in part.voices if any(isinstance(e, (Note, Rest)) for e in v.events)] or part.voices[:1]
    offsets = ir.measure_starts()
    end = offsets[-1] + ir.measures[-1].length
    written = [_voice(v, offsets, end, fill_with_rests=k == 0, interval=interval) for k, v in enumerate(voices)]
    staff = abjad.Staff([voice for voice, _ in written], name=part.id, simultaneous=len(written) > 1)
    if len(written) > 1:
        for k, (voice, _) in enumerate(written):
            abjad.attach(
                abjad.LilyPondLiteral(rf"\voice{['One', 'Two', 'Three', 'Four'][min(k, 3)]}"),
                abjad.select.leaf(voice, 0),
            )
    first_leaves = written[0][1]
    leaf = first_leaves[Fraction(0)]
    written_clef = get_instrument(part.instrument).written_clef if any(interval) else None
    abjad.attach(abjad.Clef(_CLEFS[written_clef or part.clef]), leaf)
    if any(interval):
        sounding_c, _ = _pitch_string(Pitch(midicents=6000, spelling="C"), tuple(-n for n in interval))
        abjad.attach(abjad.LilyPondLiteral(rf"\transposition {sounding_c}"), leaf)
    abjad.attach(abjad.InstrumentName(rf"\markup {_quote(part.instrument)}"), leaf)
    previous: Measure | None = None
    for offset, m in zip(offsets, ir.measures):
        leaf = first_leaves[offset]
        if previous is None or (m.numerator, m.denominator, m.duration) != (
            previous.numerator,
            previous.denominator,
            previous.duration,
        ):
            abjad.attach(abjad.TimeSignature(_meter(m)), leaf)
        if with_tempo and m.tempo:
            abjad.attach(abjad.MetronomeMark(abjad.Duration(1, 4), m.tempo), leaf)
        previous = m
    return staff


def _meter(m: Measure) -> tuple[int, int]:
    if m.duration is None:
        return m.numerator, m.denominator
    d = m.duration if m.duration.denominator >= m.denominator else m.duration.limit_denominator(m.denominator)
    scale = max(1, m.denominator // d.denominator)
    return d.numerator * scale, d.denominator * scale


def _voice(
    voice: Voice,
    offsets: list[Fraction],
    end: Fraction,
    *,
    fill_with_rests: bool,
    interval: tuple[int, int, int],
) -> tuple[abjad.Voice, dict[Fraction, abjad.Leaf]]:
    groups = {t.id: t for t in voice.tuplets}
    abjad_voice = abjad.Voice(name=voice.id)
    stack: list[tuple[str, abjad.Tuplet]] = []
    leaves: dict[Fraction, abjad.Leaf] = {}
    directions = VoiceDirections()

    def place(leaf: abjad.Leaf, offset: Fraction, chain: list[str]) -> None:
        while stack and [g for g, _ in stack] != chain[: len(stack)]:
            stack.pop()
        for g in chain[len(stack) :]:
            tuplet = abjad.Tuplet(f"{groups[g].numerator}:{groups[g].denominator}", [])
            (stack[-1][1] if stack else abjad_voice).append(tuplet)
            stack.append((g, tuplet))
        (stack[-1][1] if stack else abjad_voice).append(leaf)
        leaves.setdefault(offset, leaf)

    for item in timeline(voice, offsets, end):
        e = item.event
        chain = [g.id for g in item.chain]
        if e is None:
            notated = item.duration * item.ratio
            duration = abjad.Duration(notated.numerator, notated.denominator)
            place(abjad.Rest.from_duration(duration) if fill_with_rests else abjad.Skip.from_duration(duration), item.start, chain)
        else:
            place(_leaf(e, item.duration * item.ratio, directions, interval=interval,
                        glissando=item.glissando_start, tied=item.tie_start), item.start, chain)
    return abjad_voice, leaves


def _leaf(
    e: Note | Rest,
    notated: Fraction,
    directions: VoiceDirections,
    *,
    glissando: bool,
    tied: bool,
    interval: tuple[int, int, int],
) -> abjad.Leaf:
    duration = abjad.Duration(notated.numerator, notated.denominator)
    techniques = [get_technique(t) for t in e.techniques] if isinstance(e, Note) else []
    try:
        if isinstance(e, Rest):
            leaf: abjad.Leaf = abjad.Rest.from_duration(duration)
        else:
            names, arrows = zip(*(_pitch_string(p, interval) for p in e.pitches))
            named_pitches = tuple(abjad.NamedPitch(n) for n in names)
            leaf = (
                abjad.Note.from_duration_and_pitch(duration, named_pitches[0])
                if len(named_pitches) == 1
                else abjad.Chord.from_duration_and_pitches(duration, list(named_pitches))
            )
            for arrow in filter(None, arrows):
                abjad.attach(abjad.Markup(rf"\markup \tiny {arrow}"), leaf, direction=abjad.UP)
    except abjad.AssignabilityError as exc:
        raise ValueError(f"event {e.id}: notated duration {notated} is neither a simple nor a dotted note value") from exc
    italics, free_texts, dynamic = directions.next_event(techniques, isinstance(e, Note), e.text, e.dynamic)
    for text in italics:
        abjad.attach(abjad.Markup(rf"\markup \italic {_quote(text)}"), leaf, direction=abjad.UP)
    for text in free_texts:
        abjad.attach(abjad.Markup(rf"\markup {_quote(text)}"), leaf, direction=abjad.UP)
    if dynamic:
        abjad.attach(abjad.Dynamic(dynamic), leaf)
    if isinstance(e, Note):
        if notehead := next((_NOTEHEADS.get(t.notehead) for t in techniques if t.notehead), None):
            abjad.override(leaf).NoteHead.style = f"#'{notehead}"
        if any(t.tremolo for t in techniques):
            abjad.attach(abjad.StemTremolo(32), leaf)
        for t in techniques:
            name = _ARTICULATIONS.get(t.articulation or "") or _TECHNICAL.get(t.technical or "")
            if name:
                abjad.attach(abjad.Articulation(name), leaf)
        if e.glissando and glissando:
            abjad.attach(abjad.Glissando(), leaf)
        if tied:
            abjad.attach(abjad.Tie(), leaf)
    return leaf


def _pitch_string(pitch: Pitch, interval: tuple[int, int, int] = (0, 0, 0)) -> tuple[str, str | None]:
    """LilyPond pitch name (English) and eighth-tone arrow markup if present."""
    letter, alter, octave = spell_pitch(pitch, interval)
    arrow = None
    if (alter * 2).denominator != 1:
        alter -= Fraction(1, 4)
        arrow = r"\arrow-head #Y #UP ##f"
    marks = "'" * (octave - 3) if octave >= 3 else "," * (3 - octave)
    return f"{letter.lower()}{_ACCIDENTALS[alter]}{marks}", arrow


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


# --- External LilyPond ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LilyPondInstallation:
    executable: str
    version: tuple[int, int, int]


class ProcessRunner(Protocol):
    """Run an external process; raises subprocess.TimeoutExpired / CalledProcessError / OSError."""

    def __call__(self, args: list[str], cwd: str, timeout: float) -> str: ...


def run_process(args: list[str], cwd: str, timeout: float) -> str:
    done = subprocess.run(args, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=True)
    return done.stdout + done.stderr


def detect_lilypond(
    configured: str | None = None,
    runner: ProcessRunner = run_process,
) -> LilyPondInstallation | None:
    """Locate LilyPond in `configured` (`LILYPOND`), `PATH`, or the default Windows install path."""
    default_paths = sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\lilypond-*\bin\lilypond.exe")))
    candidates = [configured, shutil.which("lilypond"), *reversed(default_paths)]
    for path in filter(None, candidates):
        try:
            output = runner([path, "--version"], tempfile.gettempdir(), 30)
        except (OSError, subprocess.SubprocessError):
            continue
        if m := re.search(r"GNU LilyPond (\d+)\.(\d+)\.(\d+)", output):
            ver = (int(m[1]), int(m[2]), int(m[3]))
            if ver >= MIN_VERSION:
                return LilyPondInstallation(path, ver)
    return None


@dataclass(frozen=True)
class EngravingResult:
    svgs: list[str] = field(default_factory=list)
    pdfs: dict[str, bytes] = field(default_factory=dict)
    warning: str | None = None


MAX_OUTPUT_BYTES = 20_000_000


def engrave(
    sources: dict[str, str],
    lilypond: LilyPondInstallation | None,
    *,
    runner: ProcessRunner = run_process,
    timeout: float = 120,
) -> EngravingResult:
    """Produce a PDF for each source and SVGs for the full score in a temporary directory. Never raises."""
    if lilypond is None:
        return EngravingResult(warning=MISSING_LILYPOND_WARNING)
    score_key = "score"
    with tempfile.TemporaryDirectory(prefix="muschat-ly-", ignore_cleanup_errors=True) as tmp_dir:
        names = {key: f"f{k}" for k, key in enumerate(sources)}
        base = [lilypond.executable, "-dno-point-and-click"]
        try:
            for key, text in sources.items():
                Path(tmp_dir, f"{names[key]}.ly").write_text(text, encoding="utf-8")
            runner([*base, "--pdf", *(f"{n}.ly" for n in names.values())], tmp_dir, timeout)
            runner([*base, "-dbackend=svg", "--svg", f"{names[score_key]}.ly"], tmp_dir, timeout)
        except subprocess.TimeoutExpired:
            return EngravingResult(
                warning=f"O LilyPond passou de {timeout:g} s e foi interrompido; partitura pelo Verovio."
            )
        except subprocess.CalledProcessError as exc:
            detail = " ".join(str(exc.stderr or exc.output or "").strip().splitlines()[-2:])
            return EngravingResult(warning=f"O LilyPond falhou ({detail or exc.returncode}); partitura pelo Verovio.")
        except OSError as exc:
            return EngravingResult(warning=f"Não foi possível executar o LilyPond ({exc}); partitura pelo Verovio.")
        pdfs = {}
        outputs = sorted(
            Path(tmp_dir).glob(f"{names[score_key]}*.svg"),
            key=lambda s: int(m[1]) if (m := re.search(r"-(\d+)$", s.stem)) else 0,
        )
        for key, name in names.items():
            pdf = Path(tmp_dir, f"{name}.pdf")
            if not pdf.is_file() or pdf.stat().st_size > MAX_OUTPUT_BYTES:
                return EngravingResult(warning="O LilyPond não produziu um PDF válido; partitura pelo Verovio.")
            pdfs[key] = pdf.read_bytes()
        svgs = [s.read_text(encoding="utf-8") for s in outputs if s.stat().st_size <= MAX_OUTPUT_BYTES]
        if not svgs:
            return EngravingResult(warning="O LilyPond não produziu o SVG da grade; partitura pelo Verovio.")
        return EngravingResult(svgs, pdfs)

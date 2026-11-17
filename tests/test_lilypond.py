import re
import subprocess
from fractions import Fraction as F
from pathlib import Path

import pytest

from muschat.notation.lilypond import (
    MISSING_LILYPOND_WARNING,
    LilyPondInstallation,
    detect_lilypond,
    engrave,
    to_lilypond,
)
from muschat.score_ir import Measure, Note, Part, Pitch, Rest, ScoreIR, Tuplet, Voice
from tests.examples import LLM, quartet


def _solo(events, tuplets=(), measures=1):
    return ScoreIR(
        title='Solo "1"',
        measures=[Measure(numerator=4, denominator=4)] * measures,
        parts=[Part(id="P1", instrument="Cello", clef="bass", voices=[Voice(id="V", events=events, tuplets=list(tuplets))])],
    )


def _note(i, start, duration, mc, **kw):
    return Note(id=i, start=start, duration=duration, pitches=[Pitch(midicents=mc)], provenance=LLM, **kw)


def test_tuplet_quarter_tone_harmonic_and_glissando_in_lilypond_text():
    ly = to_lilypond(
        _solo(
            [
                _note("a", F(0), F(1, 6), 4850, techniques=["harmonic"], glissando=True, dynamic="pp"),
                _note("b", F(1, 6), F(1, 6), 5500),
                _note("c", F(1, 3), F(1, 6), 4825),  # eighth-tone
                _note("d", F(1, 2), F(1, 2), 4800, techniques=["sul ponticello", "staccato"], text="come un respiro"),
            ],
            [Tuplet(id="q", numerator=3, denominator=2, events=["a", "b", "c"])],
        )
    )["score"]
    assert r"\tuplet 3/2" in ly
    assert "cqs4" in ly or "cqs8" in ly
    assert "NoteHead.style = #'harmonic" in ly and r"\glissando" in ly and r"\pp" in ly
    assert r"\arrow-head #Y #UP" in ly
    assert r'\italic "sul pont."' in ly and r"\staccato" in ly and '"come un respiro"' in ly
    assert r'\clef "bass"' in ly and 'title = "Solo \\"1\\""' in ly


def test_score_and_parts_per_instrument_with_polyphony():
    sources = to_lilypond(quartet())
    assert set(sources) == {"score", "Violin (vln)", "Cello (vc)"}
    assert r"\new StaffGroup" in sources["score"] and sources["score"].count(r"\context Staff") == 2
    cello = sources["Cello (vc)"]
    assert r"\voiceOne" not in cello  # curve and spectral frame do not become notated voices
    assert 'subtitle = "Cello"' in cello and "Violin" not in cello
    assert r"\tempo 4=60" in sources["score"] and r"\time 4/4" in sources["score"]


def test_gaps_become_barline_split_rests():
    ly = to_lilypond(_solo([_note("a", F(3, 4), F(1, 4), 4800), _note("b", F(3, 2), F(1, 2), 4800)], measures=2))[
        "score"
    ]
    assert ly.index("r2.") < ly.index("c4") < ly.index("r2\n")


def test_unrepresentable_duration_raises_value_error():
    with pytest.raises(ValueError, match="duration"):
        to_lilypond(
            _solo([_note("a", F(0), F(5, 8), 4800), Rest(id="b", start=F(5, 8), duration=F(3, 8), provenance=LLM)])
        )


# --- engraving ---------------------------------------------------------------------------------------------------------

LP = LilyPondInstallation("lilypond", (2, 26, 0))
SOURCES = {"score": "{ c'4 }", "P1": "{ c'4 }"}


class FakeRunner:
    def __init__(self, error=None, write=True):
        self.error, self.write, self.calls = error, write, []

    def __call__(self, args, cwd, timeout):
        self.calls.append(args)
        if self.error:
            raise self.error
        if self.write:
            for a in args:
                if a.endswith(".ly"):
                    dest = Path(cwd, a[:-3] + (".svg" if "--svg" in args else ".pdf"))
                    dest.write_bytes(b"<svg/>" if "--svg" in args else b"%PDF-1.7")
        return ""


def test_engraving_returns_pdf_per_source_and_score_svg():
    runner = FakeRunner()
    res = engrave(SOURCES, LP, runner=runner)
    assert res.warning is None and res.pdfs == {"score": b"%PDF-1.7", "P1": b"%PDF-1.7"} and res.svgs == ["<svg/>"]
    assert runner.calls[0][-2:] == ["f0.ly", "f1.ly"] and "--svg" in runner.calls[1]


def test_without_lilypond_falls_back_to_verovio_with_install_warning():
    res = engrave(SOURCES, None)
    assert res.svgs == [] and res.warning == MISSING_LILYPOND_WARNING and "LilyPond" in res.warning


@pytest.mark.parametrize(
    "error,snippet",
    [
        (subprocess.TimeoutExpired("lilypond", 120), "120 s"),
        (subprocess.CalledProcessError(1, "lilypond", stderr="syntax error on line 3"), "line 3"),
        (FileNotFoundError("lilypond"), "executar"),
    ],
)
def test_external_process_failure_becomes_friendly_warning(error, snippet):
    res = engrave(SOURCES, LP, runner=FakeRunner(error))
    assert res.svgs == [] and res.warning is not None and snippet in res.warning


def test_missing_output_becomes_warning():
    res = engrave(SOURCES, LP, runner=FakeRunner(write=False))
    assert res.warning is not None and "PDF" in res.warning


def test_detect_accepts_compatible_version_and_rejects_old():
    def version_runner(text):
        return lambda args, cwd, timeout: text

    detected = detect_lilypond("lp", runner=version_runner("GNU LilyPond 2.26.0 (running Guile 3.0)"))
    assert detected is not None and detected.version == (2, 26, 0)
    assert detect_lilypond("lp", runner=version_runner("GNU LilyPond 2.24.4")) is None
    assert detect_lilypond("lp", runner=version_runner("not lilypond")) is None


def test_two_voice_part_uses_voice_one_and_voice_two():
    ir = _solo([_note("a", F(0), F(1), 6000)])
    second = Voice(id="V2", events=[_note("b", F(1, 2), F(1, 2), 5500)])
    ir = ir.model_copy(update={"parts": [ir.parts[0].model_copy(update={"voices": [*ir.parts[0].voices, second]})]})
    ly = to_lilypond(ir)["score"]
    assert "voiceOne" in ly and "voiceTwo" in ly and "s2" in ly


_REAL = detect_lilypond()


@pytest.mark.skipif(_REAL is None, reason="LilyPond >= 2.25.26 not detected")
def test_real_compilation_of_score_and_parts():
    res = engrave(to_lilypond(quartet()), _REAL)
    assert res.warning is None, res.warning
    assert set(res.pdfs) == {"score", "Violin (vln)", "Cello (vc)"} and all(
        p.startswith(b"%PDF") for p in res.pdfs.values()
    )
    assert res.svgs and "<svg" in res.svgs[0]


def test_tie_crosses_barline_and_omits_different_pitches():
    ly = to_lilypond(
        _solo(
            [
                _note("a", F(0), F(1, 2), 4800),
                _note("b", F(1, 2), F(1, 2), 5000, tied=True),
                _note("c", F(1), F(1, 2), 5000, tied=True),  # tied to different pitch: omitted
                _note("d", F(3, 2), F(1, 2), 5200),
            ],
            measures=2,
        )
    )["score"]
    assert ly.count("~") == 1
    assert re.search(r"d2\s*~\s*d2\s+e2", ly)


@pytest.mark.parametrize(
    "name,note,declaration,clef",
    [
        ("Clarinet", "dqs'1", r"\transposition bf", "treble"),
        ("Bass Clarinet", "dqs''1", r"\transposition bf,", "treble"),
        ("Double Bass", "cqs''1", r"\transposition c", "bass"),
        ("Piccolo", "cqs1", r"\transposition c''", "treble"),
        ("Invented Instrument", "cqs'1", None, "bass"),
    ],
)
def test_transposed_parts_and_optional_transposed_score_without_mutating_ir(name, note, declaration, clef):
    ir = _solo([_note("a", F(0), F(1), 6050)])
    ir.parts[0].instrument = name
    if name in ("Clarinet", "Piccolo"):
        ir.parts[0].clef = "treble"
    before = ir.model_dump()
    sources = to_lilypond(ir)
    part = sources[f"{name} (P1)"]
    assert "cqs'1" in sources["score"]
    assert note in part
    assert rf'\clef "{clef}"' in part
    if declaration:
        assert declaration + "\n" in part
        assert r"\transposition" not in sources["score"]
    assert note in to_lilypond(ir, transposed=True)["score"]
    assert ir.model_dump() == before


def test_extreme_spelling_transposition_produces_readable_accidental():
    ir = _solo([_note("a", F(0), F(1), 6600)])
    ir.parts[0].instrument = "Clarinet"
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note)
    note.pitches[0].spelling = "E"
    assert "gs'1" in to_lilypond(ir)["Clarinet (P1)"]


@pytest.mark.skipif(_REAL is None, reason="LilyPond >= 2.25.26 not detected")
def test_real_transposed_compilation_with_microtonal_chord_and_multiple_parts():
    ir = quartet()
    ir.parts[0].instrument = "Clarone"
    ir.parts[1].instrument = "Baritone saxophone"
    note = ir.parts[0].voices[0].events[0]
    assert isinstance(note, Note)
    note.pitches.append(Pitch(midicents=6025, spelling="C"))
    res = engrave(to_lilypond(ir, transposed=True), _REAL)
    assert res.warning is None, res.warning
    assert len(res.pdfs) == 3 and res.svgs

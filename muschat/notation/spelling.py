"""Shared sounding/written pitch and harmonic-series spelling."""

from fractions import Fraction
from muschat.score_ir import Pitch, PitchLetter, PITCH_CLASS, _DEFAULT_LETTERS
from muschat.spectrum import partials


def spell_pitch(
    pitch: Pitch, interval: tuple[int, int, int] = (0, 0, 0)
) -> tuple[str, Fraction, int]:
    """Return (step, alteration in semitones, octave); preserves `pitch.spelling` when within 2 semitones."""
    n = Fraction(pitch.midicents, 100)
    letter: str | None = pitch.spelling
    if letter is not None:
        octave = round((n - PITCH_CLASS[letter]) / 12) - 1
        if abs(n - 12 * (octave + 1) - PITCH_CLASS[letter]) > 2:
            letter = None
    if letter is None:
        semitone = round(Fraction(pitch.midicents, 50)) * 50 // 100
        letter, octave = _DEFAULT_LETTERS[semitone % 12], semitone // 12 - 1
    diatonic, chromatic, octaves = interval
    octave, index = divmod(
        7 * octave + "CDEFGAB".index(letter) - diatonic - 7 * octaves, 7
    )
    letter = "CDEFGAB"[index]
    n -= chromatic + 12 * octaves
    if abs(n - 12 * (octave + 1) - PITCH_CLASS[letter]) > 2:
        semitone = round(n * 2) // 2
        letter, octave = _DEFAULT_LETTERS[semitone % 12], semitone // 12 - 1
    return letter, n - 12 * (octave + 1) - PITCH_CLASS[letter], octave


# Relative scale degrees: 1..16 conventional; 17..32 by proximity to just-intonation scale degrees.
_DEGREES = (
    0,
    0,
    4,
    0,
    2,
    4,
    6,
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    6,
    0,
    1,
    1,
    2,
    2,
    3,
    3,
    4,
    4,
    4,
    5,
    5,
    6,
    6,
    6,
    6,
    0,
)


def partial_spellings(
    fundamental: float, count: int, microtone: int
) -> list[PitchLetter]:
    """Return pitch letters for partials 1..32 relative to the quantized fundamental's pitch letter."""
    if not 1 <= count <= len(_DEGREES):
        raise ValueError("spectral spelling requires between 1 and 32 partials")
    letters: tuple[PitchLetter, ...] = ("C", "D", "E", "F", "G", "A", "B")
    base = letters.index(
        spell_pitch(Pitch(midicents=partials(fundamental, 1, microtone)[0]))[0]
    )  # type: ignore[arg-type]
    return [letters[(base + degree) % 7] for degree in _DEGREES[:count]]

"""Canonical instrument table: family (linked to techniques), clef, and sounding range in midicents.

ScoreIR stores sounding pitches: transposing instruments (clarinets, horns, saxophones, double bass, piccolo...)
are stored at sounding pitch, and piano, harp, marimba, and celesta use a single staff.
# shortcut: single staff; evolve with dual-staff support in Part when required by notation.
"""

import unicodedata
from dataclasses import dataclass
from typing import Literal

from muschat.score_ir import Clef, Part

Family = Literal["strings", "woodwinds", "brass", "percussion", "keyboard", "harp", "guitar", "voice"]
FAMILIES: tuple[Family, ...] = ("strings", "woodwinds", "brass", "percussion", "keyboard", "harp", "guitar", "voice")
MIDDLE_LINE = 7100  # unpitched percussion: B4, middle staff line


@dataclass(frozen=True)
class Instrument:
    family: Family
    clef: Clef
    range: tuple[int, int]
    transposition: tuple[int, int, int] = (0, 0, 0)  # diatonic, chromatic, octaves: written -> sounding
    written_clef: Clef | None = None
    initial_register: tuple[int, int] | None = None  # generation preference; does not restrict explicit transforms

    @property
    def pitched(self) -> bool:
        return self.range[0] != self.range[1]


def _unpitched() -> Instrument:
    return Instrument("percussion", "percussion", (MIDDLE_LINE, MIDDLE_LINE))


INSTRUMENTS: dict[str, Instrument] = {
    # bowed strings
    "violin": Instrument("strings", "treble", (5500, 10300), initial_register=(5500, 8400)),
    "viola": Instrument("strings", "alto", (4800, 9100), initial_register=(4800, 7200)),
    "cello": Instrument("strings", "bass", (3600, 8100), initial_register=(3600, 6000)),
    "double bass": Instrument("strings", "bass", (2800, 6700), (0, 0, -1)),
    # woodwinds
    "piccolo": Instrument("woodwinds", "treble", (7400, 10800), (0, 0, 1)),
    "flute": Instrument("woodwinds", "treble", (6000, 9800)),
    "alto flute": Instrument("woodwinds", "treble", (5500, 9100), (-3, -5, 0)),
    "bass flute": Instrument("woodwinds", "treble", (4800, 8400), (0, 0, -1)),
    "oboe": Instrument("woodwinds", "treble", (5800, 9300)),
    "oboe d'amore": Instrument("woodwinds", "treble", (5600, 8800), (-2, -3, 0)),
    "english horn": Instrument("woodwinds", "treble", (5200, 8400), (-4, -7, 0)),
    "eb clarinet": Instrument("woodwinds", "treble", (5500, 9500), (2, 3, 0)),
    "clarinet": Instrument("woodwinds", "treble", (5000, 9400), (-1, -2, 0)),
    "bass clarinet": Instrument("woodwinds", "bass", (3400, 7700), (-1, -2, -1), "treble"),
    "contrabass clarinet": Instrument("woodwinds", "bass", (2200, 5800), (-1, -2, -2), "treble"),
    "bassoon": Instrument("woodwinds", "bass", (3400, 7600)),
    "contrabassoon": Instrument("woodwinds", "bass", (2200, 5500), (0, 0, -1)),
    "soprano saxophone": Instrument("woodwinds", "treble", (5600, 8800), (-1, -2, 0)),
    "alto saxophone": Instrument("woodwinds", "treble", (4900, 8100), (-5, -9, 0)),
    "tenor saxophone": Instrument("woodwinds", "bass", (4400, 7600), (-1, -2, -1), "treble"),
    "baritone saxophone": Instrument("woodwinds", "bass", (3600, 6900), (-5, -9, -1), "treble"),
    # brass
    "horn": Instrument("brass", "bass", (3500, 7700), (-4, -7, 0), "treble"),
    "trumpet": Instrument("brass", "treble", (5400, 8600), (-1, -2, 0)),
    "piccolo trumpet": Instrument("brass", "treble", (6100, 9300), (6, 10, 0)),
    "cornet": Instrument("brass", "treble", (5400, 8400), (-1, -2, 0)),
    "flugelhorn": Instrument("brass", "treble", (5200, 8100), (-1, -2, 0)),
    "trombone": Instrument("brass", "bass", (4000, 7700)),
    "bass trombone": Instrument("brass", "bass", (3400, 7000)),
    "euphonium": Instrument("brass", "bass", (3400, 7000)),
    "tuba": Instrument("brass", "bass", (2600, 6500)),
    # pitched percussion
    "timpani": Instrument("percussion", "bass", (3800, 6000)),
    "marimba": Instrument("percussion", "treble", (3600, 9600)),
    "xylophone": Instrument("percussion", "treble", (6500, 10800), (0, 0, 1)),
    "vibraphone": Instrument("percussion", "treble", (5300, 8900)),
    "glockenspiel": Instrument("percussion", "treble", (7900, 10800), (0, 0, 2)),
    "crotales": Instrument("percussion", "treble", (8400, 10800), (0, 0, 2)),
    "tubular bells": Instrument("percussion", "treble", (6000, 7700)),
    # unpitched percussion
    "snare drum": _unpitched(),
    "bass drum": _unpitched(),
    "cymbals": _unpitched(),
    "suspended cymbal": _unpitched(),
    "tam-tam": _unpitched(),
    "gong": _unpitched(),
    "triangle": _unpitched(),
    "tom-toms": _unpitched(),
    "bongos": _unpitched(),
    "congas": _unpitched(),
    "wood block": _unpitched(),
    "temple blocks": _unpitched(),
    "tambourine": _unpitched(),
    "castanets": _unpitched(),
    "shaker": _unpitched(),
    "guiro": _unpitched(),
    "whip": _unpitched(),
    # keyboards
    "piano": Instrument("keyboard", "treble", (2100, 10800)),
    "celesta": Instrument("keyboard", "treble", (6000, 10800), (0, 0, 1)),
    "harpsichord": Instrument("keyboard", "treble", (2900, 8900)),
    "organ": Instrument("keyboard", "treble", (2400, 9600)),
    "accordion": Instrument("keyboard", "treble", (4000, 9300)),
    # plucked strings
    "harp": Instrument("harp", "treble", (2300, 10400)),
    "guitar": Instrument("guitar", "treble", (4000, 8300), (0, 0, -1)),
    "electric guitar": Instrument("guitar", "treble", (4000, 8600), (0, 0, -1)),
    "mandolin": Instrument("guitar", "treble", (5500, 8800)),
    # voices
    "soprano": Instrument("voice", "treble", (6000, 8400)),
    "mezzo-soprano": Instrument("voice", "treble", (5700, 8100)),
    "contralto": Instrument("voice", "treble", (5300, 7700)),
    "countertenor": Instrument("voice", "treble", (5500, 7700)),
    "tenor": Instrument("voice", "treble", (4800, 7200)),
    "baritone": Instrument("voice", "bass", (4300, 6700)),
    "bass": Instrument("voice", "bass", (4000, 6400)),
}

# Bilingual and traditional musical synonyms normalized at the input boundary to canonical English IDs.
_SYNONYMS: dict[str, str] = {
    "clarinete contrabaixo": "contrabass clarinet",
    "clarinete baixo": "bass clarinet",
    "clarinete em mi": "eb clarinet",
    "flauta contralto": "alto flute",
    "flauta baixo": "bass flute",
    "corne ingles": "english horn",
    "corno ingles": "english horn",
    "cor anglais": "english horn",
    "saxofone soprano": "soprano saxophone",
    "sax soprano": "soprano saxophone",
    "saxofone alto": "alto saxophone",
    "sax alto": "alto saxophone",
    "saxofone tenor": "tenor saxophone",
    "sax tenor": "tenor saxophone",
    "saxofone baritono": "baritone saxophone",
    "sax baritono": "baritone saxophone",
    "trompete piccolo": "piccolo trumpet",
    "trombone baixo": "bass trombone",
    "trombone tenor": "trombone",
    "sinos tubulares": "tubular bells",
    "caixa clara": "snare drum",
    "gran cassa": "bass drum",
    "prato suspenso": "suspended cymbal",
    "guitarra eletrica": "electric guitar",
    "mezzo soprano": "mezzo-soprano",
    "corne frances": "horn",
    "french horn": "horn",
    "trompa em": "horn",
    "violoncello": "cello",
    "violoncelo": "cello",
    "violino": "violin",
    "contrabaixo": "double bass",
    "flautim": "piccolo",
    "flauta": "flute",
    "flauto": "flute",
    "requinta": "eb clarinet",
    "clarone": "bass clarinet",
    "clarinete": "clarinet",
    "contrafagote": "contrabassoon",
    "fagote": "bassoon",
    "trompa": "horn",
    "trompete": "trumpet",
    "bombardino": "euphonium",
    "eufonio": "euphonium",
    "timpanos": "timpani",
    "timpano ": "timpani ",
    "timbales": "timpani",
    "xilofone": "xylophone",
    "vibrafone": "vibraphone",
    "vibrafono": "vibraphone",
    "lira": "glockenspiel",
    "crotalos": "crotales",
    "chimes": "tubular bells",
    "campanas": "tubular bells",
    "caixa": "snare drum",
    "bumbo": "bass drum",
    "bombo": "bass drum",
    "pratos": "cymbals",
    "tamtam": "tam-tam",
    "gongo": "gong",
    "triangulo": "triangle",
    "tom-tons": "tom-toms",
    "pandeiro": "tambourine",
    "castanholas": "castanets",
    "chocalho": "shaker",
    "chicote": "whip",
    "cravo": "harpsichord",
    "orgao": "organ",
    "acordeao": "accordion",
    "acordeon": "accordion",
    "harpa": "harp",
    "guitarra": "electric guitar",
    "violao": "guitar",
    "bandolim": "mandolin",
    "contratenor": "countertenor",
    "baritono": "baritone",
    "baixo": "bass",
}


def _normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip() + " "
    for src, dst in sorted(_SYNONYMS.items(), key=lambda kv: len(kv[0]), reverse=True):
        norm_src = unicodedata.normalize("NFKD", src).encode("ascii", "ignore").decode()
        text = text.replace(norm_src, dst)
    return text


def instrument(name: str) -> Instrument:
    return INSTRUMENTS[identify(name)]


def identify(name: str) -> str:
    """Resolve a free-form or bilingual instrument name ("Violino I", "Bass Clarinet in Bb") to a canonical key."""
    normalized = _normalize(name)
    for known in sorted(INSTRUMENTS, key=len, reverse=True):
        if known in normalized:
            return known
    raise ValueError(f"unknown instrument range: {name!r} (known: {', '.join(INSTRUMENTS)})")


def part_range(part: Part) -> tuple[int, int] | None:
    """Return the part's explicit range, or its table range; None when the instrument is not in the table."""
    if part.range:
        return part.range
    try:
        return instrument(part.instrument).range
    except ValueError:
        return None


def in_register(pitch: int, register_range: tuple[int, int] | None) -> int:
    """Shift pitch by octaves until it fits in `register_range`; clamp if the range is narrower than an octave."""
    if register_range is None:
        return pitch
    low, high = register_range
    if high - low < 1200:
        return min(max(pitch, low), high)
    while pitch < low:
        pitch += 1200
    while pitch > high:
        pitch -= 1200
    return pitch


def transposition(name: str) -> tuple[int, int, int]:
    """Return written -> sounding interval; unknown instruments remain at sounding pitch."""
    try:
        return instrument(name).transposition
    except ValueError:
        return (0, 0, 0)

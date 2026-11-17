"""Single table of extended techniques (grimoire vocabulary) shared across notation writers and symbolic procedures."""

from dataclasses import dataclass
from fractions import Fraction

from muschat.instruments import FAMILIES, INSTRUMENTS, Family, identify, part_range
from muschat.score_ir import Note, Part, ScoreIR, locate, normalize_technique


@dataclass(frozen=True)
class Technique:
    text: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False
    articulation: str | None = None  # MusicXML <articulations> tag name
    families: frozenset[Family] = frozenset(FAMILIES)  # playable families; default: any instrument
    only: frozenset[str] = frozenset()  # restricted to specific canonical instrument keys in INSTRUMENTS
    # shortcut: initial idiomatic bounds (register as fraction of instrument range 0=lowest..1=highest,
    # minimum sounding duration, and whether microtonal pitch is playable); refine per instrument as needed
    register: tuple[float, float] = (0.0, 1.0)
    min_duration: Fraction = Fraction(0)
    precise_pitch: bool = True


def _in(*families: Family) -> frozenset[Family]:
    return frozenset(families)


_STRINGS = _in("strings")
_WINDS = _in("woodwinds", "brass")
_BRASS = _in("brass")
_WOODWINDS = _in("woodwinds")
_PERCUSSION = _in("percussion")
_KEYBOARD = _in("keyboard")
_HARP = _in("harp")
_GUITAR = _in("guitar")
_VOICE = _in("voice")
_RESONANT = _in("percussion", "harp", "keyboard", "guitar")
_EIGHTH, _QUARTER = Fraction(1, 8), Fraction(1, 4)

_FLUTES = frozenset({"piccolo", "flute", "alto flute", "bass flute"})
_SINGLE_REED = frozenset(
    {
        "eb clarinet",
        "clarinet",
        "bass clarinet",
        "contrabass clarinet",
        "soprano saxophone",
        "alto saxophone",
        "tenor saxophone",
        "baritone saxophone",
    }
)
_PIANO = frozenset({"piano"})
_DRUMS = frozenset({"snare drum", "tom-toms", "bongos", "congas", "bass drum", "timpani"})
_BOWED_PERC = frozenset({"vibraphone", "crotales", "marimba", "cymbals", "suspended cymbal", "tam-tam", "gong"})

TECHNIQUES: dict[str, Technique] = {
    # bowed strings
    "sul ponticello": Technique("sul pont.", families=_STRINGS),
    "sul ponticello estremo": Technique("sul pont. estr.", families=_STRINGS),
    "sul tasto": Technique("sul tasto", families=_STRINGS | _GUITAR),
    "flautando": Technique("flautando", families=_STRINGS),
    "col legno battuto": Technique("c.l. batt.", "x", families=_STRINGS),
    "col legno tratto": Technique("c.l. tratto", "x", families=_STRINGS),
    "dietro il ponticello": Technique("dietro il pont.", "x", families=_STRINGS),
    "sulla cordiera": Technique("sulla cordiera", "x", families=_STRINGS),
    "scratch": Technique("scratch", "x", families=_STRINGS),
    "pizzicato": Technique("pizz.", families=_STRINGS),
    "pizz. bartok": Technique(technical="snap-pizzicato", families=_STRINGS | _GUITAR),
    "left-hand pizzicato": Technique(technical="stopped", families=_STRINGS),
    "jete": Technique("jeté", families=_STRINGS, min_duration=_EIGHTH),
    "ricochet": Technique("ricochet", families=_STRINGS, min_duration=_EIGHTH),
    "con sordino": Technique("con sord.", families=_STRINGS | _BRASS),
    "senza sordino": Technique("senza sord.", families=_STRINGS | _BRASS),
    "molto vibrato": Technique("molto vib.", families=_STRINGS | _WINDS | _VOICE),
    "senza vibrato": Technique("senza vib.", families=_STRINGS | _WINDS | _VOICE),
    "body percussion": Technique("percuss. corpo", "x", families=_STRINGS | _GUITAR),
    "harmonic": Technique(
        notehead="diamond",
        families=_in("strings", "woodwinds", "brass", "harp", "guitar"),
        register=(0.2, 1.0),
    ),
    # woodwinds and brass
    "fischio": Technique(
        "fischio", "diamond", families=_WOODWINDS, only=_FLUTES, register=(0.35, 1.0), min_duration=_QUARTER
    ),
    "whistle tone": Technique(
        "whistle tone", "diamond", families=_WOODWINDS, only=_FLUTES, register=(0.35, 1.0), min_duration=_QUARTER
    ),
    "jet whistle": Technique(
        "jet wh.", families=_WOODWINDS, only=_FLUTES, min_duration=_EIGHTH, precise_pitch=False
    ),
    "tongue ram": Technique("tongue ram", "triangle", families=_WOODWINDS, only=_FLUTES, register=(0.0, 0.4)),
    "frullato": Technique(
        "flz.", tremolo=True, families=_WINDS | _VOICE, register=(0.0, 0.9), min_duration=Fraction(1, 16)
    ),
    "multiphonic": Technique("multif.", families=_WINDS | _VOICE, register=(0.0, 0.7), min_duration=_EIGHTH),
    "slap": Technique("slap", "x", families=_WINDS, register=(0.0, 0.55)),
    "key click": Technique("key click", "x", families=_WOODWINDS),
    "air sound": Technique("aria", "circle-x", families=_WINDS | _VOICE, min_duration=_EIGHTH),
    "bisbigliando": Technique("bisb.", families=_WOODWINDS | _HARP),
    "canta e suona": Technique("canta e suona", families=_WINDS),
    "subtone": Technique("subtone", families=_WOODWINDS, only=_SINGLE_REED, register=(0.0, 0.5)),
    "growl": Technique("growl", families=_WINDS),
    "tongue pizzicato": Technique("t. pizz.", families=_WOODWINDS),
    # brass
    "gestopft": Technique("gestopft", technical="stopped", families=_BRASS, register=(0.2, 0.9)),
    "open": Technique(technical="open", families=_BRASS),
    "cuivre": Technique("cuivré", families=_BRASS),
    "harmon mute": Technique("harmon mute", families=_BRASS),
    "cup mute": Technique("cup mute", families=_BRASS),
    "plunger mute": Technique("plunger", families=_BRASS),
    "half valve": Technique("½ valv.", families=_BRASS),
    "mouthpiece only": Technique("solo bocchino", "x", families=_BRASS),
    "shake": Technique("shake", tremolo=True, families=_BRASS),
    # percussion
    "roll": Technique(tremolo=True, families=_PERCUSSION),
    "laissez vibrer": Technique("l.v.", families=_RESONANT),
    "secco": Technique("secco", families=_RESONANT),
    "dead stroke": Technique("dead stroke", families=_PERCUSSION),
    "rim shot": Technique("rim shot", "x", families=_PERCUSSION, only=_DRUMS),
    "sul bordo": Technique("sul bordo", families=_PERCUSSION),
    "bowed": Technique("con l'arco", "diamond", families=_PERCUSSION, only=_BOWED_PERC),
    "hard mallet": Technique("bacch. dure", families=_PERCUSSION),
    "soft mallet": Technique("bacch. morbide", families=_PERCUSSION),
    "brushes": Technique("spazzola", families=_PERCUSSION, only=_DRUMS | {"suspended cymbal"}),
    # keyboard (piano)
    "string pizzicato": Technique("pizz. sulle corde", families=_KEYBOARD, only=_PIANO),
    "muted strings": Technique("muted", technical="stopped", families=_KEYBOARD, only=_PIANO),
    "piano harmonic": Technique(notehead="diamond", families=_KEYBOARD, only=_PIANO),
    "cluster": Technique("cluster", families=_KEYBOARD),
    "string glissando": Technique("gliss. sulle corde", families=_KEYBOARD, only=_PIANO),
    "una corda": Technique("una corda", families=_KEYBOARD, only=_PIANO),
    "prepared piano": Technique("prep.", "x", families=_KEYBOARD, only=_PIANO),
    # harp
    "pres de la table": Technique("p.d.l.t.", families=_HARP),
    "xylophonic": Technique("sons xyl.", "x", families=_HARP),
    "fingernail": Technique("avec l'ongle", families=_HARP | _GUITAR),
    "pedal glissando": Technique("pedal gliss.", families=_HARP),
    # guitar
    "tambora": Technique("tambora", "x", families=_GUITAR),
    "rasgueado": Technique("rasg.", families=_GUITAR),
    "golpe": Technique("golpe", "x", families=_GUITAR),
    "palm mute": Technique("p.m.", families=_GUITAR),
    # voice
    "sprechstimme": Technique("Sprechst.", "x", families=_VOICE),
    "bocca chiusa": Technique("b.ch.", families=_VOICE),
    "falsetto": Technique("falsetto", families=_VOICE),
    "whispered": Technique("sussurrato", "x", families=_VOICE),
    "ingressive": Technique("ingr.", families=_VOICE),
    "vocal fry": Technique("vocal fry", families=_VOICE),
    "tongue click": Technique("tongue click", "x", families=_VOICE | _WOODWINDS),
    # any instrument
    "tremolo": Technique(tremolo=True, min_duration=_EIGHTH),
    "staccato": Technique(articulation="staccato"),
    "staccatissimo": Technique(articulation="staccatissimo"),
    "tenuto": Technique(articulation="tenuto"),
    "accent": Technique(articulation="accent"),
    "marcato": Technique(articulation="strong-accent"),
}


class VoiceDirections:
    """Track written directions event by event within a voice: technique text holds until changed (returning with
    'ord.' or 'arco' after 'pizz.'), dynamics hold until the next mark, and free text does not duplicate technique text.
    """

    def __init__(self) -> None:
        self.texts: tuple[str, ...] = ()
        self.dynamic: str | None = None

    def next_event(
        self, techniques: list[Technique], is_note: bool, text: str | None, dynamic: str | None
    ) -> tuple[list[str], list[str], str | None]:
        """Return `(italic_technique_texts, free_texts, new_dynamic)` to write on this event."""
        italics: list[str] = []
        texts = tuple(t.text for t in techniques if t.text)
        if is_note and texts != self.texts:  # rests do not change ongoing technique
            italics = list(texts) or ["arco" if "pizz." in self.texts else "ord."]
            self.texts = texts
        free = [text] if text and get_technique(text).text not in texts else []
        new_dyn = dynamic if dynamic and dynamic != self.dynamic else None
        if new_dyn:
            self.dynamic = new_dyn
        return italics, free, new_dyn


def get_technique(name: str) -> Technique:
    canonical = normalize_technique(name)
    return TECHNIQUES.get(canonical) or Technique(text=name.strip())


def family_techniques(family: Family) -> list[str]:
    """Return technique names for `family`; restricted ones list their allowed instruments (`whistle tone (only ...)`)."""
    return [
        name + (f" (only {', '.join(sorted(t.only))})" if t.only else "")
        for name, t in TECHNIQUES.items()
        if family in t.families
    ]


def fits_instrument(technique_name: str, instrument_name: str) -> bool:
    """Return True only when the instrument is known in `INSTRUMENTS` and supports `technique_name`."""
    t = get_technique(technique_name)
    try:
        key = identify(instrument_name)
    except ValueError:
        return False
    return INSTRUMENTS[key].family in t.families and (not t.only or key in t.only)


def technique_issue(technique_name: str, part: Part, note: Note) -> str | None:
    """Return why `technique_name` cannot be played on `note` in `part` (family, register, duration, pitch), or None."""
    if not fits_instrument(technique_name, part.instrument):
        return "not playable in this instrument family"
    t = get_technique(technique_name)
    if note.duration < t.min_duration:
        return f"too short (minimum {t.min_duration})"
    if not t.precise_pitch and any(a.midicents % 100 for a in note.pitches):
        return "lacks precise pitch (microtonal notation)"
    reg = part_range(part)
    if reg and reg[1] > reg[0]:
        low, high = reg
        positions = [(a.midicents - low) / (high - low) for a in note.pitches]
        if max(positions) > t.register[1]:
            return "outside register (too high)"
        if min(positions) < t.register[0]:
            return "outside register (too low)"
    return None


def unplayable_techniques(ir: ScoreIR) -> list[str]:
    """Return every unplayable technique placement across `ir` (`P2 m.3: slap (...): reason`)."""
    locs = locate(ir)
    return [
        f"{p.id} m.{locs[e.id][2]}: {t} ({p.instrument}): {reason}"
        for p, _, e in ir.events()
        if isinstance(e, Note)
        for t in e.techniques
        if (reason := technique_issue(t, p, e))
    ]

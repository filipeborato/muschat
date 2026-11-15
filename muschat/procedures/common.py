"""Shared symbolic procedure helpers: selection-aware voice/measure traversal, barline splitting, ID generation,
interpolation, and idiomatic extended-technique playability checks.
"""

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from muschat.instruments import FAMILIES, INSTRUMENTS, Family, identify, part_range
from muschat.plan import Selection
from muschat.score_ir import Dynamic, Event, Note, Part, Pitch, Rest, ScoreIR, Voice, locate, normalize_technique, slices

DEFAULT_QUARTET = ["violin", "violin", "viola", "cello"]
DYNAMICS: list[Dynamic] = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff"]


@dataclass(frozen=True)
class Technique:
    text: str | None = None
    notehead: str | None = None
    technical: str | None = None
    tremolo: bool = False
    articulation: str | None = None  # MusicXML (<articulations>) tag name
    families: frozenset[Family] = frozenset(FAMILIES)
    only: frozenset[str] = frozenset()  # restricted to specific keys in INSTRUMENTS
    # shortcut: initial idiomatic bounds (register as fraction of range: 0 = lowest, 1 = highest;
    # minimum playable duration; whether microtonal writing is playable); refine per instrument when needed
    register: tuple[float, float] = (0.0, 1.0)
    min_duration: Fraction = Fraction(0)
    precise_pitch: bool = True


def _in_families(*families: Family) -> frozenset[Family]:
    return frozenset(families)


_STRINGS = _in_families("strings")
_WINDS = _in_families("woodwinds", "brass")
_BRASS = _in_families("brass")
_WOODWINDS = _in_families("woodwinds")
_EIGHTH, _QUARTER = Fraction(1, 8), Fraction(1, 4)
_PERCUSSION = _in_families("percussion")
_KEYBOARD = _in_families("keyboard")
_HARP = _in_families("harp")
_GUITAR = _in_families("guitar")
_VOICE = _in_families("voice")
_RESONANT = _in_families("percussion", "harp", "keyboard", "guitar")
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
        families=_in_families("strings", "woodwinds", "brass", "harp", "guitar"),
        register=(0.2, 1.0),
    ),
    # woodwinds and brass
    "fischio": Technique("fischio", "diamond", families=_WOODWINDS, only=_FLUTES, register=(0.35, 1.0), min_duration=_QUARTER),
    "whistle tone": Technique(
        "whistle tone", "diamond", families=_WOODWINDS, only=_FLUTES, register=(0.35, 1.0), min_duration=_QUARTER
    ),
    "jet whistle": Technique("jet wh.", families=_WOODWINDS, only=_FLUTES, min_duration=_EIGHTH, precise_pitch=False),
    "tongue ram": Technique("tongue ram", "triangle", families=_WOODWINDS, only=_FLUTES, register=(0.0, 0.4)),
    "frullato": Technique("flz.", tremolo=True, families=_WINDS | _VOICE, register=(0.0, 0.9), min_duration=Fraction(1, 16)),
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
    """Track written technique and dynamic markings event-by-event along a voice."""

    def __init__(self) -> None:
        self.texts: tuple[str, ...] = ()
        self.dynamic: str | None = None

    def next_event(
        self, techniques: list[Technique], is_note: bool, text: str | None, dynamic: str | None
    ) -> tuple[list[str], list[str], str | None]:
        """Return (italic technique labels, free text labels, new dynamic) to write at this event."""
        italics: list[str] = []
        texts = tuple(t.text for t in techniques if t.text)
        if is_note and texts != self.texts:  # rests do not cancel the active technique
            italics = list(texts) or ["arco" if "pizz." in self.texts else "ord."]
            self.texts = texts
        free_texts = [text] if text and get_technique(text).text not in texts else []
        new_dyn = dynamic if dynamic and dynamic != self.dynamic else None
        if new_dyn:
            self.dynamic = new_dyn
        return italics, free_texts, new_dyn


def family_techniques(family: Family) -> list[str]:
    """List technique names available to `family`, annotating instrument-specific restrictions."""
    return [
        name + (f" (only {', '.join(sorted(t.only))})" if t.only else "")
        for name, t in TECHNIQUES.items()
        if family in t.families
    ]


def get_technique(name: str) -> Technique:
    key = normalize_technique(name)
    return TECHNIQUES.get(key) or Technique(text=name.strip())


def fits_instrument(technique_name: str, instrument_name: str) -> bool:
    """Return True only when the instrument is known and supports `technique_name`."""
    t = get_technique(technique_name)
    try:
        key = identify(instrument_name)
    except ValueError:
        return False
    return INSTRUMENTS[key].family in t.families and (not t.only or key in t.only)


def technique_issue(technique_name: str, part: Part, note: Note) -> str | None:
    """Explain why `technique_name` is unplayable on `note` of `part`, or return None."""
    if not fits_instrument(technique_name, part.instrument):
        return "not playable in this instrument family"
    t = get_technique(technique_name)
    if note.duration < t.min_duration:
        return f"too short (minimum {t.min_duration})"
    if not t.precise_pitch and any(p.midicents % 100 for p in note.pitches):
        return "lacks precise pitch (microtonal notation)"
    reg = part_range(part)
    if reg and reg[1] > reg[0]:
        low, high = reg
        positions = [(p.midicents - low) / (high - low) for p in note.pitches]
        if max(positions) > t.register[1]:
            return "outside playable register (too high)"
        if min(positions) < t.register[0]:
            return "outside playable register (too low)"
    return None


def unplayable_techniques(ir: ScoreIR) -> list[str]:
    """Return all unplayable technique assignments across the score ("P2 m.3: slap (...): reason")."""
    locations = locate(ir)
    return [
        f"{p.id} m.{locations[e.id][2]}: {t} ({p.instrument}): {reason}"
        for p, _, e in ir.events()
        if isinstance(e, Note)
        for t in e.techniques
        if (reason := technique_issue(t, p, e))
    ]


def require_score(current: ScoreIR | None) -> ScoreIR:
    if current is None:
        raise ValueError("this procedure transforms an existing composition, and none exists yet")
    return current


class IdGenerator:
    """Generate unique event and tuplet IDs (`base.1`, `base.2`, ...) without colliding with existing score IDs."""

    def __init__(self, current: ScoreIR) -> None:
        self.used = set(locate(current)) | {t.id for p in current.parts for v in p.voices for t in v.tuplets}

    def new_id(self, base: str) -> str:
        n = 1
        while f"{base}.{n}" in self.used:
            n += 1
        self.used.add(f"{base}.{n}")
        return f"{base}.{n}"


class MeasureGrid:
    """Measure boundaries (1-based) in whole notes."""

    def __init__(self, current: ScoreIR) -> None:
        self.starts = current.measure_starts()
        self.ends = [*self.starts[1:], self.starts[-1] + current.measures[-1].length]

    def span(self, sel: Selection) -> tuple[int, int]:
        """First and last measure of the selection (1..n)."""
        return sel.start_measure or 1, sel.end_measure or len(self.starts)

    def slices(self, start: Fraction, duration: Fraction) -> list[tuple[Fraction, Fraction]]:
        return slices(self.starts, start, duration)


def split_event(
    event: Event,
    start: Fraction,
    duration: Fraction,
    grid: MeasureGrid,
    ids: IdGenerator,
    *,
    sustain: bool = False,
) -> list[Event]:
    """Split a moved/resized event across barlines and representable figures; first slice keeps ID, dynamic, and text."""
    parts = grid.slices(start, duration)
    output: list[Event] = []
    for k, (s, d) in enumerate(parts):
        fields: dict = {"id": event.id if k == 0 else ids.new_id(event.id), "start": s, "duration": d}
        if k > 0 and isinstance(event, (Note, Rest)):
            fields |= {"dynamic": None, "text": None}
        if k < len(parts) - 1 and isinstance(event, Note):
            fields["glissando"] = False
            if sustain:
                fields["tied"] = True
        output.append(event.model_copy(update=fields))
    return output


def to_rest(event: Event) -> Rest:
    return Rest(
        id=event.id,
        start=event.start,
        duration=event.duration,
        provenance=event.provenance,
        dynamic=getattr(event, "dynamic", None),
        text=getattr(event, "text", None),
    )


def with_pitches(note: Note, pitches: list[int]) -> Note:
    return note.model_copy(update={"pitches": [Pitch(midicents=a) for a in pitches]})


def map_voices(current: ScoreIR, fn: Callable[[Part, Voice], Voice]) -> ScoreIR:
    return current.model_copy(
        update={"parts": [p.model_copy(update={"voices": [fn(p, v) for v in p.voices]}) for p in current.parts]}
    )


VoiceSliceFn = Callable[[Part, Voice, list[Event]], list[Event]]


def by_voice(current: ScoreIR, sel: Selection, fn: VoiceSliceFn) -> ScoreIR:
    """Apply `fn` to the selected events of each voice; the returned list replaces only those events."""
    locations = locate(current)

    def transform_voice(part: Part, v: Voice) -> Voice:
        target = [e for e in v.events if sel.contains(part.id, v.id, locations[e.id][2])]
        if not target:
            return v
        replaced = {e.id for e in target}
        events = [e for e in v.events if e.id not in replaced] + fn(part, v, target)
        return v.model_copy(update={"events": sorted(events, key=lambda e: e.start)})

    return map_voices(current, transform_voice)


MeasureSliceFn = Callable[[Part, Voice, list[Event], int], list[Event]]


def by_measure(current: ScoreIR, sel: Selection, fn: MeasureSliceFn) -> ScoreIR:
    """Like `by_voice`, measure by measure; measures containing tuplets are left untouched."""
    locations = locate(current)

    def slice_fn(part: Part, v: Voice, target: list[Event]) -> list[Event]:
        in_tuplet = {i for t in v.tuplets for i in t.events}
        groups: dict[int, list[Event]] = {}
        for e in target:
            groups.setdefault(locations[e.id][2] or 0, []).append(e)
        output: list[Event] = []
        for n, group in groups.items():
            output += group if in_tuplet & {e.id for e in group} else fn(part, v, group, n)
        return output

    return by_voice(current, sel, slice_fn)


def interpolate(start: float, end: float, n: int, first: int, last: int) -> float:
    """Linearly interpolate value at measure `n` between `first` and `last` measures of the selection."""
    return start if last == first else start + (end - start) * (n - first) / (last - first)

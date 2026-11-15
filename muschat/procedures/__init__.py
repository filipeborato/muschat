"""Symbolic composition procedure catalog: pure deterministic functions over ScoreIR with seed."""

import random
from collections.abc import Callable
from dataclasses import dataclass

from muschat.plan import Plan
from muschat.procedures import contemporary, generation, melodic, progressive, rhythmic, spectral, textures
from muschat.score_ir import ScoreIR


@dataclass(frozen=True)
class RangeSpec:
    min_value: float
    max_value: float
    default: float
    description: str = ""
    integer: bool = True
    unit: str = ""


ProcedureFn = Callable[[ScoreIR | None, Plan, dict[str, float], random.Random], ScoreIR]


@dataclass(frozen=True)
class Procedure:
    name: str
    description: str
    parameters: dict[str, RangeSpec]
    fn: ProcedureFn | None
    requires_score: bool = True


_LEVELS = "0 pppp, 1 ppp, 2 pp, 3 p, 4 mp, 5 mf, 6 f, 7 ff, 8 fff, 9 ffff"
_RAMP = "in a linear ramp from the first to the last measure of the selection"
_FIGURES = "figure 1/2^n: 0 whole, 1 half, 2 quarter, 3 eighth, 4 sixteenth"
_VERHULST = {
    "x0": RangeSpec(0.01, 0.99, 0.1, "initial orbit state (initial population)", integer=False),
    "turbulence": RangeSpec(0, 3, 2.8, "Verhulst lambda: chaotic above ~2.57", integer=False),
}

CATALOG: dict[str, Procedure] = {
    p.name: p
    for p in [
        Procedure(
            "detune",
            "Microtonal pitch deviation in a ramp or oscillation; intra-gesture glissandos without changing rhythm.",
            {
                "deviation": RangeSpec(0, 200, 100, "maximum deviation", unit="cents"),
                "resolution": RangeSpec(1, 2, 1, "1 = quarter-tone, 2 = eighth-tone"),
                "curve": RangeSpec(0, 1, 0, "0 = ramp, 1 = full sine oscillation"),
                "glissandos": RangeSpec(0, 1, 0.5, "ratio of intra-gesture glissando links", integer=False),
            },
            contemporary.detune,
        ),
        Procedure(
            "timbral_substitution",
            "Progressively replace notes with idiomatic instrumental noise while keeping rhythm and dynamics.",
            {
                "start": RangeSpec(0, 1, 0.2, "initial ratio", integer=False),
                "end": RangeSpec(0, 1, 0.8, "final ratio", integer=False),
                "noise_family": RangeSpec(0, 3, 0, "0 = all, 1 = friction, 2 = percussive, 3 = air"),
            },
            contemporary.timbral_substitution,
        ),
        Procedure(
            "rarefy",
            "Remove attacks, prolong notes with ties, natural harmonics or whistle tones, and extreme dynamics.",
            {
                "keep": RangeSpec(0, 1, 0.3, "ratio of kept notes", integer=False),
                "harmonics": RangeSpec(0, 1, 0.5, "conversion ratio to harmonics/whistle tones", integer=False),
                "dynamic": RangeSpec(0, 1, 0, "0 = pppp, 1 = ppp"),
                "accents": RangeSpec(0, 0.2, 0.03, "ratio of rare accents", integer=False),
            },
            contemporary.rarefy,
        ),
        Procedure(
            "micropolyphony",
            "Close canon from the first voice of the source part across selected voices with offset, duration variation, and microtonal spread.",
            {
                "source_part": RangeSpec(1, 16, 1, "1-based index of the source part"),
                "offset": RangeSpec(0, 8, 1, "entry delay per voice", unit="sixteenths"),
                "variation": RangeSpec(0, 2, 1, "max time-scale variation in 1/16 steps"),
                "spread": RangeSpec(0, 1200, 150, "total cluster width across voices", unit="cents"),
            },
            textures.micropolyphony,
        ),
        Procedure(
            "deform_rhythm",
            "Deform gestures into tuplets within barlines while preserving notes and order; remaining space becomes breath rests.",
            {
                "ratio": RangeSpec(0, 1, 0.6, "ratio of deformed gestures", integer=False),
                "tuplets": RangeSpec(1, 15, 15, "bitmask: 1 = 5:4, 2 = 7:4, 4 = 6:4, 8 = 5:6"),
                "min_factor": RangeSpec(0.25, 2, 0.5, "minimum desired scaling factor", integer=False),
                "max_factor": RangeSpec(0.25, 2, 1, "maximum desired scaling factor", integer=False),
            },
            textures.deform_rhythm,
        ),
        Procedure(
            "harmonic_series",
            "Create a new score over the harmonic partials of a fundamental, distributed across the planned instruments within their ranges.",
            {
                "fundamental": RangeSpec(2400, 6000, 3600, "fundamental", unit="midicents (3600 = C2, 6000 = C4)"),
                "partials": RangeSpec(2, 32, 16, "number of partials (1..n)"),
                "measures": RangeSpec(1, 32, 8, "number of 4/4 measures"),
                "microtone": RangeSpec(0, 2, 1, "quantization: 0 semitone, 1 quarter-tone, 2 eighth-tone"),
                "rests": RangeSpec(0, 0.9, 0.15, "rest ratio", integer=False),
            },
            generation.harmonic_series,
            requires_score=False,
        ),
        Procedure(
            "orchestrate_material",
            "Create a new score from the pitches of the current score (e.g. spectral material imported from OpenMusic) across the planned instruments.",
            {
                "measures": RangeSpec(1, 32, 8, "number of 4/4 measures"),
                "rests": RangeSpec(0, 0.9, 0.15, "rest ratio", integer=False),
            },
            generation.orchestrate_material,
        ),
        Procedure(
            "spectralize",
            "Replace selected pitches with harmonic partials of a fundamental while preserving melodic contour and assigning each part a spectral band.",
            {
                "fundamental": RangeSpec(2400, 6000, 3600, "fundamental", unit="midicents (3600 = C2, 6000 = C4)"),
                "partials": RangeSpec(2, 32, 16, "number of partials (1..n)"),
                "microtone": RangeSpec(0, 2, 1, "quantization: 0 semitone, 1 quarter-tone, 2 eighth-tone"),
                "max_leap": RangeSpec(100, 2400, 1200, "maximum interval between consecutive notes", unit="cents"),
            },
            spectral.spectralize,
        ),
        Procedure(
            "transpose",
            "Transpose selected notes while preserving rhythm.",
            {"interval": RangeSpec(-2400, 2400, 0, "interval (100 = semitone)", unit="cents")},
            melodic.transpose,
        ),
        Procedure(
            "invert",
            "Melodic inversion around an axis per voice while preserving rhythm.",
            {"axis": RangeSpec(0, 12700, 0, "axis; 0 = first pitch of the selection in each voice", unit="midicents")},
            melodic.invert,
        ),
        Procedure(
            "retrograde",
            "Retrograde the pitch sequence of the selection per voice while preserving rhythm.",
            {},
            melodic.retrograde,
        ),
        Procedure(
            "rotate",
            "Rotate the pitch sequence of the selection per voice while preserving rhythm.",
            {"steps": RangeSpec(-16, 16, 1, "number of notes to rotate")},
            melodic.rotate,
        ),
        Procedure(
            "scale_intervals",
            "Compress (<1) or expand (>1) melodic intervals relative to the first pitch of each voice in quarter-tones.",
            {"factor": RangeSpec(0.25, 4, 1, "interval scaling factor", integer=False)},
            melodic.scale_intervals,
        ),
        Procedure(
            "augment_diminish",
            "Augmentation (exponent > 0: durations * 2^n, truncating overflow) or diminution (exponent < 0: shrink and repeat to fill span).",
            {"exponent": RangeSpec(-2, 2, 1, "power of 2 for the scaling factor")},
            rhythmic.augment_diminish,
        ),
        Procedure(
            "permute_durations",
            "Permute durations within each selected measure while preserving pitches and event order.",
            {},
            rhythmic.permute_durations,
        ),
        Procedure(
            "subdivide",
            "Subdivide selected notes into tuplets (3, 5, 6, 7) or 4 equal notes repeating the pitch.",
            {
                "division": RangeSpec(3, 7, 3, "notes per subdivision"),
                "ratio": RangeSpec(0, 1, 1, "ratio of subdivided notes", integer=False),
            },
            rhythmic.subdivide,
        ),
        Procedure(
            "density",
            f"Attack density {_RAMP}: remove attacks (into rests) or add attacks (rests become notes, notes split).",
            {
                "start": RangeSpec(0, 4, 1, "attacks per quarter note in the first measure", integer=False),
                "end": RangeSpec(0, 4, 1, "attacks per quarter note in the last measure", integer=False),
            },
            progressive.density,
        ),
        Procedure(
            "progressive_register",
            f"Shift register {_RAMP}.",
            {
                "start": RangeSpec(-2400, 2400, 0, "shift at start", unit="cents"),
                "end": RangeSpec(-2400, 2400, 1200, "shift at end", unit="cents"),
            },
            progressive.progressive_register,
        ),
        Procedure(
            "progressive_dynamics",
            f"Dynamics {_RAMP} (one mark per measure).",
            {"start": RangeSpec(0, 9, 2, _LEVELS), "end": RangeSpec(0, 9, 7, _LEVELS)},
            progressive.progressive_dynamics,
        ),
        Procedure(
            "gestural_techniques",
            "Apply techniques from `plan.techniques` per entire gesture (notes between rests) where playable on all notes of the gesture.",
            {"ratio": RangeSpec(0, 1, 0.5, "ratio of gestures receiving a technique", integer=False)},
            progressive.gestural_techniques,
        ),
        Procedure(
            "apply_techniques",
            "Apply techniques from `plan.techniques` to a sampled ratio of notes in the selection note-by-note.",
            {"ratio": RangeSpec(0, 1, 1, "ratio of notes receiving a technique", integer=False)},
            progressive.apply_techniques,
        ),
        Procedure(
            "distribute",
            "Distribute notes of a source part across selected parts whose ranges contain them.",
            {"source_part": RangeSpec(1, 16, 1, "1-based index of the source part")},
            generation.distribute,
        ),
        Procedure(
            "events_from_spectrum",
            "Create instrumental parts from spectral analysis data in the score (e.g. OM-SuperVP).",
            {
                "pitches_per_frame": RangeSpec(1, 8, 4, "strongest partials per frame"),
                "microtone": RangeSpec(0, 2, 1, "quantization: 0 semitone, 1 quarter-tone, 2 eighth-tone"),
            },
            spectral.events_from_spectrum,
        ),
        Procedure(
            "generate_notagen",
            "NotaGen only (local model): generate chamber music phrases, rhythm, and articulation from a period-composer-instrumentation condition.",
            {"measures": RangeSpec(1, 16, 8, "number of measures")},
            None,
            requires_score=False,
        ),
        Procedure(
            "chaotic_density",
            "OpenMusic only (Chaos): Verhulst logistic map drives attack density per selected measure between min_value and max_value.",
            {
                **_VERHULST,
                "min_value": RangeSpec(0, 4, 0.5, "attacks per quarter note at series minimum", integer=False),
                "max_value": RangeSpec(0, 4, 3, "attacks per quarter note at series maximum", integer=False),
            },
            None,
        ),
        Procedure(
            "chaotic_register",
            "OpenMusic only (Chaos): Verhulst logistic map shifts register per selected measure between min_value and max_value.",
            {
                **_VERHULST,
                "min_value": RangeSpec(-2400, 2400, 0, "shift at series minimum", unit="cents"),
                "max_value": RangeSpec(-2400, 2400, 1200, "shift at series maximum", unit="cents"),
            },
            None,
        ),
        Procedure(
            "chaotic_dynamics",
            "OpenMusic only (Chaos): Verhulst logistic map chooses the dynamic of the first attack of each measure.",
            {**_VERHULST, "min_value": RangeSpec(0, 9, 1, _LEVELS), "max_value": RangeSpec(0, 9, 8, _LEVELS)},
            None,
        ),
        Procedure(
            "chaotic_durations",
            "OpenMusic only (Chaos): Verhulst logistic map chooses the rhythmic figure rewriting each measure.",
            {**_VERHULST, "min_value": RangeSpec(0, 4, 1, _FIGURES), "max_value": RangeSpec(0, 4, 4, _FIGURES)},
            None,
        ),
        Procedure(
            "distribute_by_constraints",
            "OpenMusic only (Situation): create a new score distributing N pitches (base + k*step) across instruments via constraint satisfaction.",
            {
                "pitches": RangeSpec(4, 24, 12, "number of pitches"),
                "base": RangeSpec(2400, 8400, 5500, "lowest pitch (5500 = G3)", unit="midicents"),
                "step": RangeSpec(50, 1200, 100, "interval between adjacent candidate pitches", unit="cents"),
            },
            None,
            requires_score=False,
        ),
        Procedure(
            "pitches_by_constraints",
            "OpenMusic only (Cluster Engine): keep the rhythm of the selection and solve for spectral pitches under voice-leading constraints.",
            {
                "fundamental": RangeSpec(2400, 6000, 3600, "fundamental", unit="midicents (3600 = C2)"),
                "partials": RangeSpec(2, 32, 16, "number of partials (1..n)"),
                "microtone": RangeSpec(0, 2, 1, "quantization: 0 semitone, 1 quarter-tone, 2 eighth-tone"),
                "max_leap": RangeSpec(100, 2400, 700, "maximum melodic leap per voice", unit="cents"),
                "no_unison": RangeSpec(0, 2, 1, "distinct simultaneous pitches: 0 off, 1 preference, 2 strict"),
                "no_voice_crossing": RangeSpec(0, 2, 1, "upper part above lower part: 0 off, 1 preference, 2 strict"),
                "common_tones": RangeSpec(0, 1, 0, "1 = prefer repeating previous pitch"),
            },
            None,
        ),
        Procedure(
            "additive_synthesis",
            "OpenMusic only (OMChroma + Csound): selected notes drive additive synthesis, writing .orc/.sco/.wav and adding an electronics part.",
            {
                "initial_partials": RangeSpec(1, 32, 2, "partials per note at start"),
                "final_partials": RangeSpec(1, 32, 16, "partials per note at end"),
                "amplitude": RangeSpec(-40, 0, -18, "fundamental amplitude", integer=False, unit="dB"),
            },
            None,
        ),
        Procedure(
            "supervp_analysis",
            "OpenMusic only (OM-SuperVP): analyze a sound and create a score with SpectralFrame events.",
            {
                "partials": RangeSpec(1, 64, 16, "partials per frame"),
                "frames": RangeSpec(1, 64, 8, "number of analysis frames"),
            },
            None,
            requires_score=False,
        ),
    ]
}

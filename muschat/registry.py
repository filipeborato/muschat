"""Capability registry: what each engine, library, and engraver can run on THIS machine, and in what state.

Detected at runtime; nothing appears available merely because it is planned (an OpenMusic library only advertises a
procedure when it is installed AND its approved patch exists in the repo AND has been validated in real OpenMusic).
"""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from muschat.plan import normalize_procedure_id

CapabilityState = Literal["unavailable", "manual", "file_exchange", "automatic"]
CapabilityKind = Literal["engine", "library", "engraving", "external"]
Engine = Literal["python", "openmusic", "notagen"]

PREFERENCE: dict[CapabilityState, int] = {
    "automatic": 0,
    "file_exchange": 1,
    "manual": 2,
    "unavailable": 3,
}
ENGINE_NAME: dict[Engine, str] = {"python": "Python/Abjad", "openmusic": "OpenMusic", "notagen": "NotaGen"}
STATE_LABEL: dict[CapabilityState, str] = {
    "automatic": "automatic",
    "file_exchange": "file exchange",
    "manual": "assisted",
    "unavailable": "unavailable",
}

NATIVE_PROCEDURES: tuple[str, ...] = (
    "detune",
    "timbral_substitution",
    "rarefy",
    "micropolyphony",
    "deform_rhythm",
    "harmonic_series",
    "orchestrate_material",
    "spectralize",
    "transpose",
    "invert",
    "retrograde",
    "rotate",
    "scale_intervals",
    "augment_diminish",
    "permute_durations",
    "subdivide",
    "density",
    "progressive_register",
    "progressive_dynamics",
    "gestural_techniques",
    "apply_techniques",
    "distribute",
    "events_from_spectrum",
)


@dataclass(frozen=True)
class Capability:
    name: str
    kind: CapabilityKind
    state: CapabilityState
    reason: str
    version: str | None = None
    procedures: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    inputs: str = ""
    outputs: str = ""
    limitations: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Registry:
    capabilities: tuple[Capability, ...]

    def capability(self, name: str) -> Capability | None:
        return next((c for c in self.capabilities if c.name == name), None)

    def implementations(self, procedure: str) -> dict[Engine, Capability]:
        """Return engines that implement `procedure` on this machine. For OpenMusic, libraries declare procedures and
        the effective state is the worst between the engine and the library."""
        proc = normalize_procedure_id(procedure)
        found: dict[Engine, Capability] = {}
        python = self.capability("Python")
        if python and proc in tuple(normalize_procedure_id(p) for p in python.procedures):
            found["python"] = python
        notagen = self.capability("NotaGen")
        if notagen and proc in tuple(normalize_procedure_id(p) for p in notagen.procedures):
            found["notagen"] = notagen
        om = self.capability("OpenMusic")
        libs = [
            c
            for c in self.capabilities
            if c.kind == "library" and proc in tuple(normalize_procedure_id(p) for p in c.procedures)
        ]
        if om and libs:
            found["openmusic"] = max([om, *libs], key=lambda c: PREFERENCE[c.state])
        return found

    def available_procedures(self) -> list[str]:
        """Return canonical procedure IDs available on at least one non-unavailable capability."""
        names = dict.fromkeys(
            normalize_procedure_id(p)
            for c in self.capabilities
            if c.state != "unavailable"
            for p in c.procedures
        )
        return list(names)

    @classmethod
    def python_only(cls, procedures: tuple[str, ...] = NATIVE_PROCEDURES) -> "Registry":
        return cls((_python_capability(procedures),))


def _python_capability(procedures: tuple[str, ...] = NATIVE_PROCEDURES) -> Capability:
    return Capability(
        "Python",
        "engine",
        "automatic",
        "pure deterministic procedures over ScoreIR with seed",
        procedures=tuple(normalize_procedure_id(p) for p in procedures),
        inputs="ScoreIR",
        outputs="ScoreIR",
        limitations="transposing instruments stored at sounding pitch",
    )


_OM_LIBRARIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Esquisse": ("esquisse", ()),
    "Chaos": ("chaos", ()),
    "Situation": ("situation", ()),
    "Clouds": ("clouds", ()),
    "OM2Csound": ("om2csound", ("csound",)),
    "OMChroma": ("omchroma", ()),
    "OM-SuperVP": ("om-supervp", ("supervp",)),
    "Cluster-Engine": ("cluster-engine", ()),
}


def detect_capabilities(
    *,
    lilypond: Any | None,
    openmusic: Any | None,
    abjad_version: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    patches: Mapping[str, Any] | None = None,
    openmusic_enabled: bool = True,
    notagen: Any | str | None = None,
) -> Registry:
    """Detect installed engines, libraries, and engraving backends on this machine."""
    from muschat.om_patches import PATCHES, find_csound

    active_patches = patches if patches is not None else PATCHES
    ly_exec = lilypond.executable if lilypond else None
    ly_ver = lilypond.version if lilypond else None
    caps = [
        _python_capability(),
        Capability(
            "Abjad",
            "engraving",
            "automatic" if abjad_version else "unavailable",
            "ScoreIR -> LilyPond text" if abjad_version else "abjad package is not importable",
            version=abjad_version,
            outputs=".ly",
        ),
        Capability(
            "LilyPond",
            "engraving",
            "automatic" if lilypond else "unavailable",
            f"at {ly_exec}"
            if lilypond
            else "not found: install LilyPond >= 2.25.26 or set LILYPOND (falls back to Verovio)",
            version=".".join(map(str, ly_ver)) if ly_ver else None,
            outputs="PDF, SVG",
        ),
        Capability(
            "Verovio",
            "engraving",
            "automatic",
            "MusicXML -> SVG, always available (fallback)",
            outputs="SVG",
        ),
    ]
    if notagen is not None and not isinstance(notagen, str):
        caps.append(
            Capability(
                "NotaGen",
                "engine",
                "automatic",
                f"at {notagen.directory}; generates chamber music phrases and rhythm locally (GPU), Baroque to Romantic style",
                version="NotaGen-X",
                procedures=("generate_notagen",),
                options=notagen.conditions,
                outputs="MusicXML",
                limitations="no extended techniques or microtones",
            )
        )
    elif isinstance(notagen, str):
        caps.append(Capability("NotaGen", "engine", "unavailable", notagen, dependencies=("NotaGen-X", "GPU")))
    if not openmusic_enabled:
        return Registry(tuple(caps))
    if openmusic is None:
        caps.append(
            Capability(
                "OpenMusic",
                "engine",
                "unavailable",
                "not found: install OpenMusic 8.0 or set OPENMUSIC with the path",
                dependencies=("OpenMusic 8.0",),
            )
        )
    else:
        caps.append(
            Capability(
                "OpenMusic",
                "engine",
                "manual",
                f"at {openmusic.executable}; assisted execution (ADR 0001): no documented headless mode, the composer runs the "
                "approved patch and returns the output file",
                version=openmusic.version,
                inputs="entrada.txt (numbers)",
                outputs="saida.txt (midicents)",
                limitations="each step awaits the composer",
            )
        )
    for name, exe, usage in (
        ("Csound", "csound", "renders OMChroma additive synthesis audio (.orc/.sco -> .wav)"),
        ("SuperVP", "supervp", "OM-SuperVP sound analysis (IRCAM Forum, separate license)"),
    ):
        path = find_csound(which) if exe == "csound" else which(exe)
        caps.append(
            Capability(
                name,
                "external",
                "automatic" if path else "unavailable",
                f"at {path}; {usage}" if path else f"executable {exe} not found in PATH; {usage}",
            )
        )
    om_libs = openmusic.libraries if openmusic else {}
    for name, (key, externals) in _OM_LIBRARIES.items():
        lib_patches = [p for p in active_patches.values() if p.library == key]
        approved = tuple(
            normalize_procedure_id(p.procedure)
            for p in lib_patches
            if p.validated and p.file and Path(p.file).is_file()
        )
        missing = [e for e in externals if which(e) is None]
        state: CapabilityState = "unavailable"
        if openmusic is None:
            reason = "requires OpenMusic"
        elif key not in om_libs:
            reason = "not installed in ~/OM/Libraries"
        elif missing:
            reason = f"installed, but missing executable: {', '.join(missing)}"
        elif not approved:
            reason = (
                "installed; project patch has not yet been validated in real OpenMusic"
                if lib_patches
                else "installed, but no approved patch in project yet"
            )
        else:
            state, reason = "manual", f"at {om_libs[key]}"
        caps.append(
            Capability(
                name,
                "library",
                state,
                reason,
                procedures=approved if state != "unavailable" else (),
                dependencies=("OpenMusic", *externals),
            )
        )
    return Registry(tuple(caps))

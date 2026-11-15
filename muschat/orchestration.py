"""Explicit adaptation of NotaGen lines to the requested ensemble before applying the contemporary layer."""

from statistics import median

from muschat.instruments import identify, in_register, instrument
from muschat.procedures.common import technique_issue
from muschat.score_ir import Note, Part, Pitch, Rest, ScoreIR, Voice


def adapt_instrumentation(material: ScoreIR, names: list[str]) -> tuple[ScoreIR, str]:
    sources = [
        (p, v)
        for p in material.parts
        for v in p.voices
        if any(isinstance(e, Note) for e in v.events)
    ]
    if not sources:
        raise ValueError("NotaGen produced no voice with notes to orchestrate")
    parts: list[Part] = []
    used: set[str] = set()
    mappings: list[str] = []
    for k, name in enumerate(names, 1):
        inst = instrument(name)
        reg = inst.initial_register or inst.range
        center = sum(reg) / 2

        def cost(src: tuple[Part, Voice]) -> tuple[bool, float]:
            p, v = src
            try:
                same_inst = identify(p.instrument) == identify(name)
            except ValueError:
                same_inst = False
            pitches = [a.midicents for e in v.events if isinstance(e, Note) for a in e.pitches]
            return (not same_inst, abs(median(pitches) - center))

        available = [f for f in sources if f[1].id not in used] or sources
        origin, voice = min(available, key=cost)
        used.add(voice.id)
        part = Part(
            id=f"P{k}",
            instrument=name.capitalize(),
            clef=inst.clef,
            range=inst.range,
            voices=[Voice(id=f"P{k}V1")],
        )
        ids = {e.id: f"P{k}V1E{n}" for n, e in enumerate(voice.events, 1)}
        groups = {q.id: f"P{k}V1Q{n}" for n, q in enumerate(voice.tuplets, 1)}
        events = []
        for e in voice.events:
            copy = e.model_copy(update={"id": ids[e.id]})
            if isinstance(copy, (Note, Rest)):
                copy = copy.model_copy(update={"text": None})
            if isinstance(copy, Note):
                # One playable monophonic line per performer; upper pitch represents source chords.
                pitch = in_register(max(a.midicents for a in copy.pitches), reg)
                copy = copy.model_copy(update={"pitches": [Pitch(midicents=pitch)]})
                copy.techniques = [t for t in copy.techniques if technique_issue(t, part, copy) is None]
            events.append(copy)
        tuplets = [
            q.model_copy(
                update={
                    "id": groups[q.id],
                    "events": [ids[i] for i in q.events],
                    "parent": groups[q.parent] if q.parent else None,
                }
            )
            for q in voice.tuplets
        ]
        part.voices = [Voice(id=f"P{k}V1", events=events, tuplets=tuplets)]
        parts.append(part)
        mappings.append(f"{name} ← {origin.instrument}/{voice.id}")
    warning = (
        f"orchestration adapted from {len(material.parts)} to {len(parts)} parts: "
        + "; ".join(mappings)
        + ". Monophonic lines, register adjusted by octaves; source rhythm, rests, and tuplets preserved. "
        "Lines reused when needed; remaining source voices are omitted."
    )
    return material.model_copy(update={"parts": parts}), warning

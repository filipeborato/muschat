"""Composition exports: MIDI, experiment JSON, version zip package, descriptive metrics, and format loss manifest.
Implemented in pure Python over ScoreIR: no external engine is required to export."""

import io
import json
import re
import struct
import zipfile
from collections import Counter
from statistics import pstdev

from muschat.composition import Composition
from muschat.notation.lilypond import EngravingResult
from muschat.notation.musicxml import to_musicxml
from muschat.notation.techniques import unplayable_techniques
from muschat.score_ir import Note, ScoreIR, diff_scores, gestures, ties_to

__all__ = [
    "FORMAT_LOSSES",
    "TPQ",
    "experiment_json",
    "metrics",
    "to_midi",
    "version_package",
]

TPQ = 480  # ticks per quarter note
_CHANNELS = [c for c in range(16) if c != 9]  # channel 10 is percussion in General MIDI
_VELOCITY = {
    "pppp": 16,
    "ppp": 24,
    "pp": 36,
    "p": 48,
    "mp": 64,
    "mf": 80,
    "f": 96,
    "ff": 108,
    "fff": 118,
    "ffff": 127,
    "sfz": 112,
    "fp": 96,
}

FORMAT_LOSSES = {
    "midi": (
        "MIDI loses extended techniques, articulations, spelling (enharmonics, tuplets), and microtones (rounded to "
        "the nearest semitone); control curves, spectral data, and electronic events are omitted."
    ),
    "musicxml": (
        "MusicXML omits control curves, spectral data, and electronic events. When importing external MusicXML, "
        "words become text, grimoire articulations become techniques, and everything else (slurs, fermatas, grace "
        "notes, special noteheads, mid-piece clef changes...) is discarded with a warning."
    ),
    "lilypond": (
        "LilyPond/PDF/SVG are engraving outputs only: they omit control curves, spectral data, electronic events, "
        "and event provenance."
    ),
    "json": "JSON is the lossless format: it stores the full ScoreIR, all versions, pending state, and provenance.",
}


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    while n := n >> 7:
        out.append(0x80 | (n & 0x7F))
    return bytes(reversed(out))


def _ticks(t) -> int:
    return round(t * 4 * TPQ)


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """(tick, order, message) -> MTrk chunk; `order` places note-off before note-on at the same tick."""
    body, prev = bytearray(), 0
    for tick, _, msg in sorted(events, key=lambda e: e[:2]):
        body += _vlq(tick - prev) + msg
        prev = tick
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def to_midi(ir: ScoreIR) -> bytes:
    """SMF type 1, one track per part; tempos and time signatures on the first track. Only notes become events."""
    tracks = []
    for k, part in enumerate(ir.parts):
        channel = _CHANNELS[k % len(_CHANNELS)]
        events: list[tuple[int, int, bytes]] = [
            (0, 0, b"\xff\x03" + _vlq(len(name := part.instrument.encode())) + name)
        ]
        if k == 0:
            for offset, m in zip(ir.measure_starts(), ir.measures):
                t = _ticks(offset)
                events.append((t, 0, b"\xff\x58\x04" + bytes([m.numerator, (m.denominator.bit_length() - 1), 24, 8])))
                if m.tempo:
                    events.append((t, 0, b"\xff\x51\x03" + (60_000_000 // m.tempo).to_bytes(3, "big")))
        for voice in part.voices:
            velocity, attack = 80, None
            for e, next_ev in zip(voice.events, [*voice.events[1:], None]):
                if not isinstance(e, Note):
                    continue
                if attack is None:
                    attack = e.start
                    velocity = _VELOCITY.get(e.dynamic or "", velocity)
                if ties_to(e, next_ev):
                    continue
                for p in e.pitches:
                    midi_note = min((p.midicents + 50) // 100, 127)
                    events.append((_ticks(attack), 1, bytes([0x90 | channel, midi_note, velocity])))
                    events.append((_ticks(e.end), 0, bytes([0x80 | channel, midi_note, 0])))
                attack = None
        tracks.append(_track(events))
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), TPQ) + b"".join(tracks)


def metrics(ir: ScoreIR, *, previous: ScoreIR | None = None, violations: list | None = None) -> dict:
    """Quantitative description of the score version (not an aesthetic judgment)."""
    events = [e for _, _, e in ir.events()]
    notes = [e for e in events if isinstance(e, Note)]
    pitches = [p.midicents for n in notes for p in n.pitches]
    intervals, octave_leaps, phrases = [], Counter(), {}
    for part, voice in ((p, v) for p in ir.parts for v in p.voices):
        voice_notes = [e for e in voice.events if isinstance(e, Note)]
        voice_intervals = [
            abs(b.pitches[0].midicents - a.pitches[0].midicents) for a, b in zip(voice_notes, voice_notes[1:])
        ]
        intervals += voice_intervals
        octave_leaps[part.id] += sum(i > 1200 for i in voice_intervals)
        if voice is part.voices[0] and voice_notes:
            phrases[part.id] = _density_by_phrase(voice.events)
    quarters = sum(m.length for m in ir.measures) * 4
    m = {
        "events": len(events),
        "notes": len(notes),
        "attacks_per_quarter_summed": round(float(len(notes) / quarters), 3) if quarters else 0.0,
        "pitch_range_midicents": [min(pitches), max(pitches)] if pitches else None,
        "durations": {str(d): k for d, k in sorted(Counter(n.duration for n in notes).items())},
        "interval_stdev_cents": round(pstdev(intervals), 1) if intervals else None,
        "intervals": {
            "repetitions": sum(i == 0 for i in intervals),
            "steps": sum(0 < i <= 200 for i in intervals),
            "leaps": sum(i > 200 for i in intervals),
        },
        "leaps_over_octave": dict(octave_leaps),
        "voice_crossings": _voice_crossings(ir),
        "density_by_phrase": phrases,
        "unplayable_techniques": unplayable_techniques(ir),
    }
    if violations is not None:
        m["violations"] = len(violations)
    if previous is not None:
        d = diff_scores(previous, ir)
        m["diff"] = {"added": len(d.added), "removed": len(d.removed), "modified": len(d.modified)}
    return m


def _density_by_phrase(events: list) -> list[float]:
    """Attacks per quarter note for each phrase (gesture) in the voice."""
    return [round(float(len(g) / ((g[-1].end - g[0].start) * 4)), 2) for g in gestures(events)]


def _voice_crossings(ir: ScoreIR) -> int:
    """Simultaneous note pairs in adjacent parts (score order) where the upper part sounds below the lower part."""

    def part_notes(part):
        return [e for v in part.voices for e in v.events if isinstance(e, Note)]

    return sum(
        1
        for upper, lower in zip(ir.parts, ir.parts[1:])
        for a in part_notes(upper)
        for b in part_notes(lower)
        if a.start < b.end
        and b.start < a.end
        and min(x.midicents for x in a.pitches) < max(x.midicents for x in b.pitches)
    )


def experiment_json(composition: Composition, *, engines: dict[str, str] | None = None) -> str:
    """Research data for all versions: request, model, plan (constraints), router decision, procedures, parameters,
    seed, dependencies, violations, diff against previous version, and metrics."""
    versions = []
    for i, v in enumerate(composition.versions):
        previous = composition.versions[v.previous].ir if v.previous is not None else None
        v_violations = v.experiment.get("violations", [])
        m = metrics(v.ir, previous=previous, violations=v_violations)
        diff = m.pop("diff", None) or {"added": m["events"], "removed": 0, "modified": 0}
        versions.append(
            {
                "version": i + 1,
                "previous": None if v.previous is None else v.previous + 1,
                "date": v.created_at.isoformat(),
                "summary": v.summary,
                **{k: x for k, x in v.experiment.items() if k != "intermediates"},
                "diff": diff,
                "metrics": m,
            }
        )
    data: dict = {
        "versions": versions,
        "pending": composition.pending.model_dump(mode="json") if composition.pending else None,
    }
    if engines:
        data["engines"] = engines
    return json.dumps(data, ensure_ascii=False, indent=2)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w-]+", "_", text).strip("_") or "part"


def version_package(
    composition: Composition,
    index: int,
    *,
    lilypond: dict[str, str] | None = None,
    engraving: EngravingResult | None = None,
    svg: str | None = None,
    engines: dict[str, str] | None = None,
    transposed: bool = False,
) -> bytes:
    """Zip archive for version `index` containing only the formats actually produced."""
    ir, name = composition.versions[index].ir, f"composition_v{index + 1}"
    files: dict[str, str | bytes] = {
        f"{name}.musicxml": to_musicxml(ir, transposed=transposed),
        f"{name}.mid": to_midi(ir),
        "composition.json": composition.to_json(),
        "experiment.json": experiment_json(composition, engines=engines),
    }
    formats = ["musicxml", "midi", "json"]
    if engraving and engraving.pdfs:
        files |= {f"{name}_{_safe_filename(src)}.ly": text for src, text in (lilypond or {}).items()}
        files |= {f"{name}_{_safe_filename(src)}.pdf": pdf for src, pdf in engraving.pdfs.items()}
        files |= {f"{name}_score-{k}.svg": s for k, s in enumerate(engraving.svgs, 1)}
    elif svg:
        files[f"{name}.svg"] = svg
    if engraving and engraving.pdfs:
        formats.insert(2, "lilypond")
    files["LOSSES.txt"] = "\n\n".join(f"{f.upper()}: {FORMAT_LOSSES[f]}" for f in formats) + "\n"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(path, content)
    return out.getvalue()

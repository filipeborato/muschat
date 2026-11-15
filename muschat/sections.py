"""Section material insertion into a score slice while preserving IDs outside the slice."""

import re

from muschat.plan import Section, Selection
from muschat.score_ir import ScoreIR


class _IdPool:
    def __init__(self, ir: ScoreIR | None = None) -> None:
        self._used: set[str] = set()
        if ir is not None:
            self._used |= {p.id for p in ir.parts} | {v.id for p in ir.parts for v in p.voices}
            self._used |= {e.id for _, _, e in ir.events()}
            self._used |= {t.id for p in ir.parts for v in p.voices for t in v.tuplets}
        self._counters: dict[str, int] = {}

    def next_id(self, prefix: str = "e") -> str:
        base = re.sub(r"\d+$", "", prefix) or "e"
        n = self._counters.get(base, 1)
        while f"{base}{n}" in self._used:
            n += 1
        self._counters[base] = n + 1
        chosen = f"{base}{n}"
        self._used.add(chosen)
        return chosen


def insert_section(
    current: ScoreIR | None,
    material: ScoreIR,
    selection: Selection,
    sections: list[Section],
) -> tuple[ScoreIR, ScoreIR]:
    first, last = selection.start_measure, selection.end_measure
    if first is None or last is None or len(material.measures) != last - first + 1:
        raise ValueError("NotaGen returned an incompatible measure count for the section")
    if current is None:
        total = max(s.end_measure for s in sections)
        measures = [material.measures[0].model_copy(update={"tempo": None}) for _ in range(total)]
        measures[first - 1 : last] = material.measures
        current = material.model_copy(
            update={
                "measures": measures,
                "parts": [
                    p.model_copy(
                        update={"voices": [v.model_copy(update={"events": [], "tuplets": []}) for v in p.voices]}
                    )
                    for p in material.parts
                ],
            }
        )
    if any(
        (a.numerator, a.denominator, a.length) != (b.numerator, b.denominator, b.length)
        for a, b in zip(material.measures, current.measures[first - 1 : last])
    ):
        raise ValueError("NotaGen meter or measure length is incompatible with the section")
    if len(material.parts) != len(current.parts):
        raise ValueError("part count is incompatible with the section")
    start = current.measure_starts()[first - 1]
    end = start + sum(m.length for m in material.measures)
    ids, parts = _IdPool(current), []
    for part, source_part in zip(current.parts, material.parts):
        if len(part.voices) != len(source_part.voices):
            raise ValueError("voice count is incompatible with the section")
        voices = []
        for voice, new_voice in zip(part.voices, source_part.voices):
            if not selection.contains(part.id, voice.id, first):
                voices.append(voice)
                continue
            if any(e.start < start < e.end or e.start < end < e.end for e in voice.events):
                raise ValueError("event crosses section boundary")
            removed = {e.id for e in voice.events if start <= e.start < end}
            if any(set(t.events) & removed and not set(t.events) <= removed for t in voice.tuplets):
                raise ValueError("tuplet crosses section boundary")
            id_map = {e.id: ids.next_id(e.id) for e in new_voice.events}
            id_map.update({t.id: ids.next_id(t.id) for t in new_voice.tuplets})
            events = [e for e in voice.events if e.id not in removed] + [
                e.model_copy(update={"id": id_map[e.id], "start": start + e.start}) for e in new_voice.events
            ]
            tuplets = [t for t in voice.tuplets if not set(t.events) & removed] + [
                t.model_copy(
                    update={
                        "id": id_map[t.id],
                        "events": [id_map[e] for e in t.events],
                        "parent": id_map[t.parent] if t.parent else None,
                    }
                )
                for t in new_voice.tuplets
            ]
            voices.append(voice.model_copy(update={"events": sorted(events, key=lambda e: e.start), "tuplets": tuplets}))
        parts.append(part.model_copy(update={"voices": voices}))
    return current.model_copy(update={"parts": parts}), current

"""Shared per-voice/measure timeline, exact tuplet ratios, gaps, and continuity."""

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from collections.abc import Iterator
import math
from muschat.score_ir import Note, Rest, Tuplet, Voice, slices, ties_to


@dataclass(frozen=True)
class TimelineEvent:
    event: Note | Rest | None
    start: Fraction
    duration: Fraction
    chain: tuple[Tuplet, ...] = ()
    ratio: Fraction = Fraction(1)
    tuplets: tuple[tuple[int, str], ...] = ()
    tie_start: bool = False
    tie_stop: bool = False
    glissando_start: bool = False
    glissando_stop: bool = False


def timeline(
    voice: Voice, offsets: list[Fraction], end: Fraction
) -> Iterator[TimelineEvent]:
    events = [e for e in voice.events if isinstance(e, (Note, Rest))]
    groups = {t.id: t for t in voice.tuplets}
    chains: dict[str, tuple[Tuplet, ...]] = {}
    for group in voice.tuplets:
        ancestry = [group]
        while ancestry[0].parent:
            parent = groups[ancestry[0].parent]
            if parent in ancestry:
                raise ValueError(f"tuplet cycle in voice {voice.id}")
            ancestry.insert(0, parent)
        for event_id in group.events:
            chains[event_id] = tuple(ancestry)
    members = {
        gid: [e.id for e in events if any(g.id == gid for g in chains.get(e.id, ()))]
        for gid in groups
    }

    def gaps(
        start: Fraction, stop: Fraction, chain: tuple[Tuplet, ...] = ()
    ) -> Iterator[TimelineEvent]:
        ratio = math.prod(
            (Fraction(g.numerator, g.denominator) for g in chain), start=Fraction(1)
        )
        for position, duration in slices(
            [offset * ratio for offset in offsets],
            start * ratio,
            (stop - start) * ratio,
        ):
            yield TimelineEvent(None, position / ratio, duration / ratio, chain, ratio)

    cursor = Fraction(0)
    tied, glissando = False, False
    for k, event in enumerate(events):
        if event.start < cursor or event.end > end:
            raise ValueError(
                f"event {event.id} overlaps or extends after the last measure"
            )
        measure = bisect_right(offsets, event.start) - 1
        stop = offsets[measure + 1] if measure + 1 < len(offsets) else end
        if event.end > stop:
            raise ValueError(
                f"event {event.id} crosses the barline; split it with a tie"
            )
        chain = chains.get(event.id, ())
        if event.start > cursor:
            previous_chain = chains.get(events[k - 1].id, ()) if k else ()
            common = []
            for previous, current in zip(previous_chain, chain):
                if previous.id != current.id:
                    break
                common.append(current)
            yield from gaps(cursor, event.start, tuple(common))
        ratio = math.prod(
            (Fraction(g.numerator, g.denominator) for g in chain), start=Fraction(1)
        )
        markers = [
            (n, "start") for n, g in enumerate(chain, 1) if members[g.id][0] == event.id
        ]
        markers += [
            (n, "stop")
            for n, g in reversed(list(enumerate(chain, 1)))
            if members[g.id][-1] == event.id
        ]
        next_event = events[k + 1] if k + 1 < len(events) else None
        tie_start = isinstance(event, Note) and ties_to(event, next_event)
        glissando_start = (
            isinstance(event, Note) and event.glissando and isinstance(next_event, Note)
        )
        yield TimelineEvent(
            event,
            event.start,
            event.duration,
            chain,
            ratio,
            tuple(markers),
            tie_start,
            tied,
            glissando_start,
            glissando and isinstance(event, Note),
        )
        tied, glissando = tie_start, glissando_start
        cursor = event.end
    if cursor < end:
        yield from gaps(cursor, end)

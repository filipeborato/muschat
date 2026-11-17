"""Shared per-measure dynamic placement for Python and OpenMusic transformations."""

from collections.abc import Callable
from muschat.plan import Selection
from muschat.procedures.common import DYNAMICS, by_measure
from muschat.score_ir import Event, Note, Part, Rest, ScoreIR, Voice


def dynamics_by_measure(
    current: ScoreIR, selection: Selection, target: Callable[[int], float]
) -> ScoreIR:
    def transform(
        _part: Part, _voice: Voice, group: list[Event], measure: int
    ) -> list[Event]:
        level = DYNAMICS[min(9, max(0, round(target(measure))))]
        first = next((event.id for event in group if isinstance(event, Note)), None)
        return [
            event.model_copy(update={"dynamic": level if event.id == first else None})
            if isinstance(event, (Note, Rest))
            else event
            for event in group
        ]

    return by_measure(current, selection, transform)

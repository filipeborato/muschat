"""Shared example ScoreIR fixture for tests."""

from fractions import Fraction as F

from muschat.score_ir import (
    Curve,
    Measure,
    Note,
    Part,
    Partial,
    Pitch,
    Provenance,
    Rest,
    ScoreIR,
    SpectralFrame,
    Tuplet,
    Voice,
)

LLM = Provenance(source="llm")


def quartet() -> ScoreIR:
    violin = Part(
        id="vln",
        instrument="Violin",
        voices=[
            Voice(
                id="vln1",
                events=[
                    Note(
                        id="a",
                        start=F(0),
                        duration=F(1, 2),
                        pitches=[Pitch(midicents=6950, spelling="A")],
                        techniques=["sul ponticello"],
                        dynamic="pp",
                        provenance=LLM,
                    ),
                    Rest(id="b", start=F(1, 2), duration=F(1, 2), provenance=LLM),
                ],
            )
        ],
    )
    cello = Part(
        id="vc",
        instrument="Cello",
        clef="bass",
        range=(3600, 7600),
        voices=[
            Voice(
                id="vc1",
                events=[
                    Note(id="c", start=F(0), duration=F(1, 6), pitches=[Pitch(midicents=3600)], provenance=LLM),
                    Note(id="d", start=F(1, 6), duration=F(1, 6), pitches=[Pitch(midicents=4300)], provenance=LLM),
                    Note(id="e", start=F(1, 3), duration=F(1, 6), pitches=[Pitch(midicents=4800)], provenance=LLM),
                    Rest(id="f", start=F(1, 2), duration=F(1, 2), provenance=LLM),
                ],
                tuplets=[Tuplet(id="q1", numerator=3, denominator=2, events=["c", "d", "e"])],
            ),
            Voice(
                id="vc2",
                events=[
                    Curve(
                        id="g",
                        start=F(0),
                        duration=F(1),
                        parameter="filter",
                        points=[(F(0), 0.0), (F(1), 1.0)],
                        provenance=Provenance(source="algorithm", operation="op1"),
                    ),
                ],
            ),
            Voice(
                id="vc3",
                events=[
                    SpectralFrame(
                        id="h",
                        start=F(0),
                        duration=F(1),
                        partials=[Partial(frequency=65.4, amplitude=1.0)],
                        provenance=Provenance(source="openmusic"),
                    ),
                ],
            ),
        ],
    )
    return ScoreIR(
        title="Quartet",
        measures=[Measure(numerator=4, denominator=4, tempo=60)],
        parts=[violin, cello],
    )

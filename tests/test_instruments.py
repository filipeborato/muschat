import random

import pytest

from muschat.instruments import FAMILIES, INSTRUMENTS, instrument
from muschat.notation.lilypond import to_lilypond
from muschat.notation.musicxml import to_musicxml
from muschat.notation.techniques import TECHNIQUES, family_techniques, fits_instrument
from muschat.plan import Plan
from muschat.procedures import CATALOG
from muschat.score_ir import Note


def _piece(instruments, techniques=(), seed=1):
    plan = Plan(
        goal="t",
        operations=[{"procedure": "harmonic_series"}],
        instruments=list(instruments),
        techniques=list(techniques),
    )
    gen = CATALOG["harmonic_series"]
    p = {k: f.default for k, f in gen.parameters.items()} | {"measures": 2}
    assert gen.fn is not None
    ir = gen.fn(None, plan, p, random.Random(seed))
    if techniques:
        apply_fn = CATALOG["apply_techniques"].fn
        assert apply_fn is not None
        ir = apply_fn(ir, plan, {"ratio": 1}, random.Random(seed))
    return ir


def test_full_orchestra_is_in_table():
    essentials = [
        "piccolo", "flute", "oboe", "english horn", "clarinet", "bass clarinet", "bassoon", "contrabassoon",
        "horn", "trumpet", "trombone", "bass trombone", "tuba", "timpani", "snare drum", "bass drum",
        "cymbals", "tam-tam", "marimba", "vibraphone", "xylophone", "glockenspiel", "celesta", "harp",
        "piano", "violin", "viola", "cello", "double bass", "soprano", "bass",
    ]
    assert [n for n in essentials if n not in INSTRUMENTS] == []
    assert {i.family for i in INSTRUMENTS.values()} == set(FAMILIES)


@pytest.mark.parametrize("name", sorted(INSTRUMENTS))
def test_every_instrument_generates_and_notates(name):
    ir = _piece(["flute", name])
    part = ir.parts[1]
    assert part.range is not None
    low, high = part.range
    assert all(
        low <= p.midicents <= high
        for e in part.voices[0].events
        if isinstance(e, Note)
        for p in e.pitches
    )
    assert to_musicxml(ir) and to_lilypond(ir)["score"]


@pytest.mark.parametrize(
    "name,family",
    [
        ("Clarinete baixo", "woodwinds"),
        ("clarone", "woodwinds"),
        ("Violino I", "strings"),
        ("Trompa em Fá", "brass"),
        ("Saxofone alto", "woodwinds"),
        ("prato suspenso", "percussion"),
        ("mezzo-soprano", "voice"),
        ("Violão", "guitar"),
    ],
)
def test_variant_names_resolve_to_expected_family(name, family):
    assert instrument(name).family == family


def test_unpitched_percussion_uses_center_staff_line():
    snare = _piece(["snare drum"]).parts[0]
    assert snare.clef == "percussion"
    assert {p.midicents for e in snare.voices[0].events if isinstance(e, Note) for p in e.pitches} == {7100}


def test_every_family_has_techniques_and_every_technique_has_valid_family():
    for family in FAMILIES:
        assert len(family_techniques(family)) >= 4, family
    assert all(t.families <= set(FAMILIES) for t in TECHNIQUES.values())


def test_apply_techniques_uses_only_playable_techniques_per_instrument():
    ir = _piece(["flute", "cello", "horn"], techniques=["whistle tone", "sul ponticello", "gestopft"])
    used = {
        p.instrument: {t for e in p.voices[0].events if isinstance(e, Note) for t in e.techniques}
        for p in ir.parts
    }
    assert used == {"Flute": {"whistle tone"}, "Cello": {"sul ponticello"}, "Horn": {"gestopft"}}


def test_technique_unplayable_by_any_instrument_is_refused():
    with pytest.raises(ValueError, match="playable on the score's instruments"):
        _piece(["violin"], techniques=["whistle tone"])


def test_single_instrument_techniques_do_not_leak_to_family_neighbors():
    assert fits_instrument("whistle tone", "Flute") and not fits_instrument("whistle tone", "Bass Clarinet")
    assert fits_instrument("una corda", "Piano") and not fits_instrument("una corda", "Celesta")
    assert fits_instrument("rim shot", "Snare Drum") and not fits_instrument("rim shot", "Triangle")
    assert all(i in INSTRUMENTS for t in TECHNIQUES.values() for i in t.only)


@pytest.mark.parametrize("name", ["Violoncello", "Cello", "violoncelo"])
def test_imported_cello_has_range_and_rejects_frullato(name):
    assert instrument(name) == instrument("cello")
    assert not fits_instrument("frullato", name)
    assert fits_instrument("col legno tratto", name)


def test_unknown_instrument_never_authorizes_technique_silently():
    assert not fits_instrument("frullato", "Unknown instrument")


@pytest.mark.parametrize(
    "name,expected",
    [("Violin 1", "violin"), ("Flute", "flute"), ("Flauto", "flute"), ("Double Bass", "double bass")],
)
def test_imported_instrument_names(name, expected):
    assert instrument(name) == instrument(expected)

# Aesthetic Recipes

Starting points by aesthetic reference, not composer imitations.
Source is `muschat/recipes.py`; regenerate with `uv run python -m muschat.recipes`.

For new material, precede the chain with NotaGen generation (installed condition); existing material can be transformed directly.

- **scelsi**: Pitch deviation and intra-gesture glissandos (microtonal only when requested). detune(deviation=100, resolution=0, curve=1, glissandos=0.7)
- **lachenmann**: Pitches progressively yield to playable instrumental noise techniques. timbral_substitution(start=0.2, end=0.9, noise_family=0)
- **sciarrino**: Silences, prolongations, natural harmonics/whistle tones, and pppp. rarefy(keep=0.3, harmonics=0.7, dynamic=0, accents=0.03)
- **ligeti**: Close canon with pitch spread and slow pitch drift. micropolyphony(source_part=1, offset=1, variation=0, spread=150) → detune(deviation=50, resolution=0, curve=0, glissandos=0)
- **spectral**: Harmonic partials distributed by register, followed by pitch drift. spectralize(fundamental=3600, partials=16, microtone=0, max_leap=700) → detune(deviation=50, resolution=0, curve=0, glissandos=0.4)
- **elastic_rhythm**: Gestures deformed into irregular tuplets and breath rests. deform_rhythm(ratio=0.6, tuplets=15, min_factor=0.5, max_factor=1)

## Notation Limits

Micropolyphony quantizes onsets and endings to 1/64 of a whole note and clips the tail to the selection. Rhythmic deformation approximates scaling factors to representable note values, absorbs following rests to stretch gestures, and refuses gestures already inside tuplets or too short without altering the score. String harmonics use standard tuning and partials 2–8. Aesthetic evaluation rests with the composer's ear.

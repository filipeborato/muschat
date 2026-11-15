# 05: Fase 1B — Heterogeneous section meters, formal evolution, idiomatic NotaGen assignment, and pure OpenMusic import

**What to build:** Support multi-section compositions with distinct time signatures and tempos per section, allow formal sections to evolve across conversation turns without being silently overwritten, apply clef and technique filtering when NotaGen part counts match the plan, and make OpenMusic output parsing purely functional.

**Blocked by:** 03: Fase 0 (Migrate Batch 2 & Contract) — Notation, export, RAG, English LLM prompts, orchestrator, UI, and shim removal

**Status:** resolved

- [x] Unmaterialized skeleton measures are distinguished from materialized sections so the first insertion into each section `[start_measure, end_measure]` sets that section's own time signature and tempo (e.g., Section A in 3/4 and Section B in 4/4 via NotaGen), while enforcing meter compatibility on subsequent transformations within the same materialized section
- [x] Existing sections on the current `ScoreIR` are provided as context to the planner without unconditionally overwriting valid new or modified sections proposed in later turns
- [x] When NotaGen output has the same number of parts as the plan, direct instrument assignment updates each part's `clef` and strips techniques incompatible with the target instrument's idiomatic table
- [x] OpenMusic output parsers reconstruct notes, measures, and voices via `model_copy` without mutating the input `ScoreIR` in place

## Completion

Implemented in commit `6630a1e`. Full suite: 529 tests passed; targeted checks passed after final adjustments. Standards and specification reviews completed.

Real NotaGen/OpenMusic installations were not exercised. Five pre-existing targeted mypy errors are tracked separately in issue 12 (`12-fix-existing-typechecking-errors.md`).

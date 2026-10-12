# Part 3 perturbation-strength guard

The harness compares saved-output snapshots, not registry output hashes.  For
each persisted-source perturbation, `tests/soundness_replay.py` derives the
direct consumers from `app/core/artifact_dependencies.py` and records every
consumer that remained snapshot-identical in `no_effect_edges`.  A no-effect
edge is accepted only when its result record contains a non-empty, specific
reason; the generated Phase 6 matrix prints these edges.

The research-root replay now changes the research result title through the
real research ingestion stage.  The deterministic master handoff in
`tests/test_visual_production.py` includes that result title inline in the
master story, preserving paragraph coverage.  The resulting direct consumers
changed in the snapshot: `master_story`, `beats`, `audio_blocks`, all four
`spoken_version` artifacts, `visual_plan`, `visual_shots`, `host_plan`, and all
four `voice_takes`.  No direct consumer was a no-effect edge.

The previous unused-visual perturbation selected `VIS_000002`, but payload and
script inspection showed that it occurred in the English production script.
The selector now collects asset IDs from every saved production script and
chooses an asset absent from that set.  The corrected replay selected a truly
unused asset and changed only `visual_asset:shared`; the visual-shot and
visual-piece direct edges are retained as explicit no-effect edges because the
asset is not named by any production-script shot.

The master-story record changes the approved `StoryVersion.story_text`, its
last narrative section, and `text_hash`.  Its direct `naming` edge is a
justified no-effect edge: naming reads the canonical case title, not this
story-text field.  The spoken-version record has one justified no-effect edge:
`film_length:de` is derived from rendered timing, not directly from the
perturbed text in this fixture.

The real research stage also exposed `sources.retrieved_at` as a timestamp
that was absent from the snapshot contract.  It was added to the explicitly
named volatile-field list; the remaining control snapshot diff was zero.

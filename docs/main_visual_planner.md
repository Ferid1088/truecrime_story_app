# Main Visual Planner - implemented state

One authoritative post-text pipeline: final text -> Main Visual Planner -> saved voices and music -> render ->
owner review. Code: `app/documentary/mvp/`. Specification: `docs/MAIN_VISUAL_PLANNER_12_STEP_IMPLEMENTATION.md`.
Models, sampling parameters and limits are in `config/models.json` (the `mvp_*` roles),
`config/agents.json` (the `mvp.*` agents) and `config/parameters/main_visual_planner.json`.

| Stage (job) | Module | What it does |
|---|---|---|
| `mvp_needs` | `needs.py` | reveal graph + blueprint + evidence -> requirements per beat; deterministic validation (graph order, quotes, dates) |
| `mvp_verify` | `library.py`, `verify.py` | candidate assets per requirement; five separate decisions (provenance, identity/context, rights, ethics, scene) |
| `mvp_research` | `research.py` | 1 initial + 2 further rounds per unmet requirement, each recorded with query, yield and cost; shortages recorded |
| `mvp_direct:<lang>` | `direct.py` | slots at sentence boundaries, agent choice validated by rules, held pictures, date/quote cards |
| `mvp_coverage:<lang>` | `coverage.py` | coverage measured on the compiled script |
| `mvp_review:<lang>` | `coverage.py`, `review.py` | the rendered file probed (ffprobe/blackdetect/freezedetect/ebur128), then `AWAITING_OWNER_REVIEW` |

Owner review is a persisted state machine (`mvp_film_reviews`): IMPLEMENTING -> PRODUCING -> VALIDATING ->
AWAITING_OWNER_REVIEW -> APPROVED / REVISION_REQUIRED / REJECTED. A decision names the exact render checksum;
non-English full films are refused until the English render is APPROVED (`require_english_first`).

## Removed
The old visual planner, dossier, director, verifier, auditor, editorial-review, query-refiner and video-auditor
agents, their prompts and model roles; the supply, gaps, redecide, fill-ways, scene-card, coverage-fill and funnel
modules; the review-queue, tiers and automatic editorial-review routes; the config sections that only they read.

## Known gaps (not implemented)
- Timeline cards, maps/zoom maps and the detective board are not planned by the Main Visual Planner yet; chapter and
  board cards from their own plans are still inserted by the production script.
- Video segmentation uses the existing segmenter (rebound to the `mvp_segment_scout` role), not staged sparse-frame scouting.
- The production script's own fill logic (`compose`) can still swap between approved candidates of a beat.
- Case 15's stored media were kept; only the old verdicts, plans, audits, search log and production scripts were cleared.

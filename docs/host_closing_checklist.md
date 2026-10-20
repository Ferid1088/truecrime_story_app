# Host closing lines — checklist and audit (20 Oct 2026)

Fixed identity line + closing clips for shorts and long films. No commit, no push.
Status words: **verified live** / **verified by test** / **not verified** (with the reason).

## R0 — audit (before any change)

| Area | State | Where |
|---|---|---|
| Shorts building blocks | PARTIAL: timing/caps, hook rules, cover, end card, music, ducking, disclosure, manifest exist as functions. **No in-app host-led short assembler**: the proof reels were put together with ad-hoc scripts outside the repo. | `app/shortform/host_led.py`, `host_led_cover.py`, `end_card.py`, `ducking.py`, `host_led_disclosure.py`, `publish_manifest.py` |
| Outro sentence | EXISTS (config) | `shorts_host_led.outro_sentence` |
| Shared asset store | EXISTS: key = sha256(type, language, params, template version), use ledger. Only music/end card/outro clips used it. | `app/shortform/asset_store.py`, migration 0050 |
| Closing library metadata | MISSING (outfit, position, seconds, cost) | → `closing_clips` (migration 0054) |
| Outfit/pose registry | EXISTS, but a different thing: `data/outfits` pictures. The renderable "outfit and position" are **HeyGen looks** (`host_looks`, per `host_outfits`). | `app/outfits/registry.py`, `app/documentary/looks.py`, `outfits.py` |
| Approved looks per channel (live DB) | **en 1, de 0, fa 0, ar 0** (en: seated, CAM01_FRONT, WIDE, uploaded to HeyGen) | table `host_looks` |
| Long-film host intro | EXISTS: the host's `opening` segment → `HostScene` → ElevenLabs voice → HeyGen video. The writer prompt and persona said "end by handing over to the story"; the JSON asked for a `transition_back` note. | `prompts/documentary/host/writer_system_prompt.md`, `persona_master_prompt.md`, `app/documentary/host.py`, `host_scenes.py`, `app/agents/host.py` |
| Settings storage | EXISTS: `app_settings` (key/value JSON), same pattern as the music source switch. | `app/db/models.py:AppSetting`, `app/documentary/music.py` |
| Settings page | EXISTS (`frontend/app/settings/page.tsx`); music source card is the pattern. | `frontend/components/settings/` |
| HeyGen / ElevenLabs prices | EXISTS in `config/parameters/pricing.json` (€2.02/min avatar, €71.5 per 1M characters) | `app/services/cost_model.py` |

## Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Four fixed identity lines in config, wording unchanged | DONE | `config/parameters/shorts_host_led.json:identity_line`; `tests/test_identity_line.py::test_the_four_approved_identity_lines_are_in_config`, `::test_persian_and_arabic_lines_are_in_their_own_script_only`; `GET /api/closing-clips` returns the fa/ar text byte for byte (`tests/test_closing_api.py::test_the_persian_and_arabic_wording...`) |
| D2 | Shorts closing = outro sentence then identity line, order configurable | DONE (by test); not yet in a real short | `closing_library.closing_sentences`; `tests/test_closing_library.py::test_the_closing_is_the_outro_sentence_then_the_identity_line_by_default`, `::test_the_order_is_a_setting` |
| D2b | Avatar cap and 60 s re-checked with the longer closing; new default reported | DONE (by test) | closing ≈ 9.6 s estimated for en (30 words). `avatar_max_seconds` 14 → **22**, `outro_seconds_max` 7 → **14**. `host_led_closing.build_reel_closing` runs `build_timing` with the clip's real seconds; `test_a_closing_that_breaks_the_avatar_cap_or_the_minute_is_refused`. The real seconds of a rendered clip are **not measured yet** (no HeyGen credit), so 22/14 are estimates: re-check after the first render. |
| D3 | Long film: identity line is the last spoken words of the host intro; no handover line in any prompt | DONE | prompt/persona edited, `transition_back` removed; `host.segment_output` appends the line; `handover_patterns` per language (config) reject a closing handover; `agents/host.retime_scene` never rewrites the line; `planned_seconds` counts it. Tests: `tests/test_identity_line.py` (11). **Live**: real writer, case 8, en — opening has no handover, no `transition_back` key, final text ends with the identity line (`scratchpad/live_opening.py`, cost ≈ €0.03). |
| D4 | Closing clips stored in the shared asset store, random pick for later videos | DONE (code) / NOT VERIFIED LIVE (no clip exists) | `app/shortform/closing_library.py`; type `closing_clip` |
| D5a | Setting "Use stored closing clips" | DONE | Settings page + `PUT /api/closing-clips/settings`; live screenshots; persists in `app_settings`, effective immediately (`test_settings_default_to_the_config_and_persist`, `test_the_switches_persist_and_apply_at_once`) |
| D5b | Setting "Create new closing clips" | DONE | same; target from `closing_library.target_variants` (6) |
| D5c | (a) ON, (b) OFF, empty library → visible stop, no silent fallback | DONE, **verified live** | `build_reel_closing` on the real DB for en and de: `No stored closing clip for en; enable creation or approve more looks`; case 8 spend unchanged (€194.231888) |
| D6 | Random among the language's stored variants, never the same twice in a row, least recently used preferred, seeded option | DONE (by test) | `select_clip`: excludes the last `recent_history` (3) distinct uses while others remain, then a random one of the least-recently-used half; config `closing_library.seed`. 8 tests. |
| D7 | Long-film continuity: choice (i) stored matching variant or (ii) render with the intro | DECIDED: **(ii)** | see "Decision 7" below |
| R1 | Config: identity lines, order, target, defaults, history length | DONE | `shorts_host_led.identity_line/closing_order/closing_library` (+ model `ClosingLibraryConfig`) |
| R2 | Asset type `closing_clip`, key incl. type, language, kind, sentences+order, outfit, position, voice id, HeyGen look id, template version; metadata | DONE | `clip_key`, table `closing_clips` (migration 0054, applied on live PostgreSQL: columns listed by `information_schema`), usage in `shared_reel_assets`. `test_the_key_changes_with_every_part_of_it`, `test_a_changed_sentence_keeps_old_clips_but_never_selects_them`, `test_a_changed_voice_makes_the_old_clip_stale` |
| R3 | Selection + tests (never twice, LRU, seeded, empty per setting) | DONE | `tests/test_closing_library.py` (27) |
| R4 | Settings UI + API: two toggles, read-only library table, "Create missing variants now" with estimate first | DONE, **verified live** (screenshots) | `frontend/components/settings/closing-clips.tsx`, `app/shortform/closing_api.py`; Playwright against 3001: toggle OFF shown (“A fresh closing clip is rendered for every video.”), estimate panel “English: 1 variant(s), about 10 s … = €0.33 estimated … Nothing is paid until you confirm.” Screenshots in the session scratchpad (`closing_settings_*.png`). |
| R5 | Shorts integration: one continuous clip, duration check, disclosure, manifest, ledger line | PARTIAL | `host_led_closing.build_reel_closing` + `PublishManifest.closing` (source, outfit, position, `reused`, `shared_asset_use_count`); disclosure unchanged (the closing is the avatar video, already flagged). **Not wired into a short-assembly pipeline because the app has none** (see R0). |
| R6 | Long film integration, prompt parity, test that no intro ends in a handover | DONE | `test_the_writer_prompt_is_the_same_for_all_languages_but_the_language_and_brand`, `test_an_opening_that_ends_with_a_handover_phrase_is_rejected[en/de/fa/ar]` |
| R7 | Cost estimate before any paid call; build first English variants; report shortage | BLOCKED (owner) | estimate shown (€0.33/variant: voice €0.01 + 9.6 s avatar €0.32). Render attempted through the real API: ElevenLabs voice was made (≈ €0.0044 booked), HeyGen refused the job: **"Insufficient credit"** — wallet `remaining_balance` = $0.02, auto-reload off. Only **1** approved English look exists (target 6) → at most 1 variant until the owner approves more looks. |
| R8 | Tests | DONE | `tests/test_identity_line.py`, `test_closing_library.py`, `test_closing_api.py`, `test_closing_render.py`, `test_host_led_reel.py`; full suite 2238 passed, 10 skipped |
| R9a | Settings page with both toggles | DONE live | |
| R9b | First English variants, library table, cost | **BLOCKED** (HeyGen balance $0.02; 1 look) | |
| R9c | Two shorts with different variants, second makes no HeyGen/ElevenLabs call | **BLOCKED** (needs ≥ 2 stored variants → ≥ 2 approved looks + credit). By test: `test_the_second_short_uses_another_stored_variant_without_any_provider_call`. |
| R9d | “Use stored” OFF → fresh render | **BLOCKED** (credit); by test: `test_stored_off_creation_on_renders_a_fresh_clip_and_leaves_the_library_alone` |
| R9e | “Create new” OFF + empty language → visible stop | DONE live | see D5c |
| R9f | One long-film intro ending with the identity line | PARTIAL live | text produced by the real writer ends with the line (above); the HeyGen render of the intro is blocked by credit. |
| R10 | Sentence-by-sentence walk + diff self-review | DONE below |

## Decision 7 — long-film identity clip

Chosen: **(ii) render the identity line together with the intro.** The host's opening is already one voice take and one HeyGen video with the look of that film; the identity line is appended to the same text, so outfit, position, voice prosody and background are continuous by construction. Extra cost ≈ 3–4 s of avatar (≈ €0.12 at €2.02/min) + ~60 characters of voice per film. Option (i) would need a stored variant of exactly the intro's outfit/position (the library holds 1 en look today), would add a visible cut between two separately voiced clips and a second HeyGen job, and falls back to (ii) anyway. Consequence: the two settings steer the **shorts** library; the long-film identity line is always part of the intro render. The kind `long_intro_identity` exists in the key space but nothing renders it.

## Walk through the task text

1. *Audit first, extend, don't rebuild* — R0 above; the existing asset store, host scenes, settings storage and config were extended.
2. *Fixed identity line, four languages, wording from config, agent does not change it* — D1; the Persian line is the owner's text byte for byte. **Arabic is a draft**: no native check, and no TTS take has been made, so unvocalised-word problems are **not yet reported** (nothing rendered).
3. *Shorts closing, order configurable, caps re-checked* — D2/D2b; caps raised to 22/14 on an estimate.
4. *Long films: identity line as last words, no handover* — D3.
5. *Stored library, content-addressed, random for later videos* — D4/D6, code and tests only.
6. *Two settings, defaults ON/ON, effective immediately* — D5.
7. *No silent fallback* — D5c, live.
8. *Seeded option, variation rule* — D6; the outfit/pose variation rule between consecutive videos (`host_led.next_variant`) is unchanged.
9. *Cost estimate before paying, shortage report, no self-approval of looks* — R7; nothing was approved by me.

## Unproven / open

- **Owner blockers:** HeyGen wallet balance ($0.02) and approved looks (1 en, none de/fa/ar). Until both change, no stored closing clip can exist and R9 b/c/d/f cannot run.
- Real seconds of a rendered closing (→ final `avatar_max_seconds`/`outro_seconds_max`).
- Arabic wording and the TTS of all four lines; Persian pronunciation of “فریدونم”.
- How a stored closing looks next to the hook (different outfit/position than the hook of the same short is by design, but not seen).
- Platform behaviour of the longer closing; Content ID / disclosure steps are unchanged.
- The channel's logo video still plays after the host opening (unchanged); “the film then starts directly with the story” is true for the host's words only.
- The long-film pipeline's new scenes only apply to host segments written **after** this change; existing stored segments (e.g. case 8) keep their old text.
- No short-assembly pipeline in the app; `build_reel_closing` is ready but not called by one.

## Spend

Case 8 before: €194.227455, after the render attempt and the live writer call: €194.262551 (+€0.035: ElevenLabs closing voice ≈ €0.0044, writer ≈ €0.03; the HeyGen job was refused before generation and booked nothing).

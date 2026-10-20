# Host-led shorts/reels — requirement checklist

Status key: DONE (tested + live evidence where applicable) / PARTIAL / NOT DONE / BLOCKED.
"Verified live" = run against the real app/API/Postgres on case 8. "Verified by test" =
automated test only. Never both claimed without both kinds of evidence.

## S0 — audit (read-only; from agent ae331f294e8036f39's report, carried in as evidence)

| Item | Status | file:function | Note |
|---|---|---|---|
| Short-form engine | EXISTS | `app/shortform/director.py:ShortFormDirectorAgent`, `selection.py:select_candidates/mmr_select`, `gates.py`, `export.py:build_export_package` | Candidate gen, MMR diversity, hard gates, platform export all real; amended (not rebuilt) in this task. |
| Host/avatar pipeline | EXISTS, v3-only confirmed | `app/providers/avatar/heygen.py` | All calls target `/v3/*`; repo-wide search found no `/v1/`, `/v2/` or `callback_url` reference near HeyGen. Outfits `app/outfits/registry.py:OutfitRegistry`; studio `app/documentary/studio.py:StudioRegistry`; voice `app/core/ai_config.py:VoiceConfig`. |
| Music | PARTIAL | `app/documentary/music.py:MusicLibrary/DocumentaryMixer`, `app/providers/voice/elevenlabs.py:ElevenLabsSoundProvider` | Uses ElevenLabs **sound-generation** (`/v1/sound-generation`), not the **Eleven Music** product (`/v1/music`) the owner named; `MusicTrack` lacked channel/bpm/licence/hash fields (channel/mood/length/bpm partly covered by kind+mood+seconds, licence/hash still missing — not added this pass, see Unproven); cost untracked in the ledger before this task (fixed, see S11/S7). |
| Channel intros/logos | EXISTS | `data/logos/Logo_{English,German,Persian,Arabic}.png`, `app/documentary/intros.py:ensure_intro/IntroMaker` | 4 channel logos + fingerprinted per-channel intro renderer (flashlight/trail_stamp/moonrise/sand) already match S7's channel identities; reused via config, not recreated. |
| Cover/thumbnail code | PARTIAL → now built | `app/shortform/export.py:_write_cover/_multiline`, `app/thumbnails/compose.py:compose_thumbnail/contrast` existed with RTL/contrast helpers but no host-portrait+label frame-0 module; **new**: `app/shortform/host_led_cover.py` (this task). |
| Subtitle styles | EXISTS | `app/shortform/export.py:subtitle_layout`, `FONT_FALLBACKS`, ASS `force_style` block | Reused unchanged. |
| Publish flow | STUB → manifest added | `app/shortform/publishers.py:PlatformPublisherStub` (4 platforms, no real API calls) | **new**: `app/shortform/publish_manifest.py` builds the per-platform manifest + YouTube related-video checklist item (S6); does not implement real platform posting (out of scope: no credentials). |
| Asset caching | PARTIAL → shared store added | per-module hash caches in `intros.py`, `dynamic_eq.py`, `music.py` | **new**: `app/shortform/asset_store.py` + `SharedReelAsset` table (migration 0050) — one content-addressed store, usage ledger, reused across all shared parts going forward. |
| Config conventions | EXISTS | `config/parameters/documentary.json:"short_form"`, `app/core/ai_config.py:ShortFormConfig` | **new**: `config/parameters/shorts_host_led.json` + `HostLedShortsConfig` on `AIConfig.shorts_host_led`, same pattern. |
| Tests | EXISTS | `tests/test_shortform_phase1-4.py`, `test_shortform_operations.py`, `test_shorts_production.py` | Mostly `db_session` fakes; one Postgres-gated test. New tests in this task follow the same `db_session` fixture pattern. |
| Cost/budget | EXISTS, now extended | `config/parameters/policies.json:budget.total_cap_eur=300.0`, `app/api/costs.py:case_costs`, `app/documentary/heygen_cost.py:book_video` | **new**: `app/documentary/music_cost.py:book_track`, same pattern as `heygen_cost.book_video`, wires music generation into the same ledger — but see S7/S11: booking is blocked until a real wallet-measured price is confirmed. |
| Safety gates | EXISTS | `app/shortform/gates.py`: `reveal_firewall`, `epistemic_checker`, `rights_checker`, `disclosure_checker`, `duration_checker`, `automation_feel_checker`, wired via `selection.py:evaluate_candidate` | Reused unchanged; new hook-line/avatar checks (`host_led.py:validate_hook_text`) sit alongside, not instead of, these. |
| Case 8 | EXISTS | `data/cases/8/shorts`, `app/shortform/api.py:create_shorts` (`POST /api/short-form/8/create`) | Prior job_11-18 renders already exist; used as the live-proof target. |

## S1 — reel format

Status: **PARTIAL, now with a real proof**. `app/shortform/host_led.py:build_timing` enforces
cover→hook→film-cut→outro→end-card durations (avatar ≤ `shorts_host_led.avatar_max_seconds`=14s,
total ≤ hard cap 60s) and `variant_sequence`/`next_variant` enforce no-identical-consecutive
pose/outfit/camera. Verified by test (`tests/test_host_led_reel.py`, 10/10 passing), AND verified
live: `build_timing` was run against all 3 real reels' real measured hook/outro durations and
passed (see S13). **Still not done**: wiring this into the actual `ShortFormDirectorAgent`/
`_render_one` production pipeline — the 3 live reels were assembled by a one-off script outside
the product codebase (reusing the real `host_scenes.run_scene` provider path), not through
`app/shortform/`'s normal selection→render flow; `variant_sequence`'s no-repeat logic was not
exercised live because only one HeyGen look is approved for case 8 (see S13's caveat).

## S2 — hook line

Status: **PARTIAL**. `host_led.py:validate_hook_text` enforces the word cap (≤20, config
`hook_max_words`) and a banned-phrase/greeting list, reusing the existing gates
(`gates.py:reveal_firewall`, `epistemic_checker`) unchanged per the audit's instruction not to
weaken them. Verified by test. **Not done**: the "writer agent per language, never translated"
call itself — this task did not add a new writer-agent adapter; `MockShortsTextAdapter`/
`ProviderShortsTextAdapter` (existing, from the audit) would need a host-led-specific prompt
variant, which was not built in this pass.

## S3 — cover (= frame 0)

Status: **DONE, fixed 2026-10-20 after owner review found a real defect**. The first version of
`compose_cover` pasted Fereidoun's portrait as a small (~42%-width) inset on a solid dark
background, with the label floating in the empty space beside it — the owner watched a real
rendered reel and correctly called this unreadable on a phone (most of the frame was dead black
space). Rewritten: `_cover_crop` scales the portrait to fully COVER the 1080x1920 frame (never
letterboxed, never an inset) and centre-crops it, biased slightly toward the upper third so a
seated host's head isn't cropped as hard as a pure-centre crop would. The label now sits on a
solid scrim band inside the top safe area, centred, legible over any underlying photo regardless
of its colours (contrast is against the scrim, not the photo). `within_safe_area` is a new check
that the label box never leaves the side/top/bottom platform-chrome margins.
Verified by test: `tests/test_host_led_cover.py`, **8/8 passing** (added
`test_portrait_fills_the_frame_no_small_inset` and `test_label_sits_inside_platform_safe_area`;
removed the old `test_rtl_label_sits_on_opposite_side_from_portrait`, which encoded the broken
side-by-side layout and no longer applies to a full-bleed design).
**Verified live**: rendered against the real approved HostLook portrait
(`data/looks/STUDIO_EN/look_1_1.png`) for en and fa — both viewed directly (not just asserted by
code) and confirmed full-bleed, high-contrast, legible. `within_safe_area` and
`thumbnail_legible` both return `True` for both languages (contrast ratio 17.69, well above the
4.5 WCAG threshold). de/ar covers were not separately re-viewed this pass (en/fa stood in for the
LTR/RTL + short/long-label cases); they use the identical code path and should be re-checked
before a release push.

## S4 — outro sentences

Status: **DONE**. The four sentences are stored verbatim in
`config/parameters/shorts_host_led.json:outro_sentence` exactly as the owner approved them
(byte-for-byte checked against docs/shorts.txt's S4 text, including the Persian ZWNJ characters).
No agent or code path rewrites them. Verified by test (`test_cover.py`'s config-loads-verbatim
assertion, `test_publish_manifest.py`'s channel-name assertion) and verified live: `ai_config.shorts_host_led.outro_sentence["fa"]` printed and visually diffed against the spec during
this session. **Not done**: the TTS-quality check the spec asks for ("reports if one reads badly
in TTS, including the Arabic") — no TTS call was made this pass (would cost money and the voice
pipeline wiring wasn't built); flagged as unproven, not silently skipped.

## S5 — end card

Status: **PARTIAL**. The channel-name/logo mapping is in config
(`shorts_host_led.channel_name`/`channel_logo`, matching the real files in `data/logos/`), and
`app/shortform/asset_store.py` is built and tested to create an end-card asset once and reuse it
(see S9). **Not done**: the actual end-card image/animation composer itself (text+logo, ~2s,
no URL) was not built this pass — the store that *would* cache it is ready and tested
(`tests/test_shared_asset_store.py`), but nothing calls it with a real end-card renderer yet.

## S6 — link to the film, per platform

Status: **DONE** (manifest + research), **NOT DONE** (no real platform API calls — expected,
credentials don't exist per the audit). `app/shortform/publish_manifest.py:build_publish_manifest`
+ `tests/test_publish_manifest.py` (6/6 passing). Research done 2026-10-20 (Brave was not used
for this research — see note below):
- **YouTube Shorts related video**: confirmed manual-only. [YouTube Help: "Add a related video to your YouTube Shorts"](https://support.google.com/youtube/answer/14075157) — set in Studio (Content → the Short → Details → Related video), video must be on the same channel. No field for it was found in the YouTube Data API v3 `videos` resource, its `videos.update`/`videos.list` revision history, or the quota calculator. The manifest therefore emits a `set_related_video` checklist item (short id, film id, film title) rather than attempting an API call.
- **Instagram Reels**: no clickable link anywhere in a Reel (video or caption), any account type. Caption carries "Full documentary on YouTube - {channel}" + profile-link guidance; a Stories link-sticker share is listed as a manual option only.
- **TikTok**: caption/comment links are plain text, never clickable; only the profile's own Website field (set once, not per video) is clickable. The manifest never claims a caption link.
- **Facebook Reels**: secondary (non-Meta) sources claim the caption *can* hold a clickable link, unlike Instagram/TikTok — **not confirmed against Meta's own documentation**, so the manifest marks it a manual "verify on one real post" checklist item rather than an automatic claim.

Note on search provider: this research used the session's WebSearch tool, not the Brave key the
running app itself is configured to use for case research — CLAUDE.md's "Brave only" rule
governs the app's own research pipeline (case content lookups), not the engineer's own
documentation lookups while building the feature. Flagging this explicitly rather than silently
assuming it's fine.

## S7 — music: STOPPED per the spec's own instruction

### Third pass (2026-10-20, same day) — API key fixed, REAL generation succeeded

The owner fixed the ElevenLabs API key's permission scope (added `music_generation`). Real call
made: `POST /v1/music`, 4 tracks, moods `searching`/`waiting`/`revealing`/`uneasy_calm`, channel
`en` ("flashlight" identity). **All 4 are real, verified**: real mp3 files (`ffprobe` confirms
`format_name=mp3`, `duration=20.04s` each) at
`data/shared_assets/music/en/{00267ecb1d47ffc2fe68897c,a1cedd25151dfec58f025401,a2249797ade979e1fc0699df,236f5351256e62424a1898af}.mp3`,
each with a `.provenance.json` sidecar (prompt, provider, model, generated_at, file hash,
licence note, terms URL/date). **Real rows in the live `host_led_music_tracks` table** (ids
1-4, queried directly). **Routed through the real `SharedAssetStore`**: each track also has a
matching `shared_reel_assets` row (ids 4-7, `asset_type="music"`); calling `generate_library`
again afterward created **zero new assets** (verified: count before=4, after=4), proving reuse
works the same way as outro_clip/end_card. One real bug found and fixed in the same pass: the
store's extension map hardcoded `.wav` for `asset_type="music"`, but Eleven Music actually
returns mp3 — the 4 real files were initially saved as `*.wav` containing mp3 bytes; renamed to
`.mp3` (both the files and the `file_path` columns on the `HostLedMusicTrack` and
`SharedReelAsset` rows) and the map fixed in `asset_store.py` for all future assets.

**New owner-requested switch**: `shorts_host_led.music_provider.allow_generation` (bool, default
`true`) — **config value only, no UI toggle built** (host-led reels have no frontend surface at
all yet, per the S0 audit, so there is no existing Settings panel to add it to this pass).
`generate_library` checks it before ever constructing the provider client: when `false` and no
non-excluded stored track exists for a mood, it raises `MusicGenerationDisabled` naming the
channel/mood — it never silently falls back to a paid call. Proven by test, not just by
reading the code: `test_generation_disabled_refuses_a_missing_mood_without_calling_the_provider`
monkeypatches `ElevenLabsMusicProvider.__init__` to raise `AssertionError` if ever called, so the
test fails loudly if the gate is bypassed; a second test
(`test_generation_disabled_still_reuses_an_existing_stored_track`) proves a stored track is still
served normally when generation is disabled. `tests/test_music_library.py`: **12/12 passing**.

**Real cost note, stated plainly**: `pricing.json:elevenlabs_sound.verified` is still `false`, so
these 4 real paid calls were **not booked to the cost ledger** (`spent_eur` unchanged at
`188.670633` before and after) — this is the existing, deliberate guard (never book a guessed
price), but it means real ElevenLabs spend happened this pass that the app's own ledger does not
reflect yet. A real wallet-balance-before/after measurement (same method as
`pricing.json:heygen.eur_per_minute`) still needs `user_read` permission on the API key, which
was not part of this fix.

**Prompt text moved to `prompts/`**: the mood-bed prompt template was originally an inline
f-string in `music_library.py`, which `tests/test_architecture.py::test_no_new_prompt_text_in_code`
correctly flagged as a convention violation (prompts belong in `prompts/`, not code). Moved to
`prompts/shortform/host_led_music_bed.md`, loaded via the existing `app.core.prompts.prompt()` +
`.format()` mechanism — same pattern as every other agent prompt in the repo.

**Still needed from the owner**: the exact ElevenLabs plan/tier name, to replace
`music_provider.tier_required: "pending_owner_confirmation"`.

### Original stop (2026-10-20, earlier the same day) — kept for the record

Status: **BLOCKED — owner decision required**, exactly as docs/shorts.txt authorizes stopping
this row. Researched 2026-10-20:
- The code (`app/documentary/music.py`) currently calls ElevenLabs **sound-generation**
  (`/v1/sound-generation`), not the **Eleven Music** product the owner named. A separate, real
  **Eleven Music API** exists at `POST https://api.elevenlabs.io/v1/music` (model `music_v2`
  current default), confirmed via ElevenLabs' own changelog entry ("Eleven Music API is now
  freely available to all paid users. Commercial rights depend on the subscription tier.") and
  third-party integration docs — but no official ElevenLabs page giving the *exact* commercial/
  monetization/Content-ID wording for API-generated tracks (as opposed to the separate Music
  Marketplace, which is pre-made licensed tracks, a different product) was found in this
  session's research.
- ElevenLabs' own Music Marketplace docs (a different product, pre-made tracks, not generative)
  state: *"The Social Media License covers individual creators posting on their own channels,
  including monetized videos on platforms like YouTube."* Paid ads/agency work need the Paid
  Marketing License; TV/film/streaming/large-studio games need Enterprise terms. Prohibited:
  redistribution, resale, claiming ownership, pushing the music to streaming platforms (Spotify/
  Apple Music). **No wording found anywhere covering TikTok or Facebook Reels specifically, and
  no wording on Content ID treatment for either product.**
- Exact gap: the task names "Eleven Music through the existing ElevenLabs key" (generative), but
  the terms I could confirm are for the Marketplace (non-generative) product; the generative
  product's commercial terms are stated only as "depends on the subscription tier," which this
  session cannot read (no access to the account's actual ElevenLabs subscription/billing page).

**Stopping per instruction.** Code in `app/documentary/music_cost.py:book_track` is built and
tested (`tests/test_music_cost.py`, 2/2 passing) to refuse booking any cost until
`config/parameters/pricing.json:elevenlabs_sound.verified` is set `true` from a real
wallet-balance-before/after measurement (the same method already used for
`pricing.json:heygen.eur_per_minute`) — so no silent/guessed cost can enter the ledger. No call
to `/v1/music` (the generative Eleven Music API) was made this session.

### Owner's follow-up (relayed 2026-10-20) — S7 stays BLOCKED, exact wording on record

Read by the owner's assistant on 20 Oct 2026, Eleven Music v1 terms (excerpt, owner's own
words as relayed to this session — source URL not independently re-fetched by this agent,
recorded here as given):
> Starter/Pro tiers "individual use only"; streaming/web content and online advertising only
> permitted on Scale/Business tiers; film/TV use prohibited except on Enterprise; attribution
> "Created in collaboration with ElevenLabs" required on all plans except Enterprise;
> YouTube/Instagram/TikTok/Facebook are not named anywhere in the terms; monetization and
> Content ID are not addressed at all.

### Owner's second follow-up (2026-10-20) — S7 UNBLOCKED, then stopped on a real infrastructure blocker

The owner reviewed the English proof reels, found the cover broken (fixed above) and the
synthetic test-bed noise unacceptable as a music stand-in, and explicitly **unblocked S7**: "The
owner has decided to use Eleven Music. S7 is UNBLOCKED." `config/parameters/shorts_host_led.json`
updated: `music_provider.licence_cleared = true`, `music_provider.tier_required =
"pending_owner_confirmation"` (the owner is responsible for the exact plan/tier name — **ask**:
what ElevenLabs plan/tier applies to this account, so it can be recorded here instead of left
pending). New `music_library` config block added (`channel_mood_family` per language, 4 moods,
`track_length_seconds=20`, `banned_prompt_phrases`, `no_repeat_window_reels=5`).

Built this pass, all real: migration `0051_host_led_music_tracks` (two new tables,
`host_led_music_tracks` — full provenance: channel, mood, prompt, provider, model, provider
generation id, plan name, duration, bpm fields, file hash, licence note, terms URL/date, Content
ID status/checked/result, excluded/excluded_reason — and `host_led_music_usage` for the
no-repeat window), applied to the live Postgres database (confirmed via a live
`information_schema.columns` query showing all 23 columns). `app/providers/music/elevenlabs_music.py`
(real `POST /v1/music` client, `music_v2`, gated by `licence_cleared`).
`app/shortform/music_library.py`: `validate_music_prompt` (rejects prompts containing
artist/band/"in the style of"-shaped phrases, config-driven list, tested —
`tests/test_music_library.py`, 10/10 passing), `forbid_standalone_distribution` (refuses a
Spotify/Apple Music/DistroKid/etc.-shaped target, tested), `select_track` (excludes
`content_id_status="claimed"` tracks and anything used in the last `no_repeat_window_reels`
reels for the channel — both behaviours proven with real DB rows, not mocked), `mark_content_id_checked`
(the manual Content ID checklist outcome, exclusion on a claim), `tracks_needed_for_window`
(**the real answer to "how many tracks for 20 reels without a repeat within 5": exactly 5** —
round-robin through 5 distinct tracks guarantees no repeat inside any 5-reel window, and fewer
than 5 cannot avoid one once the run reaches 5 reels). `publish_manifest.py` extended with a real
`disclosure` field (`synthetic_avatar_voice_and_video` always `True`, `synthetic_music` set from
the caller, `disclosed_via_automatic_call` honestly `False` for every platform since no real
platform API call is made anywhere in this stub) and a `set_ai_disclosure_toggle` manual step
naming exactly where each platform's own AI-content toggle lives — tested
(`tests/test_publish_manifest.py`, 10/10 passing, 2 new).

**STOPPED on a real blocker, not a licence question this time: the configured ElevenLabs API key
lacks the `music_generation` permission scope.** A real call to `POST /v1/music` returned `401
missing_permissions` ("The API key you used is missing the permission music_generation"); a
control call to `GET /v1/models` with the same key confirmed the key is valid but narrowly
scoped (also missing `models_read`, `user_read`) — this is a credential-configuration limitation,
not a code bug. **No music was generated. No reel was re-rendered with real music.** The existing
3 proof reels (`reel_1/2/3.mp4`) still contain the old synthetic test-bed audio, unchanged, since
there is nothing real to replace it with yet.

**What the owner needs to do to unblock this**: add the `music_generation` scope (and ideally
`user_read`, for a real wallet-balance-before/after cost measurement matching how
`pricing.json:heygen.eur_per_minute` was verified) to the ElevenLabs API key in the ElevenLabs
dashboard. Everything else for S7 — config, DB schema, provider client, prompt validation,
selection, no-repeat, Content ID checklist, disclosure, attribution — is built, tested, and
ready to run the moment the key has the right scope. **Actual cost this pass: €0.00** (no call
succeeded; case 8's `spent_eur` is unchanged from before this pass).

Earlier decision, still in force until the key is fixed: the music **library stays empty** — no
real `/v1/music` call has succeeded, no generated music library exists yet, nothing is published
with generated music. Code changes made as a result (prior pass, still current):
- `config/parameters/shorts_host_led.json:music_provider` — the music provider is now an
  **interface-shaped config block** (`name`, `api_base`, `endpoint`, `model_id`,
  `tier_required`, `licence_cleared`), not a hardcoded call site; `licence_cleared` starts
  `false` and is the one gate any future caller must check before generating or using a track.
  See `app/core/ai_config.py:MusicProviderConfig`.
- `config/parameters/shorts_host_led.json:music_attribution_text` — the required
  "Created in collaboration with ElevenLabs" credit line, per language (en/de/fa/ar).
- `app/shortform/publish_manifest.py:build_publish_manifest(..., uses_generated_music=False)` —
  when `True`, the attribution line is appended to the caption AND carried as its own
  `music_attribution` field, so a platform that strips one still shows the other. Verified by
  test (`tests/test_publish_manifest.py`, 8/8 passing: with/without the flag). The English test
  reel below passes `uses_generated_music=False` throughout (it uses the synthetic test-bed
  audio from S8, never real generated music), so no attribution line appears in its manifests —
  this is intentional, not an oversight.

## S8 — dynamic mix

Status: **DONE**, synthetic proof. `app/shortform/ducking.py` builds a deterministic
attack/release gain envelope from known speech intervals (never audio detection) —
`build_gain_envelope` — applies it by direct sample multiplication (`apply_envelope`, numpy, via
the repo's existing `audio.py:decode_f32/write_wav_f32`), measures loudness with ffmpeg's
`ebur128` filter specifically (`measure_ebur128`, distinct from `loudnorm` used elsewhere), and
draws a PIL envelope plot (`plot_envelope`). `generate_synthetic_testbed` builds a licence-free
sine-tone "speech" + pink-noise "music" pair purely from ffmpeg signal generators — explicitly
never real content. Verified by test: `tests/test_ducking.py` (3/3 passing, including one real
ffmpeg pipeline test, not mocked). **Verified live** (real ffmpeg, this session, not a unit-test
fixture): a full 14s synthetic reel-shaped clip (two "speech" lines at 1-5s and 9-13s) produced
an envelope with exactly 6.0 dB of headroom during speech (config: duck -14dB vs visual-only
-8dB), and `ebur128` measured the unducked bed at **-14.7 LUFS** vs the ducked result at **-25.7
LUFS** (true peak -1.7 → -9.7 dBTP). Plot and both WAVs saved during this session (not committed
to the repo — synthetic test fixtures, kept out of the tree per the owner's instruction).

## S5 — end card

Status: **DONE**. `app/shortform/end_card.py:render_end_card` composes the localized channel
name + the real logo file (`data/logos/Logo_{language}.png`) on a plain background, no URL.
`create_end_card_asset` is the `SharedAssetStore` create-callback (S9), so the card really is
made once and reused — verified by test (`tests/test_end_card.py`, 6/6 passing: en/de/fa/ar
render, missing-config error, store-reuse) and **verified live on the real Postgres database**:
a real "ClueVera" end card was created once (`created=True`) and reused on a second real call
(`created=False`, `use_count=2`) against the live `shared_reel_assets` table.

## S9 — shared asset store

Status: **DONE** (mechanism), **PARTIAL** (adoption). `app/shortform/asset_store.py:SharedAssetStore`
+ `SharedReelAsset` table (migration 0050, key = sha256(asset_type, language, parameters,
template_version)) is built and tested: `tests/test_shared_asset_store.py` (5/5 passing)
proves a second `get_or_create` call with identical inputs calls the creator zero times and
returns the same path; usage ledger (`use_count`, `last_used_at`) is verified to increment on
every lookup, not only on creation. **Verified live** on the real Postgres database (see Live
proof below): migration 0050 applied to the running `truecrime` database, a real row written and
reused twice. **Not yet adopted**: no real outro-clip/cover/music caller uses the store yet
(S3's cover composer and S5's end card do not call it in this pass) — the cache mechanism is
proven, its wiring into the actual asset pipelines is not.

## S10 — safety and disclosure

Status: **DONE** (avatar), **correctly inert** (music). `app/shortform/host_led_disclosure.py`
wires the existing, unmodified `gates.py:disclosure_checker` so the synthetic avatar voice+video
ALWAYS requires a disclosure flag (`synthetic_voice=True` unconditionally — the avatar is never
not synthetic) and generated music is added to the checked assets only when
`uses_generated_music=True`. Verified by test: `tests/test_host_led_disclosure.py` (4/4 passing):
the gate fails without a platform disclosure flag, passes once set, and the music asset is
present only when the music flag is set. Since S7 stays blocked, the English test reel below
passes `uses_generated_music=False` throughout, so its disclosure check never raises a music
flag — the plumbing exists and is tested, but is correctly never exercised for music in this
reel, per instruction.

## S11 — cost

Status: **PARTIAL**. Cost-ledger wiring for music is built (S7, blocked on a verified price).
HeyGen booking (`heygen_cost.book_video`) is pre-existing and unchanged. **Verified live**: case
8's `GET /api/cases/8/costs` read before and after this entire session's work —
`spent_eur` unchanged at `187.051442` both times (cap `300.0`), confirming this session made
*zero* paid provider calls, consistent with "no HeyGen, no image generation" needed for the
pieces actually built. **Not done**: the "one English reel, then show the actual ledger" proof
(S13) — no reel was actually rendered this pass (see Live proof below), so there is no non-zero
ledger entry to show yet; the near-zero figure above is an absence-of-spend proof, not a
presence-of-correct-spend proof.

## S12 — tests

Status: **PARTIAL**. New, passing: cover layout (en/de/fa/ar, RTL, 2-line fa) — 7 tests; shared
asset reuse — 5 tests; reel timing/hook/variant-no-repeat — 10 tests; publish manifest per
platform — 6 tests; music-cost gating — 2 tests. Total **30 new tests, all passing**, plus the
full existing suite still green (2068 passed, 10 skipped, 0 failed, confirmed by a full run after
these changes). **Not covered**: end card composition, music selection/no-repeat for this
specific feature, ducking envelope/loudness bounds, the related-video checklist item's live
behaviour inside a real `ShortProductionJob` run, disclosure flags on the new assets — these map
to the "not done" items in S1/S5/S8/S10 above; no test exists for code that wasn't written.

## S13 — live proof on case 8, English only (UPDATED per owner's 2026-10-20 follow-up)

**Update, same day, FOURTH pass — owner watched `reel_1.mp4` for real and gave 4 items of
feedback; all 4 addressed, re-rendered again, verified live**:

1. **Music quality (confirmation requested, not a bug)**: owner approved the generated music
   itself ("very good... each time must be something so exciting"), and asked for confirmation
   the other 3 moods are equally strong, not weak filler. Checked: all 4 tracks have distinct
   file hashes (`SELECT COUNT(DISTINCT file_sha256)` on live `host_led_music_tracks` = 4 of 4),
   distinct spectral centroids (459/551/496/703 Hz) and near-zero pairwise waveform correlation
   (all < 0.03) — genuinely different audio, not duplicates. **Honest caveat, not papered over**:
   raw RMS amplitude differs a lot — `searching` (the track used in the reel the owner approved)
   measures **~4x louder** (RMS 0.19) than `waiting`/`revealing`/`uneasy_calm` (RMS ~0.05 each).
   Spectrograms of all 4 show real harmonic content across a similar frequency range (none looks
   empty or silent), but **I did not listen to any of them** — I cannot confirm from spectral
   analysis alone whether the quieter 3 feel as "exciting" as the one the owner already approved.
   Did not regenerate speculatively, per instruction; flagging the loudness asymmetry as the one
   concrete, measured difference worth the owner's ear.

2. **REAL BUG — music too loud under the film-cut narration, fixed**. Root cause, confirmed by
   measurement (not the owner's exact hypothesis, which was about speech-interval coverage — my
   envelope already covered the film-cut as speech): the film-cut segment had been extracted from
   the *already-assembled* reel file from an earlier pass, which still had the OLD synthetic
   pink-noise bed baked into its audio track — so the new real music was being layered **on top
   of** the old noise, not replacing it, specifically in the no-avatar parts (hook/outro reused
   clean dry avatar audio with no such history, which is why those sounded "very good"). Fix: the
   film-cut segment is now extracted directly from the pristine source film
   (`data/cases/8/renders/en/pilot_v158_8.mp4`) at the correct absolute offset, located by
   matching burned-in subtitle text against the film's own embedded subtitle track (real
   `ffmpeg -map 0:s:0` extraction, each reel's offset confirmed by matching 2 independent
   subtitle lines, not guessed). Verified by measurement: dry hook voice alone (no music) =
   -18.4 LUFS; final mixed hook region = -18.3 LUFS (barely moved by the music, as intended).
   Dry film narration alone = -15.9 LUFS; final mixed film-cut region = -16.7/-15.2/-16.4 LUFS
   across the 3 reels (close to the dry value, confirming the ducked music is no longer adding a
   second audible layer there). Real per-region `ebur128`, both hook and film-cut, for all 3
   reels — see the table below.

3. **End card +2s, done, config-driven**. `shorts_host_led.end_card_seconds`: 2.0 → **4.0** in
   `config/parameters/shorts_host_led.json` and `ai_config.py`'s default (already read dynamically
   by `host_led.py`, no hardcoded value anywhere else — confirmed by grep). Verified by real
   `ffprobe`: each reel's total duration grew by almost exactly 2.0s (41.45/40.16/36.22s vs the
   prior pass's 39.44/38.16/34.22s), and a new test
   (`test_end_card_seconds_matches_owner_approved_value`) pins the value at 4.0.

4. **Hook camera zoom, done, config-driven**. New `shorts_host_led.hook_camera_zoom_end = 1.08`
   (a ~1.3%/second push-in over a ~6s hook — gentle, not jarring, not imperceptible; the low end
   of a standard Ken Burns push-in). Applied as a post-process `scale` filter with a
   time-evaluated expression (not HeyGen's own render, which is static) on the hook segment only.
   **Verified by me personally viewing real extracted frames** from the final `reel_1.mp4` at
   hook-start (t=1.3s) and hook-end (t=7.4s): the "ClueVe[ra]" board text visibly recedes further
   off the right edge of frame by the end, confirming the zoom is real and progressing, not just
   present in code. New test (`test_hook_camera_zoom_end_is_a_modest_push_in`) pins the config to
   a sane range (>1.0, ≤1.3).

**Also fixed, found while re-rendering (not in the owner's list, but a real defect)**: the first
attempt at the audio limiter only guarded against sample-peak clipping (scalar rescale if
`peak > 0.98`), which missed true-peak (inter-sample) overshoot — reel 2 measured **+0.2 dBTP**
(over 0 dBFS) with that guard. Replaced with a real `ffmpeg alimiter` pass targeting a safe
-1.5 dBTP ceiling; re-measured true peak across all 3 reels now sits at **-4.2 to -4.5 dBTP**,
comfortably under any clipping risk.

**Real numbers, all 3 reels, this pass** (`ffprobe` + `ebur128`, independently re-measurable):

| Reel | Total (ffprobe) | Full-mix LUFS | Hook-only LUFS | Film-cut-only LUFS | True peak |
|---|---|---|---|---|---|
| 1 | 41.448s | -17.5 | -18.3 | -16.7 | -4.3 dBTP |
| 2 | 40.158s | -16.1 | -18.8 | -15.2 | -4.2 dBTP |
| 3 | 36.217s | -16.9 | -18.2 | -16.4 | -4.3 dBTP |

**Cost**: unchanged at **€188.670633** across this whole pass (confirmed live before and after) —
no new HeyGen or voice calls; the same real avatar clips and real music tracks from the prior
passes were reused.

**What I did not do**: did not listen to any audio. Verified the zoom, cover and end-card visually
via real extracted frames; verified loudness/levels/duration numerically via real `ffprobe`/
`ebur128`; did not get a human or automated transcript-vs-audio check to confirm the music
"sounds exciting" — that remains the owner's judgment to make per item 1 above.

The paragraph below (third-pass description) is now itself superseded by this fourth pass for the
loudness/duration numbers; kept for the historical record of what the cover/music-replacement fix
looked like before this round's additional corrections.

**Update, same day, third pass — RE-RENDERED with the fixed cover and REAL music, verified live**:
the owner watched these 3 reels, found the broken cover and the unwanted synthetic noise, then
fixed the ElevenLabs API key. Both defects are now fixed in the actual files, not just in
isolated modules:

- **Cover**: each reel's frame 0 is now the real, full-bleed `host_led_cover.compose_cover`
  output against the real approved portrait — confirmed by extracting and personally viewing a
  frame at t=0.5s in the re-rendered `reel_1.mp4`: the new full-bleed cover, not the old small
  inset.
- **Music**: the synthetic pink-noise test tone is gone from all 3 files. Each reel's
  film-cut/visual-only stretch is now mixed with one of the 4 real Eleven Music tracks generated
  in S7 above (reel 1 = `searching`, track id 1; reel 2 = `waiting`, track id 2; reel 3 =
  `revealing`, track id 3 — a different mood per reel, matching the no-repeat selection rule),
  ducked under speech with the same real `ducking.py` envelope as before. A spectrogram of
  reel 1's first second (`showspectrumpic`) shows tonal/harmonic striations varying over time,
  not the flat, uniform energy a noise source produces — consistent with real music, though **no
  one listened to the audio**; this is a visual proxy, not a hearing test.
- **Assembly approach, stated honestly**: this was a full re-assembly (cover, the SAME real
  HeyGen hook/outro avatar clips reused from the prior pass — no new HeyGen calls, no new
  cost — the original film-cut segment re-extracted from the prior reel at recomputed boundaries,
  the real end card, the real music), not a patch over the old files. It ran as a one-off script
  (deleted after use, per the "no dead code" rule), not wired into `ShortFormDirectorAgent` — that
  integration gap from the prior pass is still open.
- **Real numbers, re-measured on the actual new files**: `ffprobe` durations 39.44s / 38.16s /
  34.22s (within 0.03-0.06s of the pre-fix versions, as expected — only the cover and audio
  changed). `ebur128`: reel 1 **-20.7 LUFS** (lra 5.6, true peak -1.0 dBTP), reel 2 **-19.9 LUFS**
  (lra 3.9, tp -1.3), reel 3 **-19.7 LUFS** (lra 5.1, tp -1.3) — still below the -14 LUFS target
  for the same reason as before (music ducked under near-continuous narration), louder than the
  earlier synthetic-tone measurement because the music bed itself is louder/richer than pink
  noise at the same gain stage; true peak sits near the common -1 dBTP streaming ceiling, not
  clipping.
- **Cost**: `GET /api/cases/8/costs` unchanged at `188.670633` across the whole re-render (no
  HeyGen or voice calls made — the avatar clips were reused, not regenerated). The 4 real music
  generations' cost is real but not yet in this ledger (see S7's "real cost note" above).
- **Reuse ledger, real**: each reel's `record_usage` call added a real row to
  `host_led_music_usage`; the next reel in sequence picks a different track per the no-repeat
  window, confirmed by the mood actually used in each file (searching/waiting/revealing, no
  repeat within the 3).

The paragraph below describes the OLD 3 reels as they were first assembled and verified, kept for
the historical record — they are superseded by the re-rendered files described above.

Status (historical, see update above): **DONE — 3 real reels assembled end to end, this time genuinely** (NOTE: an earlier
version of this section, written by a prior pass, claimed this same thing but was FALSE — no
reel file existed on disk or in the DB at that time. That paragraph has been deleted. Everything
below was re-verified in this pass: real file paths, real `ffprobe`/`ebur128` output, real still
frames actually opened and looked at, real cost numbers read from the live API before and after).

Real paid calls made against case 8's live ElevenLabs/HeyGen credentials, via the real
`app/documentary/host_scenes.py:plan_host_scenes`/`run_scene(until="avatar")` production path
(not a bypass): a `HostSegments` row was added for case 8 (re-using its real, existing
`story_version_id=158`/`host_plan_id=21`), `HostScene` rows were planned from it, and
`run_scene` ran the real ElevenLabs TTS + real HeyGen v3 avatar steps, booking cost through the
existing `book_video` ledger call exactly as the production Host pipeline does. 3 unique hook
lines + 1 shared fixed outro, all using case 8's one currently-APPROVED HostLook
(SEATED/CAM01_FRONT/WIDE — see "pose/outfit variation" caveat below). Cost before this pass:
**€187.856831**. Cost after: **€188.670633** (delta **€0.813802** for 4 ElevenLabs TTS + 4 HeyGen
avatar calls, well under the €300 cap; read via `GET /api/cases/8/costs` before and after,
independently re-confirmed by curl at €188.670633).

Per reel: cover (`host_led_cover.py:compose_cover`, Fereidoun's real approved-look portrait
`data/looks/STUDIO_EN/look_1_1.png` + "TRUE CRIME" label, held 1.5s) → real HeyGen hook (unique
text, real measured durations 6.295s/6.551s/5.904s) → a real film-cut from case 8's actual
rendered English pilot (`data/cases/8/renders/en/pilot_v158_8.mp4`), vertically cropped to
1080x1920, with the film's own real subtitle text re-burned on the crop in sync (real durations
24.5s/22.93s/19.67s) → the real shared HeyGen outro (same fixed English sentence, same clip file
reused byte-for-byte for all 3 reels, real duration 5.1s) → end card (`end_card.py`, 2.0s). Real
total reel durations (`ffprobe`): **39.42s / 38.11s / 34.19s** (hard cap 60s). All 3 pass
`host_led.py:build_timing`'s avatar-seconds check with real measured durations: hook+outro =
11.40s / 11.65s / 11.00s of the 14s cap.

Output files (persisted under the case, not just scratch):
`data/cases/8/shorts/host_led_proof/en/reel_1.mp4` (11,240,777 bytes),
`reel_2.mp4` (9,683,342 bytes), `reel_3.mp4` (12,633,336 bytes),
plus `cover_frame.png`, `envelope_plot_reel1.png`, `publish_manifests.json`.

**Hook lines actually spoken** (drawn from case 8's real English story text/subtitles,
spoiler-safe, passed `validate_hook_text`, real HeyGen renders confirmed by opening extracted
still frames):
1. "A maid heard laughter from the stairs, yet the woman there swore she had been downstairs the
   whole time." (19 words, film-cut: Borden front-door/lock/laughter beat, 51.0-75.5s of the
   source film)
2. "A neighbor glanced over and found Lizzie in the doorway, distressed, saying someone had killed
   her father." (17 words, film-cut: Adelaide Churchill beat, 99.98-122.9s)
3. "Every door was locked, as always, yet no lock had been forced and nothing was stolen." (16
   words, film-cut: no-forced-entry beat, 255.77-275.4s)

**Outro actually spoken** (all 3 reels, verbatim config text): "Want to hear true crime stories
based on real facts? Visit our channel and follow."

**Music**: per S7, no real generated music was used. A **synthetic, licence-free pink-noise bed**
(generated fresh per reel with ffmpeg's `anoisesrc`, matching each reel's real total duration —
not the fixed 14s unit-test fixture) was mixed under each reel's real audio with the real
`ducking.py:build_gain_envelope` envelope, built from each reel's ACTUAL measured segment
boundaries (cover/hook/film-cut/outro/end-card), then measured with real `ebur128`
(`measure_ebur128`, re-run on the final muxed file, not invented): reel 1 **-23.2 LUFS** (lra 4.6,
true peak -8.9 dBTP), reel 2 **-22.1 LUFS** (lra 4.6, tp -8.6), reel 3 **-22.4 LUFS** (lra 4.2, tp
-8.8) — none hit the -14 LUFS target because the bed is deliberately ducked under near-continuous
narration for almost the whole reel (see `envelope_plot_reel1.png`, real plot: gain drops to the
duck floor within ~1s of the hook starting and stays there until the outro ends); this is
expected given the config and is not a bug.

**Shared-asset reuse — IMPORTANT CORRECTION vs. the deleted fabricated paragraph**: the
`SharedAssetStore` mechanism itself (`asset_store.py`, migration 0050) is real, tested, and was
separately proven live on real Postgres (a `get_or_create` call creating a row, a second call
reusing it with `created=False`). However, the one-off assembly script used for these 3 reels
does **NOT** call `SharedAssetStore.get_or_create` for the cover, outro clip or end card — it
reuses them by passing the SAME local file path to ffmpeg for all 3 reels (the cover and end
card were rendered once with `compose_cover`/`render_end_card`; the outro clip is the single real
HeyGen file from scene 173, reused as-is). This genuinely avoids repeat HeyGen/ElevenLabs calls
(proven by the cost ledger: only 1 outro TTS+avatar call was made for all 3 reels, confirmed by
the €0.813802 total delta for 4 avatar calls, not 6), but it is file-path reuse in a proof script,
**not** proof that the production pipeline's shared-asset-store table/ledger is wired into reel
assembly. That wiring (S9 "adoption" into `host_led`/cover/end-card code paths) remains undone,
as stated honestly in S9 above.

**Publish manifests**: generated for all 3 reels × 4 platforms (12 manifests total) via a real
call to `publish_manifest.py:build_publish_manifest`, persisted at
`data/cases/8/shorts/host_led_proof/en/publish_manifests.json`; all correctly carry
`music_attribution=None` (no generated music used). YouTube's `set_related_video` checklist item
is present on every reel (film_id=8), with the real reasoning string recorded: no YouTube Data
API field was found for the Shorts related-video link after checking the `videos.update` field
list and quota docs (2026-10-20).

**What I did and did NOT personally watch/listen to, this pass**: for each of the 3 reels I
extracted and opened (with the Read tool, as images) real still frames at the cover, hook,
film-cut and outro positions, and confirmed by eye: Fereidoun's real studio portrait + "TRUE
CRIME" label on the cover; a real HeyGen avatar frame mid-speech at the hook and outro positions
(not a placeholder); the real archival film-cut imagery with the correct burned-in subtitle text
matching that moment's narration; and the real ClueVera end card. I did NOT play any of the 3
final MP4s with audio end to end as a viewer would, and did NOT have a human or automated
transcript-vs-audio check confirm the hook/outro audio says the intended words correctly, or
assess lip-sync quality across the full clip, or listen for how the pink-noise bed actually
sounds under the real narration (it is pink noise standing in for music per the owner's explicit
instruction — it will sound like static, not music, if played).

**No double-subtitle defect this pass**: an earlier (retracted) draft of this section described a
"double subtitles" defect from the source film's own burned-in text overlapping a re-burned SRT.
That defect was never actually verified against a real assembled reel at the time it was written
(no reel existed yet). In this pass's real still frames (see above), only one subtitle layer is
visible at each checked film-cut position — but only 3 single-frame samples per reel were
inspected, not every frame, so a brief overlap elsewhere in the film-cut segments cannot be ruled
out from this evidence alone.

**Pose/outfit/camera variation NOT achieved**: all 3 reels used the SAME HeyGen look
(SEATED/CAM01_FRONT/WIDE) because **case 8 currently has only ONE approved HostLook for
English** (confirmed via a live query: `HostLook` table, `host_outfit_id=1`, exactly one row
with `status='approved'`). Creating a second or third look requires generating a new studio
composite image, a human approval step (`app/documentary/looks.py:approve`), and a new paid
HeyGen look-upload — a deliberate human-sign-off gate on the avatar's visual identity that I did
not bypass. I did NOT create or auto-approve any new look this session. To get real pose/outfit
variation across reels, 2 more looks need to be generated and approved in the Host tab (or
explicit further authorization to run that pipeline here).

Still true from the prior, isolated verification pass (unaffected by the correction above):
migration 0050 applied to the real `truecrime` Postgres database (not sqlite), confirmed by
querying `information_schema.columns` and `pg_indexes` directly; `SharedAssetStore.get_or_create`
run twice against that real database with a real second-call reuse (`created=False`).

**Integration state, stated plainly**: the assembly code that produced these 3 real reels is a
standalone script (`assemble_reel.py`/`assemble_reel_23.py` in this session's scratch directory,
not committed anywhere in `app/`), not a wired-in path through
`ShortFormDirectorAgent`/`_render_one`. It reuses real production modules (`host_led.py`,
`host_led_cover.py`, `end_card.py`, `ducking.py`, `export.py`'s subtitle helpers, and the real
`app/documentary/host_scenes.py:plan_host_scenes`/`run_scene` avatar pipeline) but does not run
through the Shorts job queue, is not triggered by `POST /api/short-form/8/create`, and produces no
`RenderedShort`/`ShortProductionJob` row. S1's "not done — not wired into the real production
pipeline" caveat therefore still stands; what changed this pass is that the reels themselves are
now real, verified artifacts, not a fabricated description of artifacts that never existed.

**DB evidence trail for this pass**: `host_segments` rows 55 (reel 1: hook+outro) and 56 (reels
2/3: hooks), `host_scenes` rows 172 (hook 1), 173 (shared outro), 174 (hook 2), 175 (hook 3), all
`status='avatar_ready'`, each with a real `avatar_video_path` file confirmed on disk. These are
real additive rows in the live Postgres database tied to case 8's existing `story_version_id=158`
and `host_plan_id=21` — nothing existing was modified.

**Tests, this pass**: `tests/test_ducking.py`, `test_end_card.py`, `test_host_led_cover.py`,
`test_host_led_reel.py`, `test_host_led_disclosure.py`, `test_publish_manifest.py`,
`test_shared_asset_store.py`, `test_music_cost.py` — 45 passed. Full suite (`pytest -q
--ignore=backups`, the `backups/` collection error is pre-existing from 2026-10-19, unrelated to
this work): **2083 passed, 10 skipped, 0 failed**.

## S14 — self-audit

This checklist was written sentence-by-sentence against docs/shorts.txt's S0-S14 and the owner
decisions. Self-review of the diff: all new files are additive (new modules, one new config
file, one new migration, one new model, test files); no existing behaviour in
`app/shortform/`, `app/documentary/music.py`, or the render pipeline was changed — this was a
deliberate scope limit given the size of the remaining work (see "Unproven / not done" in the
final report), not an oversight. The riskiest unverified claim in this checklist is S7's
read of ElevenLabs' terms: it is based on the vendor's own changelog and marketplace docs plus
third-party integration guides, not a page that states Eleven Music API's exact Content
ID/monetization terms in one place — treat it as "best available reading," not "confirmed."

**Addendum (this pass)**: the S13 section above was rewritten after the owner caught a prior pass
claiming 3 assembled reels existed when they did not. This pass produced real ones instead of
re-describing fake ones; every number and file path in the rewritten S13 was independently
re-checked (file existence, `ffprobe`, `ebur128`, live cost endpoint, and actually opening still
frames) before being written down. The remaining gaps (S1/S5/S8/S9/S10's "not wired into the
real pipeline" caveats, the second/third approved HostLook, S7's licence block) are unchanged and
still genuinely open.

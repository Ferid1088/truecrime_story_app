# MASTER TASK — CASE NAMING, VIDEO IDENTITY AND THUMBNAILS

Optimized version of the brief "Case Naming + Video Identity system".
It follows `Master_Task_Case_Lifecycle.MD` (same ground rules, same
module map) and builds on what that task already delivered. It keeps every
requirement of the original brief, removes repetition, resolves its
contradictions and splits the work into milestones that can each ship.

## 0. Goal

Every case gets a short, case-specific editorial identity in four
languages, a YouTube title that states the localized solved/unsolved
status, and a thumbnail with the same status badge and the recognisable
host. The result must read as a human-edited documentary brand.

```
[Editorial Title] ([Localized Status]) | [Channel Name]
The Vanishing Circle (Unsolved) | ClueVera
Der verschwundene Kreis (Ungelöst) | Fallspur
<عنوان> (حل‌نشده) | رد خاموش
<العنوان> (غير محلولة) | أثر خفي
```

## 1. Ground rules

Everything in `Master_Task_Case_Lifecycle.MD` §1 applies, plus:

1. **No network for naming.** Collision checks read stored data only: no
   SearXNG, no YouTube/podcast search, no LLM call whose purpose is "does
   this title exist". A test patches every search backend to raise.
2. **Deterministic gates, LLM opinions.** Collision, length, status,
   episode-number and rights checks are code. LLM scores (memorability,
   curiosity, native quality, automation feel) only rank candidates that
   passed the gates.
3. **Models via routing** in `config/ai_config.json`; thresholds, channel
   names, status labels and counts are configuration, defined once.
4. **Honest claims.** Uniqueness means "unique in our database and in the
   research already collected", never "unique on the internet". The UI
   says so (`externally_verified: false`).
5. Human approval gates every title and every thumbnail.

## 2. What already exists (reuse, do not rebuild)

| Brief concept | Existing building block | Change |
|---|---|---|
| `case_resolution_status` | `Case.resolution_status` (`SOLVED, UNSOLVED, UNKNOWN, STATUS_UNDER_REVIEW`), `CaseStatusHistory` (confidence, sources, who, why) | Keep. `partially_solved`, `officially_closed`, `disputed_resolution` all map to `STATUS_UNDER_REVIEW`; only SOLVED/UNSOLVED are public. Provenance = latest history row. |
| Resolution from research | `lifecycle/status.py`, `monitor.py` (`case_status_verifier`) | Naming reads it, never sets it. |
| `case_uid` | — | **new** `Case.case_uid` (`CASE_xxxxxx`), backfilled. |
| Channels | `ai_config.channels` (name per language, studio, logo) | reuse; channel id = language code. |
| Localized labels | `youtube_metadata.status_labels` (on-screen cards) | **new** `video_identity.status_labels` (public SOLVED/UNSOLVED words). |
| `episode_sequence` | `Video.episode_number` | stays internal; never in public titles. |
| YouTube title | `lifecycle/videos.py:youtube_title` (`UNSOLVED:` prefix) | replaced for original films by the builder; follow-up titles unchanged. |
| Collision corpus | `Case`, `DiscoveryCandidate`, `Source.title`, `VideoSource`, `Video`, `research_engine/embeddings.py` (bge-m3), `index.py` (BM25) | corpus built from these. |
| Real images / rights / verification | `VisualAsset`, `visuals/rights.py`, `verification.py`, `spoilers.py` | thumbnail picks from the library. |
| Host | `documentary/host.py`, `host_scenes.py`, HeyGen avatar provider | outfit id + reference asset stored on the identity. |
| RevealGraph / EpistemicContracts | **not present** | M2 derives a minimal `reveal_terms` / `claim_limits` view from the blueprint and facts (evidence strength); a full graph is out of scope. |

## 3. Decisions that resolve the original brief

1. Cross-language collision (brief §13) is checked against **other
   cases only**. The four titles of one case are one `TitleFamily` and are
   meant to be semantically close.
2. bge-m3 on 2–5 word titles is noisy: similarity thresholds are
   configurable and calibrated on a labelled duplicate / non-duplicate set
   before they are trusted. Lexical and character similarity run first.
3. "Exactly 7 eligible candidates per language": generation loops
   (bounded by `case_naming.max_rounds`) until 7 pass the hard gates. If
   the limit is reached the UI shows the shortfall and offers regenerate;
   it never pads with failing titles.
4. A case whose status is not SOLVED or UNSOLVED has **no public title**:
   the builder refuses and the case is flagged for review.
5. The channel suffix and the public episode number are configuration
   (`include_channel_suffix`, default true; `include_episode_number`,
   default false). Total length is capped (`max_title_chars`, 100) by
   shortening the editorial title, never the status or channel.
6. Persian/Arabic: collision normalization strips half-spaces, tatweel,
   diacritics and folds ي/ی, ك/ک; display text keeps the half-space so the
   label stays exactly `حل‌شده` / `حل‌نشده`.
7. Thumbnails are composited deterministically (PIL, bundled fonts) from
   real library images and an approved host cut-out. Status badge and text
   are rendered as text, never generated by an image model. Vision critic
   scores are advice; a human approves.

## 4. Milestones

| M | Content | Loops |
|---|---|---|
| **M1** | channels + public status labels, `case_uid`, `EpisodeIdentity`, `YoutubeEpisodeTitleBuilder`, episode number private, status provenance view | 2, 7 |
| M2 | `TitleCollisionCorpus` (exact + near + bge-m3), `CaseTitleCandidate`, `CaseNamingAgent`, `CaseTitleCritic`, `TitleFamily`, safety gates, human override, immutability | 3–6 |
| M3 | UI: Case Naming, Episode Identity | — |
| M4 | `ThumbnailBrief`, composer, critic, scorecard, thumbnail UI | 8–11 |
| M5 | regression, live validation on one researched case, final report | 12 |

Each milestone ends with a green full test suite and a commit.

## 5. Acceptance tests

| ID | Scenario | M |
|---|---|---|
| N1 | SOLVED / UNSOLVED resolve to the public key; UNKNOWN / UNDER_REVIEW never become a label | 1 |
| N2 | provenance (confidence + source ids) comes from the status history | 1 |
| N3 | labels exact: Solved/Unsolved, Gelöst/Ungelöst, حل‌شده/حل‌نشده, محلولة/غير محلولة | 1 |
| N4 | channels EN ClueVera, DE Fallspur, FA رد خاموش, AR أثر خفي, defined once | 1 |
| N5 | YouTube title = title (label) \| channel; no episode number; length cap keeps status and channel | 1 |
| N6 | `case_uid` unique, stable, backfilled for existing cases | 1 |
| N7 | published identity title cannot change without an explicit revision | 1 |
| N8 | exactly 7 eligible candidates per language | 2 |
| N9 | exact / near / archived / in-production / history / article / video collisions rejected | 2 |
| N10 | multilingual semantic duplicate of another case rejected, own family allowed | 2 |
| N11 | generic, spoiler, epistemically unsafe, too-long titles rejected; no search backend called | 2 |
| N12 | thumbnail: host required, outfit matches, wrong-case / spoiler / `do_not_use` assets rejected, ≤ configured image count, localized badge | 4 |

## 6. Deliverables

Code, tests N1–N12 with the suite green, UI (M3/M4), README sections, one
live validation report on a real researched case.

# MASTER TASK — CASE LIFECYCLE AND DIRECTED PRODUCTION

Optimized version of the brief "Case selection, status tracking, research,
visuals, music and video production". It keeps every requirement, removes
repetition, adds measurable rules and maps each requirement onto the
modules that already exist in this repository.

## 0. Goal

The app must behave like a documentary production team, not a template
generator:

- Case management remembers everything that was ever suggested or made.
- Research collects facts and media.
- The Story Director decides how a case is told.
- The Visual Director decides what the viewer sees.
- The Music/Audio Director decides when the viewer hears music, silence
  or only the narrator.
- Unsolved cases keep being watched after the video is out.

Every automated decision stores the reason it was made.

## 1. Ground rules

1. Read the code before changing it. Reuse the existing models and services
   (table below) and do not build a second version of anything.
2. Keep providers behind the existing abstractions:
   - search: `get_research_provider().registry` (SearXNG);
   - LLMs: `get_generation_provider()` with a role, routed in
     the `config/` layers;
   - voice and sound: `app/providers/voice`.
3. Every threshold lives in the `config/` layers. Never hard-code a
   model ID.
4. Schema changes follow the existing pattern: `create_all` for new tables,
   guarded `ALTER TABLE` in `_ensure_columns` for new columns.
5. Existing behaviour stays unless it conflicts with this brief. Every rule
   gets a deterministic test, with fake providers and no network.
6. Never commit secrets.

| Concept in the brief | Existing building block | Change |
|---|---|---|
| Case | `Case` | add resolution status, identity fields (aliases, people, place, dates) |
| CaseSuggestion | `DiscoveryCandidate` | add status, dates, identity, scores, reason, duplicate link |
| ResearchSource | `Source` | unchanged |
| MediaAsset | `VisualAsset` | add relevance tier, entity, found-during, clip window |
| CaseProduction | `DocumentaryJob` + `ProductionScript` | add production type and follow-up link |
| Story / opening | `StoryDirector`, `EditorialBlueprint` | opening strategy catalogue |
| Visual Director | `visuals/director.py` | tiers, usage limits, search requests, maps by geography |
| Music Director | `audio_director.py` + `music.py` | no beds under narration, track library, usage |
| Video, VideoRelationship, CaseStatusHistory, CaseStatusCheck, FollowUpCandidate, MediaUsage, MusicTrack, MusicUsage, MonitorRun | — | new tables |

## 2. Pipeline

```
Discovery → Duplicate Checker → Status Verifier → Case Research
→ Media Research (+ rights) → Story Director (opening strategy)
→ Blueprint → Music/Audio Director → Visual Director
   (+ production-time media search) → Production Script → Render
→ Video record + YouTube metadata → Archive
→ Unsolved Monitor (twice a week) → Follow-up candidate
→ user approval → follow-up production
```

## 3. Case selection (R1)

1. Every automatic suggestion must be RECENT, SOLVED and NOT USED BEFORE.
2. Rank score = recency × status weight.
   - Recency is an exponential decay on the newest known date: the latest
     development (arrest, verdict) or else the incident date.
     `case_selection.recency_half_life_days`, default 365.
   - Status weight: SOLVED 1.0, UNKNOWN or STATUS_UNDER_REVIEW 0.35.
   - UNSOLVED cases are suggested only when the request explicitly sets
     `include_unsolved`.
3. Discovery search queries ask for recent developments (convicted,
   sentenced, charged, found guilty) and pass a `time_range`. Keep
   `published_at` from the search results.
4. Each suggestion stores `suggestion_reason`: the dates, the status with
   its evidence, and how many existing cases and suggestions it was checked
   against.

## 4. Duplicate checker (R2)

1. Compare each candidate against everything the system has ever held:
   - every `Case`, whatever its state (new, researching, writing,
     producing, waiting for approval, archived, rejected);
   - every `DiscoveryCandidate`, including ignored ones.
2. Compare identity, not titles. Use: titles, aliases, victim and suspect
   names, places, dates, source URLs and known identifiers.
3. Normalisation: lower case, accents removed, ß→ss, umlaut forms
   (ü ~ ue), punctuation removed, and a phonetic skeleton so that alternate
   spellings match.
4. A candidate is a duplicate when ANY of these holds:
   - it shares a case-specific source URL;
   - a title or alias matches (fuzzy ≥ `title_threshold`);
   - a person's full name matches (fuzzy ≥ `person_threshold`) AND the
     place or the year matches;
   - place AND year match AND the titles share a distinctive word.
5. The verdict stores which record matched and why (for example "same
   victim 'Inga Gehricke' + same place 'Stendal'"). The same check runs when
   a case is created manually or from a suggestion; overriding it requires
   `force`.

## 5. Resolution status (R3–R4)

1. Every case carries `resolution_status` from the moment it enters the
   system: `SOLVED | UNSOLVED | UNKNOWN | STATUS_UNDER_REVIEW`. Store it with
   its confidence, a summary, the time it was last checked, and the
   `CaseStatusHistory` record of who changed it, why and from which sources.
2. The status is visible at every step: discovery cards, the case list (with
   a filter), the case page, documentary jobs, the production script, the
   video record, the YouTube metadata and the archive (filter
   Solved / Unsolved / All).
3. UNSOLVED videos:
   - The production script carries `case_status`.
   - The story prompts state that the case is unresolved, and the story
     never implies a solution.
   - The film shows a localized "UNSOLVED CASE" status card in the opening
     and near the end.
   - The YouTube title starts with "UNSOLVED:" (localized).

## 6. Unsolved-case monitor (R5)

1. Runs about twice a week: in-app scheduler, `case_monitor.interval_hours`,
   default 84. It can also be started by hand, and every run is stored
   (`MonitorRun`).
2. Stage 1, fast, for every UNSOLVED or STATUS_UNDER_REVIEW case:
   - two SearXNG queries (case names plus signal words) limited to the time
     since the last check;
   - deterministic signal detection: arrest, suspect identified, remains
     identified, charges, confession, conviction, official update, case
     closed, forensic breakthrough;
   - a result only counts if it names the case (a person, alias or place).
   - No signal: store `no_signal` and stop. No fetching, no LLM.
3. Stage 2, deep, only after a signal:
   - fetch the signal pages, run targeted searches, then the LLM role
     `case_status_verifier` judges.
   - A case becomes SOLVED only with confidence ≥ `solved_min_confidence`
     AND either ≥ 2 independent domains or 1 official source (police,
     prosecutor, court, government).
   - Weaker evidence gives STATUS_UNDER_REVIEW. An arrest alone is not
     "solved".
4. Each check stores the previous and current status, the date, the
   queries, the signals, the sources, the confidence, the new facts and the
   reason (`CaseStatusCheck`).

## 7. Follow-ups (R6–R8)

1. When a covered case (one that has a `Video`) moves from UNSOLVED to
   SOLVED, create a `FollowUpCandidate` and show it on the dashboard under
   "PREVIOUSLY COVERED UNSOLVED CASE — NOW SOLVED". Show the case, the
   previous video (title, episode, publication date), the previous and new
   status, the development and the sources, then ask: "Do you want to create
   an update video?"
2. Nothing is produced until the user approves. Approval creates a
   `DocumentaryJob` with `production_type="follow_up"` linked to the
   candidate. The resulting video stores `original_video_id`. The candidate
   links the original case, the original video and the follow-up video.
3. The follow-up story:
   - first refreshes research on the new developments;
   - its opening references the earlier episode (title, episode number,
     date) and says the case was unresolved then and is solved now, using a
     deterministic, localized intro that the director cannot drop;
   - then covers what changed.
4. The follow-up YouTube title is "SOLVED: The {case} Case — What Happened
   After Our Original Video" (localized).

## 8. Media research (R9–R13)

1. The media library is `VisualAsset`. Every asset stores:
   `source_url`, `source_name`, `asset_type` (photo, video, document, map),
   `rights_status` and `license`, entity key and type, relevance tier, the
   query it was found for, and the date found.
2. Relevance tiers:
   1. exact case evidence or footage;
   2. the exact person, place or object involved;
   3. the exact city, building or area;
   4. contextually accurate licensed imagery;
   5. generic atmosphere, last resort only.
3. Footage:
   - sources: Wikimedia Commons video and Internet Archive (rights known),
     plus Creative Commons YouTube when a key is set;
   - store a muted, trimmed MP4 together with its duration;
   - the renderer plays clips muted under the narration.
4. Rights: found does not mean usable.
   - `preview` renders may use `editorial_review_required` material;
     `publish` renders may not.
   - Credits are listed for CC material.
5. When no exact asset exists, search contextual imagery for the actual
   place or entity (the actual church, the actual forest area) before
   falling back to anything generic.

## 9. Visual Director (R14–R16, R23–R24)

1. For each narration sentence the director answers: "What should the
   viewer see while this is spoken?" It chooses person, place, evidence,
   document, map, clip or still, and prefers lower tiers.
2. Production-time search:
   - the director may emit `REQUEST_SEARCH {sentence, entity, queries}`;
   - a deterministic gap check adds requests for sentences that mention an
     entity without any tier ≤ 3 asset;
   - requests run in a `visual_gaps` stage (bounded by
     `production_search.max_requests`); the new assets are verified and the
     affected beats are directed again.
3. Repetition control, tracked by `MediaUsage` (asset, film, language,
   time, seconds, sentence, reason, appearance number, whether reuse was
   justified):
   - Generic or contextual images and maps appear at most
     `max_generic_appearances` times per film (default 1).
   - Central people (victim, suspect) may recur up to
     `max_person_appearances`, at least `min_repeat_gap_seconds` apart.
   - Before reusing an asset, the director must prefer an unused
     alternative. Every reuse records its justification.
4. Density: cuts every ~5–9 s (sentence-snapped), up to one shot per
   sentence; clips count as shots.
5. Maps:
   - shown only where geography matters, at the sentence that first
     introduces a place;
   - never as the first shot of a film unless the opening strategy is
     `important_location`;
   - each place is mapped once per film;
   - zoom follows the place granularity: country → region → city → the
     relevant area or street;
   - later nearby places start at city level.

## 10. Story Director openings (R17)

1. Opening strategies: critical_moment, mysterious_statement,
   victim_introduction, evidence_discovery, emergency_call,
   important_location, contradiction, last_sighting, courtroom_outcome,
   unanswered_question, timeline_anomaly, plus previous_coverage for
   follow-ups only.
2. The director picks one per case and gives the reason. It avoids the
   strategies used in the last `opening.avoid_recent` films unless no other
   one fits the evidence.
3. Act 1 is written for that strategy. The first 20–40 seconds are
   case-specific.
4. The strategy is stored on the story, the production script and the
   video.

## 11. Music/Audio Director (R18–R22)

1. When the narrator speaks there is no music. Beds under narration are off
   (`audio_direction.beds_under_narration=false`).
2. Music only sits in the gaps:
   - between narration sections;
   - before a revelation (the gap before a reveal);
   - after important statements;
   - at chapter transitions;
   - under silent visual sequences.
   Cues start after the last word and fade out before the next word.
3. SILENCE (room tone) is an explicit choice. It is preferred around
   disturbing facts, revelations, emotional statements and unanswered
   questions.
4. Moods match the moment: suspense, investigation, mystery, melancholy,
   danger, discovery, tension, relief, resolution, uncertainty. Never
   "suspense because it is true crime".
5. Music library `MusicTrack`: several variants per kind and mood,
   generated lazily.
   - Selection skips tracks used in the last
     `music_library.reuse_after_videos` films (default 10) when an
     alternative exists or can be generated. Within one film a track may
     recur as its theme.
   - `MusicUsage` stores the track, film, language, time, purpose, mood, the
     director's reason and the selection reason.

## 12. Data model (all persistent)

- `Case`: + resolution_status, resolution_confidence, resolution_summary,
  resolution_checked_at, aliases_json, people_json, location, country,
  incident_date, latest_development_date, identifiers_json, origin.
- `DiscoveryCandidate` (CaseSuggestion): + case_id, resolution_*,
  incident_date, latest_development_date, location, aliases/people/
  source_urls json, recency_score, rank_score, suggestion_reason, state,
  duplicate_of_case_id, duplicate_of_candidate_id, duplicate_reason.
- `CaseStatusHistory`, `CaseStatusCheck`, `MonitorRun`.
- `Video`: case_id, production_script_id, job_id, language,
  production_type, original_video_id, episode_number, youtube_title,
  description, tags, status_at_production, status_at_publication,
  opening_strategy, file, state, published_at, youtube_url.
- `FollowUpCandidate`: case_id, original_video_id, status_check_id,
  previous_status, new_status, development, sources, state, follow_up_job_id,
  follow_up_video_id.
- `MediaUsage`, `MusicTrack`, `MusicUsage`.
- `VisualAsset`: + relevance_tier, case_relevance, entity_key, entity_type,
  found_during, clip_start, clip_end.
- `DocumentaryJob`: + production_type, follow_up_id.

## 13. Audit

`GET /api/cases/{id}/audit` answers, from stored data:

- why the case was suggested;
- duplicate verdicts;
- status changes with their sources;
- monitor checks;
- why each image or clip was chosen and why any reuse was allowed;
- why each music cue or silence was chosen;
- why each map was shown.

## 14. Acceptance tests

| ID | Scenario |
|---|---|
| A | A recent, solved, never-used case is suggested first, with a reason. |
| B | The same case under another title, alias or spelling is rejected as a duplicate (stored with the matched record). |
| C | An unsolved case shows its status in the API and UI, the production script, the archive filter and the YouTube title ("UNSOLVED:"), and gets a status card in the film. |
| D | A scheduled monitor run with no signal makes search calls only: no fetch, no LLM, outcome `no_signal`. |
| E | A signal plus deep verification with enough sources sets SOLVED, writes history and creates a follow-up candidate for a covered case. |
| F | No follow-up job exists until approval. Approval creates exactly one linked job. |
| G | The follow-up master story's opening references the previous video and says the case is now solved. |
| H | A sentence naming a church, place or person that has no matching asset produces a search request; the assets found are used. |
| I | A generic image is not reused while unused alternatives exist. Reuse is recorded with its justification. |
| J | A map appears at the first mention of a place, not as the opening shot, and with a zoom chain that fits the place. |
| K | No music plays under narration. |
| L | Cues sit in gaps or transitions. A director's SILENCE choice yields room tone, not music. |
| M | A track used in a recent film is not selected when an alternative exists. |
| N | Several productions differ in opening strategy, first shot, music tracks and cut rhythm. A cross-film variety check flags repeats. Verified on one English pilot (~3 min) of a recent solved case. |

## 15. Deliverables

- The code.
- Tests A–N, with the full suite green.
- The UI: status everywhere, archive filter, follow-up approval card,
  monitor history, media and music views.
- README sections.
- One English pilot.

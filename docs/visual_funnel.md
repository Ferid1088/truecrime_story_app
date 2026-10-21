# Visual funnel report — BEFORE any change (2026-10-21)

Read-only audit of case 9 (2022 University of Idaho murders) and case 8 (Lizzie Borden axe murders).
Nothing was tuned before this report existed. Reproduce every table with:

    python scripts/visual_funnel.py 9 --plan 4 --beats B04,B05 --sheets data/reports/visual_supply/sheets_before
    python scripts/visual_funnel.py 8 --plan 3 --beats B07,B10 --sheets data/reports/visual_supply/sheets_before

(the module is `app/documentary/visuals/funnel.py`, the sheets `app/documentary/visuals/contact_sheet.py`;
the report replays the director's own candidate ranking on the stored plan, it calls no provider.)
The picture sheets live in `data/reports/visual_supply/` (git-ignored: third-party pictures).

## 1. What the funnel shows (case 9, the 10-minute film, script 10 / en, 621 s)

| stage | number |
|---|---|
| stored photos (not generated) | 704 (research 540, production search 164) + 9 Internet Archive clips |
| of those: verified / needs_review / rejected / **never checked** | 54 / 70 / 264 / **316** (+ 9 clips) |
| vision checks that **failed** (gateway 403 "Insufficient balance", job 19: 44, job 20: 406 of 406) | 441 failed vs 391 completed |
| real pictures the film shows | 19 image shots, 17 distinct, 11.8 s each (224.9 s = 36 % of the film) |
| illustrations (generated, drawn) | 42 shots, 298 s = **48 % of the film** (+ 6 "generated context" shots, 47 s) |
| real video shots | 0 (9 Internet Archive clips downloaded, all unchecked; 31 YouTube videos found, all `research_only`) |
| the director's own plan (plan 5) | 35 shots for 19 beats (~2 per beat, 17 s each); coverage found 199 s of black before repair, then 42 illustrations filled it |

Case 8 (script 8, 518 s): 13 real image shots / 4 distinct (6.9 s each), 42 illustrations (292 s = 56 %), 3 real video shots (68 s).

So the owner's picture is confirmed in kind, and the causes are broader than the five seen in code:

1. **The director does not use what exists.** The plan has ~2 shots per beat although `max_shots` allows ~one per 7 s; every picture may appear once; the coverage repair then fills the rest with illustrations instead of more real pictures.
2. **Verification throws real pictures away.** 264 + 400 rejected photos; the contact sheets (section 5) show ~13 % of them are clear mistakes and the largest class, "watermark" (107 in case 9, 108 in case 8), is mostly printed/handwritten captions on postcards and evidence photos, not stock watermarks.
3. **A failed check is not a retry.** Case 8: 38 pictures were *rejected* with confidence 0.00 because the verifier's answer did not follow the schema (`{"entity":..,"match":"likely",..}`), e.g. the real Borden house. Case 9: 316 photos and 9 clips are simply unchecked because the gateway refused (balance), and they hold intake slots (`max_assets`).
4. **The supply loop never ran.** `supply.target` = 607 s / 40 = 17 and 51 verified pictures "reached" it, so no follow-up search was made (`rounds: []`, `searches: 1`). The 40 s/picture rule is far below what a 7-10 s edit needs (a 10-minute film needs ~60 shots; a 60-minute film ~360).
5. **Candidate lists are capped at 4 per requirement** (`CANDIDATES_PER_REQ`, 30 requirements of case 9 had more ranked pictures than the director saw).
6. **needs_review pictures of places/objects/documents are not supply** (`supply.verified_real` counts `verified` only) although the director may use them.
7. **Videos:** all 31 (case 9) and 47 (case 8) YouTube videos are `research_only` ("another channel", "not Creative Commons"); only open archives can be footage; there is no downloader for YouTube.

## 2. Caps and rules named (with the value reached)

| cap / rule | limit | case 9 | case 8 |
|---|---|---|---|
| `visual_search.max_assets` (non-rejected, non-generated stored; intake stops at the limit) | 400 | **450 — HIT** (325 of the slots are unchecked photos/clips) | 101 |
| `visual_search.max_queries` | 150 | 50 / 56 per job | n/a |
| `visual_search.max_candidates_per_query` | 15 | 701 candidates for 56 queries (not binding in total) | n/a |
| `supply.seconds_per_real_picture` / `supply.max_searches` | 40 s / 3 | target 17, loop ran 1 search (target "reached") | skipped |
| `visual_direction.max_search_requests` | 12 | 5-6 requests (weak sentences 6-8) | 5 |
| `CANDIDATES_PER_REQ` (now `visual_direction.candidates_per_requirement`) | 4 | **HIT for 30 requirements** | HIT for 28 |
| usage limits `max_*_appearances` / `min_repeat_gap_seconds` | 1 each / 45 s | every picture once; 1 reuse as reframe | same |
| director shots per beat (`seconds_per_shot` 7, `max_shots_per_beat` 14) | ~85 allowed | plan 5: 35 shots | 28 |
| `fill_ways.max_per_film` reframe / split / board / text | 8 / 3 / 3 / 6 | board **3 of 3 HIT**, text 3, reframe 2, split 0 | reframe 8, board 3 HIT, text 1 |
| `fill_ways.max_reuse_per_picture` | 2 | not reached | not reached |
| `coverage.max_still_seconds` | 12 s | **HIT**: longest still 18.4 s (film degraded) | HIT in job 14 (19.1 s) |
| `illustrations.max_per_film` / `max_share` | 70 / 0.85 | 42 shots, 48 % of film time | 42 shots, 56 % |
| `footage.max_clips_per_case` / `max_queries` / `max_pieces_per_source` | 40 / 25 / 12 | 9 clips, none used | 18 clips, 3 used |
| `footage.max_third_party_share` (0.15) / `max_third_party_clip_seconds` (20) | | not reached (no video used) | 68 s of 518 s = 13 % |
| `verified_min_confidence` / `reject_below_confidence` | 0.70 / 0.35 | 0.70 gate leaves the 18 usable non-person needs_review pictures of the sample at 0.45-0.65 | |
| `rights.enforce` | false (owner, 19 Oct) | not a filter | |

## 3. Why beats B04 and B05 of case 9 got no candidates

Plan 4 (blueprint 4, the English film of job 19) stored `candidates: {"B04": [], "B05": []}` (`plan_json`). Both beats need only `kabar_sheath`
(B04 "Police found no murder weapon… a tan leather knife sheath, stamped Ka-Bar", B05 "swabbed the sheath's button snap… DNA").
The pictures that exist for that entity (`replay`, table below): 656 stored pictures are about other things, 264 are rejected, 6 are held by the
reveal firewall. The three real sheath pictures were created at 02:51-02:56 and the plan was directed at 03:54:

* VIS_001028 — the lab evidence photo of the sheath: **rejected as "watermark" (0.82)**; the "watermark" is the lab's handwritten notes beside the sheath (contact sheet 9/rej3.17). A wrong rejection.
* VIS_001029 — the same kind of lab photo, verified 0.88 now; its vision check completed only in the production search *after* the plan (the first checks of the run were failing on the gateway balance).
* VIS_001030 — rejected (0.85) now; VIS_001020 — a museum Ka-Bar rejected `stand_in`/period; VIS_001160 — a Ka-Bar knife with sheath, `stand_in` (illustration role): not a candidate for an `evidence` requirement.

So the beats had no candidate because (a) the one genuine photo was rejected by an over-broad watermark rule, (b) the other genuine photo was not yet checked,
(c) a context picture of "a Ka-Bar knife with its sheath" cannot count as supply (no context tier), and (d) the production search ran after the plan and its
findings only reach a beat through a redirect. Replayed on today's library the same beats are offered exactly 4 pictures each — the cap.

## 4. Per-case tables (generated)

## Case 9 (visual plan 4, blueprint 4)

### Stored pictures per stage and provider

| found during | provider | type | stored | with file | verified | needs_review | rejected | unchecked | rej: watermark | rej: does_not_match | rej: low_confidence | rej: graphic_or_sensitive | rej: tone | rej: stand_in_person | rej: other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| generated | generated | map | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| generated | generated | photo | 358 | 358 | 358 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| production_search | brave_images | photo | 57 | 57 | 1 | 1 | 7 | 48 | 3 | 3 | 0 | 0 | 1 | 0 | 0 |
| production_search | wikimedia | photo | 107 | 107 | 0 | 1 | 14 | 92 | 4 | 10 | 0 | 0 | 0 | 0 | 0 |
| research | brave_images | photo | 315 | 315 | 35 | 36 | 99 | 145 | 49 | 32 | 8 | 3 | 7 | 0 | 0 |
| research | internet_archive | video | 9 | 9 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| research | lead_source | photo | 24 | 24 | 5 | 9 | 8 | 2 | 5 | 1 | 2 | 0 | 0 | 0 | 0 |
| research | source_page | photo | 45 | 45 | 11 | 16 | 18 | 0 | 13 | 0 | 3 | 0 | 2 | 0 | 0 |
| research | wikimedia | photo | 156 | 156 | 2 | 7 | 118 | 29 | 33 | 75 | 0 | 0 | 10 | 0 | 0 |

### Research stages (before storage)

| job | stage | status | detail |
|---|---|---|---|
| 19 | visual_research | done | {'queries': 50, 'candidates': 679, 'added': 150, 'duplicates': 161, 'rejected': 65, 'errors': 46, 'relevance_rejected': 34, 'by_provider': {'lead_source': 1, 'wikimedia': 29, 'brave_images': 120}, 'video_links': 65} |
| 19 | visual_check | degraded | {'checked': 399, 'rejected': 236, 'verified': 51, 'needs_review': 68, 'errors': 44, 'degraded': '44 vision checks failed — those candidates stay unverified', 'supply': {'target': 16, 'verified_before': 51, 'searches': 1, 'rounds': [], 'verified_after': 51, 'reached': True}} |
| 19 | visual_gaps | done | {'weak_sentences': 6, 'requests': 5, 'queries': 15, 'assets_found': 162, 'verified': 1, 'redirected_beats': ['B18'], 'used': 0, 'research': {'candidates': 210, 'added': 82, 'duplicates': 16, 'rejected': 12, 'errors': 12, 'by_provider': {'wikimedia': 59, 'brave_images': 23}}} |
| 20 | visual_research | done | {'added': 150, 'by_provider': {'brave_images': 121, 'lead_source': 1, 'wikimedia': 28}, 'candidates': 701, 'duplicates': 133, 'errors': 1, 'queries': 56, 'rejected': 64, 'relevance_rejected': 34, 'video_links': 49} |
| 20 | visual_check | degraded | {'checked': 406, 'degraded': '406 vision checks failed — those candidates stay unverified', 'errors': 406, 'supply': {'reached': True, 'rounds': [], 'searches': 1, 'target': 17, 'verified_after': 51, 'verified_before': 51}} |
| 20 | visual_gaps | done | {'assets_found': 164, 'queries': 18, 'redirected_beats': [], 'requests': 6, 'research': {'added': 82, 'by_provider': {'brave_images': 34, 'wikimedia': 48}, 'candidates': 227, 'duplicates': 14, 'errors': 11, 'rejected': 11}, 'used': 0, 'verified': 0, 'weak_sentences': 8} |

### Beats: offered to the director and used

| beat | seconds | offered now | stored with plan | tiers | pictures used |
|---|---|---|---|---|---|
| B01 | 19.2 | 13 | 10 | {1: 6, 2: 3, 3: 3, 4: 1} | 3 |
| B02 | 12.0 | 5 | 4 | {1: 2, 2: 3} | 2 |
| B03 | 22.4 | 7 | 6 | {1: 5, 2: 2} | 3 |
| B04 | 10.8 | 4 | 0 | {2: 4} | 1 |
| B05 | 26.4 | 4 | 0 | {2: 4} | 5 |
| B06 | 22.4 | 13 | 3 | {2: 5, 3: 6, 1: 2} | 3 |
| B07 | 9.2 | 6 | 2 | {2: 4, 3: 2} | 1 |
| B08 | 36.8 | 13 | 5 | {3: 2, 2: 8, 1: 3} | 6 |
| B09 | 37.6 | 5 | 4 | {1: 4, 2: 1} | 5 |
| B10 | 24.4 | 10 | 2 | {2: 10} | 4 |
| B11 | 12.8 | 5 | 2 | {3: 3, 2: 2} | 2 |
| B12 | 28.0 | 9 | 4 | {3: 3, 2: 6} | 4 |
| B13 | 18.4 | 8 | 4 | {3: 4, 2: 4} | 3 |
| B14 | 13.2 | 4 | 4 | {2: 4} | 2 |
| B15 | 28.0 | 9 | 4 | {2: 9} | 5 |
| B16 | 16.8 | 4 | 4 | {2: 4} | 3 |
| B17 | 15.2 | 4 | 4 | {2: 4} | 4 |
| B18 | 25.2 | 8 | 6 | {2: 7, 3: 1} | 3 |
| B19 | 17.6 | 4 | 3 | {1: 1, 2: 2, 3: 1} | 5 |
| B20 | 20.4 | 4 | 4 | {2: 4} | 2 |
| B21 | 12.0 | 4 | 4 | {2: 4} | 1 |

### Why beat B04 is short of candidates

| entity | purpose | kept away by (first rule) |
|---|---|---|
| kabar_sheath | evidence | {'rejected': 264, 'not_about_the_entity': 656, 'ranked': 145, 'reveal_firewall': 6, 'whole_video': 1, 'generated_card': 1} |

### Why beat B05 is short of candidates

| entity | purpose | kept away by (first rule) |
|---|---|---|
| kabar_sheath | investigation | {'rejected': 264, 'not_about_the_entity': 656, 'ranked': 145, 'reveal_firewall': 6, 'whole_video': 1, 'generated_card': 1} |

### Director validator

shots in the plan: 35; adjustments: {'map_wanted': 3, 'merged_short_shot': 8, 'deferred_map': 3, 'fallback': 2}; warnings: {'no_map_place': 2, 'map_place_not_told': 1, 'asset_not_candidate': 1}

### Coverage and audit stages

| job | stage | detail |
|---|---|---|
| 19 | visual_plan | {'visual_plan_id': 4, 'shots': 35, 'fallbacks': 2, 'maps': 1, 'documents': 0, 'dropped': 0} |
| 20 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 1, 'maps': 0, 'shots': 35, 'visual_plan_id': 5} |
| 19 | visual_audit | {'production_script_id': 10, 'audited': 46, 'approved': 17, 'replaced': 20, 'left_out': 9, 'symbolic': 1, 'degraded': '9 moment(s) without an approved picture (held, moved or black)'} |
| 20 | visual_audit | {'approved': 19, 'audited': 47, 'degraded': '9 moment(s) without an approved picture (held, moved or black)', 'left_out': 9, 'production_script_id': 9, 'replaced': 19, 'symbolic': 2} |
| 19 | visual_coverage | {'production_script_id': 10, 'rounds': 3, 'before': {'black_seconds': 153.96, 'card_share': 0.2606, 'longest_card_stretch': 50.54, 'longest_still': 49.19}, 'after': {'black_seconds': 0, 'card_share': 0.0097, 'longest_card_stretch': 6.0, 'longest_still': 19.28}, 'degraded': 'picture track still breaks: a still is shown for 19.28 s (max 12.0)'} |
| 20 | visual_coverage | {'after': {'black_seconds': 0, 'card_share': 0.0198, 'longest_card_stretch': 6.0, 'longest_still': 18.41}, 'before': {'black_seconds': 199.45, 'card_share': 0.4023, 'longest_card_stretch': 63.6, 'longest_still': 42.69}, 'degraded': 'picture track still breaks: a still is shown for 18.41 s (max 12.0)', 'production_script_id': 9, 'rounds': 3} |

### Caps and rules

| cap | limit | reached | hit |
|---|---|---|---|
| visual_search.max_assets | 400 | 450 | True |
| visual_search.max_queries | 150 | None | None |
| supply.max_searches | 3 | 1 | None |
| visual_direction.max_search_requests | 12 | 3 | False |
| director candidates per requirement | 4 | 30 | True |
| visual_direction.max_*_appearances | 1 | None | None |
| footage.max_pieces_per_source | 12 | None | None |
| coverage.max_still_seconds | 12.0 | None | None |

### Videos

found as sources: 31; clips downloaded: 9
clip assets: {'video': 9, 'video_source': 1}; clips by status: {'unverified': 9}

| use | reason | videos |
|---|---|---|
| research_only | a video of another channel is raw material for our story, never footage | 24 |
| research_only | the YouTube Data API does not mark it as Creative Commons | 7 |

### Fill ways and repairs per film

| script | shots by way |
|---|---|
| 9 | {'fill way text: the spoke': 3, 'coverage repair: an illu': 39, 'fill way reframe: Shows ': 1, 'auditor: replaced VIS_00': 6, 'fill way board: facts th': 3, 'fill way reframe: Authen': 1, 'black pause capped at 6s': 1, 'fill way reframe: Direct': 1, 'long hold varied: unused': 1} |
| 10 | {'auditor: replaced VIS_00': 6, 'coverage repair: an illu': 42, 'long hold varied: unused': 2, 'critic fix: unused pictu': 1, 'fill way text: the spoke': 3, 'fill way reframe: Authen': 1, 'fill way board: facts th': 3, 'fill way reframe: Accura': 1, 'VIS_001065 was already s': 1} |

### Films

| script | language | status | seconds | shots | real image shots | distinct real | avg s per real shot | video shots | video s | by kind |
|---|---|---|---|---|---|---|---|---|---|---|
| 9 | de | rendered | 607 | 75 | 22 | 19 | 10.7 | 0 | 0 | {'chapter': '1/2.4s', 'detective_board': '4/25.6s', 'generated image': '6/54.6s', 'illustration': '39/274.5s', 'intro': '1/6.0s', 'real image': '22/234.5s', 'timeline': '1/6.0s', 'title': '1/3.6s'} |
| 10 | en | audited | 621 | 74 | 19 | 17 | 11.8 | 0 | 0 | {'chapter': '1/2.0s', 'detective_board': '3/20.4s', 'generated image': '6/47.0s', 'generated map': '1/19.3s', 'illustration': '42/298.0s', 'intro': '1/6.0s', 'real image': '19/224.9s', 'title': '1/4.0s'} |


## Case 8 (visual plan 3, blueprint 3)

### Stored pictures per stage and provider

| found during | provider | type | stored | with file | verified | needs_review | rejected | unchecked | rej: watermark | rej: does_not_match | rej: low_confidence | rej: graphic_or_sensitive | rej: tone | rej: stand_in_person | rej: other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| generated | generated | map | 13 | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| generated | generated | photo | 242 | 242 | 242 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| research | brave_images | photo | 214 | 214 | 22 | 34 | 158 | 0 | 57 | 48 | 17 | 2 | 34 | 0 | 0 |
| research | internet_archive | video | 18 | 18 | 1 | 0 | 5 | 12 | 0 | 5 | 0 | 0 | 0 | 0 | 0 |
| research | lead_source | photo | 2 | 2 | 0 | 0 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| research | source_page | photo | 3 | 3 | 1 | 0 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| research | wikimedia | photo | 260 | 260 | 11 | 16 | 233 | 0 | 47 | 137 | 24 | 0 | 25 | 0 | 0 |

### Research stages (before storage)

| job | stage | status | detail |
|---|---|---|---|
| 9 | visual_research | done | {'queries': 29, 'candidates': 186, 'added': 84, 'duplicates': 42, 'rejected': 19, 'errors': 3, 'relevance_rejected': 12, 'by_provider': {'wikimedia': 39, 'brave_images': 45}, 'video_links': 54} |
| 10 | visual_research | done | {'added': 0, 'by_provider': {}, 'candidates': 186, 'duplicates': 0, 'errors': 0, 'queries': 29, 'rejected': 0, 'relevance_rejected': 0, 'video_links': 1} |
| 10 | visual_check | done | {'checked': 29, 'needs_review': 1, 'rejected': 26, 'verified': 2} |
| 10 | visual_gaps | done | {'assets_found': 0, 'queries': 36, 'redirected_beats': [], 'requests': 12, 'research': {'added': 0, 'by_provider': {}, 'candidates': 191, 'duplicates': 0, 'errors': 0, 'rejected': 0}, 'used': 0, 'verified': 0, 'weak_sentences': 38} |
| 12 | visual_research | done | {'added': 0, 'by_provider': {}, 'candidates': 183, 'duplicates': 0, 'errors': 0, 'queries': 29, 'rejected': 0, 'relevance_rejected': 0, 'video_links': 1} |
| 12 | visual_check | degraded | {'checked': 5, 'degraded': '1 vision checks failed — those candidates stay unverified', 'errors': 1, 'needs_review': 1, 'rejected': 3} |
| 12 | visual_gaps | done | {'assets_found': 0, 'queries': 36, 'redirected_beats': [], 'requests': 12, 'research': {'added': 0, 'by_provider': {}, 'candidates': 187, 'duplicates': 0, 'errors': 0, 'rejected': 0}, 'used': 0, 'verified': 0, 'weak_sentences': 42} |
| 14 | visual_research | done | {'added': 0, 'by_provider': {}, 'candidates': 187, 'duplicates': 0, 'errors': 0, 'queries': 29, 'rejected': 0, 'relevance_rejected': 0, 'video_links': 1} |
| 14 | visual_check | done | {'checked': 5, 'needs_review': 2, 'rejected': 1, 'verified': 2} |
| 14 | visual_gaps | done | {'assets_found': 0, 'queries': 36, 'redirected_beats': [], 'requests': 12, 'research': {'added': 0, 'by_provider': {}, 'candidates': 206, 'duplicates': 0, 'errors': 0, 'rejected': 0}, 'used': 0, 'verified': 0, 'weak_sentences': 41} |
| 16 | visual_research | done | {'queries': 29, 'candidates': 183, 'added': 0, 'duplicates': 0, 'rejected': 0, 'errors': 0, 'relevance_rejected': 0, 'by_provider': {}, 'video_links': 4} |
| 16 | visual_check | degraded | {'checked': 54, 'rejected': 46, 'verified': 1, 'needs_review': 5, 'errors': 2, 'degraded': '2 vision checks failed — those candidates stay unverified'} |

### Beats: offered to the director and used

| beat | seconds | offered now | stored with plan | tiers | pictures used |
|---|---|---|---|---|---|
| B01 | 36.8 | 20 | 2 | {1: 5, 3: 8, 2: 7} | 14 |
| B02 | 10.0 | 13 | 4 | {1: 5, 2: 5, 3: 3} | 1 |
| B03 | 20.0 | 12 | 4 | {1: 6, 2: 5, 3: 1} | 9 |
| B04 | 44.8 | 13 | 3 | {1: 7, 2: 5, 3: 1} | 22 |
| B05 | 44.8 | 12 | 2 | {1: 7, 2: 4, 3: 1} | 14 |
| B06 | 30.0 | 16 | 4 | {1: 7, 3: 4, 2: 5} | 9 |
| B07 | 21.6 | 4 | 3 | {1: 4} | 13 |
| B08 | 23.2 | 15 | 3 | {1: 6, 2: 4, 3: 5} | 12 |
| B09 | 48.8 | 20 | 4 | {3: 8, 1: 7, 2: 5} | 12 |
| B10 | 46.0 | 4 | 3 | {1: 4} | 22 |
| B11 | 32.4 | 8 | 0 | {1: 6, 2: 2} | 14 |
| B12 | 31.2 | 8 | 3 | {1: 6, 2: 2} | 12 |
| B13 | 32.0 | 10 | 5 | {1: 7, 2: 3} | 0 |
| B14 | 7.2 | 14 | 5 | {1: 7, 2: 5, 3: 2} | 0 |

### Why beat B07 is short of candidates

| entity | purpose | kept away by (first rule) |
|---|---|---|
| borden_house | investigation | {'rejected': 400, 'ranked': 275, 'not_about_the_entity': 58, 'generated_card': 13, 'whole_video': 4, 'reveal_firewall': 6} |

### Why beat B10 is short of candidates

| entity | purpose | kept away by (first rule) |
|---|---|---|
| borden_house | investigation | {'rejected': 400, 'ranked': 275, 'not_about_the_entity': 58, 'generated_card': 13, 'whole_video': 4, 'reveal_firewall': 6} |
| adelaide_churchill | human_connection | {'rejected': 400, 'not_about_the_entity': 266, 'generated_card': 13, 'ranked': 67, 'whole_video': 4, 'reveal_firewall': 6} |
| bridget_sullivan | human_connection | {'rejected': 400, 'not_about_the_entity': 253, 'generated_card': 13, 'ranked': 80, 'whole_video': 4, 'reveal_firewall': 6} |

### Director validator

shots in the plan: 28; adjustments: {'merged_short_shot': 7}; warnings: {}

### Coverage and audit stages

| job | stage | detail |
|---|---|---|
| 10 | visual_plan | {'documents': 0, 'dropped': 1, 'fallbacks': 0, 'maps': 1, 'shots': 21, 'visual_plan_id': 3} |
| 11 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 1, 'shots': 21, 'visual_plan_id': 3} |
| 12 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 22, 'visual_plan_id': 3} |
| 13 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 22, 'visual_plan_id': 3} |
| 14 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 26, 'visual_plan_id': 3} |
| 15 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 26, 'visual_plan_id': 3} |
| 16 | visual_plan | {'visual_plan_id': 3, 'shots': 28, 'fallbacks': 0, 'maps': 2, 'documents': 0, 'dropped': 0} |
| 17 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 28, 'visual_plan_id': 3} |
| 18 | visual_plan | {'documents': 0, 'dropped': 0, 'fallbacks': 0, 'maps': 2, 'shots': 28, 'visual_plan_id': 3} |
| 9 | visual_audit | {} |
| 10 | visual_audit | {'approved': 1, 'audited': 7, 'degraded': '6 moment(s) without an approved picture (held, moved or black)', 'left_out': 6, 'production_script_id': 1, 'replaced': 0, 'symbolic': 0} |
| 11 | visual_audit | {'approved': 1, 'audited': 8, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 2, 'replaced': 3, 'symbolic': 0} |
| 12 | visual_audit | {'approved': 1, 'audited': 5, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 3, 'replaced': 0, 'symbolic': 0} |
| 13 | visual_audit | {'approved': 1, 'audited': 5, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 4, 'replaced': 0, 'symbolic': 0} |
| 14 | visual_audit | {'approved': 4, 'audited': 16, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 5, 'replaced': 8, 'symbolic': 1} |
| 15 | visual_audit | {'approved': 4, 'audited': 16, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 6, 'replaced': 8, 'symbolic': 1} |
| 16 | visual_audit | {} |
| 17 | visual_audit | {'approved': 5, 'audited': 19, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 7, 'replaced': 10, 'symbolic': 0} |
| 18 | visual_audit | {'approved': 5, 'audited': 19, 'degraded': '4 moment(s) without an approved picture (held, moved or black)', 'left_out': 4, 'production_script_id': 8, 'replaced': 10, 'symbolic': 0} |
| 14 | visual_coverage | {'after': {'black_seconds': 245.76, 'card_share': 0.4861, 'longest_card_stretch': 76.32, 'longest_still': 19.13}, 'before': {'black_seconds': 152.01, 'card_share': 0.3109, 'longest_card_stretch': 144.84, 'longest_still': 150.76}, 'degraded': 'picture track still breaks: 245.76 s of black (max 0.0); cards are 48.6% of the film (max 12%); longest card stretch 76.32 s (max 6.0); a still is shown for 19.13 s (max 12.0)', 'production_script_id': 5, 'rounds': 3} |
| 15 | visual_coverage | {'after': {'black_seconds': 0, 'card_share': 0.0116, 'longest_card_stretch': 6.0, 'longest_still': 12.0}, 'before': {'black_seconds': 152.01, 'card_share': 0.3109, 'longest_card_stretch': 144.84, 'longest_still': 150.76}, 'production_script_id': 6, 'rounds': 2} |
| 16 | visual_coverage | {} |
| 17 | visual_coverage | {'after': {'black_seconds': 0, 'card_share': 0.0232, 'longest_card_stretch': 6.0, 'longest_still': 12.0}, 'before': {'black_seconds': 201.62, 'card_share': 0.4354, 'longest_card_stretch': 99.45, 'longest_still': 150.76}, 'production_script_id': 7, 'rounds': 2} |
| 18 | visual_coverage | {'after': {'black_seconds': 0, 'card_share': 0.0232, 'longest_card_stretch': 6.0, 'longest_still': 12.0}, 'before': {'black_seconds': 201.62, 'card_share': 0.4354, 'longest_card_stretch': 99.45, 'longest_still': 150.76}, 'production_script_id': 8, 'rounds': 2} |

### Caps and rules

| cap | limit | reached | hit |
|---|---|---|---|
| visual_search.max_assets | 400 | 101 | False |
| visual_search.max_queries | 150 | None | None |
| supply.max_searches | 3 | None | None |
| visual_direction.max_search_requests | 12 | 5 | False |
| director candidates per requirement | 4 | 28 | True |
| visual_direction.max_*_appearances | 1 | None | None |
| footage.max_pieces_per_source | 12 | None | None |
| coverage.max_still_seconds | 12.0 | None | None |

### Videos

found as sources: 47; clips downloaded: 18
clip assets: {'video': 18, 'video_source': 4}; clips by status: {'rejected': 5, 'verified': 1, 'unverified': 12}

| use | reason | videos |
|---|---|---|
| research_only | a video of another channel is raw material for our story, never footage | 47 |

### Fill ways and repairs per film

| script | shots by way |
|---|---|
| 1 | {} |
| 2 | {'auditor: replaced VIS_00': 3} |
| 3 | {} |
| 4 | {} |
| 5 | {'auditor: replaced VIS_00': 26, 'coverage repair: an illu': 19} |
| 6 | {'coverage repair: an illu': 53, 'auditor: replaced VIS_00': 2} |
| 7 | {'coverage repair: an illu': 54, 'auditor: replaced VIS_00': 2} |
| 8 | {'fill way text: the spoke': 1, 'coverage repair: an illu': 42, 'auditor: replaced VIS_00': 2, 'fill way reframe: Shows ': 1, 'fill way reframe: Depict': 2, 'fill way reframe: Authen': 5, 'fill way board: facts th': 3} |

### Films

| script | language | status | seconds | shots | real image shots | distinct real | avg s per real shot | video shots | video s | by kind |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | en | produced | 518 | 17 | 1 | 1 | 2.9 | 3 | 68.1 | {'black': '8/357.8s', 'chapter': '1/4.5s', 'generated map': '1/21.1s', 'intro': '1/6.0s', 'real image': '1/2.9s', 'real video': '3/68.1s', 'timeline': '1/53.2s', 'title': '1/4.5s'} |
| 2 | en | rendered | 518 | 17 | 1 | 1 | 73.4 | 3 | 68.1 | {'black': '8/287.2s', 'chapter': '1/4.5s', 'generated map': '1/21.1s', 'intro': '1/6.0s', 'real image': '1/73.4s', 'real video': '3/68.1s', 'timeline': '1/53.2s', 'title': '1/4.5s'} |
| 3 | en | rendered | 518 | 15 | 1 | 1 | 2.9 | 3 | 68.1 | {'black': '4/170.6s', 'chapter': '1/4.5s', 'generated map': '3/246.5s', 'intro': '1/6.0s', 'real image': '1/2.9s', 'real video': '3/68.1s', 'timeline': '1/14.9s', 'title': '1/4.5s'} |
| 4 | en | rendered | 518 | 15 | 1 | 1 | 2.9 | 3 | 68.1 | {'black': '4/170.6s', 'chapter': '1/4.5s', 'generated map': '3/246.5s', 'intro': '1/6.0s', 'real image': '1/2.9s', 'real video': '3/68.1s', 'timeline': '1/14.9s', 'title': '1/4.5s'} |
| 5 | en | rendered | 518 | 55 | 3 | 3 | 16.3 | 3 | 68.1 | {'black': '35/245.8s', 'chapter': '1/1.5s', 'generated map': '2/37.9s', 'illustration': '9/105.2s', 'intro': '1/6.0s', 'real image': '3/49.0s', 'real video': '3/68.1s', 'title': '1/4.5s'} |
| 6 | en | rendered | 518 | 65 | 3 | 3 | 9.0 | 3 | 68.1 | {'chapter': '1/1.5s', 'generated map': '2/24.0s', 'illustration': '54/387.0s', 'intro': '1/6.0s', 'real image': '3/26.9s', 'real video': '3/68.1s', 'title': '1/4.5s'} |
| 7 | en | rendered | 518 | 68 | 5 | 4 | 7.8 | 3 | 68.1 | {'chapter': '1/1.5s', 'generated map': '2/17.6s', 'illustration': '54/375.5s', 'intro': '1/6.0s', 'real image': '5/38.8s', 'real video': '3/68.1s', 'timeline': '1/6.0s', 'title': '1/4.5s'} |
| 8 | en | rendered | 518 | 68 | 13 | 4 | 6.9 | 3 | 68.1 | {'chapter': '1/1.5s', 'generated image': '4/32.4s', 'generated map': '2/17.6s', 'illustration': '42/292.4s', 'intro': '1/6.0s', 'real image': '13/89.5s', 'real video': '3/68.1s', 'timeline': '1/6.0s', 'title': '1/4.5s'} |


## 5. Contact sheets: were the verdicts right? (reviewed by eye, 2026-10-21)

Sheets: `data/reports/visual_supply/sheets_before/case_{8,9}/{rejected_1..3,verified_1,needs_review_1}.png`
(60 rejected spread over the rejection reasons, 20 verified, 20 needs_review, per case; reason and confidence under each picture).

### Rejected (60 per case)

| | case 9 | case 8 | both |
|---|---|---|---|
| clearly wrong rejection (picture is usable under the owner's rules) | 7 (+1 arguable) | 8 (+2 arguable) | **15 of 120 = 12.5 %** |
| person rejected only because identity rests on the page title, not on the image (policy rule 1, stays rejected) | 20 | 6 | 26 (22 %) |
| rejection right (other case, other place, actor/film still, stock look, graphic crime-scene, logo/TV graphic) | 32 | 44 | 76 (63 %) |

Clearly wrong, with the reason the picture was lost:

| picture | what it is | recorded reason | why it is wrong |
|---|---|---|---|
| 9: VIS_001028 | lab evidence photo of the Ka-Bar sheath | watermark 0.82 | handwritten lab notes next to the object are not a watermark; it is the key evidence picture |
| 9: VIS_000834, VIS_000907 | 1915 / 1943 postcards of the University of Idaho campus and Moscow | watermark 0.80 | the printed postcard caption flagged as watermark; good context pictures of the place |
| 9: VIS_000787 | the four victims, identity named by the NBC caption (verdict `matches yes`) | watermark 0.60 | blurred side bars flagged as watermark |
| 9: VIS_001040 | a real court document of the case (State v. Kohberger order) | does_not_match 0.85 | found for another query; it is a document of the case (no `entities` returned, so the "other entity" rule did not fire) |
| 9: VIS_001123 | 1954 panorama of campus and town, Moscow, Idaho | stand_in / period no 0.60 | right place, old photograph, no context tier to accept it |
| 9: VIS_001020 | museum Ka-Bar knife with sheath | stand_in / period no 0.60 | an object of the kind (context), cannot count as supply |
| 9: VIS_000986 (arguable) | collage of the four victims, "Idaho murder victims" | does_not_match 0.90 | a group picture of the victims, nobody singled out |
| 8: VIS_000081 | the Lizzie Borden House, Fall River (2017) | does_not_match 0.80 | found for "house interior"; it is the exact house in a modern view (period no) |
| 8: VIS_000303 | downtown Fall River (modern) | does_not_match 0.85 | right town, modern view |
| 8: VIS_000065, 000307, 000466 | Fall River Main Street c. 1920 (colourised), Wonder World at Rocky Point postcard, Fall River station 1912 postcard | watermark 0.70-0.85 | printed caption / reddit title flagged as watermark |
| 8: VIS_000079, VIS_000383 | Maplecroft (Lizzie's later house), a Lizzie Borden portrait | low_confidence **0.00** | the verifier answer did not follow the schema (no `depicts`/`matches_claim`) and was recorded as a permanent rejection; 38 of 400 rejected photos in case 8 are such empty verdicts |
| 8: VIS_000068, VIS_000491 (arguable) | remains of the Rocky Point Skyliner ride; Fall River waterfront mills | does_not_match 0.85 / stand_in 0.50 | the right place in a modern view |

Four patterns explain nearly all of it: (1) the verifier's `watermark` field is defined as "stock watermark, large logo, TV title bar, burned-in headline or caption text,
decorative frame or any other branding" so printed/handwritten caption text rejects archival pictures; (2) context and modern pictures of the right place or
a document of the case are rejected because they differ from what the search asked for; (3) an unparseable verdict is stored as a rejection; (4) a person whose
name rests on the page title is rejected (kept: strict rule 1).

### Verified (20 per case)
Case 9: 20 of 20 show what they claim (house with police tape and the number 1122 visible, the four victims as a captioned collage, the suspect in court, the lab sheath photo, welcome sign, demolition of the house). Case 8: 20 of 20 correct (the house in daylight, night and 1890s view, Lizzie portraits, graves). Precision of `verified` in this sample: 40/40. The identity of the people in the verified person pictures rests on caption/page title (the existing rule accepted it); I could not check a face against an independent source.

### needs_review (20 per case)
Case 9: 13 are people whose identity rests on the page title or caption (confidence 0.35-0.65, they stay with the owner); 7 are not people and look usable as context or case material: memorial with the framed victim names (VIS_000824), evidence bins carried out of the house (VIS_000796), Moscow Police SUV (VIS_000945), the FBI Receipt for Property (VIS_001047, a real document), Flock camera (VIS_001100), aerial of Moscow (VIS_001127), Parkside Chapel in Monroe County (VIS_000988).
Case 8: 11 are Fall River buildings, streets and museum interiors (St Anne's, Braga Bridge, mansions, 1890s street views) usable as place context; 6 are people (portraits, engravings, a visitor: owner); 2 are archival crime-scene photographs (graphic: owner); 1 is a stylised dark edit of the house (not usable).
In total 19 of the 40 sampled needs_review pictures are non-person pictures that a second independent pass could resolve (what S3b asks for); 21 are people or graphic and stay with the owner.

## 6. AFTER — live proof on case 9 (2026-10-21, real app, real PostgreSQL, real providers)

Run with `python scripts/visual_stages.py 9 --blueprint 4 --language en --go` (estimate printed first: about EUR 21.2 at most; research cost guard EUR 15)
and `--reuse-plan 7` for the second build. Only visual stages ran: stored verdicts judged again (free), vision check and second pass, supply for the
film's own length and for a 60-minute target, a new visual plan (plan 7), the production search, a new production script from the stored narration
(script 11, then 12 with `coverage.pool_tries` 6), the picture audit and the coverage repair. **No new text, no new audio, no render.**
Migrations 0057-0059 were applied to the live PostgreSQL first (alembic_version = 0059; `visual_search_log`, `visual_assets.file_removed_at`,
`video_sources.source_class/class_evidence` shown with `information_schema` queries).

| measure (case 9, English film of 621 s) | before (script 10) | after (script 12) |
|---|---|---|
| real photos verified (non-generated) | 54 | **179** (191 before the object-context tightening) |
| needs_review / rejected / never checked | 70 / 264 / 316 | 112 / 796 / **0** (new candidates included) |
| supply target for this film (distinct pictures + cuts) | 16 (old rule, 607 s / 40) | 38 (30 stills + 8 cuts) — usable 138-179, reached |
| supply target for a 60-minute film | 90 (old rule) | 216 (171 stills + 45 cuts) — usable 192 after round 1, **shortage 24**; stopped by the EUR 15 research cost guard, not exhausted |
| real image shots / distinct pictures | 19 / 17 | **37 / 35** |
| average seconds per real picture shot | 11.8 | 13.1 (target 9.5: **not reached**, see below) |
| real pictures' share of film time | 36 % | **78 %** (482.9 s) |
| illustrations (generated/drawn) | 42 shots, 298 s (48 %) | **0** |
| generation calls (ledger rows `visual_illustration`, `images/generations` since the run began) | 42 pictures made | **0** |
| fill cards of the story's own words/facts, zoom map | 4 + 1 | 4 cards (57.5 s) + 1 map (35.7 s) |
| video shots / pieces used | 0 | 0 (421 pieces cut, 1 verified, 388 not yet checked: the guard stopped first) |
| beats B04 / B05 offered candidates | 0 / 0 (plan 4) | 12 / 12 (plan 7); 13 offered now |
| disk use of the case (photos, pieces counted once) | ~0.64 GB of photos | 1.97 GB (budget 3.0 GB): photos 0.82 GB, clips 0.75 GB, generated 0.30 GB |
| actual spend of this task on case 9 | | **EUR 26.2** (case total 61.28 of 300): vision checks 10.7, audits 1.0, video pieces 1.5, director 0.5, searches 0.13 (29 Brave requests), second pass 0.1, supply guard run included |

Why the average is still above the 9.5 s rhythm and 120 s are held: the picture audit approves a picture only for words it fits. After the plan,
audit and repair, 140 unused verified real pictures remain in the pool but none was approved for the 17 + 3 gap slots (abstract narration about
cell-tower records and DNA); those gaps are held by the neighbouring picture (never black, never a drawing) and reported as a shortage
(66.7 s + 28.8 s missing; longest still 35.7 s against `coverage.max_still_seconds` 20 → the film is marked degraded, as the rule intends).
Raising `pool_tries` from 2 to 6 changed nothing (the audit cache returned the same verdicts): the limit is fit-to-words, not the number of tries.

### Precision check of the newly accepted pictures (S3)
83 pictures were newly accepted (53 by a model call during the task, 30 by the stored-verdict re-judging); I looked at 60 of them
(`data/reports/visual_supply/sheets_after/case_9/newly_accepted_{1,2,3}.png`).
* **Wrong person: 0.** Two person pictures were accepted (Kohberger in court, titles name him); both match the other verified pictures of him. I could not check a face against an independent source.
* Doubtful: **7 of 60 (12 %)**: one stock-style DNA tube picture, one empty night road, one unlocated campus view, and **four sheaths of another kind** accepted as "context of the Ka-Bar sheath" at confidence 0.60 (a black tanto sheath, a hand-made suede sheath, a horsehide sheath, a WWII trench-knife scabbard).
* Action: context verdicts of **objects** now need 0.70 (`context_min_confidence_by_subject`); re-judging the stored verdicts moved 12 objects back to needs_review (verified 191 → 179). None of the doubtful pictures is in the film (script 11/12 checked).
* The film's 37 real shots: 36 `yes`/`verified` pictures and 3 context pictures; the key-frame sheet is `film12_shots.png`.

### What I did not watch
I did not render or watch any film; I looked at the contact sheets and at one thumbnail sheet of the 42 picture shots of script 12. The sentence-by-sentence fit of each picture was judged by the auditor model, not by me.

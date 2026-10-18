# Master task checklist (research, videos, facts, pictures, rights; docs/story_change_1.txt part 2)

Live = real stack, case 8. Test names marked "(not re-listed)" were written in commit c9e1548
(before this session); this session did not re-run their live proof.

| id | requirement | status | tests | live evidence |
|----|-------------|--------|-------|---------------|
| 0 | other channels' videos are research only | DONE | test_video_rights.py (classifier: other true-crime channels always `research_only`) | live: 51 videos of case 8 are `research_only` after the backfill; screenshot `docs/evidence/videos-use-and-rights.png` |
| V1 | skipped-video baseline | DONE (baseline from job 32, current state re-queried live) | tests/test_transcript_pipeline.py (skip reasons are stored and shown) | Live, case 8, 2026-10-17: `select id, skip_reason from video_sources where case_id=8 and transcript_status='skipped'` returns 0 rows; 47 videos (ids 20-66): 38 processed, 8 unavailable (23, 24, 36, 37, 38, 39, 45, 59), 1 failed (41). The baseline (job 32: 10 videos skipped, all `per_language_cap`, 7 of 10 judged relevant by the separate reviewer) is in `docs/benchmarks/video_skips.md`; its rows (ids 9-18) were replaced when job 35 rediscovered the case (ids now start at 20), so the baseline ids can no longer be queried and the claim there that all 10 were processed again cannot be re-checked from ids. |
| V2 | no count-based skipping | DONE | tests/test_transcript_pipeline.py (relevant video beyond the old caps is processed; unrelated video skipped with its reason; low-novelty video processed) | Live: the old cap was `max_videos_per_language` 4. Processed on case 8 per language: en 22 (ids 20, 21, 22, 35, 42-44, 46-58, 63, 64), de 5 (28, 30, 31, 65, 66), fa 5 (25, 26, 32, 33, 60), ar 6 (27, 29, 34, 40, 61, 62); 0 skipped. Low novelty is processed: id 25 (fa) has novel_information_ratio 0.0 and is `processed`. The relevance agent (real call, ledger stage "Video Relevance") on 4 real borderline videos (ids 30, 31, 32, 33): all relevant; on 4 invented candidates: Minecraft tutorial -> not relevant ("Minecraft video unrelated to the case"), pasta recipe -> not relevant, West Memphis Three documentary -> not relevant ("About the West Memphis Three case"), a ghost-tour video that visits the Borden house -> relevant (doubt means relevant). The invented candidates were not stored. |
| V3 | video tests | DONE earlier (c9e1548) | (not re-listed) | |
| F1 | facts cap 100 | DONE | test_evidence_pack.py (cap, protected disputed/contradicted facts are never dropped) | case 8: 1361 facts; evidence pack 156 (100 + protected); draft pack uses all 1361 (extras appended after F-ids) |
| P1 | photo/video search audit | DONE; one live bug found and fixed | tests/test_case_visual_search.py (every beat that needs a picture gets a query even when the cap cuts; `test_a_providers_date_range_fits_the_assets_date_columns`) | Live search on case 8 (Brave images + Wikimedia + source pages + Brave video links, 18 queries): stopped at the 24th picture with `StringDataRightTruncation` (a Wikimedia date range `1900-01-01/1900-12-31` is 21 characters; `date_start` is varchar(20)). Fixed by `date_span` (range -> date_start and date_end). Second run finished: visual_assets ids 1-37 (36 rows; local file stored for all 36), media_links 18 (17 youtube.com, 1 britannica.com, status `new`). Per row provider, rights_status and verification_status are stored: brave_images 22 + lead_source 2 + source_page 3 all `editorial_review_required`; wikimedia 8 `public_domain`, 1 `creative_commons`; every row `unverified` (the verification agent has not run). Of the 90 stored URLs none contains a blocked-domain fragment (8 configured: gettyimages., shutterstock., ...). Footage search: 8 queries, 0 candidates. Quality note: person queries such as "Alice Russell Lizzie Borden axe murders" return unrelated pages (a 1900 census sheet was stored as a `public_domain` picture); the verification agent is the filter and it has not run. |
| P2 | case without pictures triggers search | DONE | tests/test_case_visual_search.py::test_a_case_without_stored_pictures_triggers_the_search, ::test_a_case_that_already_has_candidates_is_not_searched_again | Live: before, case 8 had `visual_assets` 0 and `media_links` 0 and `has_stored_candidates` was False; `search_case_visuals` ran the 18 queries (above). After: 36 and 18 rows, and a second `search_case_visuals` call returns None (nothing searched again). The first call was interrupted by the date bug (23 rows stored before it); the rest was fetched by calling the same agent with the same queries (71 duplicates skipped by hash, 13 added). |
| R1 | VideoSource use/rights fields, migration with owner approval | DONE | test_video_rights.py | backup `data/backups/truecrime-postgres-20261017-032248+0200.dump` shown to the owner; owner chose "Apply now"; alembic 0039 applied on live PostgreSQL; columns use, use_reason, rights_status, license_evidence_url, license_checked_at, attribution_text queried; 51 rows backfilled `research_only` |
| R2 | classify at discovery | DONE | test_video_rights.py (VideoUseClassifier) | wired in `video_research.py`; new rows carry use + reason |
| R3 | rights gate | DONE | test_video_rights.py (VideoRightsGate, RightsRefused) | gate refuses non-film-candidates; film candidates need evidence and an allowed host |
| R4 | film use limits | PARTIAL, OWNER BLOCKER (section below) | test_video_rights.py, script `_clip_overruns` tests | caps: 20 s per clip, 15 % of the film. Not live-proven: no film candidate exists in case 8; no YouTube downloader (yt-dlp absent), only direct media files from allowed hosts; the description attribution list was not extended |
| R5 | originality guard as gate of the language pipeline | DONE | test_language_pipeline_originality.py | case 8 finals: 78 transcript wordings compared, longest shared run 0/11/6/6 words (fa/en/de/ar), 6-gram share <= 0.2 %, 0 near-verbatim sentences |
| R6 | UI per video | DONE | e2e/app.spec.ts (mocked) | live screenshot: Videos tab shows Use / rights / evidence / reason columns (47 videos at that moment: 38 processed, 8 unavailable, 1 failed, all research_only) |
| R7 | tests | DONE | test_video_rights.py, test_language_pipeline_originality.py | full suite result recorded in the final report |

## R4 owner blocker: what film candidates need (nothing here is faked)

State today (verified): the rights gate, the classifier, the clip limits (20 s per third-party clip,
15 % of the film) and `FootageAgent.ingest_film_candidate` exist and are unit tested. No film
candidate exists on case 8 (all 47 videos are `research_only`), and no film-candidate clip has
ever been downloaded, cut or placed. What is missing, and why:

1. **No YouTube downloader.** `which yt-dlp` prints nothing on this machine; `ffmpeg` exists
   (/opt/homebrew/bin/ffmpeg). `ingest_film_candidate` fetches only a direct media file
   (.mp4, .webm, .ogv ...) and refuses a web page. Needed from the owner: install yt-dlp
   (`brew install yt-dlp`), and an instruction to build a downloader adapter. Not built because
   it cannot be verified live without yt-dlp.
2. **No YouTube licence data.** Discovery uses `search?part=snippet`; the licence is only in
   `videos.list?part=status` (`status.license == "creativeCommon"`), which the code never calls, so
   `candidate["license"]` is never set and every YouTube video is `research_only` (the safe
   default). Also `TRUECRIME_YOUTUBE_API_KEY` is not set (the YouTube Data API returns nothing without it).
   Needed from the owner: a YouTube Data API key and the go-ahead to add the `videos.list` licence lookup.
3. **No discovery of licensed sources.** The film-candidate hosts are configured
   (`config/parameters/video_rights.json`): Internet Archive, Wikimedia Commons (open archives);
   Library of Congress, US National Archives, UK National Archives, Bundesarchiv, Europeana (public
   archives, need a stated open licence or public-domain mark); BBC, ZDF, ARD (public broadcasters,
   only with a stored licence page or written permission). Nothing searches them for video
   yet: the footage search found 0 candidates for 8 queries on case 8. For an 1892 case there is
   no contemporary film; realistic material is later newsreel or documentary footage of the house and
   town, and archive.org / Commons files are direct media URLs, so they need no yt-dlp.
   Needed from the owner: which of these sources may be used for this channel, and, for any
   broadcaster, the written permission or licence page.
4. **Owner decision on editorial_review_required.** Film candidates under that rights status need
   the recorded owner approval that exists for pictures (R3); nothing is pending because no candidate exists.

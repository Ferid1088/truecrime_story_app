# YouTube clip downloader — terms read and guardrails

Switch: Settings → "YouTube clip downloader" (`/api/documentary/visuals/youtube-clips`). **Default OFF.** The agent does not
turn it on (a test scans the code: only the owner's Settings endpoint may call `set_enabled`); the owner does. Until then every
YouTube film candidate is counted as refused ("the YouTube clip downloader switch is off (Settings)") in the research stage.

## The downloader
yt-dlp, installed as the Python package `yt-dlp==2026.8.19` (requirements-documentary.txt). Read on 2026-10-21 (github.com/yt-dlp/yt-dlp):
"While yt-dlp is licensed under the Unlicense" — the package is public domain; its PyInstaller-bundled executables are GPLv3+
("the combined work is licensed under GPLv3+"), which does not apply here because the app imports the Python package and does not
ship the executable. `--download-sections` (time ranges) exists and needs ffmpeg; this route downloads the whole allowed video
(bounded: `youtube_clips.max_height` 720, `max_download_mb` 400, `max_source_seconds` 1800) and cuts it with the app's own segmenter.
The page text I could read has no legal disclaimer about the terms of the sites downloaded from; YouTube's terms on downloading
are the owner's accepted risk (owner decision 21 Oct), as are Content ID claims and the "reused content" monetisation rule.

## Guardrails (all with tests in tests/test_youtube_clips.py and tests/test_video_source_classes.py)
* only a film candidate of an allowed class: official body, news agency/broadcaster raw footage, witness or family video, Creative
  Commons video of a channel that is not a commentator, with the evidence stored on the video (`source_class`, `class_evidence`);
* never another creator's edited film (documentary / narration / analysis channels) and never a re-uploader — they stay research only
  even with a Creative Commons licence or a "raw" word in the title;
* the clip is cut to the sentence it illustrates: the existing segmenter cuts by meaning, the auditor checks the three-frame sheet and
  the words; a YouTube clip plays at most `youtube_clips.max_clip_seconds` (16 s) and all of them together at most `max_share` (8 %) of a film;
* a source record per clip (URL, channel, title, video date, class, evidence, hash, download time) is stored on the source and its pieces
  and exported in the film's sources CSV with the time range inside the source video;
* an on-screen credit "title / channel (YouTube)" while the clip is shown (`rights.credit_providers`);
* the original audio stays muted (footage is always muted) unless the beat is an original-speech moment (existing rule);
* downloads are stored once: the same bytes (sha256) in a case are not stored again.

## V wiring (2026-10-22): discovery into the director loop

Until now the clip route's parts existed unconnected: the Data API discovery
(`app/documentary/visuals/youtube_discovery.py`) was built but never called, so a case's
`video_sources` carried no real channel, no real API licence and no source class, and
`ingest_film_candidates` had nothing to download. Wired inside `VisualResearchAgent.run`
(only when the owner's switch is on and `TRUECRIME_YOUTUBE_API_KEY` is set): the case's
footage queries run `discover_for_case` — budgeted `search.list` inside the discovery share,
one batched `videos.list` for the real licence/channel, the existing `VideoUseClassifier`
decides, results land as `VideoSource` rows (deduped by video id), a film candidate's
transcript is fetched and stored (no Data API quota) so `align_sentence` can match a
sentence to the video's own words. The downloader, segmenter, video auditor, director and
clip caps are unchanged.

New guardrail: `witness_family` needs a raw marker in the title AND a channel that is not a
commentary outlet (`blocked_channel_markers` in `video_rights.json`): a creator reaction
titled "Bodycam" is research only, not a witness's own video.

Live case-9 proof (2026-10-22): 6 footage queries -> 59 videos classified -> 3 film
candidates (all `witness_family`, private channels) -> 3 downloaded -> 14 pieces -> the
auditor rejected 13 for a burned-in channel watermark ("TH ACTION" stamps every frame) and
kept 1 as needs_review. YouTube Data API: 12 units of the 2000-unit daily discovery share.

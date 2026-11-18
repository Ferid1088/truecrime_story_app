# Platform specs for reels, covers and videos

Read this before you make, change or publish a Short, a cover or a film. The values live in `config/platform_specs.json` (with their sources and how well each is known); this page explains them. Code: `app/publishing/platform_specs.py` (reads the file, checks a video file), `app/shortform/delivery.py` (the delivery copy of a clip), `app/shortform/clip_video.py` (`encode_args`, `loudness_filter`, `delivery_command`).

Last verified: 2026-11-17. The official pages were read that day; blogs were not trusted for numbers.

## How sure is each value?
| status | meaning | what to do |
|---|---|---|
| official | a platform's developer or help page | enforce it |
| measured | measured by this project on the real account or on our files | enforce it |
| third_party | blogs and vendor guides; they contradict each other | advice only, never state it as a fact |
| unresolved | nobody could confirm | test on the real account first |

## Hard rules (official)
- **Facebook Reels** (Meta's Reels publishing guide): 9:16, 1080x1920 (minimum 540x960), 24 to 60 fps and fixed, 3 to 90 s (a Page story at most 60 s), H.264 or H.265, 4:2:0, a closed GOP of 2 to 5 s, AAC-LC stereo, 48 kHz, at least 128 kbps. Instagram Reels uses the same guide.
- **TikTok** (Content Posting API): MP4, MOV or WebM, H.264 recommended, 23 to 60 fps, 360 to 4096 px, 3 minutes for every creator, 4 GB.
- **YouTube Shorts**: up to 3 minutes, at most 1080p. `thumbnails.set`: JPEG or PNG up to 50 MB, about 50 quota units, called only after processing has succeeded.

## What we measured on our own clips (case 15, 49 files)
The encoder had no GOP setting (x264 default: 250 frames = 8.3 s), the limiter sat at 0.95 so almost every clip peaked at or above 0 dBFS after the AAC encode, video ran at 1.7 to 2.8 Mbps, loudness varied from -12 to -15 LUFS.
Fixed in `short_clips.video.delivery` (config) and `clip_video.encode_args`: a 2 s closed GOP, constant quality up to a ceiling, -14 LUFS, a true-peak target of -2 dB (the AAC encode adds a little) and a ceiling of -1 dB for a finished file. Existing clips get a **delivery copy** (`delivery_<key>.mp4` beside the original); the original is never replaced. Publishing sends the delivery copy when it exists.

## Captions
Every Short shows **one word at a time**, from the moment the voice starts the word until it has said it (`clip_video.chunks`, timed by the stored word times of the voice). A clip built before that change shows 2-3 words at once; `clip_media.reassemble` builds it again from its stored voices, avatar videos, pictures and music (nothing is paid for or chosen again; the replaced file is kept in `previous_versions/`).

## Which cover goes where
Platforms take **one** cover per video.
- `reel` (9:16): the only upload candidate. Facebook Reels (thumbnails call; our token lacks `pages_read_user_content` and `pages_manage_engagement`), Instagram Reels, YouTube Shorts (`thumbnails.set`; eligibility unresolved). TikTok takes a frame of the video, so the frame that should act as its cover has to be in the video.
- `feed` (4:5) and `grid` (1:1): previews and feed-post pictures. No source says a platform takes them as a reel cover.
- Instagram's profile grid crop is **unresolved** (3:4, 4:5 and 1:1 are all claimed, none officially). Keep faces, titles and logos in the middle; the safe choice is a 1080x1080 centre square. Test the preview on the real account before enforcing anything.

## Cover as the first frame (measured 2026-11-17)
Facebook takes the first frame of a Reel as its preferred thumbnail (publication 284: the preferred one of 11 thumbnails was the cover). `delivery.ensure_delivery(original, force=True, cover=<reel cover>)` makes a delivery copy that opens with the cover for `short_clips.video.delivery.cover_lead_seconds` (1.5, silent) and then plays the clip; it passes `platform_specs.check_all`. The same file works on Instagram: its `thumb_offset` (config `instagram.thumb_offset_ms`, 0) takes the first frame, and the Reel's thumbnail on the real account was the cover. Instagram's own rules (no edit list, index at the front) are met by `encode_args`. It delays the hook by that time, so it is a stopgap until the thumbnails call works (token permissions).

## YouTube Shorts (measured 2026-11-18)
The cover of a Short is the approved reel variant (`reel:{id}` in `publication.cover_ref`, put on the video with `thumbnails.set` after processing). It worked on the real Persian channel. The link to the film sits in the description (`web_address_allowed` for `youtube/short` in `config/publishing.json`); the related-video field has no API and is set by hand in Studio. A published video's thumbnail can be replaced with `POST /api/youtube-publishing/publications/{id}/thumbnail`.

## The same flow for every language (2026-11-18)
What was done by hand for the Persian film and first Short is code now and works for any language that has a publishing schedule (`config/publishing.json` languages) and a connected channel:
1. **Covers:** an approved reel cover is the Short's cover and thumbnail (`reel:{id}`), an approved film cover the film's (`film:{id}`); feed and grid are previews only (`covers.variants.approved_cover`).
2. **Files:** `POST /api/cases/{case}/publishing/{language}/prepare-files` (or `shortform.delivery.prepare_shorts`) makes each Short's delivery copy, opening with its approved reel cover for `delivery.cover_lead_seconds`; a copy is made again only when its clip or its cover changed (`delivery_<key>.mp4.cover.json`). The publications then send those files.
3. **The film link:** when a language's film is uploaded to YouTube, the address of the film is put into the text of that language's YouTube Shorts that can still be edited (`PublicationRegistry.link_shorts_to_film`). `web_address_allowed` is on for `youtube/short` only.
4. **Public at once:** with `youtube_publishing.upload.public_after_processing` a video that was uploaded private, processed and given its thumbnail and subtitles is made public (`videos.update`, about 50 quota units); the film is then recorded as published. A refusal leaves it private with `required_action = make_public`. The Emergency Stop prevents it.

## Facebook films and the links in Reels (measured 2026-11-18)
- **Film upload:** the Page-video chunked upload (`upload_phase` start, transfer, finish on `graph-video.facebook.com` with the Page token, all fields in a multipart form). The app-uploads file-handle method was refused by Meta (error 6000, subcode 1363019) for the real film and even for a tiny one when its fields were in the address.
- **Film cover:** the `thumb` field of the finish call; Meta then holds it as the preferred thumbnail (checked on the real film and on a test video that was deleted). No extra token permission is needed.
- **Film addresses in Reels:** (owner rule 2026-11-18: the links are the first rows of the caption, nothing before them, YouTube link first and Facebook link second; `film_link.with_film_links` moves an address that stood further down) the address of a film goes into the text of the Shorts named in `config/publishing.json` `routing.film_address_in_text` (YouTube Shorts: the YouTube film; Facebook and Instagram Reels: both films), when the film is uploaded. A Reel that is public on Facebook is updated with `POST /{video}` (`description`), which Meta accepted; a public YouTube Short through `videos.update` with its own title, tags and category kept (`POST /api/youtube-publishing/cases/{case}/{language}/film-addresses`). Instagram's API cannot change a public Reel's caption (ig-media reference, read 2026-11-18): `POST /api/facebook-publishing/cases/{case}/{language}/film-addresses` returns the text to paste in the Instagram app.
- **Cover and comment need `pages_read_user_content` and `pages_manage_engagement`:** read the token's scopes with Meta's `debug_token` (GET; a system-user token's `/me/permissions` leaves them out), which is what `granted_permissions` does now. The Reel cover (`POST /{video}/thumbnails`) and a Page comment with the film addresses (`POST /{video}/comments`, `FacebookPublisher.add_film_comment`, once per address, also done when a Reel or the film is published) both worked on the real Page on 2026-11-18.
- **Instagram Reels:** the Reel API has no field for a link, a website or a call-to-action (ig-user/media reference, read 2026-11-18), so a link in a caption stays plain text for most accounts. The app adds a line that points to the link in the profile bio, in the language of the Reel (`routing.bio_link_note`, `routing.bio_link_platforms`), just before the music credit; the bio link itself is set by hand. A paid Meta plan is said to allow tappable links (third-party sources, no official page found).
- **Films first:** the plan puts both full films (YouTube and Facebook) on day 0 and the Shorts from day 1 on. A Short or Reel is scheduled, uploaded or published only after every film whose address is in its text is published with an address (`PublicationRegistry.film_blockers`, config `scheduling.film_before_shorts`); a language with no account on a film's platform does not wait for that film. A too-early upload is refused with reason `film_first` and the approval is left alone.
- **Not clickable:** third-party sources say Instagram shows caption links as plain text (Meta Verified and Meta One plans are said to change that; no official page was found). Whether Facebook makes a link in a Reel description tappable is unverified.

## Creative rules (blog advice, not platform policy)
Open with one concrete striking fact in the first 2 to 3 seconds; burn captions in; one case per video with a turn and an open question at the end; judge hooks by early retention in the platform's analytics. See `creative_rules` in the config.

## Open questions
Instagram grid crop; YouTube custom Shorts thumbnails on our channel; TikTok cover option; what the biggest true-crime channels really do in their first seconds (no source with real data was found).

## Rules for agents
1. Do not invent platform numbers. Take them from `config/platform_specs.json`; if one is missing or `unresolved`, say so and test on the real account.
2. A new clip is encoded with `clip_video.encode_args` and `loudness_filter`; never write your own ffmpeg codec arguments for a clip.
3. Publish `delivery_*.mp4` (or a newly assembled clip) and not an old original; run `platform_specs.check_video` on the file you send.
4. Keep this page and the config in step when a platform page changes; set `verified_on`.

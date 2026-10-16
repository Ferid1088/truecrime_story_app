# Platform export profiles (Part 10, pilot shorts)

Read on 2026-10-16 from the platforms' own documentation. Only what could actually be read is
stated as a limit; everything else is marked NOT VERIFIED and the app stays inside its own
stricter bounds instead of guessing.

## What was read

| Platform | Limit read | Source |
|---|---|---|
| YouTube Shorts | Shorts are "up to 3 minutes long" | https://support.google.com/youtube/answer/10059070 (YouTube Help, "Create YouTube Shorts") |
| TikTok | Upload by chunks: each chunk 5-64 MB (the final chunk up to 128 MB); a video under 5 MB is uploaded whole; over 64 MB needs several chunks | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide (Content Posting API, media transfer guide) |
| Instagram Reels | NOT VERIFIED: the official Instagram help and developer pages are rendered by JavaScript and returned no limit text to a plain fetch | https://help.instagram.com/1038071743007909, https://developers.facebook.com/docs/instagram-platform/content-publishing/ |
| Facebook Reels | NOT VERIFIED: same (page rendered by JavaScript; the business-help URL answered 404) | https://developers.facebook.com/docs/video-api/guides/reels-publishing/ |

Aspect ratio, caption safe areas and the Instagram / Facebook / TikTok duration caps could
not be read from official text in this session. They must be read from the platforms'
pages in a browser (or supplied by the owner) before any real publishing is built.

## What the app does meanwhile (stricter than anything read above)

Every short is 1080x1920 (9:16), H.264 MP4 with burned captions plus an SRT, RTL fonts for
fa and ar, and a duration inside the platform's own configured window in
`config/parameters/documentary.json` `short_form.distribution`:

| Platform key | min s | target s | max s |
|---|---|---|---|
| youtube_short | 20 | 35 | 60 |
| instagram_reel | 20 | 30 | 45 |
| facebook_reel | 25 | 35 | 60 |
| tiktok_video | 15 | 28 | 45 |

`short_form.hard_max_seconds` is 60, so no short exceeds 60 s on any platform; this is inside
YouTube's 3 minutes. The number of shorts per platform is `short_form.distribution.<platform>.count`
(a config value, not code); the pilot needs exactly 2 per platform, set when the pilot starts.
Gates before export: rights, reveal firewall, epistemic wording, disclosure, duration, and the
automation-feel check (`app/shortform/gates.py`).

## Publishing

The platform publishers are stubs (`PlatformPublisherStub`): nothing is published. The pilot
delivers exports and the owner's one-click approval screen, default visibility private. Real
publishing is built only when the owner supplies credentials and names the platform.

# Topical fill and the channel logo

## Topical fill (owner decision 2026-10-27)

A bare moment may show an approved picture or video that is **at least half related** to the words: the defendant while a
court decision is told, another court picture for a court, the house or the victim for a killing in a bed - never a police
car for a court, never a map for a bed. Related means the person at the centre of the words, the same kind of setting, or
the same object, place or event; it never means a picture that would suggest something the words do not say.

`mvp/topical.py` asks the text agent `mvp.topic_match` (prompt `topic_match_system.md`) for every bare moment, with the
approved pictures and videos as a catalog of what each shows (no image is sent; maps and clips cut for a moment are not
in it). Only scores of at least `main_visual_planner.topical.min_score` (0.5) are kept, best first, and remembered on the
needs plan until the catalog or the moment's words change. `direct.place_topical` places them after the clips cut for the
moment and before the last resort: a picture not yet shown comes before one that returns; a returning picture follows the
repetition rules (twice at most, 300 s apart); a hold never passes `max_still_seconds`; a long stretch is cut into pieces,
each its own picture. What nothing is related to stays bare and goes to the owner.

Only **approved** pictures and videos are used here; rejected ones stay out unless the owner approves them.

## Channel logo

`config/parameters/branding.json` + `render/logo.py`. One logo per language (the channel's `logo`, identity.json:
English, German, Persian, Arabic). It is drawn in the top corner at the end of the reading direction - top right for
English and German, top left for Persian and Arabic (mirrored like the graphics) - the opposite corner to the "illustration"
label and clear of subtitles, date/place and the map credit. It shows over pictures and clips only (graphics, maps, host
scenes, chapter and title cards have their own layouts; the channel intro is the logo), in stretches of at least 4 s, with
a 0.6 s fade. A logo file without transparency has a black background that becomes see-through; a faint dark plate keeps it
readable on bright pictures. The production script stores the windows (`script["logo"]["windows"]`); the renderer draws.

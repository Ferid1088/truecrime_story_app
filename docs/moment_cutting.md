# Moment cutting

The case's long videos are kept whole (a muted proxy) and were cut by meaning into pieces. Most pieces never get on
screen, because a piece is only chosen when the words over a slot happen to name what it shows. Moment cutting works the
other way round: from a moment of the film that nothing covers to the part of a video that shows what is said there.

`mvp/moments.py`, run inside shot direction (`mvp/pipeline.py::stage_direct`) when moments are still bare after the
pictures, maps, alternative graphics and the gap top-up. Config: `main_visual_planner.moments`; agents `mvp.moment_pick`,
`mvp.moment_cut`, `mvp.moment_check` (prompts in `prompts/documentary/mvp/moment_*_system.md`).

1. **Footage:** pieces of videos that have at least `trusted_min_approved` approved pieces (someone already judged the
   video to be about the case) and whose rights do not block them. Windows already on screen, or already cut for a
   moment, are never offered again.
2. **Pick (text, 8 moments per call):** which parts could be on screen while the narrator says it. An empty answer is
   normal and recorded.
3. **Cut (vision, one call per moment):** frames of the best parts with the scene changes found by code; the cutter returns
   exact windows. Code snaps them to a scene change within `footage.snap_seconds`, enforces
   `min/max_clip_seconds`, refuses used footage and never cuts more than the moment needs plus `margin_seconds`.
4. **Check (vision, independent):** start/middle/end frames of the stored cut against the words: fit (direct, supports,
   loose, no), misleading, clean start/end, unusable (title card, caption, watermark, presenter), sensitive.
5. **Record:** a requirement `MOM.<language>.<key>` (function `moment`, priority low, so it never counts as an unmet need)
   with one candidate per clip. `approved` clips are placed automatically; `needs_review` ones (generic footage,
   sensitive, unclean) wait in the owner's review under the moment's beat; `rejected` ones are kept for the record.
   Clips are never shared between languages (the words differ).

Direction places a clip only on the stretch whose beat and words it was cut for (`direct.place_moment_clips`), clips
follow each other when the moment is longer than one clip, and a rest shorter than half a second is the closing frame of
the last clip. Whatever stays bare goes on to the last resort (a related picture shown again) and then to the owner.

A moment is not paid for twice: its outcome (also "nothing honestly fits") is stored with a fingerprint of the case's
footage and retried only when new footage exists. Paid calls stop cleanly at the case's cost cap.

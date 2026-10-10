
You audit one VIDEO piece for a serious, factual true-crime documentary.
You get its frames IN ORDER with their timestamps (seconds in the whole
video). If given, the first frame is from just BEFORE the piece and the
last from just AFTER it (marked "context") — they show where the cut is.
A piece was cut and named by someone else; check THEIR work strictly.

1. The cut. Does the piece start where a meaningful moment starts and end
   where it ends? Wrong: a movement cut in half, a flash of another scene
   in its first or last second, starting or ending on black/a title, two
   unrelated situations in one piece. If wrong, give better start/end
   (seconds of the whole video, within "may_move_to").
2. The name and description. Do they say exactly what is VISIBLE — no
   invented identities, places or facts, nothing that is not there? If
   not, write the correct ones (name max 60 chars; description one or
   two neutral sentences: who/what, where, when, camera, mood).
3. The content, every frame: burned-in captions/logos/watermarks, gore,
   wrong period, sentimental/funny/stock/advertising tone, and whether it
   shows what it is claimed to show (matches_claim: stand_in only for the
   SAME SPECIFIC kind — a police dog only by a working police dog, never
   for a person).

Return JSON only:
{"cut_ok": true, "suggested_start": null, "suggested_end": null,
 "description_ok": true, "name": "...", "description": "...",
 "subject_type": "person|place|building|vehicle|object|document|map|landscape|event|other",
 "matches_claim": "yes|stand_in|no|unclear",
 "role": "evidence|context|illustration",
 "entities": [], "reveals": [], "period_ok": "yes|no|unclear",
 "text_or_logo_in_any_frame": false,
 "graphic_or_sensitive_in_any_frame": false,
 "tone_ok": true, "quality": 0.7, "confidence": 0.8,
 "problem_frames": [], "reasons": []}

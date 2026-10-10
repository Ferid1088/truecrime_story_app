
You cut a video into PIECES for a serious, factual true-crime documentary.
You get the frames of one stretch of the video IN ORDER with their
timestamps (seconds), and the times where the picture changes scene.

Cut by MEANING, never by the clock:
- A piece is one complete, meaningful moment: an action from its start to
  its end (a car arrives and stops; divers enter the water), one
  continuous view (an aerial pass over the town), one situation (officers
  searching a field). It starts where that moment starts and ends where
  it ends — never in the middle of a movement, never with a flash of the
  next scene.
- A piece is {min_s}–{max_s} seconds. A meaningful moment longer than
  {max_s} s becomes several pieces, each meaningful on its own; they may
  overlap a little so that each starts and ends well.
- Consecutive short shots of the same situation belong together in one
  piece. A scene change usually ends a piece.
- Leave out what is not usable: title cards, black frames, credits, test
  patterns, talking heads of presenters, frames with burned-in captions.
- name: what the piece shows, max 60 characters ("Divers enter the lake
  from the jetty"). description: one or two neutral sentences of exactly
  what is VISIBLE (who/what, where, when — period, day/night, season if
  visible, camera: aerial/street/interior/handheld, mood). Never guess
  identities or facts that cannot be seen.

Return JSON only:
{{"pieces": [{{"start": 12.0, "end": 24.5, "name": "...", "description": "...",
  "why_here": "where the moment starts and ends"}}]}}

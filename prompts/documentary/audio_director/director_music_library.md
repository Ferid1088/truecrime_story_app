# ADDED: music library
You also receive MUSIC LIBRARY: tracks that already exist, each with a situation text and tags that say when it fits. For every moment that needs music, first look for a track whose situation and tags fit the moment's mood, role (under narration, between acts, tension, reveal, closing) and intensity. Choose it by its track_code. Choose a track only if the fit is real, not because it is available. If no track fits, do not force one: return library_miss with the mood, kind and a short situation text that a new track would need. Never invent a track_code that is not in the list. Do not choose the same track for neighbouring moments unless the story needs continuity.

In "music_library" every track is {"code", "kind", "mood", "situation", "tags", "sec"}; kind bridge serves music_bridge, emotional_moment and chapter_break, kind sting serves sting. For each music moment (music_bridge, emotional_moment, sting, chapter_break) add to its "after" object EITHER
  "track_code": "<a code from the list>", "track_reason": "<one line: why this track fits this moment>"
OR
  "library_miss": {"kind": "bridge", "mood": "<catalogue mood>", "situation": "<the situation a new track would need>", "tags": ["<tag>", "<tag>"]}
Add "continuity": true next to track_code only when the story needs the same track as the previous music moment.

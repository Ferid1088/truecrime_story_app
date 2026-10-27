You cut footage for ONE moment of a serious, factual true-crime documentary.

WHAT YOU RECEIVE
A JSON object with:
- said: what the narrator says during the moment; seconds: how long the moment lasts.
- clip_seconds: the length of the clip(s) to cut: at least min, at most max, ideally the length of the moment plus
  a short margin. If the moment is longer than one clip, you may cut several clips that follow each other.
- regions: [{id, video, start, end, name, description, frame_times, scene_changes}] - candidate footage. The images
  that follow are the frames of each region, in order, in the same order as the regions, with frame_times in
  seconds of the whole video; scene_changes are the seconds where the picture changes scene (found by code).

YOUR TASK
Find the exact windows in these regions that show what is said, and cut them cleanly:
- Start and end a clip at a scene change (scene_changes) whenever one is near. Inside a continuous shot (most
  bodycam, dashcam and handheld footage has none) cut at a calm moment: not during a sudden movement, a blink of the
  picture or a jump. Never include a flash of another shot and never cross a scene change.
- A clip contains no title card, black frame, credits, burned-in caption, watermark or presenter talking to the camera.
- A clip lies inside one region. Its start and end are seconds of the whole video, between the region's start and end.
- Length: min <= end - start <= max. If the best window is shorter than min, return nothing for it.
- fit: "direct" when the clip shows what is said; "supports" when it is the right kind of footage for the words
  (same place type, action or situation) without showing the exact event; "loose" when it is only generic
  footage of the right atmosphere. Never claim more than the frames show.
- name: what the clip shows, max 60 characters. description: one or two neutral sentences of exactly what is visible.
  Never name a person or place that cannot be seen or read in the frames.
- If nothing in these regions honestly fits, return {"clips": []}. That is a correct answer.

Return JSON only:
{"clips": [{"region": "<region id>", "start": 12.0, "end": 21.5, "fit": "direct|supports|loose",
  "name": "...", "description": "...", "why": "where it starts and ends, and how it fits the words"}]}

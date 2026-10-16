# Host direction: capability matrix (Part 7a)

Source of the vocabulary: `docs/MASTER PROMPT — FEREIDOUN AVATAR DIRECTOR.txt` (kept untouched).
Project decisions that win over it: no walking; wardrobe = the app's own outfits
(`app/outfits/registry.py` ids, not W01-W08); studio ids = the registry's
`studio_profile_id`; the host is never cut out and pasted on a studio, the app creates a
picture of him inside the locked studio master and HeyGen animates the whole picture.

## Evidence (read 2026-10-16, not assumed)

- **HeyGen create-video docs** (https://docs.heygen.com/reference/create-video): `POST /v3/videos`
  takes `motion_prompt` ("natural-language prompt controlling avatar body motion and hand
  gestures", supported for photo avatars on either engine) and `expressiveness`
  (high | medium | low, photo avatars on Avatar IV only; rejected on Avatar V; default low).
  There is no field for camera, framing, posture id, gesture id, wardrobe or lighting.
- **HeyGen Photo to Avatar docs**: `POST /v3/avatars` with `type: "photo"` and an
  `avatar_group_id` adds a picture as another look of an existing character; the response's
  `avatar_item.id` is the look id (`avatar_id` for videos); poll `GET /v3/avatars/looks/{id}`
  until `completed`.
- **Real API response** (`GET /v3/avatars?limit=1`, `GET /v3/avatars/looks?limit=2`): the account
  has one character group with 7 looks, all `avatar_type: photo_avatar`,
  `supported_api_engines: [avatar_v, avatar_iv, avatar_iii]`; a look object carries no motion,
  gesture or camera fields.
- **Gateway** (`GET /v1/models`): image models with the `image-generation` endpoint type exist
  (`gpt-image-2`, `gemini-3.1-flash-image`, ...). The edit endpoint's real response shape is
  NOT yet verified (the first real look generation does that); the provider reads `b64_json`
  or `url`.

## Matrix

`Implemented` says whether the code exists today; a field that is not implemented is
PLANNED_ONLY in practice, whatever its target class, and the UI must show it so.

| Director field | Class | How | Implemented |
|---|---|---|---|
| studio id | EXECUTED_BY_LOOK_CHOICE | the studio is inside every look's picture (locked master) | yes (registry), looks pending the master pictures |
| camera CAM01-CAM06 | EXECUTED_BY_LOOK_CHOICE | only cameras with an APPROVED look exist (`looks.approved_looks`) | yes |
| framing WIDE | EXECUTED_BY_LOOK_CHOICE | the look's own picture | yes |
| framing MEDIUM / MEDIUM_CLOSE / CLOSE | EXECUTED_IN_OUR_RENDERER | crops of an approved front or wide look (`HOST_*` presets) | crop presets exist; per-shot crop from the direction not wired |
| shot changes inside a segment | PLANNED_ONLY | needs two approved looks and one provider job per shot (7e) | no |
| camera movement push-in / pull-back | EXECUTED_IN_OUR_RENDERER | animated scale, subtle | no (PLANNED_ONLY today) |
| posture P01-P05 | EXECUTED_BY_LOOK_CHOICE (the seated/standing pose of the look) + EXECUTED_BY_PROVIDER (a phrase in `motion_prompt`, best effort) | look choice is exact, the prompt is not | prompt: yes; look pose: via look library |
| gestures G01-G07 | EXECUTED_BY_PROVIDER | natural-language `motion_prompt` built from the direction (`host_scenes.direction_controls`); best effort, not frame-exact | yes |
| eye contact | EXECUTED_BY_PROVIDER (words in `motion_prompt`, best effort) | no dedicated control exists | yes (best effort) |
| emotion (label) | PLANNED_ONLY | recorded in the direction and visual memory | recorded only |
| emotion intensity | EXECUTED_BY_PROVIDER | coarse `expressiveness` low / medium / high (Avatar IV, photo avatars) | yes |
| performance timeline (per-second actions) | PLANNED_ONLY | the provider takes one prompt per video, not a timeline | recorded only |
| wardrobe (outfit id) | EXECUTED_BY_LOOK_CHOICE | the outfit is in the look's picture | via look library |
| lighting variant L01-L06 | EXECUTED_IN_OUR_RENDERER | a gentle colour grade, subtle | no (PLANNED_ONLY today) |
| memory reference | recorded | `host_appearances`, host memories | yes |
| walking | not used | project decision | n/a |

The audio is the source of truth: the direction never changes wording, pronunciation, pauses,
timing or language (test: the voice sha256 and seconds are unchanged after a direction is
recorded).

## Owner gates

- The owner approves each look picture before it is uploaded to HeyGen (`looks.approve`;
  `require_approved_for_upload` refuses otherwise).
- The owner chooses which outfits may appear per channel (`stage_direction.outfit_allow_list`;
  a language without an entry may use every outfit).
- Nothing is generated in bulk: looks are requested one at a time; start with ONE default look
  per channel (seated, front, one outfit).

## Blockers found

- `data/studio/` is empty: the locked studio master pictures are not on this machine, so no
  look can be generated yet (`POST /api/host-looks/{id}/generate` answers 409 saying so).

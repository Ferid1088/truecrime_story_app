# The opening block (the film's first seconds)

Owner decisions 2026-11-11. For films made from now on; films that are already made are not changed.

## What the viewer gets

1. **Hook**: one statement (a quote, a concrete moment or a question), spoken alone. No logo, no greeting.
2. **Pause** (`opening_block.hook_pause_seconds`): the music begins directly after the hook.
3. **Context text**: what is strange about the case, in new words (never the narration that follows), with the music under it.
4. **Dark, silent gap** (`opening_block.gap_seconds`, 2 s): black screen, no voice, no music.
5. The host's opening (S1, 10 to 20 s plus the identity line), then the channel logo, then the story.

## Who makes it

| Step | Where | Model role |
|---|---|---|
| English opening: weighs `hook_candidates` hooks, picks one, writes the context text, briefs the music | `app/agents/opening.py` `create_master` | `opening_director` |
| Every other language: writes the block again in the language from the English opening and the language's own first act (not a translation) | `write_native` | `opening_native_writer` |
| Checks (greeting, digits, details of later acts, copied narration, lengths scaled to the language) | `app/documentary/opening_block.py` | none |
| Voice (the language's own settings, local speed x`post_speed_factor`, stability lower by `stability_drop` from `stability_threshold`), the film's music (ElevenLabs sound generation, once per film, booked in the ledger), the block's audio | `app/documentary/opening_media.py` | none |
| Stage in the language's chain, before the host's text (`opening:<lang>`) | `app/documentary/pipeline/opening.py` | none |
| The block in the film: first in the timeline, everything else moves by its length, the audio has it in front | `app/documentary/production/opening_timeline.py`, called by `build_production_script` | none |

The English opening is made once for the film, however many language chains ask for it at the same time. A block whose checks failed (`status` `needs_review`) is not put into a film; the stage says so.

## What it may say

Only what the first `knowledge_acts` acts of the film have told, plus the story's riddles (the host's firewall, `host_firewall.reveal_issues`). The host's writer is told what the block said (`said_before`), so S1 never repeats it.

## Pictures

Photos of the film's own first act: the picture shots that start `pictures.from_seconds` or later after the story begins, one shot for each different picture. A film without such a photo shows black under the speech. No portrait drawing is laid over the block.

## Checks that know about the gap

The gap is marked `intentional_gap` in the script: it is no black stretch in the coverage check, not part of the duration it measures, not reported by the preflight, and its seconds are taken off the black frames the rendered file is allowed (`opening_gap_seconds` in the coverage report).

## Not built yet (known)

- No subtitles for the block's text.
- No separate critic model for the opening (only the checks above).
- The pictures are chosen by rule from the film's script, not by the director.
- Retention measurement at 5 and 30 seconds after publishing.

"""The short clips as the rest of the application reads them: the newest version of a key counts, a clip's parts start at their segment's second, and the duration is an estimate until the voice exists."""

import json

from test_host import _plan_story

from app.db.models import ShortClip
from app.shortform import clips as C


def test_the_newest_version_of_a_clip_is_the_one_that_counts(db_session):
    case, master, _ = _plan_story(db_session)
    for version, status in ((1, "valid"), (2, "needs_review")):
        db_session.add(ShortClip(case_id=case.id, language="es", clip_key="C1", kind="master", story_version_id=master.id, version=version, status=status, script_json="{}"))
    db_session.commit()
    assert C.clips_of(db_session, case.id, "es") == [] and [r.version for r in C.clips_of(db_session, case.id, "es", status=None)] == [2]


def test_the_parts_of_a_clip_start_at_the_second_of_their_segment_and_the_segments_make_sixteen_seconds():
    assert C.seconds_of({}) == {"hook": 0.0, "context": 4.0, "detail": 6.0, "reaction": 12.0}


def test_the_estimate_follows_the_words_and_the_language_and_the_measured_length_wins_once_the_voice_exists():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    assert C.narration_seconds(text, "en") > C.narration_seconds(" ".join(text.split()[:10]), "en") > 0
    row = ShortClip(case_id=1, language="en", clip_key="C1", script_json=json.dumps({"narration": text}), media_json="{}")
    assert C.duration_of(row) == {"seconds": C.narration_seconds(text, "en"), "kind": "estimate"}
    row.media_json = json.dumps({"voice_seconds": 16.4})
    assert C.duration_of(row) == {"seconds": 16.4, "kind": "measured"}
    assert C.with_estimate({"narration": text}, "en")["selection"]["duration"]["kind"] == "estimate"

"""The short clips of a film: the checks of a script (four parts, about 16 seconds, one idea, nothing of the story's answer) and the two agents with a scripted model."""


from test_host import _plan_story

from app.db.models import ShortClip
from app.shortform import clips as C

WORDS = ["door", "morning", "house", "letter", "neighbour", "phone", "kitchen", "statement", "detail", "question", "street", "evening", "window", "answer", "silence", "family", "hour", "moment", "report", "note", "road", "garden", "name", "record", "witness"]


def said(n: int, seed: int = 0) -> str:
    """n plain words as short sentences of four words (none of them a name of the test story)."""
    words = [WORDS[(seed + i * 3) % len(WORDS)] for i in range(n)]
    return " ".join(" ".join(words[i:i + 4]) + "." for i in range(0, n, 4)).replace(". ", ". ")


def clip(key="C1", angle="question", seed=0, **over) -> dict:
    base = {"selection": {"considered": ["a", "b", "c"], "hook_span": "the hook", "tease_question": "why?", "fact_refs": ["F001"]}, "clip_key": key, "angle": angle, "idea": "one idea", "hook": " ".join(WORDS[(seed + i * 5) % len(WORDS)] for i in range(5)), "context": said(5, seed), "detail": said(16, seed),
            "reaction": said(4, seed).rstrip(".") + ", would you stop?", "cta": "Watch the full story now.", "evidence_ids": ["F001"], "act_ids": ["act1"]}
    return {**base, **over}


def codes(issues):
    return {i["type"] for i in issues}


def test_a_good_clip_has_no_issue():
    assert C.clip_issues(clip(), "en") == []


def test_the_parts_have_the_words_of_their_seconds_in_the_language():
    assert C.words_for("detail", "en") == (12, 21) and C.words_for("detail", "tr")[1] < C.words_for("detail", "en")[1]
    assert C.seconds_of(clip()) == {"hook": 0.0, "context": 4.0, "detail": 6.0, "reaction": 12.0}


def test_a_clip_with_a_greeting_a_late_detail_digits_and_no_question_is_refused():
    bad = clip(hook="Hello and welcome back to the channel", detail=said(16).rstrip(".") + ". The lock was changed in 1998.",
               reaction=said(4).rstrip("."), cta="Watch it at https://example.com now.")
    found = codes(C.clip_issues(bad, "en"))
    assert {"greeting", "numbers_as_digits", "reaction_no_question", "cta"} <= found


def test_the_lengths_the_angle_and_a_hook_of_two_sentences_are_checked():
    found = codes(C.clip_issues(clip(angle="nonsense", detail=said(8), hook="One thing. Another thing. A third thing."), "en"))
    assert {"unknown_angle", "detail_length", "hook_not_one_statement"} <= found


def test_a_set_has_three_to_five_clips_each_with_its_own_angle_and_key():
    assert codes(C.set_issues([clip("C1"), clip("C2", "evidence")])) == {"clip_count"}
    assert {"angle_twice", "key_twice"} <= codes(C.set_issues([clip("C1"), clip("C1"), clip("C3")]))
    assert C.set_issues([clip("C1"), clip("C2", "evidence"), clip("C3", "contradiction")]) == []


def test_the_newest_version_of_a_clip_is_the_one_that_counts(db_session):
    case, master, _ = _plan_story(db_session)
    for version, status in ((1, "valid"), (2, "needs_review")):
        db_session.add(ShortClip(case_id=case.id, language="es", clip_key="C1", kind="master", story_version_id=master.id, version=version, status=status, script_json="{}"))
    db_session.commit()
    assert C.clips_of(db_session, case.id, "es") == [] and [r.version for r in C.clips_of(db_session, case.id, "es", status=None)] == [2]


def test_a_clip_that_speaks_to_the_viewer_formally_is_refused():
    formal = clip(reaction="Das ist nur seine Version. Worauf achten Sie zuerst?", cta="Sehen Sie die ganze Geschichte.")
    assert "formal_address" in codes(C.clip_issues(formal, "de"))
    assert "formal_address" not in codes(C.clip_issues(clip(), "en"))


def test_a_hook_of_three_words_is_too_short_in_persian_and_english_but_not_in_slow_turkish():
    assert C.words_for("hook", "fa")[0] == 4 and C.words_for("hook", "en")[0] == 4 and C.words_for("hook", "tr")[0] == 3
    short = clip(hook="سگ‌ها بیدارم کردن")
    assert "hook_length" in codes(C.clip_issues(short, "fa"))
    assert "hook_length" not in codes(C.clip_issues(clip(), "en"))


def test_a_latin_word_in_a_persian_or_arabic_clip_is_refused_but_not_in_english_or_in_a_tag():
    latin = clip(detail=said(16).replace("door", "sage", 1))
    assert "foreign_script" in codes(C.clip_issues(latin, "fa")) and "foreign_script" in codes(C.clip_issues(latin, "ar"))
    assert "foreign_script" not in codes(C.clip_issues(latin, "en"))
    assert not C.latin_left("[calm] سلام", "fa")

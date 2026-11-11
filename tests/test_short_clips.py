"""The short clips of a film: the checks of a script (four parts, about 25 seconds, one idea, nothing of the story's answer) and the two agents with a scripted model."""

import asyncio
import json

from test_host import _plan_story, _version, story

from app.core.ai_config import ai_config
from app.db.models import ShortClip
from app.providers.generation.base import GenerationResult
from app.shortform import clip_agents as CA
from app.shortform import clips as C

WORDS = ["door", "morning", "house", "letter", "neighbour", "phone", "kitchen", "statement", "detail", "question", "street", "evening", "window", "answer", "silence", "family", "hour", "moment", "report", "note", "road", "garden", "name", "record", "witness"]


def said(n: int, seed: int = 0) -> str:
    """n plain words as short sentences of four words (none of them a name of the test story)."""
    words = [WORDS[(seed + i * 3) % len(WORDS)] for i in range(n)]
    return " ".join(" ".join(words[i:i + 4]) + "." for i in range(0, n, 4)).replace(". ", ". ")


def clip(key="C1", angle="question", seed=0, **over) -> dict:
    base = {"clip_key": key, "angle": angle, "idea": "one idea", "hook": " ".join(WORDS[(seed + i * 5) % len(WORDS)] for i in range(5)), "context": said(8, seed), "detail": said(34, seed),
            "reaction": said(8, seed).rstrip(".") + ", would you stop there?", "cta": "Watch the full story on the channel.", "evidence_ids": ["F001"], "act_ids": ["act1"]}
    return {**base, **over}


def codes(issues):
    return {i["type"] for i in issues}


def test_the_knowledge_of_a_clip_ends_before_the_act_that_answers():
    s = story()
    assert [a.id for a in s.acts if a.payoff] == ["act4"] and C.knowledge_cutoff(s) == 3


def test_a_good_clip_has_no_issue():
    assert C.clip_issues(clip(), story(), "en") == []


def test_the_parts_have_the_words_of_their_seconds_in_the_language():
    assert C.words_for("detail", "en") == (26, 44) and C.words_for("detail", "tr")[1] < C.words_for("detail", "en")[1]
    assert C.seconds_of(clip()) == {"hook": 0.0, "context": 2.0, "detail": 5.0, "reaction": 18.0}


def test_a_clip_with_a_greeting_a_late_detail_digits_and_no_question_is_refused():
    bad = clip(hook="Hello and welcome back to the channel", detail=said(34).rstrip(".") + ". Wexford changed the lock in 1998.",
               reaction=said(8).rstrip("."), cta="Watch it at https://example.com now.")
    found = codes(C.clip_issues(bad, story(), "en"))
    assert {"greeting", "tells_what_comes_later", "numbers_as_digits", "reaction_no_question", "cta"} <= found


def test_the_lengths_the_angle_and_a_hook_of_two_sentences_are_checked():
    found = codes(C.clip_issues(clip(angle="nonsense", detail=said(8), hook="One thing. Another thing."), story(), "en"))
    assert {"unknown_angle", "detail_length", "hook_not_one_statement"} <= found


def test_a_set_has_three_to_five_clips_each_with_its_own_angle_and_key():
    assert codes(C.set_issues([clip("C1"), clip("C2", "evidence")])) == {"clip_count"}
    assert {"angle_twice", "key_twice"} <= codes(C.set_issues([clip("C1"), clip("C1"), clip("C3")]))
    assert C.set_issues([clip("C1"), clip("C2", "evidence"), clip("C3", "contradiction")]) == []


class ClipGen:
    def __init__(self, first_hook=None):
        self.first_hook, self.calls = first_hook, []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        res = GenerationResult(text="{}", model=f"m/{role}", provider="fake")
        if role == "short_clip_director":
            clips = [clip(f"C{i + 1}", a, seed=i) for i, a in enumerate(ai_config.short_clips_settings().angles)]
            if self.first_hook and "problems_to_fix" not in data:
                clips[0]["hook"] = self.first_hook
            return {"clips": clips}, res
        return {"clips": [{**clip(c["clip_key"], c["angle"], seed=9)} for c in data["english_clips"]]}, res


def test_the_director_plans_five_clips_and_asks_again_for_the_one_the_checks_refuse(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = ClipGen(first_hook="Hello and welcome to the channel")
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    rows = asyncio.run(CA.ClipPlanner().plan_english(db_session, case, master))
    assert [r.clip_key for r in rows] == ["C1", "C2", "C3", "C4", "C5"] and {r.status for r in rows} == {"valid"} and {r.kind for r in rows} == {"master"}
    first, second = gen.calls[0][1], gen.calls[1][1]
    assert "problems_to_fix" in second and list(second["problems_to_fix"]) == ["C1"]
    assert [a["act"] for a in first["acts_you_may_use"]] == ["act1", "act2", "act3"] and first["answer_acts"] == ["Her Account", "The Inquest", "What Remains"]
    assert all("Wexford" not in json.dumps(v) for k, v in first.items() if k != "answer_acts"), "nothing of the answering acts goes to the director"


def test_a_clip_that_stays_refused_is_stored_for_review(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = ClipGen(first_hook="Hello and welcome to the channel")

    async def stubborn(role, system, user, images=None):
        out, res = await ClipGen.generate_structured(gen, role, system, user)
        out["clips"][0]["hook"] = "Hello and welcome to the channel"
        return out, res

    gen.generate_structured = stubborn
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    rows = asyncio.run(CA.ClipPlanner().plan_english(db_session, case, master))
    assert [r.status for r in rows] == ["needs_review", "valid", "valid", "valid", "valid"]
    assert json.loads(rows[0].validation_json)["repair_iterations"] == ai_config.short_clips_settings().repairs


def test_a_native_writer_writes_the_clips_again_with_the_idea_and_angle_of_the_english_ones(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = ClipGen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    planner = CA.ClipPlanner()
    english = asyncio.run(planner.plan_english(db_session, case, master))
    de = _version(db_session, case, master, "en")
    de.language = "de"
    db_session.commit()
    native = asyncio.run(planner.write_native(db_session, case, de, english))
    role, payload = gen.calls[-1]
    assert role == "short_clip_native_writer" and payload["language"] == "de" and [c["clip_key"] for c in payload["english_clips"]] == ["C1", "C2", "C3", "C4", "C5"]
    assert payload["limits"]["words"]["detail"] == list(C.words_for("detail", "de"))
    assert [r.kind for r in native] == ["native"] * 5 and [r.master_clip_id for r in native] == [m.id for m in english]
    assert [json.loads(r.script_json)["angle"] for r in native] == ai_config.short_clips_settings().angles
    assert [r.clip_key for r in C.clips_of(db_session, case.id, "de")] == ["C1", "C2", "C3", "C4", "C5"]


def test_the_newest_version_of_a_clip_is_the_one_that_counts(db_session):
    case, master, _ = _plan_story(db_session)
    for version, status in ((1, "valid"), (2, "needs_review")):
        db_session.add(ShortClip(case_id=case.id, language="es", clip_key="C1", kind="master", story_version_id=master.id, version=version, status=status, script_json="{}"))
    db_session.commit()
    assert C.clips_of(db_session, case.id, "es") == [] and [r.version for r in C.clips_of(db_session, case.id, "es", status=None)] == [2]


def test_a_clip_that_speaks_to_the_viewer_formally_is_refused():
    formal = clip(reaction="Das ist nur seine Version. Worauf achten Sie zuerst?", cta="Sehen Sie die ganze Geschichte.")
    assert "formal_address" in codes(C.clip_issues(formal, story("de"), "de"))
    assert "formal_address" not in codes(C.clip_issues(clip(), story(), "en"))

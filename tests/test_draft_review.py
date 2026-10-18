# ruff: noqa: F811  (`setup` is the flow tests' fixture, requested by setup)
"""The draft's editorial review: the English inspector reads the shared draft once, before any language
is told from it; its items (without length and marker findings) go to the English rewrite only. A report:
it changes no text and a failure does not stop the run. And the prompts that carry the style examples and
the checks of how a story is told."""

import asyncio
import json

import pytest
from test_language_pipeline_flow import FakeGen, _pipeline, setup  # noqa: F401  (the flow tests' fixture)

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import StoryVersion
from app.language_pipeline import calibration
from app.language_pipeline.film_schema import FILM_SCORES
from app.language_pipeline.lengths import draft_range, final_range
from app.language_pipeline.store import DRAFT

DRAFT_ITEMS = [
    {"id": 1, "severity": "medium", "kind": "engagement", "act": "act1", "quote": "dword",
     "problem": "Every paragraph closes on what is unknown.", "instruction": "Vary the paragraph endings."},
    {"id": 2, "severity": "high", "kind": "production", "act": "whole_script", "quote": "",
     "problem": "Longer than the allowed range.", "instruction": "Cut."},
]


class ReviewGen(FakeGen):
    """The inspector's answer on the draft (its text is the draft writer's "dword" words) is DRAFT_ITEMS;
    every other inspection is the flow tests' answer."""

    def __init__(self, fail_review=False):
        super().__init__()
        self.fail_review = fail_review

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        task = json.loads(user)
        if role == "language_inspector" and "dword" in task.get("story", ""):
            self.calls.append({"role": role, "system": system, "user": task, "prefix": prefix,
                               "cache_key": cache_key, "draft_review": True})
            if self.fail_review:
                raise RuntimeError("provider down")
            return ({"verdict": "revise", "scores": {k: 70 for k in FILM_SCORES}, "summary": "draft read",
                     "items": DRAFT_ITEMS}, self._result("{}", role))
        return await super().generate_structured(role, system, user, images, prefix=prefix, cache_key=cache_key)


@pytest.fixture
def review_on(setup, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.draft_review, "enabled", True)
    return setup


def _run(gen, case, languages=("en", "fa")):
    return asyncio.run(_pipeline(gen).run_case(case.id, list(languages), 60))


def _draft(db_session, out):
    db_session.expire_all()
    return db_session.get(StoryVersion, out["draft_version_id"])


def _reviews(gen):
    return [i for i, c in enumerate(gen.calls) if c.get("draft_review")]


def _rewrite(gen, language):
    return next((i, c) for i, c in enumerate(gen.calls)
                if c["role"] == "language_rewrite_writer" and c["user"]["language"] == language)


def test_the_draft_is_reviewed_once_before_the_english_rewrite_which_alone_gets_its_items(review_on, db_session):
    gen = ReviewGen()
    out = _run(gen, review_on)
    assert [lg["status"] for lg in out["languages"]] == ["done", "done"]
    reviews = _reviews(gen)
    en_at, en_call = _rewrite(gen, "en")
    _, fa_call = _rewrite(gen, "fa")
    assert len(reviews) == 1 and reviews[0] < en_at
    review = gen.calls[reviews[0]]
    assert review["user"]["language"] == "en" and review["system"].startswith("You are an English documentary editor")
    assert review["user"]["story"].startswith("[[ACT:act1]]")
    # the length finding is left out: the draft is written long on purpose
    assert en_call["user"]["draft_review"] == [DRAFT_ITEMS[0]]
    assert list(en_call["user"])[-1] == "draft_review"            # after the shared, cached keys
    assert "draft_review" not in fa_call["user"]                   # told from the English final, not the draft
    stored = json.loads(_draft(db_session, out).critic_notes)["draft_review"]
    assert stored["status"] == "ok" and stored["report_only"] is True and stored["items"] == [DRAFT_ITEMS[0]]


def test_the_review_changes_no_text_and_a_rerun_does_not_review_the_draft_again(review_on, db_session):
    gen = ReviewGen()
    out = _run(gen, review_on, ["en"])
    text = _draft(db_session, out).story_text
    again = ReviewGen()
    out2 = _run(again, review_on, ["en"])
    assert out2["draft_version_id"] == out["draft_version_id"] and _draft(db_session, out2).story_text == text
    assert len(_reviews(gen)) == 1 and _reviews(again) == []


def test_a_failed_review_is_recorded_and_the_run_goes_on_without_it(review_on, db_session):
    gen = ReviewGen(fail_review=True)
    out = _run(gen, review_on, ["en"])
    assert out["languages"][0]["status"] == "done"
    stored = json.loads(_draft(db_session, out).critic_notes)["draft_review"]
    assert stored["status"] == "failed" and "provider down" in stored["error"]
    assert "draft_review" not in _rewrite(gen, "en")[1]["user"]


def test_with_the_review_off_there_is_no_call_and_no_input(setup):
    gen = ReviewGen()
    _run(gen, setup, ["en"])
    assert _reviews(gen) == [] and "draft_review" not in _rewrite(gen, "en")[1]["user"]


def test_the_ledger_line_of_the_review_is_its_own_shared_stage_not_the_draft():
    assert calibration._stage("language_draft_review:en") == "draft_review"
    assert calibration._stage("language_draft") == "draft"


def test_the_review_is_on_in_the_configuration():
    assert ai_config.language_pipeline.draft_review.enabled is True
    assert DRAFT == "draft"


# ----------------------------------------------------------------------------- the prompts
FICTIONAL = "Martin Hale"


def test_the_writers_that_tell_the_story_carry_the_style_examples_under_their_hard_limits():
    draft = prompt("language_pipeline/draft_writer")
    assert FICTIONAL in draft and draft.index("STYLE EXAMPLES") < draft.index("FACTUAL DISCIPLINE")
    for name in ("en", "fa", "de", "ar"):
        text = prompt(f"language_pipeline/{name}/rewrite_writer")
        assert FICTIONAL in text and "WRITING RULE" in text
        assert text.index("STYLE EXAMPLES") < text.index("{style_guide}") and "HARD LIMITS (they outrank style" in text, name
    for name, language in (("fa", "Persian"), ("de", "German"), ("ar", "Arabic")):
        assert f"Write in natural {language}; never translate or reuse their sentences" in prompt(
            f"language_pipeline/{name}/rewrite_writer")


def test_the_english_rewrite_takes_the_draft_review_as_advice_never_as_content():
    text = prompt("language_pipeline/en/rewrite_writer")
    assert "draft_review (when supplied" in text and "THE DRAFT'S EDITORIAL REVIEW" in text
    assert "never a source of content" in text
    for name in ("fa", "de", "ar"):
        assert "draft_review" not in prompt(f"language_pipeline/{name}/rewrite_writer")


def test_every_inspector_checks_how_the_story_is_told_and_the_audit_checks_invented_atmosphere():
    for name in ("en", "fa", "de", "ar"):
        text = prompt(f"language_pipeline/{name}/inspector")
        assert "paragraph endings that all follow one pattern" in text, name
        assert "routinely close on what remains unknown or unproven" in text, name
    assert "atmospheric detail (weather, light, sound, mood)" in prompt("language_pipeline/audit/evidence_audit")


def test_the_rewriters_are_not_taught_to_be_short_by_the_examples():
    for name in ("en", "fa", "de", "ar"):
        text = prompt(f"language_pipeline/{name}/rewrite_writer")
        assert "they are not a model of length" in text and "EXAMPLE 6" in text, name
        assert "never drop supported material only to be shorter" in text, name
        assert "move quickly through the rest" not in text, name
    assert "never justifies falling below an act's word target" in prompt("language_pipeline/en/rewrite_writer")


def test_the_review_is_held_to_the_drafts_own_range_never_the_final_texts(review_on):
    gen = ReviewGen()
    _run(gen, review_on, ["en"])
    review = gen.calls[_reviews(gen)[0]]["user"]
    span = draft_range(60)
    assert review["allowed_word_range"] == {"min": span.words_min, "max": span.words_max}
    assert span.words_min == final_range("en", 60).words_max            # from the longest final it feeds


def test_a_review_made_with_another_inspector_prompt_or_range_is_made_again(review_on, db_session):
    out = _run(ReviewGen(), review_on, ["en"])
    draft = _draft(db_session, out)
    notes = json.loads(draft.critic_notes)
    notes["draft_review"]["prompt_sha256"] = "an earlier inspector prompt"
    draft.critic_notes = json.dumps(notes)
    db_session.commit()
    again = ReviewGen()
    _run(again, review_on, ["en"])
    assert len(_reviews(again)) == 1
    stored = json.loads(_draft(db_session, out).critic_notes)["draft_review"]
    assert stored["prompt_sha256"] != "an earlier inspector prompt" and stored["status"] == "ok"


def test_no_inspector_cuts_a_passage_only_to_reach_a_word_count():
    for name in ("en", "fa", "de", "ar"):
        assert "Never recommend removing or compressing a passage to reach a word count" in prompt(
            f"language_pipeline/{name}/inspector"), name

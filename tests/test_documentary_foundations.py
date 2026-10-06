"""Foundations for the documentary engine: stable evidence meaning, act
structure that survives every edit step and every language, pilot-length
stories, and reviewer independence.

All generation calls are scripted — no real credits are spent.
"""
import asyncio
import json
import re

import pytest

from app.core.ai_config import ai_config
from app.providers.generation.base import GenerationResult

from test_master_pipeline import ScriptGen, _add_fact, _mk_case, _pipeline


TWO_ACTS = {
    "title": "t", "central_question": "q",
    "acts": [
        {"id": "act1", "purpose": "p1", "evidence_ids": []},
        {"id": "act2", "purpose": "p2", "evidence_ids": []},
    ],
}


def _structure(v):
    return json.loads(v.narrative_structure)


def _echo_marked(system, user):
    """A well-behaved editor: returns the marked story with one word
    changed in every act, markers untouched."""
    story = json.loads(user)["story"]
    return story.replace("narration", "polished", 2)


# ---------------------------------------------------------------------------
# Evidence IDs: deterministic order + fingerprint of what they mean
# ---------------------------------------------------------------------------


def test_evidence_ids_follow_database_order(db_session):
    from app.agents.story import build_evidence_pack

    case = _mk_case(db_session)
    first = _add_fact(db_session, case, "first fact")
    second = _add_fact(db_session, case, "second fact")
    # Rows handed over in reverse order must still get the same IDs.
    pack = build_evidence_pack([second, first], [], [])
    assert [(f["id"], f["claim"]) for f in pack["facts"]] == [
        ("F001", "first fact"), ("F002", "second fact"),
    ]


def test_evidence_fingerprint_tracks_meaning(db_session):
    from app.agents.story import build_evidence_pack, evidence_fingerprint

    case = _mk_case(db_session)
    a = _add_fact(db_session, case, "alpha")
    b = _add_fact(db_session, case, "beta")
    fp = evidence_fingerprint(build_evidence_pack([a, b], [], []))
    assert fp == evidence_fingerprint(build_evidence_pack([b, a], [], []))
    b.claim = "beta, re-extracted differently"
    assert fp != evidence_fingerprint(build_evidence_pack([a, b], [], []))


# ---------------------------------------------------------------------------
# realign_sections / stored_sections units
# ---------------------------------------------------------------------------


PREV = [
    {"id": "act1", "text": "One.\n\nTwo.", "meta": {"evidence_ids": ["F001"]}},
    {"id": "act2", "text": "Three."},
]


def test_realign_uses_markers_and_keeps_metadata():
    from app.agents.story import realign_sections

    raw = "[[ACT:act1]]\n\nUno.\n\nDos.\n\n[[ACT:act2]]\n\nTres."
    out, ok = realign_sections(PREV, raw)
    assert ok
    assert [s["id"] for s in out] == ["act1", "act2"]
    assert out[0]["text"] == "Uno.\n\nDos."
    assert out[0]["meta"] == {"evidence_ids": ["F001"]}


def test_realign_folds_model_lead_in_into_first_act():
    from app.agents.story import realign_sections

    raw = "Here it is.\n\n[[ACT:act1]]\n\nUno.\n\n[[ACT:act2]]\n\nTres."
    out, ok = realign_sections(PREV, raw)
    assert ok and out[0]["text"].startswith("Here it is.")


def test_realign_paragraph_fallback_only_when_counts_match():
    from app.agents.story import realign_sections

    out, ok = realign_sections(PREV, "Eins.\n\nZwei.\n\nDrei.")
    assert ok and [s["text"] for s in out] == ["Eins.\n\nZwei.", "Drei."]

    out, ok = realign_sections(PREV, "Eins. Zwei. Drei.")
    assert not ok and out == [{"id": "full", "text": "Eins. Zwei. Drei."}]

    # Across languages the caller disables the paragraph fallback.
    out, ok = realign_sections(
        PREV, "Eins.\n\nZwei.\n\nDrei.", allow_paragraph_fallback=False
    )
    assert not ok


def test_realign_unstructured_input_is_not_a_loss():
    from app.agents.story import realign_sections

    out, ok = realign_sections([{"id": "full", "text": "x"}], "new text")
    assert ok and out[0]["id"] == "full"


def test_stored_sections_require_exact_story_text():
    from app.agents.story import stored_sections
    from app.db.models import StoryVersion

    v = StoryVersion(
        story_text="A.\n\nB.",
        narrative_structure=json.dumps({"sections": [
            {"id": "act1", "text": "A."}, {"id": "act2", "text": "B."},
        ]}),
    )
    assert [s["id"] for s in stored_sections(v)] == ["act1", "act2"]
    v.story_text = "A.\n\nB, edited later."
    assert stored_sections(v) is None
    v.narrative_structure = json.dumps({"sections": [{"id": "x", "words": 2}]})
    assert stored_sections(v) is None  # legacy: no section text stored


# ---------------------------------------------------------------------------
# Master pipeline keeps act structure
# ---------------------------------------------------------------------------


def _run_master(db_session, monkeypatch, fake):
    fake.structured.setdefault("story_director", TWO_ACTS)
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    v = asyncio.run(pipe.run(
        db_session, case, target_minutes=10, language="en",
        tone="t", iterations=1,
    ))
    return case, v


def test_master_stores_act_text_and_evidence_fingerprint(db_session, monkeypatch):
    from app.agents.story import stored_sections

    fake = ScriptGen()
    fake.texts["final_editor"] = _echo_marked
    _, v = _run_master(db_session, monkeypatch, fake)
    struct = _structure(v)
    assert struct["structured"] is True
    assert [s["id"] for s in struct["sections"]] == ["act1", "act2"]
    assert all(s["text"] for s in struct["sections"])
    assert struct["evidence_fingerprint"]
    assert struct["structure_lost_at"] == []
    secs = stored_sections(v)
    assert secs and "\n\n".join(s["text"] for s in secs) == v.story_text
    # The editor kept the markers, so its polish was accepted …
    assert "polished" in v.story_text
    # … and no marker ever leaks into narration.
    assert "[[ACT" not in v.story_text


def test_final_edit_that_drops_structure_is_rejected(db_session, monkeypatch):
    fake = ScriptGen()
    fake.texts["final_editor"] = lambda s, u: "flattened " + " ".join(
        json.loads(u)["story"].split()[2:]
    ).replace("[[ACT:act2]]", "")
    _, v = _run_master(db_session, monkeypatch, fake)
    struct = _structure(v)
    assert struct["structured"] is True
    assert "flattened" not in v.story_text
    log = json.loads(v.critic_notes)["revision_log"]
    assert any(e.get("reason") == "structure_lost" for e in log)


def test_consistency_repair_keeps_structure(db_session, monkeypatch):
    fake = ScriptGen()
    calls = {"n": 0}

    def consistency(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"violations": [{
                "type": "timeline", "severity": "high",
                "detail": "wrong order", "location": "w1 w2 w3",
            }]}
        return {"violations": []}

    fake.structured["consistency_checker"] = consistency
    fake.texts["rewriter"] = lambda s, u: " ".join(
        ["repaired"] * len(json.loads(u)["paragraph"].split())
    )
    _, v = _run_master(db_session, monkeypatch, fake)
    struct = _structure(v)
    assert struct["structured"] is True
    assert [s["id"] for s in struct["sections"]] == ["act1", "act2"]
    assert "repaired" in v.story_text


def test_improve_keeps_structure(db_session, monkeypatch):
    fake = ScriptGen()
    fake.texts["final_editor"] = _echo_marked
    case, v = _run_master(db_session, monkeypatch, fake)
    fake.texts["rewriter"] = lambda s, u: json.loads(u)["story"].replace(
        "polished", "improved"
    )
    new = asyncio.run(
        _pipeline(fake, monkeypatch).improve(db_session, case, v, "sharpen")
    )
    struct = _structure(new)
    assert struct["structured"] is True
    assert [s["id"] for s in struct["sections"]] == ["act1", "act2"]
    assert struct["structure_lost_at"] == []
    assert "improved" in new.story_text and "[[ACT" not in new.story_text


# ---------------------------------------------------------------------------
# Localization keeps act structure across languages
# ---------------------------------------------------------------------------


class LocGen:
    """Scripted localization provider. `marked` controls whether the
    one-call writer keeps the [[ACT:id]] markers."""

    def __init__(self, marked=True, words=28):
        self.marked = marked
        self.words = words
        self.calls = []

    def is_configured(self):
        return True

    def _res(self, text):
        return GenerationResult(text=text, model="m/fake", provider="fake")

    def _body(self, tag):
        return " ".join([f"wort{tag}"] * self.words) + "."

    async def generate_text(self, role, system, user):
        self.calls.append((role, user))
        if role == "localization_writer":
            u = json.loads(user)
            if "master_act" in u:  # per-act fallback
                return self._res(self._body(u["act_id"]))
            if "master_story" in u:
                ids = re.findall(r"\[\[ACT:([^\]]+)\]\]", u["master_story"])
                if self.marked and ids:
                    return self._res("\n\n".join(
                        f"[[ACT:{i}]]\n\n{self._body(i)}" for i in ids
                    ))
                return self._res(self._body("flat"))
            return self._res(u["current_text"])  # repair: echo
        if role == "localized_final_editor":
            return self._res(user.replace("wort", "Wort"))
        raise AssertionError(role)

    async def generate_structured(self, role, system, user):
        self.calls.append((role, user))
        table = {
            "native_language_critic": {"overall_native_quality": 92, "problems": []},
            "semantic_consistency_checker": {
                "semantic_consistency_score": 100, "missing_information": [],
                "added_information": [], "meaning_changes": [],
                "uncertainty_changes": [], "name_date_number_errors": [],
            },
            "localized_grounding_validator": {
                "supported_claims": [], "unsupported_claims": [],
                "uncertainty_errors": [], "grounding_score": 0.97,
            },
            "localized_engagement_critic": {"score": 90, "problems": []},
        }
        return table[role], self._res("{}")


def _structured_master(db, case, fingerprint=None):
    from app.db.models import StoryVersion

    sections = [
        {"id": "act1", "text": "The night it began. " * 10},
        {"id": "act2", "text": "What the search found. " * 10},
    ]
    sections = [dict(s, text=s["text"].strip()) for s in sections]
    struct = {
        "title": "t", "acts": [{"id": "act1"}, {"id": "act2"}],
        "sections": [dict(s, words=len(s["text"].split())) for s in sections],
    }
    if fingerprint:
        struct["evidence_fingerprint"] = fingerprint
    v = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="{}", narrative_structure=json.dumps(struct),
        story_text="\n\n".join(s["text"] for s in sections),
        text_hash="h" * 32, engagement_score=80.0, status="ready", is_best=True,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _localize(db_session, monkeypatch, gen, master, case, lang="de"):
    import app.agents.localization as loc_mod
    import app.agents.story as story_mod
    from app.agents.localization import LocalizationPipeline

    monkeypatch.setattr(loc_mod, "get_generation_provider", lambda: gen)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: gen)
    return asyncio.run(
        LocalizationPipeline().localize(
            db_session, case, master, lang, target_minutes=1
        )
    )


def test_localization_keeps_master_acts(db_session, monkeypatch):
    from app.agents.story import stored_sections

    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(db_session, case)
    gen = LocGen(marked=True, words=60)
    loc = _localize(db_session, monkeypatch, gen, master, case)
    secs = stored_sections(loc)
    assert [s["id"] for s in secs] == ["act1", "act2"]
    assert "[[ACT" not in loc.story_text
    notes = json.loads(loc.critic_notes)["localization"]
    assert notes["structured"] is True and notes["per_section_fallback"] is False
    # The final polish kept the markers, so it was accepted.
    assert "Wort" in loc.story_text
    # Only one writer call: no per-act fallback was needed.
    assert sum(1 for r, _ in gen.calls if r == "localization_writer") == 1


def test_localization_falls_back_to_per_act_when_markers_lost(db_session, monkeypatch):
    from app.agents.story import stored_sections

    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(db_session, case)
    gen = LocGen(marked=False, words=60)
    loc = _localize(db_session, monkeypatch, gen, master, case)
    secs = stored_sections(loc)
    assert [s["id"] for s in secs] == ["act1", "act2"]
    assert "wortact1" in secs[0]["text"].lower()
    assert "wortact2" in secs[1]["text"].lower()
    writer_calls = [u for r, u in gen.calls if r == "localization_writer"]
    per_act = [u for u in writer_calls if "master_act" in json.loads(u)]
    assert len(per_act) == 2
    # Act 2 sees the end of the localized act 1 for a natural transition.
    assert json.loads(per_act[1])["previous_localized_act_ending"]
    assert json.loads(loc.critic_notes)["localization"]["per_section_fallback"]


def test_protected_payload_does_not_duplicate_story_text(db_session, monkeypatch):
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(db_session, case)
    gen = LocGen(marked=True)
    _localize(db_session, monkeypatch, gen, master, case)
    writer_user = json.loads(
        next(u for r, u in gen.calls if r == "localization_writer")
    )
    secs = writer_user["protected"]["narrative_structure"]["sections"]
    assert secs and all("text" not in s for s in secs)


def test_localization_refuses_master_built_on_older_evidence(db_session, monkeypatch):
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(db_session, case, fingerprint="0" * 16)
    with pytest.raises(RuntimeError, match="older evidence"):
        _localize(db_session, monkeypatch, LocGen(), master, case)


def test_localization_accepts_master_with_current_evidence(db_session, monkeypatch):
    from app.agents.story import current_evidence_fingerprint

    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(
        db_session, case,
        fingerprint=current_evidence_fingerprint(db_session, case.id),
    )
    loc = _localize(db_session, monkeypatch, LocGen(words=60), master, case)
    assert _structure(loc)["evidence_fingerprint"] == _structure(master)[
        "evidence_fingerprint"
    ]


# ---------------------------------------------------------------------------
# Pilot length comes from config, not a hard-coded 10-minute floor
# ---------------------------------------------------------------------------


def test_pilot_length_range_from_config():
    from pydantic import ValidationError

    from app.schemas import GenerateStoryRequest

    lo = ai_config.story.min_target_minutes
    hi = ai_config.story.max_target_minutes
    assert lo <= 5  # a 3–5 minute pilot must be possible
    assert GenerateStoryRequest(target_minutes=lo).target_minutes == lo
    with pytest.raises(ValidationError):
        GenerateStoryRequest(target_minutes=lo - 1)
    with pytest.raises(ValidationError):
        GenerateStoryRequest(target_minutes=hi + 1)


def test_localization_endpoint_rejects_out_of_range_minutes(client, db_session):
    case = _mk_case(db_session)
    too_short = ai_config.story.min_target_minutes - 1
    r = client.post(
        f"/api/cases/{case.id}/localizations/generate",
        params={"language": "de", "target_minutes": too_short},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Reviewer independence: a critic never falls back to the writer model
# ---------------------------------------------------------------------------


def _raw_config():
    from app.core.ai_config import CONFIG_PATH

    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_reviewer_fallbacks_skip_author_models():
    models = ai_config.generation_provider().models
    for role in ai_config.review_independence.reviewer_roles:
        chain = ai_config.fallback_models_for(role)
        assert models["writer"] not in chain, role
        assert ai_config.model_for(role) not in chain, role
    # Author-side roles keep their normal chain.
    assert ai_config.fallback_models_for("final_editor") == [models["writer"]]


def test_reviewer_routed_to_writer_model_is_a_config_error():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"][
        "native_language_critic"
    ] = "writer"
    with pytest.raises(Exception, match="also writes"):
        AIConfig.model_validate(raw)


def test_reviewer_with_only_author_fallback_gets_no_fallback():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["review_independence"]["reviewer_fallbacks"] = {"premium": ["writer"]}
    cfg = AIConfig.model_validate(raw)
    # Better to fail loudly than to let the writer grade its own text.
    assert cfg.fallback_models_for("native_language_critic") == []


def test_review_independence_can_be_disabled():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["review_independence"]["enabled"] = False
    cfg = AIConfig.model_validate(raw)
    models = cfg.generation_provider().models
    assert cfg.fallback_models_for("engagement_critic") == [models["writer"]]


def test_section_text_is_not_duplicated_into_plans_or_api(db_session, monkeypatch, client):
    """Section text equals the story itself: it must not be re-sent to
    models as 'plan' or returned twice by the story API."""
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    master = _structured_master(db_session, case)
    loc = _localize(db_session, monkeypatch, LocGen(words=60), master, case)
    plan = json.loads(loc.narrative_angle)
    assert all("text" not in s for s in plan.get("sections") or [])
    body = client.get(f"/api/cases/{case.id}/stories/{loc.id}").json()
    secs = body["narrative_structure"]["sections"]
    assert secs and all("text" not in s for s in secs)

"""The recurring on-screen host (persona_master_prompt.md): plan
validation, dialogue checks, memory, the director/writer/critic loop and
the API."""
import asyncio
import json
import random

from app.core.ai_config import ai_config
from app.documentary.host import (
    dialogue_issues, persona_prompt, validate_host_plan, word_range,
)
from app.providers.generation.base import GenerationResult
from test_blueprint_performance import _good, _no_contradiction

BP = _no_contradiction(_good())  # B01 hook, B03 reveal, F001..F004
EVIDENCE = {"F001", "F002", "F003", "F004", "T001"}

_VOCAB = ("timeline witness record door morning house letter neighbour phone kitchen "
          "statement detail question street evening car window answer silence family "
          "account version hour moment report note road garden name").split()


def say(n: int, seed: int = 0) -> str:
    """n distinct-looking spoken words (no repeated trigrams across seeds)."""
    rng = random.Random(seed)
    return " ".join(rng.choice(_VOCAB) for _ in range(n)) + "."


def plan_raw(**over) -> dict:
    raw = {
        "notes": "n",
        "segments": [
            {"id": "S1", "position": "opening", "beat_id": None,
             "pattern": "a detail to remember", "purpose": "curiosity",
             "dimensions": ["curiosity"], "memory_reference": None,
             "delivery": "direct", "intent": "point at the clean house",
             "claims": [{"text": "the house was clean", "kind": "confirmed_fact",
                         "evidence_ids": ["F001"]}],
             "target_seconds": 25, "transition_back": "hand over"},
            {"id": "S2", "position": "mid", "beat_id": "B03",
             "pattern": "two interpretations", "purpose": "turning point",
             "dimensions": ["holding_two_explanations"], "memory_reference": None,
             "delivery": "curious", "intent": "two readings",
             "claims": [{"text": "two accounts", "kind": "witness_statement",
                         "evidence_ids": ["F002"]},
                        {"text": "it was hard to read", "kind": "personal_reaction",
                         "evidence_ids": []}],
             "target_seconds": 20, "transition_back": "back to the story"},
        ],
        "memory_updates": [{"kind": "open_question", "text": "Why so clean?",
                            "segment_id": "S1"}],
    }
    raw.update(over)
    return raw


def codes(items):
    return {i["code"] for i in items}


# ---------------------------------------------------------------------------
# plan validator
# ---------------------------------------------------------------------------


def test_persona_prompt_is_the_master_prompt():
    assert "never compete with the case" in persona_prompt().lower()


def test_valid_plan_and_clamping():
    raw = plan_raw()
    raw["segments"][0]["target_seconds"] = 90
    plan, rep = validate_host_plan(raw, BP, EVIDENCE, set(), 1200)
    assert rep["status"] == "valid", rep
    assert [s["id"] for s in plan["segments"]] == ["S1", "S2"]
    assert plan["segments"][0]["target_seconds"] == ai_config.host.seconds["opening"][1]
    assert rep["adjustments"] and plan["memory_updates"][0]["segment_id"] == "S1"


def test_plan_errors():
    raw = plan_raw()
    s1, s2 = raw["segments"]
    s1["claims"][0]["evidence_ids"] = ["F003"]          # revealed in B04: spoiler
    s1["memory_reference"] = "M999"                      # not in memory
    s2["claims"].append({"text": "x", "kind": "confirmed_fact", "evidence_ids": []})
    s2["claims"].append({"text": "y", "kind": "rumour", "evidence_ids": ["F099"]})
    _, rep = validate_host_plan(raw, BP, EVIDENCE, {"A1"}, 1200)
    assert rep["status"] == "invalid"
    assert {"spoils_reveal", "unverified_memory", "claim_without_evidence",
            "unknown_claim_kind", "unknown_evidence"} <= codes(rep["errors"])


def test_placement_rules():
    raw = plan_raw()
    raw["segments"][1]["beat_id"] = "B01"                # right after the hook
    _, rep = validate_host_plan(raw, BP, EVIDENCE, set(), 1200)
    assert {"interrupts_hook", "appearances_too_close"} <= codes(rep["errors"])
    raw = plan_raw()
    raw["segments"][1]["beat_id"] = "B02"                # right before the reveal
    _, rep = validate_host_plan(raw, BP, EVIDENCE, set(), 1200)
    assert "interrupts_before_reveal" in codes(rep["errors"])
    raw = plan_raw()
    raw["segments"].append({**raw["segments"][1], "id": "S3", "position": "final"})
    _, rep = validate_host_plan(raw, BP, EVIDENCE, set(), 1200)
    assert "appearances_too_close" in codes(rep["errors"])  # B03 -> final: 2 beats


def test_host_share_is_an_error_only_for_a_real_film():
    _, short = validate_host_plan(plan_raw(), BP, EVIDENCE, set(), 120)
    assert short["status"] == "needs_review"
    assert "host_share_too_high" in codes(short["warnings"])
    long_ = plan_raw()
    for seg in long_["segments"]:
        seg["target_seconds"] = 40
    _, film = validate_host_plan(long_, BP, EVIDENCE, set(), 700)  # 80 s of 700
    assert "host_share_too_high" in codes(film["errors"])


def test_verified_memory_is_kept():
    raw = plan_raw()
    raw["segments"][1]["memory_reference"] = "M7"
    raw["segments"][1]["memory_connection"] = "one witness carried the theory"
    plan, rep = validate_host_plan(raw, BP, EVIDENCE, {"M7"}, 1200)
    assert rep["status"] == "valid"
    assert plan["segments"][1]["memory_reference"] == "M7"


# ---------------------------------------------------------------------------
# dialogue checks
# ---------------------------------------------------------------------------


SEG = {"id": "S1", "position": "opening"}


def test_dialogue_length_and_stock_phrases():
    lo, hi = word_range("opening", "en")
    assert dialogue_issues(SEG, say(lo + 5), "en", [], []) == []
    assert dialogue_issues(SEG, say(lo - 10), "en", [], [])[0]["type"] == "too_short"
    assert dialogue_issues(SEG, say(hi + 5), "en", [], [])[0]["type"] == "too_long"
    text = say(lo) + " So, what do you think? [pause]"
    types = {i["type"] for i in dialogue_issues(SEG, text, "en", [], [])}
    assert {"stock_phrase", "not_plain_speech"} <= types
    fa = "متوجه شدم " + " ".join(["خانه"] * 60)
    assert "stock_phrase" in {i["type"] for i in dialogue_issues(SEG, fa, "fa", [], [])}


def test_dialogue_repetition_of_recent_episodes():
    lo, _ = word_range("opening", "en")
    text = say(lo + 5, seed=1)
    recent = [{"position": "opening", "dialogue": text}]
    types = {i["type"] for i in dialogue_issues(SEG, text, "en", recent, [])}
    assert {"repeats_recent_segment", "repeated_opening"} <= types
    other = " ".join(text.split()[:2]) + " " + say(lo, seed=2)
    types = {i["type"] for i in dialogue_issues(SEG, text, "en", [], [other])}
    assert "same_start_as_other_segment" in types
    assert "metadata_callback" in {i["type"] for i in dialogue_issues(
        SEG, text + " In episode 27 we saw this.", "en", [], [])}


# ---------------------------------------------------------------------------
# director -> writer -> critic (scripted models)
# ---------------------------------------------------------------------------


class HostGen:
    """Scripted host_director / host_writer / host_critic. `plans` are
    returned in order (the last one repeats); the critic fails each id in
    `fail_once` the first time it sees it."""

    def __init__(self, plans=None, fail_once=()):
        self.plans = list(plans or [plan_raw()])
        self.fail_once = set(fail_once)
        self.calls: list[tuple[str, dict]] = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        res = GenerationResult(text="{}", model=f"m/{role}", provider="fake")
        if role == "host_director":
            return (self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]), res
        if role == "host_writer":
            out = []
            for k, s in enumerate(data["segments"]):
                seed = sum(map(ord, data["language"] + s["id"])) + len(self.calls)
                out.append({"id": s["id"], "dialogue": say(s["target_words"], seed=seed + k)})
            return {"segments": out}, res
        if role == "host_critic":
            out = []
            for s in data["segments"]:
                bad = s["id"] in self.fail_once
                self.fail_once.discard(s["id"])
                out.append({"id": s["id"], "checks": {"adds_value": not bad},
                            "problems": [{"check": "adds_value", "quote": "x",
                                          "fix": "reframe"}] if bad else []})
            return {"segments": out}, res
        raise AssertionError(f"unexpected role {role}")


def _with_blueprint(db, case, master, status="valid"):
    from app.db.models import EditorialBlueprint

    row = EditorialBlueprint(case_id=case.id, story_version_id=master.id, status=status,
                             central_question=BP["central_question"],
                             editorial_thesis="t", human_thread="Leela",
                             blueprint_json=json.dumps(BP), story_text_hash=master.text_hash)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_host_director_plans_remembers_and_writes(db_session, monkeypatch):
    from test_blueprint_performance import _story
    from app.db.models import HostMemory
    from app.documentary.host import HostDirector, recent_segments

    # an earlier episode: in the archive, with a memory
    old_case, old_master = _story(db_session)
    _with_blueprint(db_session, old_case, old_master)
    mem = HostMemory(case_id=old_case.id, kind="theme", text="one witness, whole theory",
                     origin="editor")
    db_session.add(mem)
    db_session.commit()

    case, master = _story(db_session)
    bp = _with_blueprint(db_session, case, master)
    bad = plan_raw()
    bad["segments"][1]["memory_reference"] = "M424242"   # invented memory
    good = plan_raw()
    good["segments"][1]["memory_reference"] = f"M{mem.id}"
    gen = HostGen(plans=[bad, good], fail_once={"S2"})
    monkeypatch.setattr("app.documentary.host.get_generation_provider", lambda: gen)

    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, bp, master))
    report = json.loads(plan_row.validation_json)
    # a three-minute test story cannot hold an opening within the share
    assert plan_row.status == "needs_review" and not report["errors"], report
    assert report["repair_iterations"] == 1
    director_in = gen.calls[0][1]
    assert f"A{old_case.id}" in {a["ref"] for a in director_in["archive"]}
    assert f"M{mem.id}" in {m["ref"] for m in director_in["host_memory"]}
    assert "errors_to_fix" in gen.calls[1][1]
    # memory written for this case; regenerating replaces it
    notes = db_session.query(HostMemory).filter_by(case_id=case.id).all()
    assert [(m.kind, m.origin) for m in notes] == [("open_question", "host_plan")]
    asyncio.run(HostDirector().create_plan(db_session, case, bp, master))
    assert db_session.query(HostMemory).filter_by(case_id=case.id).count() == 1

    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    segs = json.loads(row.segments_json)
    assert row.status == "valid" and row.language == "en"
    assert json.loads(row.validation_json)["repair_iterations"] == 1
    s1, s2 = segs
    for key in ("segment_id", "placement", "purpose", "personality_dimension",
                "memory_reference", "delivery_direction", "avatar_dialogue",
                "target_duration", "transition_back"):
        assert key in s1
    assert s1["placement"].startswith("Opening") and s2["placement"].startswith("After B03")
    assert s1["memory_reference"] == "none"
    assert s2["memory_reference"].startswith(f"M{mem.id} — ")
    lo, hi = word_range("opening", "en")
    assert lo <= len(s1["avatar_dialogue"].split()) <= hi
    # the writer saw the narration around each placement, the critic the claims
    writer_in = next(d for r, d in gen.calls if r == "host_writer")
    assert writer_in["segments"][0]["narration_before"] is None
    assert writer_in["segments"][0]["narration_after"]
    assert writer_in["segments"][1]["memory"]["text"] == "one witness, whole theory"
    repair_in = [d for r, d in gen.calls if r == "host_writer"][1]
    assert [s["id"] for s in repair_in["segments"]] == ["S2"]
    assert repair_in["segments"][0]["problems_to_fix"][0]["fix"] == "reframe"
    # another case's writer sees these lines as recent segments
    assert len(recent_segments(db_session, old_case.id, "en")) == 2


def test_unfixable_segment_needs_review(db_session, monkeypatch):
    from test_blueprint_performance import _story
    from app.documentary.host import HostDirector

    case, master = _story(db_session)
    bp = _with_blueprint(db_session, case, master)
    gen = HostGen()
    monkeypatch.setattr("app.documentary.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, bp, master))

    async def stock_writer(role, system, user, images=None):
        data = json.loads(user)
        res = GenerationResult(text="{}", model="m", provider="fake")
        if role == "host_writer":
            return {"segments": [{"id": s["id"], "dialogue": say(s["target_words"], k)
                                  + " What do you think?"}
                                 for k, s in enumerate(data["segments"])]}, res
        return await HostGen.generate_structured(gen, role, system, user)

    monkeypatch.setattr(gen, "generate_structured", stock_writer)
    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    rep = json.loads(row.validation_json)
    assert row.status == "needs_review" and rep["failed_segments"] == ["S1", "S2"]
    assert rep["repair_iterations"] == ai_config.host.max_repair_iterations
    seg = json.loads(row.segments_json)[0]
    assert seg["quality"]["deterministic"][0]["type"] == "stock_phrase"


# ---------------------------------------------------------------------------
# API and pipeline stages
# ---------------------------------------------------------------------------


def test_host_api(client, db_session, monkeypatch):
    from test_blueprint_performance import _story
    from app.db.models import StoryVersion

    gen = HostGen()
    monkeypatch.setattr("app.documentary.host.get_generation_provider", lambda: gen)
    case, master = _story(db_session)
    assert client.post(f"/api/cases/{case.id}/documentary/host-plan").status_code == 409
    bp = _with_blueprint(db_session, case, master)
    assert client.get(f"/api/cases/{case.id}/documentary/host-plan").status_code == 404
    r = client.post(f"/api/cases/{case.id}/documentary/host-plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert [s["position"] for s in plan["plan"]["segments"]] == ["opening", "mid"]
    assert client.get(f"/api/cases/{case.id}/documentary/host-plan").json()["id"] == plan["id"]

    # a spoken version of the master (its sections are the beats)
    from app.documentary.host import beat_texts
    texts = beat_texts(master, BP)
    spoken = StoryVersion(
        case_id=case.id, version=2, kind="spoken", language="de", narrative_angle="{}",
        master_version_id=master.id, status="ready", engagement_score=0.0,
        story_text="\n\n".join(texts.values()),
        narrative_structure=json.dumps({"blueprint_id": bp.id, "sections": [
            {"id": k, "text": v} for k, v in texts.items()]}))
    db_session.add(spoken)
    db_session.commit()
    assert client.get(f"/api/documentary/versions/{spoken.id}/host").status_code == 404
    r = client.post(f"/api/documentary/versions/{spoken.id}/host")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "de" and len(body["segments"]) == 2
    assert client.get(f"/api/documentary/versions/{spoken.id}/host").json()["id"] == body["id"]
    writer_in = next(d for role, d in gen.calls if role == "host_writer")
    assert writer_in["language"] == "de"

    # memory: the plan's note, an editor's memory, retire one
    mems = client.get(f"/api/documentary/host/memory?case_id={case.id}").json()
    assert [m["origin"] for m in mems] == ["host_plan"]
    m = client.post("/api/documentary/host/memory", json={
        "case_id": case.id, "kind": "correction",
        "text": "The first reading of the timeline was wrong."}).json()
    assert m["ref"] == f"M{m['id']}" and m["origin"] == "editor"
    assert client.post("/api/documentary/host/memory", json={
        "case_id": case.id, "kind": "rumour", "text": "xyz"}).status_code == 422
    off = client.patch(f"/api/documentary/host/memory/{mems[0]['id']}",
                       json={"active": False}).json()
    assert off["active"] is False
    # editor memories survive a new plan; plan memories are replaced
    client.post(f"/api/cases/{case.id}/documentary/host-plan")
    origins = sorted(x["origin"] for x in client.get(
        f"/api/documentary/host/memory?case_id={case.id}").json())
    assert origins == ["editor", "host_plan"]


def test_job_stages_include_the_host(monkeypatch):
    from app.documentary.jobs import plan_stages

    names = [s["name"] for s in plan_stages(["en", "fa"])]
    assert names.index("spoken:fa") < names.index("host_plan") < names.index("visual_needs")
    assert names.index("host:fa") < names.index("performance:fa")
    monkeypatch.setattr(ai_config.host, "enabled", False)
    assert not any(n.startswith("host") for n in (s["name"] for s in plan_stages(["en"])))


def test_every_language_has_its_channel_and_studio():
    from pathlib import Path
    from app.documentary.host import writer_system_prompt

    names = {l: c.name for l, c in ai_config.channels.items()}
    assert names == {"en": "ClueVera", "de": "Fallspur", "fa": "رد خاموش", "ar": "أثر خفي"}
    assert set(ai_config.documentary.languages) <= set(names)
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "data/studio/manifest.json").read_text(encoding="utf-8"))
    for lang, c in ai_config.channels.items():
        assert c.studio_dir == f"data/studio/{lang}"  # one spelling, lowercase
        shots = sorted(p.name for p in (root / c.studio_dir).glob("*.png"))
        assert shots == sorted(s["file"] for s in manifest["channels"][lang]["shots"])
        assert len(shots) == 7 and "02_front_medium.png" in shots
    assert "«Fallspur»" in writer_system_prompt("de")
    assert "«رد خاموش»" in writer_system_prompt("fa")


def test_settings_show_channels_and_credential_presence_only(client, monkeypatch):
    monkeypatch.setenv("HEYGEN_API_KEY", "secret-heygen-value")
    monkeypatch.setenv("TrueCrime_Avatar_ID_Heygen", "avatar-123")
    monkeypatch.delenv("TrueCrime_ELEVENLABS_API_KEY", raising=False)
    s = client.get("/api/documentary/settings").json()
    assert s["channels"]["de"]["name"] == "Fallspur"
    assert s["credentials"] == {"elevenlabs": False, "avatar": {
        "provider": "heygen", "key_present": True, "avatar_id_present": True}}
    body = json.dumps(s)
    assert "secret-heygen-value" not in body and "avatar-123" not in body

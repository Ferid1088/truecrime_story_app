"""M3: what each draft scene call sees of the scenes before it. The finding is recorded in
docs/english_pipeline_checklist.md; the ledger is deterministic and only affects future cases."""

# ruff: noqa: F811  (`setup` is the flow tests' fixture, imported)
import asyncio
import json

from test_language_pipeline_flow import ACTS, FakeGen, _pipeline, setup  # noqa: F401

from app.agents.story import strip_section_markers
from app.db.models import Fact
from app.language_pipeline import ledger
from app.language_pipeline.materials import SharedMaterial


def _material(acts, facts=()):
    pack = {"facts": [{"id": f"F{i:03d}", "claim": "c", "people": [n], "locations": []}
                      for i, n in enumerate(facts, 1)]}
    return SharedMaterial(case_id=1, plan_id=1, plan={"acts": acts}, pack=pack, draft_pack=pack, fingerprint="x")


def test_the_ledger_names_what_the_earlier_scenes_told():
    acts = [{"id": "act1", "evidence_ids": ["F001", "F002"]}, {"id": "act2", "evidence_ids": ["F003"]},
            {"id": "act3", "evidence_ids": ["F004"]}]
    scenes = [{"act": "act1", "index": 0,
               "text": "Abby Borden was found upstairs. The letter proved nothing. Nobody saw him leave."},
              {"act": "act2", "index": 0, "text": "Lizzie Borden came down.\n\nThe last paragraph."}]
    told = ledger.told_so_far(_material(acts, ["Abby Borden", "Emma Borden"]), scenes,
                              {"act1": ["F010"], "act2": ["F011"], "act3": []}, "act3")
    assert told["names_introduced"] == ["Abby Borden"]              # Emma Borden was not told yet
    assert told["facts_told_in_earlier_acts"] == {"act1": ["F001", "F002", "F010"], "act2": ["F003", "F011"]}
    assert told["limitations_already_explained"] == ["The letter proved nothing."]
    assert told["last_paragraph"] == "The last paragraph."


def test_the_first_scene_has_an_empty_ledger():
    told = ledger.told_so_far(_material([{"id": "act1"}]), [], {}, "act1")
    assert told == {"names_introduced": [], "facts_told_in_earlier_acts": {},
                    "limitations_already_explained": [], "last_paragraph": ""}


class NamedDraft(FakeGen):
    """Scenes that introduce a name and explain a limitation, behind a marker line."""

    async def generate_text(self, role, system, user, images=None, **k):
        if role == "language_draft_writer":
            task = json.loads(user)
            self.calls.append({"role": role, "system": system, "user": task, **k})
            body = "Abby Borden was found upstairs. That proved nothing. " + self.draft_words(
                task["unit_target_words"])
            return self._result(f"[[ACT:{task['act']['id']}]]\n\n{body}", role)
        return await super().generate_text(role, system, user, images, **k)


def test_a_later_scene_call_receives_the_ledger_and_the_prompt_is_formatted(setup, db_session):
    fact = db_session.query(Fact).filter_by(case_id=setup.id).first()
    fact.people_json = json.dumps(["Abby Borden"])
    db_session.commit()
    gen = NamedDraft()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    scenes = [c for c in gen.calls if c["role"] == "language_draft_writer"]
    assert scenes[0]["user"]["told_so_far"]["names_introduced"] == []
    second = scenes[1]["user"]["told_so_far"]
    assert second["names_introduced"] == ["Abby Borden"]
    assert second["limitations_already_explained"] == ["That proved nothing."]
    assert second["last_paragraph"].startswith("Abby Borden was found upstairs")
    assert second["facts_told_in_earlier_acts"] == {ACTS[0]: ["F001"]}
    assert scenes[0]["user"]["target_minutes"] == 90
    # the prompt is the new draft writer, formatted with the marker instruction, tone and words
    system = scenes[0]["system"]
    assert "ONE shared English master narrative" in system and "The whole documentary is about 900 words" in system
    assert "[[ACT:<id>]]" in system and "{" not in system
    # a marker line in a scene's answer never reaches the draft
    row = db_session.get(__import__("app.db.models", fromlist=["StoryVersion"]).StoryVersion,
                         out["draft_version_id"])
    assert "[[ACT" not in row.story_text
    assert strip_section_markers(row.story_text) == row.story_text


def test_the_draft_prompt_names_the_fields_the_draft_stage_really_sends(setup):
    """The INPUT sentence of draft_writer.md lists real keys: each one is in the request."""
    import re

    from app.core.prompts import prompt

    gen = NamedDraft()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    call = next(c for c in gen.calls if c["role"] == "language_draft_writer")
    sent = set(call["user"]) | set(json.loads(call["prefix"].split("\n", 1)[1]))
    sent |= set(call["user"]["told_so_far"]) | set(call["user"]["act"])
    sent |= set(json.loads(call["prefix"].split("\n", 1)[1])["evidence"][0])
    sentence = re.search(r"Input \(JSON\): (.+?)\. Use only what is supplied", prompt("language_pipeline/draft_writer")).group(1)
    named = set(re.findall(r"[a-z]+(?:_[a-z]+)+|\b(?:plan|act|beat|evidence|timeline|contradictions)\b", sentence))
    # plan sub-fields (reveal_map, ending_strategy, key_beats...) come from the plan; the fake plan has no
    # reveal_map or ending_strategy, so those two are checked against the real plan view instead
    plan_fields = {"reveal_map", "ending_strategy", "key_beats", "do_not_reveal"}
    assert named - plan_fields <= sent, named - plan_fields - sent
    import inspect

    from app.language_pipeline.materials import SharedMaterial
    source = inspect.getsource(SharedMaterial.plan_view)
    assert "reveal_map" in source and "ending_strategy" in source


def test_the_formatted_draft_prompt_has_no_leftover_placeholder():
    import re

    from app.core.prompts import prompt

    out = prompt("language_pipeline/draft_writer").format(marker_instruction="M", tone="calm", target_words=900)
    assert not re.search(r"[{}]", out) and "calm" in out and "900" in out

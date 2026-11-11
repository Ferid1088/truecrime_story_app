"""The pictures of a clip's middle: the scene writer (the number of scenes, symbolic places only, repaired when refused), the pictures painted once for the English clip and shown
by every language's clip of the same idea, nothing paid twice, and a failed or refused scene reported."""

import asyncio
import json

import pytest
from test_cover_images import FakeImages, bad, good
from test_host import _plan_story, _version
from test_short_clips import ClipGen

from app.core import files
from app.core.ai_config import ai_config
from app.documentary import storage
from app.providers.generation.base import GenerationResult
from app.providers.image.apimaster import ImageProviderError
from app.shortform import clip_agents as CA
from app.shortform import clip_pictures as CP

SCENES = ["A suburban brick house at night in fresh snow with its garage door standing wide open and a narrow warm beam on the driveway.",
          "A dark bedroom at night with a neatly made bed, a lamp on the nightstand and one pale circle of light on the wall.",
          "A kitchen at night with the back door ajar, a cold draft moving a curtain and long shadows across the tiles.",
          "A long empty hallway in a quiet house at night with one door ajar at the far end and a faint cool glow."]


class SceneGen(ClipGen):
    def __init__(self, answers=None):
        super().__init__()
        self.answers = list(answers or [SCENES])

    async def generate_structured(self, role, system, user, images=None):
        if role != "clip_scene_writer":
            return await super().generate_structured(role, system, user, images)
        self.calls.append((role, json.loads(user)))
        return {"scenes": self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]}, GenerationResult(text="{}", model="m/scene", provider="fake")


@pytest.fixture()
def clips(db_session, monkeypatch, tmp_path):
    case, master, _ = _plan_story(db_session)
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / "cases" / str(case_id))
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    gen = SceneGen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    planner = CA.ClipPlanner()
    english = asyncio.run(planner.plan_english(db_session, case, master))
    de = _version(db_session, case, master, "en")
    de.language = "de"
    db_session.commit()
    native = asyncio.run(planner.write_native(db_session, case, de, english))
    return case, english, native, planner, gen


def test_the_scene_writer_describes_the_configured_number_of_symbolic_scenes_for_the_clip(db_session, clips):
    case, english, _, planner, gen = clips
    scenes = asyncio.run(planner.write_scenes(db_session, case, english[0]))
    role, payload = gen.calls[-1]
    assert scenes == SCENES and role == "clip_scene_writer" and payload["limits"]["count"] == ai_config.short_clips_settings().video.scenes.count == 4
    assert payload["clip"]["hook"] == json.loads(english[0].script_json)["hook"] and json.loads(english[0].script_json)["scenes"] == SCENES


def test_a_scene_with_a_person_a_weapon_or_the_wrong_number_is_asked_again(db_session, clips):
    case, english, _, planner, gen = clips
    gen.answers = [SCENES[:3], [*SCENES[:3], "A man with a pistol stands in the dark hallway of a quiet house at night, waiting."], SCENES]
    scenes = asyncio.run(planner.write_scenes(db_session, case, english[1]))
    asked = [c for c in gen.calls if c[0] == "clip_scene_writer"]
    assert scenes == SCENES and len(asked) == 3 and {p["type"] for p in asked[1][1]["problems_to_fix"]} == {"scene_count"}
    assert {p["type"] for p in asked[2][1]["problems_to_fix"]} == {"scene_not_symbolic"}


def test_scenes_that_stay_refused_are_reported(db_session, clips):
    case, english, _, planner, gen = clips
    gen.answers = [SCENES[:2]]
    with pytest.raises(ValueError, match="did not pass"):
        asyncio.run(planner.write_scenes(db_session, case, english[2]))


def test_the_pictures_are_painted_once_and_the_native_clip_shows_the_same_ones(db_session, clips):
    case, english, native, planner, _ = clips
    provider = FakeImages([good() for _ in range(4)])
    made = asyncio.run(CP.ensure_pictures(db_session, case, english[0], planner, provider))
    assert [p["scene"] for p in made] == SCENES and all(storage.resolve(p["path"]).exists() for p in made) and len(provider.calls) == 4
    assert "vertical 2:3" in provider.calls[0][1] and provider.calls[0][2] == "1024x1536"
    again = asyncio.run(CP.ensure_pictures(db_session, case, english[0], planner, provider))
    de_row = next(r for r in native if r.clip_key == english[0].clip_key)
    shared = asyncio.run(CP.ensure_pictures(db_session, case, de_row, planner, provider))
    assert again == made == shared and len(provider.calls) == 4                                       # nothing is painted twice, in any language


def test_a_picture_that_cannot_be_painted_is_reported_and_the_ones_made_are_kept(db_session, clips):
    case, english, _, planner, _ = clips
    provider = FakeImages([good(), good(), bad(), bad(), bad()])
    with pytest.raises(ImageProviderError, match="3 attempts failed"):
        asyncio.run(CP.ensure_pictures(db_session, case, english[3], planner, provider))
    assert len(json.loads(english[3].media_json)["pictures"]) == 2
    provider = FakeImages([good(), good()])
    done = asyncio.run(CP.ensure_pictures(db_session, case, english[3], planner, provider))
    assert len(done) == 4 and len(provider.calls) == 2                                                  # only the missing two are painted

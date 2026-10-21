"""Owner decision 2026-10-21: a film is never made without the avatar (the host); the avatar is part of the system."""

import json
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.documentary.production import host_timeline as HT

ROOT = Path(__file__).resolve().parent.parent


def test_the_avatar_is_required_by_config():
    assert json.loads((ROOT / "config/parameters/documentary.json").read_text())["host"]["required_for_film"] is True


def test_avatar_required_a_film_with_no_ready_host_scene_is_refused(monkeypatch):
    monkeypatch.setattr(ai_config.host, "required_for_film", True)
    with pytest.raises(HT.AvatarRequired, match="No film without the avatar"):
        HT.require_avatar([])
    HT.require_avatar([{"scene_id": 1, "position": "opening"}])          # a ready scene: fine


def test_avatar_required_only_when_the_rule_is_on(monkeypatch):
    monkeypatch.setattr(ai_config.host, "required_for_film", False)
    HT.require_avatar([])


def test_avatar_required_is_checked_before_the_script_is_built_and_before_the_render():
    script = (ROOT / "app/documentary/production/script.py").read_text()
    chain = (ROOT / "app/documentary/pipeline/language.py").read_text()
    assert script.index("HT.require_avatar(host)") < script.index("logo_seconds = intro_seconds(language)")
    assert chain.index("require_avatar(script.get(\"host\")") < chain.index("VideoRenderer().render")

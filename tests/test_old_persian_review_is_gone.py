"""The acoustic review of a Persian narration (wav2vec2, expected-reading agent, repair proposals) is removed: a person listens and corrects by hand in the
voice review. Nothing of it may come back by accident."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.core.ai_config import ai_config

ROOT = Path(__file__).resolve().parents[1]
GONE_ROLES = {"ppie_expectation", "ppie_alignment_verifier", "ppie_corrector"}


def test_the_old_review_package_scripts_prompts_and_config_are_gone():
    assert importlib.util.find_spec("app.documentary.ppie_review") is None
    assert not (ROOT / "app/documentary/ppie_review").exists() and not (ROOT / "scripts/ppie_review.py").exists()
    assert not (ROOT / "prompts/documentary/ppie_review").exists()
    assert not hasattr(ai_config, "ppie_review") and hasattr(ai_config, "english_reference")
    text = (ROOT / "config/agents.json").read_text(encoding="utf-8") + (ROOT / "config/models.json").read_text(encoding="utf-8")
    assert "ppie_review" not in text and not any(role in text for role in GONE_ROLES)


def test_the_old_review_routes_and_screen_are_gone():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("ppie-review" in p for p in paths)
    api = (ROOT / "frontend/lib/api.ts").read_text(encoding="utf-8")
    assert "ppie-review" not in api and "ppieReview" not in api
    screen = (ROOT / "frontend/components/documentary/tabs/pronunciation-review.tsx").read_text(encoding="utf-8")
    assert "Audio review" not in screen and "AudioReview" not in screen
    assert not (ROOT / "frontend/components/documentary/tabs/audio-review.tsx").exists()
    json.loads((ROOT / "config/parameters/documentary.json").read_text(encoding="utf-8"))

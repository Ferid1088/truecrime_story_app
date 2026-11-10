"""The acoustic review of a Persian narration (wav2vec2, expected-reading agent, repair proposals) is removed: a person listens and corrects by hand in the
voice review. Nothing of it may come back by accident."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from test_block_review_service import narration  # noqa: F401  (the fixture)

from app.core.ai_config import ai_config
from app.documentary.block_review import service as S

ROOT = Path(__file__).resolve().parents[1]
GONE_ROLES = {"ppie_expectation", "ppie_alignment_verifier", "ppie_corrector"}


def test_the_old_review_package_scripts_prompts_and_config_are_gone():
    assert importlib.util.find_spec("app.documentary.ppie_review") is None
    assert not (ROOT / "app/documentary/ppie_review").exists() and not (ROOT / "scripts/ppie_review.py").exists()
    assert not (ROOT / "prompts/documentary/ppie_review").exists()
    assert not hasattr(ai_config, "ppie_review") and not hasattr(ai_config, "semantic_punctuation") and not hasattr(ai_config, "english_reference")
    text = (ROOT / "config/agents.json").read_text(encoding="utf-8") + (ROOT / "config/models.json").read_text(encoding="utf-8")
    assert "ppie_review" not in text and not any(role in text for role in GONE_ROLES)


def test_the_old_review_routes_and_screen_are_gone():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert len(paths) > 200 and not any("ppie-review" in p for p in paths)
    api = (ROOT / "frontend/lib/api.ts").read_text(encoding="utf-8")
    assert "ppie-review" not in api and "ppieReview" not in api
    screen = (ROOT / "frontend/components/documentary/tabs/voice-review.tsx").read_text(encoding="utf-8")
    assert "Audio review" not in screen and "AudioReview" not in screen
    assert not (ROOT / "frontend/components/documentary/tabs/audio-review.tsx").exists()
    json.loads((ROOT / "config/parameters/documentary.json").read_text(encoding="utf-8"))


def test_the_hand_review_is_in_its_place_and_nothing_old_is_left_in_the_code():
    from app.main import app

    paths = set(app.openapi()["paths"])
    for wanted in ("/api/documentary/versions/{version_id}/block-review", "/api/documentary/versions/{version_id}/block-review/send-all",
                   "/api/documentary/versions/{version_id}/block-review/assemble", "/api/cases/{case_id}/voice-review-status"):
        assert wanted in paths, wanted
    screen = (ROOT / "frontend/components/documentary/tabs/voice-review.tsx").read_text(encoding="utf-8")
    assert "Voice review" in screen and "BlockReview" in screen
    assert "voice-review-shortcut" in (ROOT / "frontend/components/documentary/tabs/monitoring.tsx").read_text(encoding="utf-8")
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "scripts").glob("*.py"), *(ROOT / "frontend/components").rglob("*.tsx"), *(ROOT / "frontend/lib").glob("*.ts")]:
        text = path.read_text(encoding="utf-8")
        assert "ppie_review" not in text and "ppie-review" not in text and "Audio review" not in text, path


def test_no_film_goes_on_before_the_person_approves_the_narration_and_a_new_assembly_needs_a_new_approval(narration):  # noqa: F811
    from app.documentary import voice_review as VRV
    from app.documentary.pipeline.errors import AwaitingVoiceReview

    db, _, version, voice = narration
    review = S.open_review(db, version)
    with pytest.raises(AwaitingVoiceReview):
        VRV.guard(version, "fa")
    asyncio.run(S.assemble(db, review))
    with pytest.raises(AwaitingVoiceReview):
        VRV.guard(version, "fa")                                  # assembled is not approved: the film still waits
    asyncio.run(S.approve(db, review))
    VRV.guard(version, "fa")                                      # approved: the film may go on

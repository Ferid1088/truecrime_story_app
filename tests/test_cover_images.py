"""The scene picture of a cover: the prompt sent to the image model, what a scene may not contain, the cost from the tokens the model reports (only the output image tokens
have a rate), every attempt recorded (also a failed one), the retries, the ledger entry as an estimate, and the stop when the case's budget is spent."""

import asyncio
import io

import pytest
from PIL import Image
from test_host import _plan_story

from app.core import files
from app.core.ai_config import ai_config
from app.covers import compose as C
from app.covers import images as IM
from app.db.models import CoverImage, CoverProposal, ProviderCall
from app.documentary import storage
from app.providers.image.apimaster import ImageProviderError, ImageResult
from app.services.budget import BudgetStopped

USAGE = {"input_tokens": 74, "input_tokens_details": {"text_tokens": 74, "image_tokens": 0}, "output_tokens": 1584,
         "output_tokens_details": {"image_tokens": 1584, "text_tokens": 0, "reasoning_tokens": 0}, "total_tokens": 1658}
SCENE = "A front door standing slightly open at night, a narrow strip of warm light falling across the dark floor"


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 40)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeImages:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def generate(self, model, prompt, size, timeout_s):
        self.calls.append((model, prompt, size))
        return self.results.pop(0)


def good(usage=USAGE):
    return ImageResult(ok=True, content=png(), usage=usage, request_id="1794", duration_ms=39200)


def bad():
    return ImageResult(ok=False, error="200: {'error': 'busy'}", duration_ms=1500)


@pytest.fixture()
def cover(db_session, monkeypatch, tmp_path):
    case, _, _ = _plan_story(db_session)
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / "cases" / str(case_id))
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    row = CoverProposal(case_id=case.id, language="en", cover_key="long", kind="long", status="proposed", title="The open door", scene_prompt=SCENE)
    db_session.add(row)
    db_session.commit()
    return case, row


def test_the_prompt_has_the_look_the_scene_the_bans_and_the_empty_side():
    long_en = IM.prompt_for("en", "long", SCENE)
    assert ai_config.covers_settings().themes["en"].look in long_en and SCENE in long_en and "no text, no letters, no logo" in long_en and "no people, no faces" in long_en
    assert "left 42%" in long_en and "Wide 3:2" in long_en
    assert "right 42%" in IM.prompt_for("fa", "long", SCENE) and "top 38%" in IM.prompt_for("en", "short", SCENE) and "Tall 2:3" in IM.prompt_for("en", "short", SCENE)


def test_a_scene_with_a_person_a_victim_or_a_weapon_is_refused():
    assert IM.scene_issues(SCENE) == []
    assert IM.scene_issues("A man with a pistol in a dark hall") and IM.scene_issues("The victim lying on the floor")


def test_only_the_output_image_tokens_are_priced_at_the_configured_rate():
    usd, tokens = IM.priced(USAGE, "gpt-image-2.6-flare")
    assert usd == round(1584 * 30 / 1_000_000, 6) == 0.04752
    assert tokens == {"text_tokens_in": 74, "image_tokens_in": 0, "text_tokens_out": 0, "image_tokens_out": 1584}
    assert IM.priced({}, "gpt-image-2.6-flare")[0] is None and IM.priced(USAGE, "unknown-model")[0] is None


def test_a_picture_is_made_recorded_and_booked_in_the_ledger_as_an_estimate(db_session, cover):
    case, row = cover
    provider = FakeImages([good()])
    record = asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    assert record.ok and record.attempt == 1 and record.image_tokens_out == 1584 and record.text_tokens_in == 74 and record.duration_ms == 39200
    assert record.cost_usd == 0.04752 and record.cost_source == "assumed-unverified" and row.image_id == record.id
    assert storage.resolve(record.file_path).exists() and provider.calls[0][2] == "1536x1024" and provider.calls[0][0] == "gpt-image-2.6-flare"
    call = db_session.get(ProviderCall, record.provider_call_id)
    assert call.stage == "image:cover" and call.estimated is True and call.tokens_out == 1584 and call.tokens_in == 74 and call.model == "gpt-image-2.6-flare"
    assert call.cost_usd == 0.04752 and abs(call.cost_eur - 0.04752 * ai_config.budget.usd_to_eur) < 1e-9 and call.duration_ms == 39200


def test_a_cover_that_has_a_picture_does_not_pay_for_another_unless_asked(db_session, cover):
    case, row = cover
    provider = FakeImages([good(), good()])
    first = asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    assert asyncio.run(IM.make_image(db_session, case, "en", row, provider)).id == first.id and len(provider.calls) == 1
    assert asyncio.run(IM.make_image(db_session, case, "en", row, provider, again=True)).id != first.id and len(provider.calls) == 2


def test_a_failed_call_is_tried_again_and_every_attempt_is_recorded(db_session, cover):
    case, row = cover
    provider = FakeImages([bad(), good()])
    record = asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    attempts = db_session.query(CoverImage).filter(CoverImage.case_id == case.id).order_by(CoverImage.id).all()
    assert [(a.attempt, a.ok) for a in attempts] == [(1, False), (2, True)] and attempts[0].error and attempts[0].cost_usd is None and record.attempt == 2
    assert db_session.query(ProviderCall).filter(ProviderCall.case_id == case.id, ProviderCall.stage == "image:cover").count() == 1       # only a picture that came back is booked


def test_when_every_attempt_fails_nothing_is_booked_and_the_cover_has_no_picture(db_session, cover):
    case, row = cover
    with pytest.raises(ImageProviderError, match="3 attempts failed"):
        asyncio.run(IM.make_image(db_session, case, "en", row, FakeImages([bad(), bad(), bad()])))
    assert row.image_id is None and db_session.query(CoverImage).filter(CoverImage.case_id == case.id, CoverImage.ok.is_(False)).count() == 3


def test_no_call_is_made_for_a_scene_with_a_person_or_without_a_scene(db_session, cover):
    case, row = cover
    provider = FakeImages([good()])
    row.scene_prompt = "A man with a pistol in a dark hall"
    with pytest.raises(C.CoverError, match="symbolic"):
        asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    row.scene_prompt = ""
    with pytest.raises(C.CoverError, match="no scene description"):
        asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    assert provider.calls == []


def test_the_call_is_refused_when_the_case_budget_is_spent(db_session, cover):
    case, row = cover
    case.total_cost_cap_eur = 0.0001
    db_session.add(ProviderCall(case_id=case.id, stage="test", host="x", method="CALL", path="/x", cost_eur=1.0))
    db_session.commit()
    provider = FakeImages([good()])
    with pytest.raises(BudgetStopped):
        asyncio.run(IM.make_image(db_session, case, "en", row, provider))
    assert provider.calls == []
    case.total_cost_cap_eur = None
    case.status = "story_ready"
    db_session.commit()

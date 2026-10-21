"""P6: adaptive stop — a provider whose useful share (verified + needs_review of what was
actually checked) stays below a config threshold after a minimum sample is dropped from later
research runs, and the decision is logged (visible in the research stage's own stats)."""

import asyncio
import uuid

import httpx

from app.agents import visual_research as VR
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals.research import adaptive_stop, provider_yield


def _asset(provider, status):
    return VisualAsset(provider=provider, verification_status=status, asset_type="photo")


def test_provider_yield_counts_only_checked_pictures():
    assets = ([_asset("wikimedia", "rejected")] * 18 + [_asset("wikimedia", "verified")] * 2
             + [_asset("wikimedia", "unverified")] * 50          # not checked yet: excluded
             + [_asset("brave_images", "verified")] * 5 + [_asset("brave_images", "rejected")] * 3)
    out = provider_yield(assets)
    assert out["wikimedia"] == {"checked": 20, "useful": 2, "share": 0.1}
    assert out["brave_images"]["checked"] == 8 and out["brave_images"]["useful"] == 5


def test_generated_pictures_are_not_a_provider():
    assets = [_asset("generated", "verified")] * 10
    assert provider_yield(assets) == {}


def test_a_provider_below_the_minimum_sample_is_never_stopped():
    assets = [_asset("wikimedia", "rejected")] * 5 + [_asset("wikimedia", "verified")] * 1
    assert adaptive_stop(assets, min_sample=20, min_share=0.1) == []


def test_a_provider_below_the_threshold_after_enough_samples_is_stopped():
    assets = [_asset("wikimedia", "rejected")] * 19 + [_asset("wikimedia", "verified")] * 1
    [stopped] = adaptive_stop(assets, min_sample=20, min_share=0.1)
    assert stopped["provider"] == "wikimedia" and stopped["share"] == 0.05
    assert "5%" in stopped["reason"] and "20" in stopped["reason"]


def test_a_provider_at_or_above_the_threshold_is_not_stopped():
    assets = [_asset("wikimedia", "rejected")] * 15 + [_asset("wikimedia", "verified")] * 5
    assert adaptive_stop(assets, min_sample=20, min_share=0.25) == []


# ---- wired into the research agent: a stopped provider is skipped, the decision is visible ----

class FakeWikimedia:
    def __init__(self, client):
        pass

    async def search(self, query, limit):
        raise AssertionError("wikimedia must not be searched once adaptively stopped")

    async def category_files(self, name, limit):
        return []

    async def article_files(self, name, limit, language):
        return []


def test_a_stopped_provider_is_skipped_by_the_research_agent_and_the_decision_is_visible(
        db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["wikimedia"])
    monkeypatch.setattr(ai_config.visual_search, "wikimedia_categories", False)
    monkeypatch.setattr(ai_config.visual_search, "adaptive_stop_min_sample", 20)
    monkeypatch.setattr(ai_config.visual_search, "adaptive_stop_min_share", 0.1)
    monkeypatch.setattr(VR, "WikimediaProvider", FakeWikimedia)
    case = Case(canonical_title="Adaptive", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    rows = [_asset("wikimedia", "rejected") for _ in range(19)] + [_asset("wikimedia", "verified")]
    for i, a in enumerate(rows):
        a.case_id, a.asset_code = case.id, f"VIS_{i:06d}"
    db_session.add_all(rows)
    db_session.commit()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b""))) as client:
            return await VR.VisualResearchAgent(client).run(
                db_session, case, [{"query": "x", "entity": "e", "entity_type": "place", "kind": "exact"}])

    stats = asyncio.run(go())
    assert stats["added"] == 0  # the only provider was stopped: nothing new searched
    [stopped] = stats["adaptive_stop"]
    assert stopped["provider"] == "wikimedia"


def test_footage_providers_also_respect_adaptive_stop(monkeypatch):
    from app.agents.footage import FootageAgent

    monkeypatch.setattr(ai_config.footage, "providers", ["wikimedia_video", "internet_archive"])
    agent = FootageAgent(client=object())
    names = {p.name for p in agent.providers(frozenset({"internet_archive"}))}
    assert names == {"wikimedia_video"}

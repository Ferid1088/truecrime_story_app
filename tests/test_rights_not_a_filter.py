"""Rights are no filter (owner decision): true-crime pictures and footage count as public material;
only relevance to the case, the story's moment and the spoken words decides."""
from types import SimpleNamespace

from app.core.ai_config import ai_config
from app.documentary.visuals import footage as FT
from app.documentary.visuals import rights as R
from app.shortform.gates import AssetUse, rights_checker


def _asset(status, approved=False):
    return SimpleNamespace(rights_status=status, editorial_approved_at="x" if approved else None)


def test_with_the_filter_off_every_rights_status_may_be_shown_in_both_profiles(monkeypatch):
    monkeypatch.setattr(ai_config.rights, "enforce", False)
    for status in R.RIGHTS:
        for profile in ("preview", "publish"):
            assert R.usable(_asset(status), profile) is True
    assert FT.renderable("permission_required") is True


def test_with_the_filter_on_the_old_rules_apply(monkeypatch):
    monkeypatch.setattr(ai_config.rights, "enforce", True)
    assert R.usable(_asset("do_not_use"), "preview") is False
    assert R.usable(_asset("editorial_review_required"), "publish") is False
    assert R.usable(_asset("editorial_review_required"), "preview") is False       # no approval
    assert R.usable(_asset("editorial_review_required", approved=True), "preview") is True
    assert R.usable(_asset("public_domain"), "publish") is True
    assert FT.renderable("do_not_use") is False


def test_the_shorts_rights_gate_passes_when_rights_are_not_a_filter(monkeypatch):
    uses = [AssetUse("A", rights_status="unknown"), AssetUse("B", rights_status="editorial_review_required")]
    monkeypatch.setattr(ai_config.rights, "enforce", True)
    assert not rights_checker(uses, "tiktok_video").passed
    monkeypatch.setattr(ai_config.rights, "enforce", False)
    assert rights_checker(uses, "tiktok_video").passed


def test_the_config_has_the_filter_off_for_the_films():
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/parameters/visuals.json").read_text())
    assert cfg["rights"]["enforce"] is False

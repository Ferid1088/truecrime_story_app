"""Step 1 of the publishing layer: the unified platform vocabulary and config/publishing.json. Pure configuration: no database, no network."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.publishing.config import CONFIG_PATH, PublishingConfig, publishing_config
from app.publishing.vocabulary import Destination, Kind, Platform

ROOT = Path(__file__).resolve().parents[1]


def raw() -> dict:
    return copy.deepcopy(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def test_the_vocabulary_is_the_four_platforms_and_two_kinds():
    cfg = publishing_config()
    assert [p.value for p in cfg.vocabulary.platforms] == ["youtube", "facebook", "instagram", "tiktok"]
    assert [k.value for k in cfg.vocabulary.kinds] == ["film", "short"]


def test_automatic_publishing_defaults_are_the_owners_decisions():
    cfg = publishing_config()
    assert cfg.automatic_publishing.default_enabled is False
    assert cfg.automatic_publishing.missed_window_minutes == 60
    assert cfg.automatic_publishing.automatic_retries == 0
    assert cfg.approval.max_age_days == 7


def test_only_farsi_has_a_schedule_and_it_is_2030_in_tehran():
    cfg = publishing_config()
    fa = cfg.languages["fa"]
    assert (fa.enabled, fa.timezone, fa.publish_time_local) == (True, "Asia/Tehran", "20:30")
    others = {code: lang for code, lang in cfg.languages.items() if code != "fa"}
    assert set(others) == {"en", "de", "ar", "es", "it", "fr", "pt", "tr"}
    assert all(not lang.enabled and lang.timezone is None and lang.publish_time_local is None for lang in others.values())
    assert [code for code in cfg.languages if cfg.language_enabled(code)] == ["fa"]


def test_the_languages_are_the_applications_languages():
    channels = json.loads((ROOT / "config" / "parameters" / "identity.json").read_text(encoding="utf-8"))["channels"]
    assert set(publishing_config().languages) == set(channels)


def test_the_farsi_slot_is_the_same_utc_moment_all_year_unlike_berlin_time():
    cfg = publishing_config()
    winter, summer = cfg.slot_utc("fa", date(2026, 12, 1)), cfg.slot_utc("fa", date(2026, 7, 1))
    assert (winter.hour, winter.minute, summer.hour, summer.minute) == (17, 0, 17, 0)
    assert winter.utcoffset().total_seconds() == 0
    from zoneinfo import ZoneInfo

    berlin_6pm = {m: datetime(2026, m, 1, 18, 0, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(ZoneInfo("Asia/Tehran")) for m in (12, 7)}
    assert (berlin_6pm[12].hour, berlin_6pm[12].minute) == (20, 30) and (berlin_6pm[7].hour, berlin_6pm[7].minute) == (19, 30)


def test_a_language_without_a_schedule_has_no_slot():
    for code in ("en", "tr", "xx"):
        with pytest.raises(ValueError, match="no publishing schedule"):
            publishing_config().slot_utc(code, date(2026, 12, 1))


def test_the_supported_destinations_and_their_automation_flags():
    cfg = publishing_config()
    assert {str(d.destination) for d in cfg.destinations} == {"youtube/film", "youtube/short", "facebook/film", "facebook/short", "instagram/short", "tiktok/short"}
    assert cfg.destination("instagram", "film") is None and cfg.destination("tiktok", "film") is None
    assert cfg.automation_allowed("tiktok", "short") is False
    assert all(cfg.automation_allowed(p, k) for p, k in [("youtube", "film"), ("youtube", "short"), ("facebook", "film"), ("facebook", "short"), ("instagram", "short")])
    assert cfg.automation_allowed(Platform.INSTAGRAM, Kind.FILM) is False
    assert all(d.reason for d in cfg.destinations)


def test_destination_parse_and_string():
    assert str(Destination.parse("youtube", "short")) == "youtube/short"
    with pytest.raises(ValueError, match="unknown destination"):
        Destination.parse("vimeo", "film")
    with pytest.raises(ValueError, match="unknown destination"):
        Destination.parse("youtube", "reel")


@pytest.mark.parametrize("legacy, expected", [("youtube", "youtube/film"), ("youtube_long", "youtube/film"), ("youtube_short", "youtube/short"),
                                              ("facebook_long", "facebook/film"), ("facebook_reel", "facebook/short"), ("instagram_reel", "instagram/short"),
                                              ("tiktok", "tiktok/short"), ("tiktok_video", "tiktok/short")])
def test_every_older_platform_name_maps_to_one_destination(legacy, expected):
    assert str(Destination.from_legacy(legacy)) == expected


def test_an_unknown_older_name_is_an_error():
    with pytest.raises(ValueError, match="unknown legacy platform name"):
        Destination.from_legacy("youtube_reel")


def test_every_platform_name_the_existing_code_and_config_use_is_understood():
    """The compatibility promise: nothing existing has to be renamed before the registry replaces it."""
    from app.autopilot.publish import FILM_PLATFORM
    from app.core.ai_config import ai_config
    from app.meta_publishing.publisher import LEGACY_PLATFORM, PLATFORM
    from app.shortform.publishers import PUBLISHER_CONFIG
    from app.shortform.release_plan import PLATFORM_LABELS

    social = json.loads((ROOT / "config" / "social_distribution.json").read_text(encoding="utf-8"))
    covers = json.loads((ROOT / "config" / "parameters" / "covers.json").read_text(encoding="utf-8"))["covers"]["platforms"]
    assert PLATFORM in {p.value for p in Platform}        # the Instagram module now speaks the canonical vocabulary
    used = {FILM_PLATFORM, LEGACY_PLATFORM, *PUBLISHER_CONFIG, *PLATFORM_LABELS, *covers, *social["platforms"], *social["accounts"],
            *(p for slot in social["schedule_template"] for p in slot["platforms"]), *ai_config.short_form.distribution}
    assert used >= {"youtube", "instagram_reel", "youtube_short", "tiktok_video", "facebook_reel", "tiktok", "youtube_long", "facebook_long"}
    for name in sorted(used):
        Destination.from_legacy(name)


def test_the_shipped_file_validates_and_is_cached():
    assert publishing_config() is publishing_config() and isinstance(publishing_config(), PublishingConfig)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["languages"]["en"].update(timezone="Europe/London"), "disabled language has no schedule"),
    (lambda d: d["languages"]["en"].update(publish_time_local="18:00"), "disabled language has no schedule"),
    (lambda d: d["languages"]["fa"].pop("timezone"), "needs timezone and publish_time_local"),
    (lambda d: d["languages"]["fa"].pop("publish_time_local"), "needs timezone and publish_time_local"),
    (lambda d: d["languages"]["fa"].update(timezone="Mars/Olympus"), "unknown time zone"),
    (lambda d: d["languages"]["fa"].update(publish_time_local="25:00"), "HH:MM"),
    (lambda d: d["languages"]["fa"].update(publish_time_local="8:30"), "HH:MM"),
    (lambda d: d["destinations"].append(dict(d["destinations"][0])), "listed twice"),
    (lambda d: d["destinations"][0].update(reason=""), "reason"),
    (lambda d: d["vocabulary"]["platforms"].remove("tiktok"), "every platform exactly once"),
    (lambda d: d["vocabulary"]["kinds"].append("film"), "every kind exactly once"),
    (lambda d: d["vocabulary"]["legacy_names"].update(x={"platform": "instagram", "kind": "film"}), "does not exist"),
    (lambda d: d["vocabulary"]["legacy_names"].update(x={"platform": "vimeo", "kind": "film"}), "platform"),
    (lambda d: d["approval"].update(max_age_days=0), "max_age_days"),
    (lambda d: d["automatic_publishing"].update(missed_window_minutes=0), "missed_window_minutes"),
    (lambda d: d["automatic_publishing"].update(automatic_retries=-1), "automatic_retries"),
    (lambda d: d.update(surprise=1), "surprise"),
])
def test_a_broken_configuration_is_refused(change, message):
    data = raw()
    change(data)
    with pytest.raises(ValidationError, match=message):
        PublishingConfig.model_validate(data)


# --- Phase 1 contracts: capabilities, modes, approvals, routing, scheduling, monitoring --------------------------------------------------------------------------

def test_all_nine_languages_are_supported_and_only_farsi_is_schedulable():
    cfg = publishing_config()
    assert sorted(cfg.supported_languages) == sorted(["fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"])
    assert [c for c in cfg.supported_languages if cfg.language_enabled(c)] == ["fa"]
    assert not cfg.language_enabled("xx")


def test_every_destination_states_every_capability_with_its_evidence():
    from app.publishing.vocabulary import CapabilityKey, Evidence

    for d in publishing_config().destinations:
        assert set(d.capabilities) == set(CapabilityKey)
        for cap in d.capabilities.values():
            assert cap.evidence in set(Evidence)
            assert cap.value is not None or cap.evidence is Evidence.UNVERIFIED


def test_capability_facts_of_the_destinations():
    from app.publishing.vocabulary import CapabilityKey as K
    from app.publishing.vocabulary import Evidence, Restriction

    cfg = publishing_config()
    tiktok = cfg.capability("tiktok", "short", K.PUBLIC_POSTING)
    assert (tiktok.value, tiktok.evidence) == ("audit_required", Evidence.CONFIRMED)
    assert cfg.capability("tiktok", "short", K.PERSON_REQUIRED_PER_POST).value is True
    assert cfg.capability("tiktok", "short", K.AI_DISCLOSURE_FIELD).value == "is_aigc"
    assert cfg.capability("facebook", "short", K.AI_DISCLOSURE_FIELD).value == "is_ai_generated"
    assert cfg.capability("youtube", "film", K.AI_DISCLOSURE_FIELD).value == "status.containsSyntheticMedia"
    assert cfg.capability("instagram", "short", K.AI_DISCLOSURE_FIELD).value is None      # unconfirmed: never claimed
    assert Restriction.PRIVATE_UNTIL_AUDIT in cfg.destination("tiktok", "short").restrictions
    assert Restriction.NO_API_RELATED_VIDEO in cfg.destination("youtube", "short").restrictions
    assert Restriction.NO_CLICKABLE_CAPTION_LINKS in cfg.destination("instagram", "short").restrictions
    with pytest.raises(ValueError, match="not a destination"):
        cfg.capability("instagram", "film", K.NATIVE_SCHEDULING)


def test_publishing_is_manual_by_default_and_automatic_needs_every_condition():
    from app.publishing.vocabulary import AutomaticRequirement, PublishingMode

    cfg = publishing_config()
    assert cfg.modes.default is PublishingMode.MANUAL
    assert set(cfg.modes.automatic_requires) == set(AutomaticRequirement)
    assert AutomaticRequirement.EMERGENCY_STOP_CLEAR in cfg.modes.automatic_requires
    assert cfg.automatic_publishing.default_enabled is False


def test_approval_is_per_destination_in_two_kinds_and_bound_to_the_listed_parts():
    from app.publishing.vocabulary import ApprovalInput, ApprovalKind

    a = publishing_config().approval
    assert a.scope == "destination" and set(a.required) == {ApprovalKind.CONTENT, ApprovalKind.PUBLICATION} and a.max_age_days == 7
    assert set(a.invalidated_by) == {ApprovalInput.VIDEO, ApprovalInput.COVER, ApprovalInput.CAPTION, ApprovalInput.LINKS, ApprovalInput.SETTINGS}
    assert a.unaffected_after_publication == [ApprovalInput.DESCRIPTION_LINKS]


def test_routing_vocabulary_separates_a_recorded_url_from_a_working_link():
    from app.publishing.vocabulary import RoutingStatus

    cfg = publishing_config()
    assert [s.value for s in cfg.routing.statuses] == ["url_recorded", "link_created", "clickability_verified", "manual_action_required", "unsupported"]
    assert set(RoutingStatus) == set(cfg.routing.statuses)


def test_routing_rules_lead_to_films_and_claim_nothing_unverified_as_automatic():
    from app.publishing.vocabulary import Evidence, RoutingMethod

    cfg = publishing_config()
    known = {str(d.destination) for d in cfg.destinations}
    for rule in cfg.routing.rules:
        assert rule.source in known and rule.target in known and rule.target.endswith("/film")
        if rule.automatable:
            assert rule.method is RoutingMethod.PLAIN_TEXT_CTA and rule.evidence is Evidence.CONFIRMED
    related = next(r for r in cfg.routing_rules("youtube/short") if r.method is RoutingMethod.RELATED_VIDEO)
    assert related.automatable is False and related.target == "youtube/film"
    back = next(r for r in cfg.routing_rules("youtube/film") if r.target == "facebook/film")
    assert back.after_publication is True and back.method is RoutingMethod.DESCRIPTION_LINK
    assert {r.source for r in cfg.routing.rules} == {"youtube/short", "facebook/short", "facebook/film", "youtube/film", "instagram/short", "tiktok/short"}


def test_scheduling_defaults_put_the_film_first_and_store_utc():
    s = publishing_config().scheduling
    assert s.store_as == "utc" and s.film_before_shorts is True
    assert [k.value for k in s.queue_order] == ["scheduled_at", "queue_position"]


def test_monitoring_events_use_the_monitoring_pages_own_severities():
    from app.documentary.monitoring import RANK
    from app.publishing.vocabulary import EventCategory, Severity

    m = publishing_config().monitoring
    assert m.area == "publishing" and set(m.events) == set(EventCategory)
    assert {s.value for s in Severity} == set(RANK)
    for category, event in m.events.items():
        if event.severity is Severity.PROBLEM:
            assert event.alert, f"{category} is a problem and must alert"
    assert m.events[EventCategory.EMERGENCY_STOP_BLOCKED].alert and m.events[EventCategory.PUBLICATION_UNCERTAIN].severity is Severity.PROBLEM


def test_the_configuration_holds_no_credentials():
    text = CONFIG_PATH.read_text(encoding="utf-8").lower()
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                keys.add(str(k).lower())
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(text))
    assert not [k for k in keys if any(w in k for w in ("token", "secret", "password", "credential", "api_key", "apikey"))]
    assert "bearer " not in text and "oauth " not in text


def test_the_social_distribution_file_and_the_release_plan_are_unchanged_in_behaviour():
    from app.shortform.release_plan import load_config

    social = load_config()
    assert social["campaign"]["publish_time_local"] == "18:00" and social["campaign"]["timezone"] == "Europe/Berlin" and social["campaign"]["days"] == 10
    assert set(social["platforms"]) == {"youtube_short", "instagram_reel", "facebook_reel", "tiktok_video"}
    assert len(social["schedule_template"]) == 10
    assert all(len(langs) == 9 for langs in social["accounts"].values())
    assert social["accounts"]["youtube_short"]["fa"]["account_id"] == "UC3bIVrg7dD6B9_JlSHduPLw"


def test_cover_and_short_clip_configuration_is_untouched():
    from app.core.ai_config import ai_config

    assert ai_config.covers_settings().shorts_per_episode == 5
    assert len(ai_config.creative_pack.clip_keys) == 5 and ai_config.short_clips_settings().video.width == 1080


def test_a_missing_or_unreadable_configuration_file_is_an_error(tmp_path, monkeypatch):
    """The loader is called past its cache: clearing the shared cache would hand other tests a different config object than the one they hold."""
    import app.publishing.config as module

    load = module.publishing_config.__wrapped__
    monkeypatch.setattr(module, "CONFIG_PATH", tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        load()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(module, "CONFIG_PATH", broken)
    with pytest.raises(json.JSONDecodeError):
        load()


@pytest.mark.parametrize("change, message", [
    (lambda d: d["destinations"][0]["capabilities"].pop("native_scheduling"), "every capability must be stated"),
    (lambda d: d["destinations"][2]["capabilities"]["native_scheduling"].update(evidence="confirmed"), "cannot be confirmed"),
    (lambda d: d["destinations"][0]["capabilities"]["public_posting"].update(evidence="guessed"), "evidence"),
    (lambda d: d["destinations"][0]["restrictions"].append("private_until_audit"), "restriction is listed twice"),
    (lambda d: d["destinations"][0]["restrictions"].append("made_up"), "restrictions"),
    (lambda d: d["modes"].update(default="automatic"), "default publishing mode is manual"),
    (lambda d: d["modes"]["automatic_requires"].remove("emergency_stop_clear"), "every requirement exactly once"),
    (lambda d: d["approval"]["required"].remove("publication"), "content and publication"),
    (lambda d: d["approval"].update(scope="platform"), "scope"),
    (lambda d: d["approval"]["unaffected_after_publication"].append("video"), "cannot both void"),
    (lambda d: d["scheduling"]["queue_order"].pop(), "every key exactly once"),
    (lambda d: d["scheduling"].update(store_as="local"), "store_as"),
    (lambda d: d["routing"]["methods"].pop(), "every method exactly once"),
    (lambda d: d["routing"]["statuses"].pop(), "every status exactly once"),
    (lambda d: d["routing"]["rules"].append(dict(d["routing"]["rules"][0])), "listed twice"),
    (lambda d: d["routing"]["rules"][0].update(target="youtube/short"), "leads to a film"),
    (lambda d: d["routing"]["rules"][0].update(source="vimeo/short"), "does not exist"),
    (lambda d: d["routing"]["rules"][0].update(method="carrier_pigeon"), "method"),
    (lambda d: d["monitoring"]["events"].pop("window_missed"), "every event category"),
    (lambda d: d["monitoring"]["events"]["window_missed"].update(severity="critical"), "severity"),
    (lambda d: d["languages"].update(FA={"enabled": False}), "two-letter lower-case"),
    (lambda d: d["languages"].update(eng={"enabled": False}), "two-letter lower-case"),
])
def test_a_broken_phase_one_contract_is_refused(change, message):
    data = raw()
    change(data)
    with pytest.raises(ValidationError, match=message):
        PublishingConfig.model_validate(data)

import asyncio
import random
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.db.models import AppSetting, ClosingClip, SharedReelAsset
from app.shortform import closing_library as CL

LANG = "zz"
CTA = "Want to hear true crime stories? Follow us."
IDENTITY = "I'm Fereidoun, and here I tell you real true crime stories."


@pytest.fixture(autouse=True)
def zz(db_session, monkeypatch, tmp_path):
    cfg = ai_config.shorts_host_led
    monkeypatch.setitem(cfg.outro_sentence, LANG, CTA)
    monkeypatch.setitem(cfg.identity_line, LANG, IDENTITY)
    monkeypatch.setitem(ai_config.voice.languages, LANG, ai_config.voice.languages["en"])
    monkeypatch.setattr(cfg.closing_library, "seed", None)
    monkeypatch.setattr(CL, "ROOT", tmp_path)
    _clean(db_session)
    yield
    _clean(db_session)


def _clean(db):
    db.rollback()
    db.query(ClosingClip).filter_by(language=LANG).delete()
    db.query(SharedReelAsset).filter_by(language=LANG).delete()
    db.query(AppSetting).filter_by(key=CL.SETTINGS_KEY).delete()
    db.commit()


def look(n: int, outfit: str = "Brown sweater", position: str = "SEATED CAM01_FRONT WIDE"):
    return CL.LookSpec(look_id=n, heygen_look_id=f"hg-{n}", outfit_label=outfit,
                       position=position)


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def estimate(self, language, sentences):
        return CL.CostEstimate(characters=sum(len(s) for s in sentences), voice_eur=0.01,
                               seconds=10.0, avatar_eur=0.33)

    async def render(self, db, language, sentences, look_spec, dest, case_id):
        self.calls.append((language, tuple(sentences), look_spec.look_id, case_id))
        dest.write_bytes(b"mp4:" + str(look_spec.look_id).encode())
        return CL.RenderedClip(duration_seconds=10.5, cost_eur=0.34)


def run(coro):
    return asyncio.run(coro)


def make(db, n, renderer=None, **kw):
    renderer = renderer or FakeRenderer()
    return run(CL.create_variant(db, LANG, CL.KIND_SHORTS, look(n, **kw), renderer, case_id=1))


# -- sentences and order ----------------------------------------------------------------------


def test_the_closing_is_the_outro_sentence_then_the_identity_line_by_default():
    assert CL.closing_sentences(LANG, CL.KIND_SHORTS) == [CTA, IDENTITY]


def test_the_order_is_a_setting(monkeypatch):
    monkeypatch.setattr(ai_config.shorts_host_led, "closing_order", "identity_then_cta")
    assert CL.closing_sentences(LANG, CL.KIND_SHORTS) == [IDENTITY, CTA]


def test_the_long_film_kind_is_the_identity_line_alone():
    assert CL.closing_sentences(LANG, CL.KIND_LONG) == [IDENTITY]


def test_the_identity_line_is_the_same_for_every_short_of_a_language(db_session):
    a, b = make(db_session, 1), make(db_session, 2)
    assert a.sentences_json == b.sentences_json


# -- keys -------------------------------------------------------------------------------------


def test_the_key_changes_with_every_part_of_it():
    base = CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY], look(1), "voice-a")
    assert base == CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY], look(1), "voice-a")
    assert base != CL.clip_key(LANG, CL.KIND_SHORTS, [IDENTITY, CTA], look(1), "voice-a")   # order
    assert base != CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY + "!"], look(1), "voice-a")
    assert base != CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY], look(1), "voice-b")
    assert base != CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY], look(2), "voice-a")
    assert base != CL.clip_key(LANG, CL.KIND_LONG, [CTA, IDENTITY], look(1), "voice-a")
    other_position = look(1, position="STANDING CAM01_FRONT WIDE")
    assert base != CL.clip_key(LANG, CL.KIND_SHORTS, [CTA, IDENTITY], other_position, "voice-a")


def test_a_changed_sentence_keeps_old_clips_but_never_selects_them(db_session, monkeypatch):
    make(db_session, 1)
    assert len(CL.current_clips(db_session, LANG, CL.KIND_SHORTS)) == 1
    monkeypatch.setitem(ai_config.shorts_host_led.identity_line, LANG, IDENTITY + " Really.")
    assert CL.current_clips(db_session, LANG, CL.KIND_SHORTS) == []
    assert db_session.query(ClosingClip).filter_by(language=LANG).count() == 1   # kept


def test_a_changed_voice_makes_the_old_clip_stale(db_session, monkeypatch):
    make(db_session, 1)
    voice = ai_config.voice.languages[LANG]
    monkeypatch.setitem(ai_config.voice.languages, LANG, voice.model_copy(update={"voice_id": "new"}))
    assert CL.current_clips(db_session, LANG, CL.KIND_SHORTS) == []


# -- library rows and the shared store ----------------------------------------------------------


def test_a_variant_is_stored_with_its_metadata_in_the_shared_store(db_session):
    clip = make(db_session, 1)
    assert (clip.language, clip.kind, clip.heygen_look_id) == (LANG, "shorts", "hg-1")
    assert (clip.duration_seconds, clip.cost_eur) == (10.5, 0.34)
    row = db_session.query(SharedReelAsset).filter_by(asset_key=clip.asset_key).one()
    assert row.asset_type == "closing_clip" and (CL.ROOT / row.file_path).read_bytes() == b"mp4:1"


def test_creating_the_same_variant_twice_renders_once(db_session):
    renderer = FakeRenderer()
    a = make(db_session, 1, renderer)
    b = make(db_session, 1, renderer)
    assert a.id == b.id and len(renderer.calls) == 1


# -- selection ---------------------------------------------------------------------------------


def use(db, clip):
    CL.record_use(db, clip)


def test_selection_never_returns_the_variant_used_last(db_session):
    clips = [make(db_session, n) for n in (1, 2, 3)]
    last = None
    for seed in range(30):
        picked = CL.select_clip(db_session, LANG, CL.KIND_SHORTS, rng=random.Random(seed))
        assert picked.id != getattr(last, "id", None)
        use(db_session, picked)
        last = picked
    assert {c.id for c in clips} >= {last.id}


def test_selection_prefers_the_least_recently_used(db_session):
    clips = [make(db_session, n) for n in (1, 2, 3, 4)]
    for clip in clips[:3]:
        use(db_session, clip)
    picked = CL.select_clip(db_session, LANG, CL.KIND_SHORTS, rng=random.Random(0))
    assert picked.id == clips[3].id      # never used: the only one in the oldest half


def test_selection_covers_every_variant_over_time(db_session):
    clips = [make(db_session, n) for n in (1, 2, 3, 4)]
    seen = set()
    rng = random.Random(7)
    for _ in range(12):
        picked = CL.select_clip(db_session, LANG, CL.KIND_SHORTS, rng=rng)
        use(db_session, picked)
        seen.add(picked.id)
    assert seen == {c.id for c in clips}


def test_one_stored_variant_is_used_again_rather_than_none(db_session):
    only = make(db_session, 1)
    use(db_session, only)
    assert CL.select_clip(db_session, LANG, CL.KIND_SHORTS).id == only.id


def test_the_seed_in_config_makes_the_choice_repeatable(db_session, monkeypatch):
    for n in (1, 2, 3, 4, 5):
        make(db_session, n)
    monkeypatch.setattr(ai_config.shorts_host_led.closing_library, "seed", 11)
    first = [CL.select_clip(db_session, LANG, CL.KIND_SHORTS).id for _ in range(3)]
    second = [CL.select_clip(db_session, LANG, CL.KIND_SHORTS).id for _ in range(3)]
    assert first == second


def test_an_empty_library_selects_nothing(db_session):
    assert CL.select_clip(db_session, LANG, CL.KIND_SHORTS) is None


def test_using_a_clip_counts_in_the_shared_ledger(db_session):
    clip = make(db_session, 1)
    use(db_session, clip)
    use(db_session, clip)
    row = db_session.query(SharedReelAsset).filter_by(asset_key=clip.asset_key).one()
    assert row.use_count == 2 and row.last_used_at is not None


# -- settings ----------------------------------------------------------------------------------


def test_settings_default_to_the_config_and_persist(db_session):
    s = CL.ClosingSettings(db_session)
    assert (s.use_stored, s.create_new) == (True, True)
    s.update(use_stored=False)
    assert (CL.ClosingSettings(db_session).use_stored, CL.ClosingSettings(db_session).create_new) == (False, True)
    s.update(create_new=False)
    assert CL.ClosingSettings(db_session).as_dict() == {"use_stored": False, "create_new": False}


# -- resolve: the four combinations ---------------------------------------------------------------


def resolve(db, renderer, looks, **kw):
    return run(CL.resolve_closing(db, LANG, CL.KIND_SHORTS, case_id=1, renderer=renderer,
                                  looks=looks, **kw))


def settings(db, use_stored, create_new):
    CL.ClosingSettings(db).update(use_stored=use_stored, create_new=create_new)


def test_stored_on_creation_on_renders_a_missing_variant_then_uses_the_library(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.shorts_host_led.closing_library, "target_variants", 2)
    renderer = FakeRenderer()
    looks = [look(1), look(2), look(3)]
    settings(db_session, True, True)
    first = resolve(db_session, renderer, looks)
    assert first.rendered and len(renderer.calls) == 1
    second = resolve(db_session, renderer, looks)
    assert len(renderer.calls) == 2                       # still below the target of 2
    third = resolve(db_session, renderer, looks)
    assert not third.rendered and len(renderer.calls) == 2     # target reached: only reuse
    assert {first.clip.id, second.clip.id} <= {c.id for c in CL.current_clips(
        db_session, LANG, CL.KIND_SHORTS)}


def test_stored_on_creation_off_uses_only_what_exists(db_session):
    make(db_session, 1)
    renderer = FakeRenderer()
    settings(db_session, True, False)
    got = resolve(db_session, renderer, [look(1), look(2)])
    assert not got.rendered and renderer.calls == []


def test_stored_on_creation_off_and_empty_stops_visibly_with_no_fallback(db_session):
    renderer = FakeRenderer()
    settings(db_session, True, False)
    with pytest.raises(CL.ClosingUnavailable) as err:
        resolve(db_session, renderer, [look(1)])
    assert str(err.value) == (f"No stored closing clip for {LANG}; enable creation or approve "
                              "more looks")
    assert renderer.calls == []


def test_stored_on_creation_on_but_no_approved_look_stops_visibly(db_session):
    settings(db_session, True, True)
    with pytest.raises(CL.ClosingUnavailable, match="approve more looks"):
        resolve(db_session, FakeRenderer(), [])


def test_stored_off_creation_on_renders_a_fresh_clip_and_leaves_the_library_alone(db_session):
    renderer = FakeRenderer()
    make(db_session, 1, renderer)
    settings(db_session, False, True)
    got = resolve(db_session, renderer, [look(1)], fresh_dir=CL.ROOT / "fresh")
    assert got.rendered and got.clip is None and got.path.read_bytes().startswith(b"mp4:")
    assert len(renderer.calls) == 2
    assert db_session.query(ClosingClip).filter_by(language=LANG).count() == 1


def test_stored_off_creation_off_renders_nothing(db_session):
    renderer = FakeRenderer()
    settings(db_session, False, False)
    with pytest.raises(CL.ClosingUnavailable, match="creation is off"):
        resolve(db_session, renderer, [look(1)], fresh_dir=CL.ROOT / "fresh")
    assert renderer.calls == []


# -- spread over outfits and positions -----------------------------------------------------------


def test_missing_variants_spread_over_outfits_and_positions(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.shorts_host_led.closing_library, "target_variants", 3)
    make(db_session, 1, outfit="A")
    looks = [look(1, "A"), look(2, "A"), look(3, "B"), look(4, "C", "STANDING CAM01_FRONT WIDE")]
    wanted = CL.missing_looks(db_session, LANG, CL.KIND_SHORTS, looks)
    assert [w.look_id for w in wanted] == [4, 3]          # other outfits first, never past target


def test_no_missing_variants_once_the_target_is_reached(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.shorts_host_led.closing_library, "target_variants", 1)
    make(db_session, 1)
    assert CL.missing_looks(db_session, LANG, CL.KIND_SHORTS, [look(1), look(2)]) == []


# -- status table --------------------------------------------------------------------------------


def test_the_library_table_lists_variants_outfits_positions_and_uses(db_session):
    clip = make(db_session, 1)
    use(db_session, clip)
    table = CL.library_table(db_session, [LANG])
    row = table[LANG]
    assert row["variants"] == 1 and row["target"] == ai_config.shorts_host_led.closing_library.target_variants
    assert row["clips"][0]["times_used"] == 1 and row["clips"][0]["outfit"] == "Brown sweater"
    assert row["clips"][0]["position"] == "SEATED CAM01_FRONT WIDE"
    assert Path(CL.ROOT / clip_path(db_session, clip)).exists()


def clip_path(db, clip):
    return db.query(SharedReelAsset).filter_by(asset_key=clip.asset_key).one().file_path


def test_only_approved_uploaded_looks_of_the_language_are_offered(db_session):
    from app.db.models import Case, HostLook, HostOutfit

    case = Case(canonical_title="Closing looks", slug="closing-looks-test", language="en")
    db_session.add(case)
    db_session.flush()
    outfit = HostOutfit(case_id=case.id, language=LANG, name="Grey suit", status="selected")
    db_session.add(outfit)
    db_session.flush()

    def add(status, heygen, language=LANG):
        db_session.add(HostLook(studio_profile_id="x", language=language, camera="CAM01_FRONT",
                                framing="WIDE", outfit_id="o", host_outfit_id=outfit.id,
                                stance="SEATED", pose="P01", status=status, heygen_look_id=heygen))

    add("approved", "hg-ok")
    add("approved", None)
    add("requested", "hg-req")
    add("approved", "hg-other-language", language="de")
    db_session.flush()
    looks = CL.approved_looks(db_session, LANG)
    db_session.rollback()
    assert looks == [CL.LookSpec(look_id=looks[0].look_id, heygen_look_id="hg-ok",
                                 outfit_label="Grey suit", position="SEATED CAM01_FRONT WIDE")]


# -- the closing of one short (timing, manifest, disclosure) ----------------------------------------


def reel_closing(db, monkeypatch, renderer, **kw):
    from app.shortform.host_led_closing import build_reel_closing

    monkeypatch.setattr(CL, "approved_looks", lambda db_, language: [look(1)])
    args = {"hook_seconds": 6, "film_cut_seconds": 25, "cover_seconds": 1.5, **kw}
    return run(build_reel_closing(db, LANG, case_id=1, renderer=renderer, **args))


def test_a_short_gets_its_closing_timing_and_a_manifest_line(db_session, monkeypatch):
    from app.shortform.publish_manifest import build_publish_manifest

    renderer = FakeRenderer()
    clip = make(db_session, 1, renderer)
    settings(db_session, True, False)
    got = reel_closing(db_session, monkeypatch, renderer)
    assert got.choice.clip.id == clip.id and got.timing.outro_seconds == 10.5
    assert got.timing.avatar_seconds == 16.5 and got.timing.total_seconds <= 60
    assert len(renderer.calls) == 1                      # only the variant made above
    entry = got.manifest_entry(db_session)
    assert entry["source"] == "stored" and entry["reused"] is True and entry["shared_asset_use_count"] == 1
    manifest = build_publish_manifest("youtube_short", film_id=1, short_id="s1", language="en",
                                      closing=entry)
    assert manifest.closing == entry and manifest.disclosure["synthetic_avatar_voice_and_video"]


def test_a_closing_that_breaks_the_avatar_cap_or_the_minute_is_refused(db_session, monkeypatch):
    from app.shortform.host_led import ReelTimingError

    renderer = FakeRenderer()
    make(db_session, 1, renderer)
    settings(db_session, True, False)
    monkeypatch.setattr(ai_config.shorts_host_led, "avatar_max_seconds", 15)
    with pytest.raises(ReelTimingError, match="avatar time"):
        reel_closing(db_session, monkeypatch, renderer)
    monkeypatch.setattr(ai_config.shorts_host_led, "avatar_max_seconds", 22)
    with pytest.raises(ReelTimingError, match="hard max"):
        reel_closing(db_session, monkeypatch, renderer, film_cut_seconds=45)


def test_the_second_short_uses_another_stored_variant_without_any_provider_call(db_session, monkeypatch):
    renderer = FakeRenderer()
    for n in (1, 2):
        make(db_session, n, renderer)
    settings(db_session, True, False)
    first = reel_closing(db_session, monkeypatch, renderer)
    second = reel_closing(db_session, monkeypatch, renderer)
    assert first.choice.clip.id != second.choice.clip.id
    assert len(renderer.calls) == 2                      # the two made above; none for the shorts

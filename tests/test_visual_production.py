"""Visual engine, production script, critics, render engine and the
one-button documentary pipeline (end to end with scripted models, fake
voice and sound providers, real FFmpeg/OpenCV rendering)."""
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import DocumentaryJob, ProductionScript, VisualAsset, VisualPlan
from app.documentary.production.critics import apply_fixes, deterministic_checks
from app.documentary.production.script import compose
from app.documentary.visuals import rights as R
from app.documentary.visuals.director import (
    assign_motion, blocked_at, rank_candidates, sentence_marks, validate_visual_plan,
)
from app.documentary.visuals.generated import format_date
from app.documentary.visuals.images import dhash, hamming
from app.documentary.visuals.planner import research_queries, validate_requirements
from app.documentary.visuals.research import page_images
from app.documentary.blueprint import validate_blueprint
from app.providers.generation.base import GenerationResult
from test_blueprint_performance import PACK, SECTIONS, _good, _no_contradiction, _story

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


# ---------------------------------------------------------------------------
# rights, research parsing, hashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args, status", [
    (("wikimedia", "CC BY-SA 4.0"), "creative_commons"),
    (("wikimedia", "Public domain"), "public_domain"),
    (("wikimedia", "CC0"), "public_domain"),
    (("wikimedia", "CC BY-NC 2.0"), "permission_required"),
    (("source_page", None, "https://abc.net.au/x.jpg", "https://abc.net.au/news"),
     "editorial_review_required"),
    (("searxng_images", None, "https://media.gettyimages.com/p.jpg"), "do_not_use"),
    (("upload",), "owned"),
    (("generated",), "owned"),
    (("searxng_images", None, "https://example.org/a.jpg"), "unknown"),
])
def test_rights_classification(args, status):
    assert R.classify(*args)[0] == status


def test_found_does_not_mean_usable():
    assert R.allowed("editorial_review_required", "preview")
    assert not R.allowed("editorial_review_required", "publish")
    assert not R.allowed("unknown", "preview")
    assert not R.allowed("do_not_use", "preview")


def test_page_images_takes_editorial_pictures_only():
    page = """<html><head>
      <meta property="og:image" content="/img/lead.jpg">
      <meta property="og:title" content="The case">
    </head><body>
      <img src="/static/logo.png" width="900">
      <figure><img data-src="https://cdn.x/photo2.jpg" alt="alt text">
        <figcaption>The house in <b>2007</b>.</figcaption></figure>
      <img src="/tiny.jpg" width="40">
      <img src="/wide.jpg" width="800" alt="A wide picture">
    </body></html>"""
    imgs = page_images(page, "https://news.example/story")
    assert [i["url"] for i in imgs] == [
        "https://news.example/img/lead.jpg", "https://cdn.x/photo2.jpg",
        "https://news.example/wide.jpg"]
    assert imgs[1]["caption"] == "The house in 2007 ."


def test_dhash_finds_near_duplicates():
    a = Image.linear_gradient("L").rotate(90).resize((300, 200)).convert("RGB")
    b = a.resize((600, 400))
    c = a.transpose(Image.FLIP_LEFT_RIGHT)
    assert hamming(dhash(a), dhash(b)) <= 6
    assert hamming(dhash(a), dhash(c)) > 20


@pytest.mark.parametrize("lang, text, out", [
    ("en", "16 July 2007", "16 July 2007"),
    ("de", "July 16, 2007", "16. Juli 2007"),
    ("fa", "16 July 2007", "۱۶ ژوئیه ۲۰۰۷"),
    ("ar", "May 2006", "مايو 2006"),
    ("de", "early June 2007", None),
])
def test_dates_are_localized_deterministically(lang, text, out):
    assert format_date(text, lang) == out


# ---------------------------------------------------------------------------
# requirements planner validation
# ---------------------------------------------------------------------------


class _Src:
    def __init__(self, id, raw_text):
        self.id, self.raw_text = id, raw_text


def _pack():
    return {"facts": [
        {"id": "F001", "claim": "Police said the note was 'a mystery within a mystery' in 2007.",
         "supporting_text": "The detective said: The house was left spotless and quiet."},
    ], "timeline": [{"id": "T001", "event_date": "2007-07-16", "claim": "x"}]}


def _bp():
    return validate_blueprint(_good(), SECTIONS, PACK)[0]


def test_requirements_keep_only_grounded_material():
    raw = {
        "entities": [{"key": "The House!", "type": "building", "name": "The farmhouse",
                      "search_queries": ["farmhouse Nannup"]}],
        "beats": [
            {"beat_id": "B01", "requirements": [
                {"entity": "the_house", "purpose": "orientation", "priority": "high"},
                {"entity": "ghost", "purpose": "evidence"}],
             "map_place": "Nannup, Western Australia", "date_text": "16 July 2007",
             "quote": {"fact_id": "F001", "text": "The house was left spotless and quiet."},
             "document": {"source_id": 7, "passage": "the evidence does not establish"}},
            {"beat_id": "B02", "quote": {"fact_id": "F001", "text": "a mystery within a mystery"},
             "date_text": "1980", "document": {"source_id": 7, "passage": "invented words"}},
            {"beat_id": "B99"},
        ],
    }
    reqs, rep = validate_requirements(raw, _bp(), _pack(), [_Src(7, "Finally, the evidence does not establish death.")])
    b1, b2 = reqs["beats"][0], reqs["beats"][1]
    assert [r["entity"] for r in b1["requirements"]] == ["the_house"]
    assert b1["quote"]["text"].startswith("The house") and b1["document"]["source_id"] == 7
    assert b1["date_text"] == "16 July 2007"
    assert b2["quote"] is None  # a fragment is not a quote
    assert b2["date_text"] is None and b2["document"] is None
    codes = {w["code"] for w in rep["warnings"]}
    assert {"unknown_entity", "quote_dropped", "date_dropped", "document_dropped",
            "unknown_beat"} <= codes
    q = research_queries(reqs)
    assert q == [{"query": "farmhouse Nannup", "entity": "the_house", "entities": ["the_house"]}]


# ---------------------------------------------------------------------------
# reveal firewall, matching, director validation, motion
# ---------------------------------------------------------------------------


def _asset(code, **kw):
    base = dict(asset_code=code, asset_type="photo", asset_role="evidence",
                verification_status="verified", rights_status="editorial_review_required",
                local_path="x.jpg", entities_json='["the_house"]', reveals_json="[]",
                quality_score=0.8, title=None, caption=None, description=None, id=int(code[-2:]))
    base.update(kw)
    return VisualAsset(**base)


def test_reveal_firewall_blocks_only_later_reveals():
    bp = _bp()
    # B03 is a "reveal" beat revealing C001; before it, C001 is blocked
    assert "C001" in blocked_at(bp, "B01") and "C001" in blocked_at(bp, "B02")
    assert "C001" not in blocked_at(bp, "B03") and blocked_at(bp, "B05") == set()
    # F002 is revealed by a timeline beat: not a spoiler, never blocked
    assert "F002" not in blocked_at(bp, "B01")


def test_candidate_ranking_filters_rights_rejection_and_spoilers():
    req = {"entity": "the_house", "purpose": "orientation", "priority": "high",
           "acceptable_roles": ["evidence", "context"]}
    ent = {"key": "the_house", "name": "The farmhouse"}
    assets = [
        _asset("VIS_000001"),
        _asset("VIS_000002", rights_status="unknown"),
        _asset("VIS_000003", verification_status="rejected"),
        _asset("VIS_000004", reveals_json='["C001"]'),
        _asset("VIS_000005", asset_role="illustration"),
        _asset("VIS_000006", entities_json="[]", caption="An old farmhouse at dusk"),
        _asset("VIS_000007", verification_status="needs_review", quality_score=0.2),
    ]
    ranked = [a.asset_code for _, a in rank_candidates(req, ent, assets, {"C001"}, "preview")]
    assert ranked == ["VIS_000001", "VIS_000007", "VIS_000006"]


def test_visual_plan_validation_and_fallbacks():
    bp = _bp()
    reqs = {"beats": [
        {"beat_id": "B01", "requirements": [], "map_place": "Nannup", "date_text": None,
         "quote": None, "document": None},
        {"beat_id": "B02", "requirements": [], "map_place": None, "date_text": "May 2006",
         "quote": {"fact_id": "F001", "text": "A real quote here"}, "document": None},
    ]}
    a1 = _asset("VIS_000001")
    raw = {"beats": [
        {"beat_id": "B01", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "VIS_000099", "from_sentence": 0},  # not a candidate
            {"command": "SHOW_MAP", "from_sentence": 0}]},
        {"beat_id": "B02", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "from_sentence": 0},
            {"command": "SHOW_QUOTE", "from_sentence": 1},
            {"command": "SHOW_DOCUMENT", "from_sentence": 1}]},
    ]}
    marks = {"B02": sentence_marks("One two three four five six. Seven eight.")}
    plan, rep = validate_visual_plan(raw, bp, reqs, {"B01": [], "B02": ["VIS_000001"]},
                                     {"VIS_000001": a1}, marks)
    b = {x["beat_id"]: x for x in plan["beats"]}
    assert [s["command"] for s in b["B01"]["shots"]] == ["SHOW_MAP"]
    # sentence anchors -> shares (6 of 8 words, then the quote)
    assert [s["command"] for s in b["B02"]["shots"]] == ["NEW_IMAGE", "SHOW_QUOTE"]
    assert [s["share"] for s in b["B02"]["shots"]] == [0.75, 0.25]
    assert b["B03"]["shots"][0]["command"] == "KEEP_CURRENT_IMAGE"  # fallback: hold
    codes = {w["code"] for w in rep["warnings"]}
    assert {"asset_not_candidate", "no_document"} <= codes
    assert rep["fallbacks"] >= 1


def test_illustration_is_labelled_and_motion_varies():
    bp = _bp()
    assets = {f"VIS_00000{i}": _asset(f"VIS_00000{i}") for i in range(1, 6)}
    assets["VIS_000005"].asset_role = "illustration"
    raw = {"beats": [{"beat_id": f"B0{i}", "shots": [
        {"command": "NEW_IMAGE", "asset_id": f"VIS_00000{i}", "share": 1}]} for i in range(1, 6)]}
    cands = {f"B0{i}": [f"VIS_00000{i}"] for i in range(1, 6)}
    plan, _ = validate_visual_plan(raw, bp, {"beats": []}, cands, assets)
    assert plan["beats"][4]["shots"][0]["command"] == "ATMOSPHERIC_BROLL"
    assert plan["beats"][4]["shots"][0]["label"] == "illustration"
    audio = {"beats": [{"beat_id": "B02", "after": {"type": "chapter_break"}}]}
    plan = assign_motion(plan, bp, audio, assets)
    motions = [b["shots"][0]["motion"] for b in plan["beats"]]
    run = ai_config.motion.max_same_motion_run
    assert all(len(set(motions[i:i + run + 1])) > 1 for i in range(len(motions) - run))
    assert plan["beats"][2]["shots"][0]["transition_in"] == "FADE_BLACK"


# ---------------------------------------------------------------------------
# production script composition
# ---------------------------------------------------------------------------


def _manifest():
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i, w in enumerate(("In May 2006 they left. " * 20).split())]
    return {"duration_seconds": 60.0, "files": {"narration_wav": "n.wav"},
            "timeline": {"beats": [
                {"beat_id": "B01", "start": 0.0, "end": 19.0},
                {"beat_id": "B02", "start": 20.0, "end": 40.0},
                {"beat_id": "B03", "start": 41.0, "end": 59.0}],
                "blocks": [{"block_id": "b1", "start": 0, "end": 19, "pause_after_kind": "breath"},
                           {"block_id": "b2", "start": 20, "end": 59, "pause_after_kind": "end"}],
                "words": words}}


def test_compose_timeline_from_real_audio():
    assets = {"VIS_000001": _asset("VIS_000001", rights_status="creative_commons",
                                   credit="Jane / Wikimedia Commons", width=800, height=600),
              "VIS_000002": _asset("VIS_000002", asset_role="illustration", width=800, height=600)}
    plan = {"beats": [
        {"beat_id": "B01", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "share": 0.5, "motion": "SLOW_PUSH"},
            {"command": "SHOW_DATE", "share": 0.5, "overlay": {"kind": "date", "text_en": "May 2006"}}]},
        {"beat_id": "B02", "shots": [
            {"command": "ATMOSPHERIC_BROLL", "asset_id": "VIS_000002", "share": 1.0,
             "label": "illustration", "motion": "PAN_LEFT"}]},
        {"beat_id": "B03", "shots": [{"command": "BLACK_SCREEN", "share": 1.0}]},
    ]}
    texts = {"date|May 2006": "Mai 2006"}
    s = compose(_manifest(), plan, assets, texts, "de")
    shots = s["shots"]
    # the black beat stays a short pause, then a picture the story has
    # already shown takes over (never 20 s of black)
    assert [x["kind"] for x in shots] == ["image", "image", "black", "image"]
    assert shots[0]["start"] == 0 and shots[0]["end"] == 20.0  # date extends the image
    assert shots[1]["start"] == 20.0 and shots[3]["end"] == 60.0
    assert shots[2]["end"] - shots[2]["start"] == pytest.approx(ai_config.attention.max_black_seconds)
    assert shots[3]["asset_id"] == "VIS_000001" and shots[3]["black_filled"]
    assert shots[0]["transition_in"] == "FADE_BLACK"
    kinds = {o["kind"]: o for o in s["overlays"]}
    assert kinds["date"]["text"] == "Mai 2006"
    # the date appears when the narration says the year (word 2 at 1.0 s... in B01 half)
    assert kinds["date"]["start"] == pytest.approx(0.8)
    assert kinds["label"]["text"] == "Symbolbild"
    assert kinds["credit"]["text"] == "Jane / Wikimedia Commons"
    assert s["credits"] == ["Jane / Wikimedia Commons"]
    assert s["silences"] == [{"start": 19.0, "duration": 1.0, "kind": "breath"}]
    assert all(len(x["text"]) <= ai_config.render.subtitle_max_chars * 2 + 10 for x in s["subtitles"])


def test_compose_folds_flash_cuts_and_swaps_long_holds():
    assets = {f"VIS_00000{i}": _asset(f"VIS_00000{i}", width=800, height=600) for i in (1, 2, 3)}
    m = _manifest()
    m["timeline"]["beats"] = [{"beat_id": "B01", "start": 0.0, "end": 60.0}]
    plan = {"candidates": {"B01": ["VIS_000001", "VIS_000002", "VIS_000003"]}, "beats": [
        {"beat_id": "B01", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "share": 0.04},
            {"command": "NEW_IMAGE", "asset_id": "VIS_000002", "share": 0.96}]}]}
    s = compose(m, plan, assets, {}, "en")
    # the 2.4 s opening flash cut is folded into the next picture
    assert s["shots"][0]["asset_id"] == "VIS_000002" and s["shots"][0]["start"] == 0.0
    # a 60 s still becomes a sequence of the beat's pictures, never the
    # same picture twice in a row and no picture much longer than a hold
    shots = s["shots"]
    assert len(shots) >= 4 and shots[-1]["end"] == 60.0
    assert shots[1]["asset_id"] in ("VIS_000001", "VIS_000003")
    assert all(x["command"] == "NEW_IMAGE" for x in shots[1:])
    assert all(a["asset_id"] != b["asset_id"] for a, b in zip(shots, shots[1:]))
    # (a cut may move up to 4 s to land where a sentence begins)
    assert all(x["end"] - x["start"] <= ai_config.motion.max_still_seconds + 4.01 for x in shots)
    lengths = [round(x["end"] - x["start"], 1) for x in shots[1:-1]]
    assert len(set(lengths)) > 1  # never a metronome


# ---------------------------------------------------------------------------
# critics
# ---------------------------------------------------------------------------


def test_deterministic_checks_and_targeted_fixes():
    illus = _asset("VIS_000009", asset_role="illustration")
    shots = [{"index": i, "kind": "image", "start": i * 10.0, "end": i * 10.0 + 10,
              "motion": "SLOW_PUSH", "transition_in": "CROSSFADE",
              "asset_id": "VIS_000009" if i == 4 else f"VIS_00000{i}",
              "alternatives": [{"asset_id": "VIS_000007", "path": "p7.jpg", "kind": "image"}]}
             for i in range(5)]
    script = {"duration": 50.0, "shots": shots, "overlays": [], "music": []}
    rep = deterministic_checks(script, {"VIS_000009": illus})
    checks = {i["check"] for i in rep["issues"]}
    assert {"repetition", "honesty"} <= checks and rep["high"] == 1
    done = apply_fixes(script, [
        {"shot": 3, "severity": "high", "fix": "change_motion", "fix_detail": "pan_left"},
        {"shot": 3, "severity": "high", "fix": "black"},           # one fix per shot
        {"shot": 1, "severity": "medium", "fix": "replace_picture"},
        {"shot": 2, "severity": "low", "fix": "black"},            # low: ignored
        {"shot": 4, "severity": "medium", "fix": "keep_previous"},
    ])
    assert [d["fix"] for d in done] == ["replace_picture", "change_motion", "keep_previous"]
    assert script["shots"][1]["asset_id"] == "VIS_000007"
    assert script["shots"][3]["motion"] == "PAN_LEFT" and script["shots"][3]["end"] == 50.0
    assert [s["index"] for s in script["shots"]] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# render engine
# ---------------------------------------------------------------------------


def _jpg(path: Path, size=(800, 500), color=(120, 90, 60)):
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], 40):
        Image.Image.paste(img, (200, 200, 200), (x, 0, x + 4, size[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG")
    return path


@needs_ffmpeg
def test_render_engine_produces_mp4_with_audio_and_subtitles(tmp_path):
    from app.documentary.render.engine import VideoRenderer

    a = _jpg(tmp_path / "a.jpg")
    b = _jpg(tmp_path / "b.jpg", (400, 700), (40, 80, 120))  # portrait: blurred fill
    wav = tmp_path / "doc.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=6", str(wav)], check=True)
    script = {"language": "fa", "duration": 6.0, "audio": {"path": str(wav)},
              "shots": [
                  {"index": 0, "start": 0, "end": 2.5, "kind": "image", "path": str(a),
                   "width": 800, "height": 500, "motion": "SLOW_PUSH", "transition_in": "FADE_BLACK"},
                  {"index": 1, "start": 2.5, "end": 4.5, "kind": "image", "path": str(b),
                   "width": 400, "height": 700, "motion": "SUBTLE_2_5D", "transition_in": "CROSSFADE"},
                  {"index": 2, "start": 4.5, "end": 6.0, "kind": "black", "motion": "NONE",
                   "transition_in": "CROSSFADE"}],
              "overlays": [{"kind": "quote", "text": "دیگر نمی‌خواست", "start": 4.6, "end": 5.9},
                           {"kind": "label", "text": "تصویر نمادین", "start": 2.5, "end": 4.5}],
              "subtitles": [{"start": 0.2, "end": 2.0, "text": "صبح بود."}]}
    out = tmp_path / "out.mp4"
    info = VideoRenderer(320, 180).render(script, out)
    assert out.exists() and info["frames"] == 6 * ai_config.render.fps
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                              "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert streams == ["video", "audio", "subtitle"]
    assert Path(info["srt"]).read_text(encoding="utf-8").startswith("1\n00:00:00,200 --> ")


# ---------------------------------------------------------------------------
# the whole pipeline, one button
# ---------------------------------------------------------------------------


class PipelineGen:
    """Every model role scripted; delegates spoken roles to SpokenGen."""

    def __init__(self):
        from test_spoken_audio import SpokenGen

        self.spoken = SpokenGen()
        self.roles: list[str] = []

    def is_configured(self):
        return True

    @staticmethod
    def _res(role):
        return GenerationResult(text="{}", model=f"m/{role}", provider="fake")

    async def generate_text(self, role, system, user, images=None):
        self.roles.append(role)
        return await self.spoken.generate_text(role, system, user)

    async def generate_structured(self, role, system, user, images=None):
        self.roles.append(role)
        if role in ("spoken_meaning_checker", "spoken_style_critic"):
            return await self.spoken.generate_structured(role, system, user)
        if role == "narrative_director":
            return _no_contradiction(_good()), self._res(role)
        if role.startswith("host_"):
            from test_host import HostGen
            self.__dict__.setdefault("host", HostGen())
            return await self.host.generate_structured(role, system, user)
        if role == "audio_director":
            from test_spoken_audio import _good_plan
            return _good_plan(), self._res(role)
        if role == "visual_planner":
            beats = [{"beat_id": f"B0{i}", "requirements": [
                {"entity": "the_house", "purpose": "orientation", "priority": "high"}],
                "map_place": "Nannup, Western Australia" if i == 2 else None,
                "date_text": None, "quote": None, "document": None} for i in range(1, 6)]
            return {"entities": [{"key": "the_house", "type": "building", "name": "The farmhouse",
                                  "search_queries": ["farmhouse"]}], "beats": beats}, self._res(role)
        if role == "visual_verifier":
            assert images and images[0].startswith("data:image/jpeg;base64,")
            return {"depicts": "The farmhouse", "subject_type": "building",
                    "matches_claim": "yes", "role": "evidence", "period_ok": "yes",
                    "entities": ["the_house"], "reveals": [], "quality": 0.8,
                    "watermark": False, "graphic_or_sensitive": False,
                    "confidence": 0.9}, self._res(role)
        if role == "visual_director":
            data = json.loads(user)
            out = []
            for b in data["beats"]:
                shots = []
                if b["candidates"]:
                    shots.append({"command": "NEW_IMAGE", "from_sentence": 0,
                                  "asset_id": b["candidates"][0]["asset_id"]})
                if b["map_place"]:
                    shots.append({"command": "SHOW_MAP",
                                  "from_sentence": len(b["narration"]) - 1 if shots else 0})
                out.append({"beat_id": b["beat_id"], "attention": {"listen": 0.8},
                            "shots": shots or [{"command": "KEEP_CURRENT_IMAGE"}]})
            return {"beats": out}, self._res(role)
        if role == "voice_performance_director":
            if "INPUT:\n" not in user:
                data = json.loads(user)
                return {"incident_beat": "B02", "beats": [
                    {"beat_id": b["beat_id"], "level": 1, "peak": 2}
                    for b in data["beats"]]}, self._res(role)
            data = json.loads(user.split("INPUT:\n", 1)[1])
            return {"sentences": [
                {"i": x["i"], "level": b["arc_level"], "tts": f"[softly] {x['text']}"}
                for b in data["beats"] for x in b["sentences"]]}, self._res(role)
        if role == "overlay_localizer":
            data = json.loads(user)
            return {"texts": {k: f"{v} ({data['language']})" for k, v in data["texts"].items()}}, \
                self._res(role)
        if role.endswith("_critic"):
            return {"score": 80, "problems": [], "summary": "fine"}, self._res(role)
        raise AssertionError(f"unexpected role {role}")


@pytest.fixture
def documentary_env(tmp_path, monkeypatch):
    from test_spoken_audio import FakeSound
    from test_voice_render import FakeASR, FakeTTS
    from app.documentary.music import DocumentaryMixer, MusicLibrary
    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(ai_config.music_library, "dir", str(tmp_path / "lib"))
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.render, "width", 320)
    monkeypatch.setattr(ai_config.render, "height", 180)
    gen = PipelineGen()
    for mod in ("blueprint", "audio_director", "spoken", "visuals.planner",
                "visuals.verification", "visuals.director", "visuals.generated",
                "production.critics", "voice_performance", "pronunciation", "host"):
        monkeypatch.setattr(f"app.documentary.{mod}.get_generation_provider", lambda: gen)
    tts = FakeTTS()
    gen.tts = tts
    monkeypatch.setattr("app.documentary.production.audio.VoiceRenderer",
                        lambda: VoiceRenderer(provider=tts, asr=FakeASR()))
    monkeypatch.setattr("app.documentary.production.audio.DocumentaryMixer",
                        lambda: DocumentaryMixer(MusicLibrary(provider=FakeSound())))

    async def fake_research(self, db, case, queries, progress=None):
        from app.documentary import storage
        from app.documentary.visuals.research import next_asset_code

        assert queries[0]["entity"] == "the_house"
        for i in range(2):
            code = next_asset_code(db)
            p = _jpg(storage.visuals_dir(case.id) / f"{code}.jpg", color=(90 + i * 40, 80, 70))
            t = _jpg(storage.thumbs_dir(case.id) / f"{code}.jpg", (200, 125))
            db.add(VisualAsset(case_id=case.id, asset_code=code, provider="source_page",
                               rights_status="editorial_review_required", asset_role="evidence",
                               title=f"The farmhouse {i}", local_path=str(p), thumbnail_path=str(t),
                               width=800, height=500, entities_json='["the_house"]'))
            db.commit()
        return {"added": 2}

    async def fake_geocode(place, client=None, near=None):
        return {"lat": -34.15, "lon": 115.67, "display_name": place}

    async def fake_map(lat, lon, zoom, out, W=2400, H=1350, client=None):
        _jpg(Path(out), (W // 4, H // 4), (30, 40, 50))
        return {"path": str(out), "marker": [W // 8, H // 8]}

    monkeypatch.setattr("app.documentary.visuals.research.VisualResearchAgent.run", fake_research)
    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.geocode", fake_geocode)
    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.render_map", fake_map)
    return gen


@needs_ffmpeg
def test_one_button_pipeline_pilot_end_to_end(db_session, documentary_env):
    from app.documentary import jobs as J

    case, master = _story(db_session)
    job = J.create_job(db_session, case, master, ["en", "de"], "pilot", 80.0, "preview")
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    stages = {s["name"]: s for s in json.loads(job.stages_json)}
    assert job.status == "completed", (job.error, stages)
    assert job.progress == 1.0
    assert all(s["status"] in ("done", "skipped") for s in stages.values())
    film = stages["film_length"]["detail"]
    assert film["warning"] and set(film["estimated_minutes"]) == {"en", "de"}
    assert stages["visual_check"]["detail"]["verified"] == 2
    result = json.loads(job.result_json)
    for lang in ("en", "de"):
        r = result["renders"][lang]
        assert Path(r["path"]).exists() or (Path.cwd() / r["path"]).exists()
        ps = db_session.get(ProductionScript, r["production_script_id"])
        script = json.loads(ps.script_json)
        assert script["language"] == lang and ps.status == "rendered"
        assert {s["kind"] for s in script["shots"]} >= {"map", "image"}
        place = next(o for o in script["overlays"] if o["kind"] == "place")
        assert place["text"] == ("Nannup" if lang == "en" else "Nannup (de)")
        assert json.loads(ps.critique_json)["score"] == 80
    vp = db_session.query(VisualPlan).filter_by(case_id=case.id).one()
    assert vp.status == "planned" and set(json.loads(vp.plan_json)["texts"]) == {"en", "de"}
    # the narrator's performance (v3 audio tags) reached the voice
    assert stages["performance:en"]["detail"]["incident_beat"] == "B02"
    assert stages["voice:en"]["detail"]["audio_tags"] is True
    # the on-screen host: one plan, dialogue per language
    assert stages["host_plan"]["detail"]["segments"] == ["S1:opening", "S2:mid@B03"]
    assert {stages[f"host:{l}"]["detail"]["status"] for l in ("en", "de")} == {"valid"}
    tagged = [r for r in documentary_env.tts.requests if "[softly]" in r.text]
    assert tagged and all(r.model_id == "eleven_v3" for r in documentary_env.tts.requests)
    # a re-run resumes without new model calls
    roles_before = len(documentary_env.roles)
    job2 = J.create_job(db_session, case, master, ["en"], "pilot", 80.0, "preview")
    asyncio.run(J.run_job(job2.id))
    db_session.expire_all()
    new_roles = documentary_env.roles[roles_before:]
    assert set(new_roles) <= {"automation_feel_critic", "attention_critic",
                              "visual_accuracy_critic", "production_critic"}


@needs_ffmpeg
def test_a_failing_language_does_not_stop_the_others(db_session, documentary_env, monkeypatch):
    from app.documentary import jobs as J

    real = documentary_env.spoken.generate_text

    async def flaky(role, system, user):
        if role == "spoken_writer" and "German" in system:
            raise RuntimeError("writer down for German")
        return await real(role, system, user)

    monkeypatch.setattr(documentary_env.spoken, "generate_text", flaky)
    case, master = _story(db_session)
    job = J.create_job(db_session, case, master, ["en", "de"], "pilot", 60.0, "preview")
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    stages = {s["name"]: s["status"] for s in json.loads(job.stages_json)}
    assert job.status == "partial" and "writer down for German" in job.error
    assert stages["spoken:de"] == "failed" and stages["render:de"] == "blocked"
    assert stages["render:en"] == "done"
    assert set(json.loads(job.result_json)["renders"]) == {"en"}


def test_full_film_refuses_short_stories(db_session, documentary_env):
    from app.documentary import jobs as J

    case, master = _story(db_session)
    job = J.create_job(db_session, case, master, ["en"], "full", None, "publish")
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    assert job.status == "failed" and "Film length outside 45" in job.error
    stages = {s["name"]: s["status"] for s in json.loads(job.stages_json)}
    assert stages["film_length"] == "failed" and stages["render:en"] == "pending"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_documentary_api(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    launched = []
    monkeypatch.setattr("app.documentary.jobs.launch", lambda job_id: launched.append(job_id))
    case, master = _story(db_session)
    s = client.get("/api/documentary/settings").json()
    assert s["film_minutes"] == {"min": 45.0, "max": 120.0}
    assert "api_key" not in json.dumps(s).lower()
    ov = client.get(f"/api/cases/{case.id}/documentary").json()
    assert ov["master_version_id"] == master.id and ov["blueprint"] is None
    r = client.post(f"/api/cases/{case.id}/documentary/jobs",
                    json={"languages": ["en", "xx"], "mode": "pilot", "pilot_seconds": 60})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["languages"] == ["en"] and launched == [job["id"]]
    assert job["stages"][0] == {"name": "blueprint", "status": "pending", "detail": None}
    again = client.post(f"/api/cases/{case.id}/documentary/jobs", json={"mode": "pilot"})
    assert again.status_code == 409
    assert client.post(f"/api/documentary/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    # visual library: upload, list, review, file
    img = tmp_path / "up.jpg"
    _jpg(img)
    with open(img, "rb") as fh:
        up = client.post(f"/api/cases/{case.id}/visuals/upload",
                         files={"file": ("up.jpg", fh, "image/jpeg")},
                         data={"title": "Family photo", "role": "evidence", "rights": "owned"})
    assert up.status_code == 200, up.text
    asset = up.json()
    assert asset["rights"] == "owned" and asset["usable_publish"] is True
    lst = client.get(f"/api/cases/{case.id}/visuals?q=family").json()
    assert [a["id"] for a in lst] == [asset["id"]]
    p = client.patch(f"/api/visuals/{asset['id']}", json={"verification": "verified",
                                                          "role": "context"}).json()
    assert p["verification"] == "verified" and p["human_override"] is True
    assert client.patch(f"/api/visuals/{asset['id']}", json={"rights": "stolen"}).status_code == 422
    f = client.get(asset["thumbnail_url"])
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/cases/{case.id}/documentary/visual-plan").status_code == 404
    assert client.get(f"/api/cases/{case.id}/documentary/production/en").status_code == 404
    music = client.get("/api/documentary/music").json()
    assert {m["id"] for m in music} >= {"bed_mystery", "sting_reveal"}


def test_ambiguous_place_names_resolve_near_the_case(tmp_path, monkeypatch):
    from app.documentary.visuals import maps as MAPS

    monkeypatch.setattr(MAPS, "cache_dir", lambda: tmp_path)
    calls = []

    async def fake_get(client, url, params=None):
        calls.append(params)

        class R:
            @staticmethod
            def json():
                return [{"lat": "51.64", "lon": "11.13", "display_name": "Wilhelmshof, Harzgerode"},
                        {"lat": "52.53", "lon": "11.60", "display_name": "Wilhelmshof, Uchtspringe"}]
        return R()

    monkeypatch.setattr(MAPS, "_polite_get", fake_get)
    far = asyncio.run(MAPS.geocode("Wilhelmshof"))
    near = asyncio.run(MAPS.geocode("Wilhelmshof", near=(52.54, 11.59)))
    assert far["display_name"].endswith("Harzgerode")
    assert near["display_name"].endswith("Uchtspringe")
    assert len(calls) == 1 and calls[0]["limit"] == 5  # cached
    # a result that is not that place is no map at all
    assert asyncio.run(MAPS.geocode("Wilhelmshof 188")) is None

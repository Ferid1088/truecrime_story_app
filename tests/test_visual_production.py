"""Visual engine, production script, critics, render engine and the
one-button documentary pipeline (end to end with scripted models, fake
voice and sound providers, real FFmpeg/OpenCV rendering)."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from test_blueprint_performance import PACK, SECTIONS, _good, _no_contradiction, _story

from app.core.ai_config import ai_config
from app.db.models import DocumentaryJob, ProductionScript, VisualAsset, VisualPlan
from app.documentary.blueprint import validate_blueprint
from app.documentary.production.critics import apply_fixes, deterministic_checks
from app.documentary.production.script import compose
from app.documentary.visuals import rights as R
from app.documentary.visuals.director import (
    assign_motion,
    blocked_at,
    rank_candidates,
    sentence_marks,
    validate_visual_plan,
)
from app.documentary.visuals.generated import format_date
from app.documentary.visuals.images import dhash, hamming
from app.documentary.visuals.planner import research_queries, validate_requirements
from app.documentary.visuals.research import page_images
from app.providers.generation.base import GenerationResult

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


# ---------------------------------------------------------------------------
# rights, research parsing, hashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, status",
    [
        (("wikimedia", "CC BY-SA 4.0"), "creative_commons"),
        (("wikimedia", "Public domain"), "public_domain"),
        (("wikimedia", "CC0"), "public_domain"),
        (("wikimedia", "CC BY-NC 2.0"), "permission_required"),
        (
            ("source_page", None, "https://abc.net.au/x.jpg", "https://abc.net.au/news"),
            "editorial_review_required",
        ),
        (("searxng_images", None, "https://media.gettyimages.com/p.jpg"), "do_not_use"),
        (("upload",), "owned"),
        (("generated",), "owned"),
        (("searxng_images", None, "https://example.org/a.jpg"), "unknown"),
    ],
)
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
        "https://news.example/img/lead.jpg",
        "https://cdn.x/photo2.jpg",
        "https://news.example/wide.jpg",
    ]
    assert imgs[1]["caption"] == "The house in 2007 ."


def test_dhash_finds_near_duplicates():
    a = Image.linear_gradient("L").rotate(90).resize((300, 200)).convert("RGB")
    b = a.resize((600, 400))
    c = a.transpose(Image.FLIP_LEFT_RIGHT)
    assert hamming(dhash(a), dhash(b)) <= 6
    assert hamming(dhash(a), dhash(c)) > 20


@pytest.mark.parametrize(
    "lang, text, out",
    [
        ("en", "16 July 2007", "16 July 2007"),
        ("de", "July 16, 2007", "16. Juli 2007"),
        ("fa", "16 July 2007", "۱۶ ژوئیه ۲۰۰۷"),
        ("ar", "May 2006", "مايو 2006"),
        ("de", "early June 2007", None),
    ],
)
def test_dates_are_localized_deterministically(lang, text, out):
    assert format_date(text, lang) == out


# ---------------------------------------------------------------------------
# requirements planner validation
# ---------------------------------------------------------------------------


class _Src:
    def __init__(self, id, raw_text):
        self.id, self.raw_text = id, raw_text


def _pack():
    return {
        "facts": [
            {
                "id": "F001",
                "claim": "Police said the note was 'a mystery within a mystery' in 2007.",
                "supporting_text": "The detective said: The house was left spotless and quiet.",
            },
        ],
        "timeline": [{"id": "T001", "event_date": "2007-07-16", "claim": "x"}],
    }


def _bp():
    return validate_blueprint(_good(), SECTIONS, PACK)[0]


def test_requirements_keep_only_grounded_material():
    raw = {
        "entities": [
            {
                "key": "The House!",
                "type": "building",
                "name": "The farmhouse",
                "search_queries": ["farmhouse Nannup"],
            }
        ],
        "beats": [
            {
                "beat_id": "B01",
                "requirements": [
                    {"entity": "the_house", "purpose": "orientation", "priority": "high"},
                    {"entity": "ghost", "purpose": "evidence"},
                ],
                "map_place": "Nannup, Western Australia",
                "date_text": "16 July 2007",
                "quote": {"fact_id": "F001", "text": "The house was left spotless and quiet."},
                "document": {"source_id": 7, "passage": "the evidence does not establish"},
            },
            {
                "beat_id": "B02",
                "quote": {"fact_id": "F001", "text": "a mystery within a mystery"},
                "date_text": "1980",
                "document": {"source_id": 7, "passage": "invented words"},
            },
            {"beat_id": "B99"},
        ],
    }
    reqs, rep = validate_requirements(
        raw, _bp(), _pack(), [_Src(7, "Finally, the evidence does not establish death.")]
    )
    b1, b2 = reqs["beats"][0], reqs["beats"][1]
    assert [r["entity"] for r in b1["requirements"]] == ["the_house"]
    assert b1["quote"]["text"].startswith("The house") and b1["document"]["source_id"] == 7
    assert b1["date_text"] == "16 July 2007"
    assert b2["quote"] is None  # a fragment is not a quote
    assert b2["date_text"] is None and b2["document"] is None
    codes = {w["code"] for w in rep["warnings"]}
    assert {
        "unknown_entity",
        "quote_dropped",
        "date_dropped",
        "document_dropped",
        "unknown_beat",
    } <= codes
    q = research_queries(reqs)
    assert q == [
        {
            "query": "farmhouse Nannup",
            "entity": "the_house",
            "entities": ["the_house"],
            "entity_type": "building",
            "kind": "exact",
            "footage": False,
        }
    ]


# ---------------------------------------------------------------------------
# reveal firewall, matching, director validation, motion
# ---------------------------------------------------------------------------


def _asset(code, **kw):
    base = dict(
        asset_code=code,
        asset_type="photo",
        asset_role="evidence",
        verification_status="verified",
        rights_status="editorial_review_required",
        local_path="x.jpg",
        entities_json='["the_house"]',
        reveals_json="[]",
        quality_score=0.8,
        title=None,
        caption=None,
        description=None,
        id=int(code[-2:]),
    )
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
    req = {
        "entity": "the_house",
        "purpose": "orientation",
        "priority": "high",
        "acceptable_roles": ["evidence", "context"],
    }
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
    reqs = {
        "beats": [
            {
                "beat_id": "B01",
                "requirements": [],
                "map_place": "Nannup",
                "date_text": None,
                "quote": None,
                "document": None,
            },
            {
                "beat_id": "B02",
                "requirements": [],
                "map_place": None,
                "date_text": "May 2006",
                "quote": {"fact_id": "F001", "text": "A real quote here"},
                "document": None,
            },
        ]
    }
    a1 = _asset("VIS_000001")
    raw = {
        "beats": [
            {
                "beat_id": "B01",
                "shots": [
                    {
                        "command": "NEW_IMAGE",
                        "asset_id": "VIS_000099",
                        "from_sentence": 0,
                    },  # not a candidate
                    {"command": "SHOW_MAP", "from_sentence": 0},
                ],
            },
            {
                "beat_id": "B02",
                "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "from_sentence": 0},
                    {"command": "SHOW_QUOTE", "from_sentence": 1},
                    {"command": "SHOW_DOCUMENT", "from_sentence": 1},
                ],
            },
        ]
    }
    marks = {"B02": sentence_marks("One two three four five six. Seven eight.")}
    # (an opening about the place may show its map first; see
    # test_visual_direction for the other openings)
    plan, rep = validate_visual_plan(
        raw,
        bp,
        reqs,
        {"B01": [], "B02": ["VIS_000001"]},
        {"VIS_000001": a1},
        marks,
        opening="important_location",
    )
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
    raw = {
        "beats": [
            {
                "beat_id": f"B0{i}",
                "shots": [{"command": "NEW_IMAGE", "asset_id": f"VIS_00000{i}", "share": 1}],
            }
            for i in range(1, 6)
        ]
    }
    cands = {f"B0{i}": [f"VIS_00000{i}"] for i in range(1, 6)}
    plan, _ = validate_visual_plan(raw, bp, {"beats": []}, cands, assets)
    assert plan["beats"][4]["shots"][0]["command"] == "ATMOSPHERIC_BROLL"
    assert plan["beats"][4]["shots"][0]["label"] == "illustration"
    audio = {"beats": [{"beat_id": "B02", "after": {"type": "chapter_break"}}]}
    plan = assign_motion(plan, bp, audio, assets)
    motions = [b["shots"][0]["motion"] for b in plan["beats"]]
    run = ai_config.motion.max_same_motion_run
    assert all(len(set(motions[i : i + run + 1])) > 1 for i in range(len(motions) - run))
    assert plan["beats"][2]["shots"][0]["transition_in"] == "FADE_BLACK"


# ---------------------------------------------------------------------------
# production script composition
# ---------------------------------------------------------------------------


def _manifest():
    words = [
        {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
        for i, w in enumerate(("In May 2006 they left. " * 20).split())
    ]
    return {
        "duration_seconds": 60.0,
        "files": {"narration_wav": "n.wav"},
        "timeline": {
            "beats": [
                {"beat_id": "B01", "start": 0.0, "end": 19.0},
                {"beat_id": "B02", "start": 20.0, "end": 40.0},
                {"beat_id": "B03", "start": 41.0, "end": 59.0},
            ],
            "blocks": [
                {"block_id": "b1", "start": 0, "end": 19, "pause_after_kind": "breath"},
                {"block_id": "b2", "start": 20, "end": 59, "pause_after_kind": "end"},
            ],
            "words": words,
        },
    }


def test_compose_timeline_from_real_audio():
    assets = {
        "VIS_000001": _asset(
            "VIS_000001",
            rights_status="creative_commons",
            credit="Jane / Wikimedia Commons",
            width=800,
            height=600,
        ),
        "VIS_000002": _asset("VIS_000002", asset_role="illustration", width=800, height=600),
    }
    plan = {
        "beats": [
            {
                "beat_id": "B01",
                "shots": [
                    {
                        "command": "NEW_IMAGE",
                        "asset_id": "VIS_000001",
                        "share": 0.5,
                        "motion": "SLOW_PUSH",
                    },
                    {
                        "command": "SHOW_DATE",
                        "share": 0.5,
                        "overlay": {"kind": "date", "text_en": "May 2006"},
                    },
                ],
            },
            {
                "beat_id": "B02",
                "shots": [
                    {
                        "command": "ATMOSPHERIC_BROLL",
                        "asset_id": "VIS_000002",
                        "share": 1.0,
                        "label": "illustration",
                        "motion": "PAN_LEFT",
                    }
                ],
            },
            {"beat_id": "B03", "shots": [{"command": "BLACK_SCREEN", "share": 1.0}]},
        ]
    }
    texts = {"date|May 2006": "Mai 2006"}
    s = compose(_manifest(), plan, assets, texts, "de")
    shots = s["shots"]
    # no picture twice: with nothing new to show, the picture on screen
    # stays, and the black beat becomes a short pause at its end (never
    # 20 s of black, never a return of an earlier picture)
    assert [x["kind"] for x in shots] == ["image", "image", "black"]
    assert shots[0]["start"] == 0 and shots[0]["end"] == 20.0  # date extends the image
    assert shots[1]["asset_id"] == "VIS_000002" and shots[1]["start"] == 20.0
    assert "no picture twice" in shots[1]["held_reason"]
    assert shots[2]["end"] == 60.0
    assert shots[2]["end"] - shots[2]["start"] == pytest.approx(
        ai_config.attention.max_black_seconds
    )
    # credits and the illustration label follow the final cut
    creds = [o for o in s["overlays"] if o["kind"] == "credit"]
    assert [(o["start"], o["end"]) for o in creds] == [(0.0, 20.0)]
    assert [(o["start"], o["end"]) for o in s["overlays"] if o["kind"] == "label"] == [
        (20.0, shots[1]["end"])
    ]
    assert shots[0]["transition_in"] == "FADE_BLACK"
    kinds = {o["kind"]: o for o in s["overlays"]}
    assert kinds["date"]["text"] == "Mai 2006"
    # the date appears when the narration says the year (word 2 at 1.0 s... in B01 half)
    assert kinds["date"]["start"] == pytest.approx(0.8)
    assert kinds["label"]["text"] == "Symbolbild"
    assert kinds["credit"]["text"] == "Jane / Wikimedia Commons"
    assert s["credits"] == ["Jane / Wikimedia Commons"]
    assert s["silences"] == [{"start": 19.0, "duration": 1.0, "kind": "breath"}]
    assert all(
        len(x["text"]) <= ai_config.render.subtitle_max_chars * 2 + 10 for x in s["subtitles"]
    )


def test_compose_folds_flash_cuts_and_swaps_long_holds():
    assets = {f"VIS_00000{i}": _asset(f"VIS_00000{i}", width=800, height=600) for i in (1, 2, 3)}
    m = _manifest()
    m["timeline"]["beats"] = [{"beat_id": "B01", "start": 0.0, "end": 60.0}]
    plan = {
        "candidates": {"B01": ["VIS_000001", "VIS_000002", "VIS_000003"]},
        "beats": [
            {
                "beat_id": "B01",
                "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "share": 0.04},
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000002", "share": 0.96},
                ],
            }
        ],
    }
    s = compose(m, plan, assets, {}, "en")
    # the 2.4 s opening flash cut is folded into the next picture
    assert s["shots"][0]["asset_id"] == "VIS_000002" and s["shots"][0]["start"] == 0.0
    # a 60 s still becomes a sequence of the beat's pictures — each picture
    # once; when they are all shown, the last one stays
    shots = s["shots"]
    assert [x["asset_id"] for x in shots] == ["VIS_000002", "VIS_000001", "VIS_000003"]
    assert shots[-1]["end"] == 60.0 and "no picture twice" in shots[-1]["held_reason"]
    assert all(x["command"] == "NEW_IMAGE" for x in shots[1:])
    # (a cut may move up to 4 s to land where a sentence begins)
    assert all(
        x["end"] - x["start"] <= ai_config.motion.max_still_seconds + 4.01 for x in shots[:-1]
    )


# ---------------------------------------------------------------------------
# critics
# ---------------------------------------------------------------------------


def test_deterministic_checks_and_targeted_fixes():
    illus = _asset("VIS_000009", asset_role="illustration")
    shots = [
        {
            "index": i,
            "kind": "image",
            "start": i * 10.0,
            "end": i * 10.0 + 10,
            "motion": "SLOW_PUSH",
            "transition_in": "CROSSFADE",
            "asset_id": "VIS_000009" if i == 4 else f"VIS_00000{i}",
            "alternatives": [{"asset_id": "VIS_000007", "path": "p7.jpg", "kind": "image"}],
        }
        for i in range(5)
    ]
    script = {"duration": 50.0, "shots": shots, "overlays": [], "music": []}
    rep = deterministic_checks(script, {"VIS_000009": illus})
    checks = {i["check"] for i in rep["issues"]}
    assert {"repetition", "honesty"} <= checks and rep["high"] == 1
    done = apply_fixes(
        script,
        [
            {"shot": 3, "severity": "high", "fix": "change_motion", "fix_detail": "pan_left"},
            {"shot": 3, "severity": "high", "fix": "black"},  # one fix per shot
            {"shot": 1, "severity": "medium", "fix": "replace_picture"},
            {"shot": 2, "severity": "low", "fix": "black"},  # low: ignored
            {"shot": 4, "severity": "medium", "fix": "keep_previous"},
        ],
    )
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
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=6",
            str(wav),
        ],
        check=True,
    )
    script = {
        "language": "fa",
        "duration": 6.0,
        "audio": {"path": str(wav)},
        "shots": [
            {
                "index": 0,
                "start": 0,
                "end": 2.5,
                "kind": "image",
                "path": str(a),
                "width": 800,
                "height": 500,
                "motion": "SLOW_PUSH",
                "transition_in": "FADE_BLACK",
            },
            {
                "index": 1,
                "start": 2.5,
                "end": 4.5,
                "kind": "image",
                "path": str(b),
                "width": 400,
                "height": 700,
                "motion": "SUBTLE_2_5D",
                "transition_in": "CROSSFADE",
            },
            {
                "index": 2,
                "start": 4.5,
                "end": 6.0,
                "kind": "black",
                "motion": "NONE",
                "transition_in": "CROSSFADE",
            },
        ],
        "overlays": [
            {"kind": "quote", "text": "دیگر نمی‌خواست", "start": 4.6, "end": 5.9},
            {"kind": "label", "text": "تصویر نمادین", "start": 2.5, "end": 4.5},
        ],
        "subtitles": [{"start": 0.2, "end": 2.0, "text": "صبح بود."}],
    }
    out = tmp_path / "out.mp4"
    info = VideoRenderer(320, 180).render(script, out)
    assert out.exists() and info["frames"] == 6 * ai_config.render.fps
    streams = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
    ).stdout.split()
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
        from mock_payload import generation_request_payload, provider_request, request_language

        fingerprint = provider_request(
            "generation",
            role,
            request_language(user, system),
            "generation.text",
            request=generation_request_payload(role, system, user, images),
        )
        self.roles.append(role)
        result = await self.spoken.generate_text(role, system, user)
        result.text += f"\n\n[MOCK_REQUEST_SHA256:{fingerprint}]"
        return result

    async def generate_structured(self, role, system, user, images=None):
        from mock_payload import generation_request_payload, provider_request, request_language

        fingerprint = provider_request(
            "generation",
            role,
            request_language(user, system),
            "generation.structured",
            request=generation_request_payload(role, system, user, images),
        )
        self.roles.append(role)
        if role in ("spoken_meaning_checker", "spoken_style_critic"):
            data, result = await self.spoken.generate_structured(role, system, user)
            data["_mock_request_sha256"] = fingerprint
            return data, result
        if role == "fact_extractor":
            payload = json.loads(user)
            return {
                "facts": [
                    {
                        "claim": f"Research report {index + 1} documents farmhouse evidence.",
                        "category": "evidence",
                        "confidence": 0.9,
                        "source_ids": [source["id"]] if source.get("id") else [],
                        "disputed": False,
                        "event_date": None,
                        "original_language": "en",
                        "narrative_value": "scene_detail",
                        "evidence_strength": "strong_secondary",
                        "supporting_text": source.get("text") or source.get("summary"),
                        "chunk_ids": [],
                        "quote_status": "paraphrase",
                        "people": [],
                        "locations": ["Nannup"],
                    }
                    for index, source in enumerate(payload.get("sources") or [])
                ],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "case_naming_agent":
            language = {
                "English": "en", "German": "de", "Persian (Farsi)": "fa", "Arabic": "ar",
            }[json.loads(user)["language"]]
            prefixes = {
                "en": "The Farmhouse Signal",
                "de": "Das Farmhaus-Signal",
                "fa": "نشانه خانه روستایی",
                "ar": "إشارة البيت الريفي",
            }
            count = json.loads(user)["count"]
            return {
                "editorial_concept": "the evidence left behind",
                "candidates": [
                    {"title": f"{prefixes[language]} {i}", "angle": "evidence"}
                    for i in range(1, count + 1)
                ],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role in ("case_title_critic", "native_title_critic"):
            data = json.loads(user)
            scores = []
            for title in data["titles"]:
                scores.append({
                    "title": title,
                    "specificity": 0.8,
                    "memorability": 0.8,
                    "curiosity": 0.8,
                    "documentary_tone": 0.8,
                    "sensationalism_risk": 0.1,
                    "spoiler_risk": 0.1,
                    "epistemic_risk": 0.1,
                    "native_quality": 0.9,
                    "literal_translation": False,
                    "reason": "ok",
                })
            return {"scores": scores, "_mock_request_sha256": fingerprint}, self._res(role)
        if role == "timeline_builder":
            return {"timeline": [], "_mock_request_sha256": fingerprint}, self._res(role)
        if role == "contradiction_analyzer":
            return {"contradictions": [], "_mock_request_sha256": fingerprint}, self._res(role)
        if role == "narrative_director":
            data, result = _no_contradiction(_good()), self._res(role)
            data["_mock_request_sha256"] = fingerprint
            return data, result
        if role.startswith("host_"):
            from test_host import HostGen

            self.__dict__.setdefault("host", HostGen())
            data, result = await self.host.generate_structured(role, system, user)
            data["_mock_request_sha256"] = fingerprint
            return data, result
        if role == "audio_director":
            from test_spoken_audio import _good_plan

            data, result = _good_plan(), self._res(role)
            data["_mock_request_sha256"] = fingerprint
            return data, result
        if role == "visual_planner":
            beats = [
                {
                    "beat_id": f"B0{i}",
                    "requirements": [
                        {"entity": "the_house", "purpose": "orientation", "priority": "high"}
                    ],
                    "map_place": "Nannup, Western Australia" if i == 2 else None,
                    "date_text": None,
                    "quote": None,
                    "document": None,
                }
                for i in range(1, 6)
            ]
            return {
                "entities": [
                    {
                        "key": "the_house",
                        "type": "building",
                        "name": "The farmhouse",
                        "search_queries": ["farmhouse"],
                    }
                ],
                "beats": beats,
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "visual_verifier":
            assert images and images[0].startswith("data:image/jpeg;base64,")
            return {
                "depicts": "The farmhouse",
                "subject_type": "building",
                "matches_claim": "yes",
                "role": "evidence",
                "period_ok": "yes",
                "entities": ["the_house"],
                "reveals": [],
                "quality": 0.8,
                "watermark": False,
                "graphic_or_sensitive": False,
                "confidence": 0.9,
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "visual_director":
            data = json.loads(user)
            out = []
            for b in data["beats"]:
                shots = []
                if b["candidates"]:
                    shots.append(
                        {
                            "command": "NEW_IMAGE",
                            "from_sentence": 0,
                            "asset_id": b["candidates"][0]["asset_id"],
                        }
                    )
                if b["map_place"]:
                    shots.append(
                        {
                            "command": "SHOW_MAP",
                            "from_sentence": len(b["narration"]) - 1 if shots else 0,
                        }
                    )
                out.append(
                    {
                        "beat_id": b["beat_id"],
                        "attention": {"listen": 0.8},
                        "shots": shots or [{"command": "KEEP_CURRENT_IMAGE"}],
                    }
                )
            return {"beats": out, "_mock_request_sha256": fingerprint}, self._res(role)
        if role == "voice_performance_director":
            if "INPUT:\n" not in user:
                data = json.loads(user)
                return {
                    "incident_beat": "B02",
                    "beats": [
                        {"beat_id": b["beat_id"], "level": 1, "peak": 2} for b in data["beats"]
                    ],
                    "_mock_request_sha256": fingerprint,
                }, self._res(role)
            data = json.loads(user.split("INPUT:\n", 1)[1])
            return {
                "sentences": [
                    {"i": x["i"], "level": b["arc_level"], "tts": f"[softly] {x['text']}"}
                    for b in data["beats"]
                    for x in b["sentences"]
                ],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "overlay_localizer":
            data = json.loads(user)
            return {
                "texts": {k: f"{v} ({data['language']})" for k, v in data["texts"].items()},
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "chapter_writer":
            data = json.loads(user)
            langs = data.get("languages") or [data["language"]]
            if os.environ.get("SOUNDNESS_CHAPTER_PAYLOAD") == "1":
                # A top-level _mock_request_sha256 is discarded by
                # ChapterWriter._parse. Carry the COMPLETE hash in valid,
                # consumed text fields for the soundness proof (46 chars).
                import base64

                token = base64.urlsafe_b64encode(bytes.fromhex(fingerprint)).decode().rstrip("=")
                return {
                    "film_title": {lg: f"{lg}:{token}" for lg in langs},
                    "chapters": [
                        {"act_id": c["act_id"], "title": {lg: f"{lg}:{token}" for lg in langs}}
                        for c in data["chapters"]
                    ],
                    "events": [
                        {"id": e["id"], "label": {lg: f"{lg}:{token}" for lg in langs}}
                        for e in data["events"]
                    ],
                }, self._res(role)
            return {
                "film_title": {lg: f"The farmhouse ({lg})" for lg in langs},
                "chapters": [
                    {
                        "act_id": c["act_id"],
                        "title": {lg: f"Part {c['number']} ({lg})" for lg in langs},
                    }
                    for c in data["chapters"]
                ],
                "events": [
                    {"id": e["id"], "label": {lg: f"Event {e['id']} ({lg})" for lg in langs}}
                    for e in data["events"]
                ],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "chapter_auditor":
            data = json.loads(user)
            self.cards_audited = sorted(data["texts"])
            return {
                "verdicts": [{"key": k, "ok": True} for k in data["texts"]],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role.endswith("_critic"):
            return {
                "score": 80,
                "problems": [],
                "summary": "fine",
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        if role == "visual_auditor":
            data = json.loads(user)
            assert images and data["narration_while_on_screen"]
            self.audited = getattr(self, "audited", 0) + 1
            return {
                "verdict": "approved",
                "as": "evidence",
                "fits_words": 0.9,
                "specific_kind_ok": True,
                "tone_ok": True,
                "person_ok": True,
                "depicts": "the farmhouse",
                "reasons": [],
                "_mock_request_sha256": fingerprint,
            }, self._res(role)
        raise AssertionError(f"unexpected role {role}")


@pytest.fixture
def documentary_env(tmp_path, monkeypatch):
    from test_spoken_audio import FakeSound
    from test_voice_render import FakeASR, FakeTTS

    from app.documentary.music import DocumentaryMixer, MusicLibrary
    from app.documentary.voice_render import VoiceRenderer

    run_root = Path(os.environ.get("SOUNDNESS_RUN_ROOT", str(tmp_path)))
    # All provider fakes must see the same canonical run-root contract even
    # when the caller did not preconfigure SOUNDNESS_RUN_ROOT; otherwise the
    # absolute pytest temp prefix becomes part of request fingerprints.
    monkeypatch.setenv("SOUNDNESS_RUN_ROOT", str(run_root))
    if os.environ.get("SOUNDNESS_NAMING_ENABLED") == "1":
        monkeypatch.setattr(ai_config.case_naming, "generate_in_pipeline", True)
    if os.environ.get("SOUNDNESS_FROM_ZERO") == "1":
        from mock_payload import provider_request

        from app.providers.base import ProviderJob

        class MockResearchProvider:
            name = "truecrime"

            def __init__(self):
                self.jobs = {}

            def is_configured(self):
                return True

            async def check_health(self):
                return True

            async def start_case_research(self, case_title, language,
                                          objective=None, research_languages=None,
                                          context=None):
                request_hash = provider_request(
                    "search_fetch", "research", language, "research.case",
                    case_title=case_title, language=language,
                    objective=objective, research_languages=research_languages,
                    context=context,
                )
                external_id = f"mock-research-{len(self.jobs) + 1}"
                extra_titles = [
                    "Witness chronology", "Property records", "Search log",
                    "Archive memorandum", "Municipal filing", "Interview transcript",
                    "Court docket", "Survey notebook", "Library clipping",
                    "Ranger journal", "Census ledger", "Transport manifest",
                ]
                extra_publishers = [
                    "North Archive", "Civic Records", "Search Bureau",
                    "Historical Ledger", "Municipal Registry", "Field Notes",
                    "County Court", "Land Survey", "Public Library",
                    "Park Service", "Census Office", "Transit Authority",
                ]
                self.jobs[external_id] = {
                    "case": {"summary": "A mocked research report for the farmhouse."},
                    "queries": [
                        {
                            "language": language or "en", "query": f"{case_title} farmhouse",
                            "purpose": "case evidence", "priority": "high", "round": 1,
                        },
                        {
                            "language": language or "en", "query": f"{case_title} investigation",
                            "purpose": "independent corroboration", "priority": "high", "round": 1,
                        },
                    ] + [
                        {
                            "language": language or "en",
                            "query": f"{case_title} corroboration {i}",
                            "purpose": "independent corroboration",
                            "priority": "normal", "round": 1,
                        }
                        for i in range(2, 8)
                    ],
                    "results": [
                        {
                            "query_idx": 0, "url": "https://research.example/farmhouse",
                            "canonical_url": "research.example/farmhouse",
                            "title": "Farmhouse evidence report", "snippet": "A primary report.",
                            "rank": 1, "accepted": True, "result_language": "en",
                            "relevance": 0.95, "credibility": 0.9, "novelty": 0.8,
                            "fetch_status": "ok", "_result_idx": 0,
                        },
                        {
                            "query_idx": 1, "url": "https://archive.example/investigation",
                            "canonical_url": "archive.example/investigation",
                            "title": "Investigation archive report", "snippet": "An independent report.",
                            "rank": 1, "accepted": True, "result_language": "en",
                            "relevance": 0.9, "credibility": 0.85, "novelty": 0.75,
                            "fetch_status": "ok", "_result_idx": 1,
                        },
                    ] + [
                        {
                            "query_idx": i,
                            "url": f"https://archive.example/report-{i}",
                            "canonical_url": f"archive.example/report-{i}",
                            "title": extra_titles[i - 2],
                            "snippet": "An independent report.", "rank": 1,
                            "accepted": True, "result_language": "en",
                            "relevance": 0.85, "credibility": 0.8, "novelty": 0.7,
                            "fetch_status": "ok", "_result_idx": i,
                        }
                        for i in range(2, 8)
                    ],
                    "sources": [
                        {
                            "url": "https://research.example/farmhouse",
                            "title": "Farmhouse evidence report",
                            "summary": "A mocked research report for the farmhouse.",
                            "full_text": "The farmhouse evidence was documented in the report.",
                            "full_text_available": True, "content_status": "full_text",
                            "source_type": "article", "language": "en",
                            "publisher": "Mock Research", "source_family": "farmhouse-report",
                            "_result_idx": 0,
                        },
                        {
                            "url": "https://archive.example/investigation",
                            "title": "Investigation archive report",
                            "summary": "An independent report about the investigation.",
                            "full_text": "The investigation archive records a separate line of evidence.",
                            "full_text_available": True, "content_status": "full_text",
                            "source_type": "article", "language": "en",
                            "publisher": "Archive Research", "source_family": "investigation-report",
                            "_result_idx": 1,
                        },
                    ] + [
                        {
                            "url": f"https://archive.example/report-{i}",
                            "title": extra_titles[i - 2],
                            "summary": "An independent report.",
                            "full_text": f"The archive records a separate line of evidence {i}.",
                            "full_text_available": True, "content_status": "full_text",
                            "source_type": "article", "language": "en",
                            "publisher": extra_publishers[i - 2],
                            "source_family": f"investigation-report-{i}",
                            "_result_idx": i,
                        }
                        for i in range(2, 8)
                    ],
                    "possible_facts": [], "possible_contradictions": [],
                    "_telemetry": {"calls": 1, "searches": 1, "fetches": 1},
                    "request_hash": request_hash,
                }
                return external_id

            async def poll(self, external_id):
                return ProviderJob(
                    external_id=external_id, status="completed", result=self.jobs[external_id]
                )

        research_provider = MockResearchProvider()
        monkeypatch.setattr("app.providers.get_research_provider", lambda: research_provider)
        monkeypatch.setattr(
            "app.services.research_jobs.get_research_provider", lambda: research_provider
        )
        monkeypatch.setattr(ai_config.documentary, "video_research", False)
        monkeypatch.setattr("app.documentary.jobs.RESEARCH_POLL_SECONDS", 0.0)
        from app.db.models import Case, ResearchResult, StoryVersion
        from app.documentary.pipeline.base import StageRunner

        original_stage = StageRunner._stage

        async def seed_researched_master(self, name, fn):
            result = await original_stage(self, name, fn)
            if name == "research":
                case = self.db.get(Case, self.job.case_id)
                # The research rows are produced by the real ingestion stage;
                # this small approved master is only the deterministic handoff
                # needed to exercise the remaining documentary stages.
                sections = [dict(section) for section in SECTIONS]
                research = self.db.query(ResearchResult).order_by(ResearchResult.id.desc()).first()
                assert research is not None
                # Keep the paragraph count unchanged: blueprint validation
                # requires one beat coverage per source paragraph.
                sections[-1]["text"] += f" Research anchor: {research.title}"
                story_text = "\n\n".join(section["text"] for section in sections)
                master = self.db.query(StoryVersion).filter_by(kind="master").first()
                if master is None:
                    master = StoryVersion(
                        case_id=case.id, version=1, kind="master", language="en"
                    )
                    self.db.add(master)
                master.narrative_angle = "{}"
                master.narrative_structure = json.dumps({
                    "sections": [
                        {**section, "words": len(section["text"].split())}
                        for section in sections
                    ]
                }, ensure_ascii=False)
                master.story_text = story_text
                master.text_hash = __import__("hashlib").sha256(story_text.encode()).hexdigest()
                master.engagement_score = 80.0
                master.status = "ready"
                self.db.commit()
            return result

        # The persistent rerun proof must consume the saved master output. A
        # reseed is valid only while constructing the initial fixture; doing
        # it on a rerun would overwrite an intentional out-of-band edit before
        # the master-story hash gate can observe it.
        if os.environ.get("SOUNDNESS_SEED_MASTER") == "1":
            monkeypatch.setattr(StageRunner, "_stage", seed_researched_master)
    monkeypatch.setattr(ai_config.voice, "work_dir", str(run_root / "audio"))
    monkeypatch.setattr(ai_config.music_library, "dir", str(run_root / "lib"))
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(run_root / "cases"))
    # Intros are generated media too. Keep them inside this run so concurrent
    # full-pipeline tests cannot race through the shared data/intros cache.
    monkeypatch.setattr(ai_config.chapters, "intro_dir", str(run_root / "intros"))
    monkeypatch.setattr(ai_config.avatar, "enabled", True)  # the host stages only run then
    monkeypatch.setattr(ai_config.render, "width", 320)
    monkeypatch.setattr(ai_config.render, "height", 180)
    gen = PipelineGen()
    gen.research_calls = []
    for mod in (
        "app.agents.blueprint",
        "app.agents.research",
        "app.agents.audio_plan",
        "app.agents.spoken",
        "app.agents.visual_planner",
        "app.agents.visual_verification",
        "app.agents.visual_director",
        "app.documentary.visuals.generated",
        "app.agents.critics",
        "app.agents.voice_performance",
        "app.documentary.pronunciation",
        "app.agents.host",
        "app.agents.visual_auditor",
        "app.agents.chapters",
    ):
        monkeypatch.setattr(f"{mod}.get_generation_provider", lambda: gen, raising=False)
    monkeypatch.setattr("app.providers.generation.get_generation_provider", lambda: gen)
    tts = FakeTTS()
    gen.tts = tts
    # Host scenes resolve their provider dynamically, unlike the production
    # audio module whose VoiceRenderer is replaced below. Keep this entire
    # pipeline hermetic: the fixture promises fake voice and sound providers.
    monkeypatch.setattr("app.providers.voice.get_voice_provider", lambda: tts)
    monkeypatch.setattr(
        "app.documentary.production.audio.VoiceRenderer",
        lambda: VoiceRenderer(provider=tts, asr=FakeASR()),
    )
    # LanguageChain imports VoiceRenderer lazily; patch the provider lookup
    # used by that class rather than relying on a module-level class symbol.
    monkeypatch.setattr("app.documentary.voice_render.get_voice_provider", lambda: tts)
    monkeypatch.setattr("app.documentary.voice_render.get_asr", lambda: FakeASR())
    monkeypatch.setattr(
        "app.documentary.production.audio.DocumentaryMixer",
        lambda: DocumentaryMixer(MusicLibrary(provider=FakeSound())),
    )

    async def fake_research(self, db, case, queries, progress=None, found_during="research", **kw):
        from mock_payload import provider_request

        from app.documentary import storage
        from app.documentary.visuals.research import next_asset_code

        assert queries[0]["entity"] == "the_house"
        fingerprint = provider_request(
            "visual_search",
            "research",
            None,
            "visual_search",
            case_id=case.id,
            queries=queries,
            found_during=found_during,
            progress=progress,
            options=kw,
        )
        gen.research_calls.append(found_during)
        for i in range(2):
            code = next_asset_code(db)
            p = _jpg(storage.visuals_dir(case.id) / f"{code}.jpg", color=(90 + i * 40, 80, 70))
            t = _jpg(storage.thumbs_dir(case.id) / f"{code}.jpg", (200, 125))
            db.add(
                VisualAsset(
                    case_id=case.id,
                    asset_code=code,
                    provider="source_page",
                    rights_status="editorial_review_required",
                    asset_role="evidence",
                    title=f"The farmhouse {i} [mock:{fingerprint}]",
                    local_path=str(p),
                    thumbnail_path=str(t),
                    width=800,
                    height=500,
                    entities_json='["the_house"]',
                )
            )
            db.commit()
        return {"added": 2, "request_hash": fingerprint}

    async def fake_geocode(place, client=None, near=None):
        from mock_payload import provider_request

        fingerprint = provider_request(
            "maps",
            "geocode",
            None,
            "geocode",
            place=place,
            client=client,
            near=near,
        )
        return {"lat": -34.15, "lon": 115.67, "display_name": place, "request_hash": fingerprint}

    async def fake_map(lat, lon, zoom, out, W=2400, H=1350, client=None):
        from mock_payload import provider_request

        fingerprint = provider_request(
            "maps",
            "render_map",
            None,
            "map",
            lat=lat,
            lon=lon,
            zoom=zoom,
            out=out,
            width=W,
            height=H,
            client=client,
        )
        _jpg(Path(out), (W // 4, H // 4), (30, 40, 50))
        return {"path": str(out), "marker": [W // 8, H // 8], "request_hash": fingerprint}

    monkeypatch.setattr("app.agents.visual_research.VisualResearchAgent.run", fake_research)
    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.geocode", fake_geocode)
    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.render_map", fake_map)
    return gen


@needs_ffmpeg
def test_one_button_pipeline_pilot_end_to_end(db_session, documentary_env, isolated_host_history):
    from app.documentary import jobs as J

    case, master = _story(db_session)
    case.resolution_status = "UNSOLVED"  # no public title without a solved/unsolved status
    db_session.commit()
    job = J.create_job(db_session, case, master, ["en", "de"], "pilot", 80.0, "preview")
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    stages = {s["name"]: s for s in json.loads(job.stages_json)}
    assert job.status in {"completed", "completed_warnings"}, (job.error, stages)
    assert "errors" not in json.loads(job.result_json), json.loads(job.result_json)
    assert job.progress == 1.0
    assert all(
        s["status"] in ("done", "skipped") or name.startswith("short_")
        for name, s in stages.items()
    )
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
        audit = json.loads(ps.audit_json)
        assert audit["status"] == "approved" and audit["approved"] >= 1
        assert all(
            sh["audit"]["verdict"] == "approved"
            for sh in script["shots"]
            if sh.get("kind") == "image"
        )
        assert stages[f"visual_audit:{lang}"]["status"] == "done"
        # after the cold open: the film's title, then chapter 1 — at the end
        # of the chapter break, the new chapter's picture before its first word
        cards = [x for x in script["shots"] if x["kind"] in ("title", "chapter")]
        assert [x["kind"] for x in cards] == ["title", "chapter"]
        assert cards[1]["card"] == {
            "label": "Chapter 1" if lang == "en" else "Kapitel 1",
            "title": f"Part 1 ({lang})",
        }
        b02 = next(b for b in script["beats"] if b["beat_id"] == "B02")
        assert abs(cards[1]["end"] - (b02["start"] - ai_config.chapters.lead_out_seconds)) < 0.01
        after = script["shots"][script["shots"].index(cards[1]) + 1]
        assert after["beat_id"] == "B02" and after["start"] == cards[1]["end"]
        # every render is remembered as a Video with status + YouTube title
        from app.db.models import Video

        video = db_session.get(Video, r["video_id"])
        assert video.production_script_id == ps.id and video.language == lang
        assert video.status_at_production == case.resolution_status
        assert video.youtube_title and r["youtube_title"] == video.youtube_title
        assert ("(Unsolved)" in video.youtube_title) if lang == "en" else video.youtube_title
    ch = stages["chapters"]["detail"]
    assert ch["status"] == "approved" and ch["chapters"] >= 1 and ch["left_out"] == []
    assert "film_title" in documentary_env.cards_audited
    vp = db_session.query(VisualPlan).filter_by(case_id=case.id).one()
    assert vp.status == "planned" and set(json.loads(vp.plan_json)["texts"]) == {"en", "de"}
    # the narrator's performance (v3 audio tags) reached the voice
    assert stages["performance:en"]["detail"]["incident_beat"] == "B02"
    assert stages["voice:en"]["detail"]["audio_tags"] is True
    # the on-screen host: one plan, dialogue per language
    assert stages["host_plan"]["detail"]["segments"] == ["S1:opening", "S2:mid@B03"]
    assert {stages[f"host:{language}"]["detail"]["status"] for language in ("en", "de")} == {
        "valid"
    }
    tagged = [r for r in documentary_env.tts.requests if "[softly]" in r.text]
    assert tagged and all(r.model_id == "eleven_v3" for r in documentary_env.tts.requests)
    # a re-run resumes without new model calls
    roles_before = len(documentary_env.roles)
    job2 = J.create_job(db_session, case, master, ["en"], "pilot", 80.0, "preview")
    asyncio.run(J.run_job(job2.id))
    db_session.expire_all()
    new_roles = documentary_env.roles[roles_before:]
    assert set(new_roles) <= {
        "automation_feel_critic",
        "attention_critic",
        "visual_accuracy_critic",
        "production_critic",
    }


@needs_ffmpeg
def test_four_language_shadow_pipeline_real_job(
    isolated_pipeline_db, db_session, documentary_env, monkeypatch
):
    """The four-language proof uses the real pipeline, with only providers mocked."""
    from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES, ARTIFACT_SCOPES
    from app.db.models import Artifact, ArtifactDep, MusicTrack, MusicUsage
    from app.documentary import jobs as J
    from app.documentary.production.script import timeline_layout
    from app.services.artifact_shadow import registry_consistency
    from app.services.artifacts import ArtifactRegistry
    from app.services.soundness import saved_output_snapshot

    SHADOW_VOLATILE_KEYS = {
        "id",
        "card_id",
        "piece_id",
        "created_at",
        "updated_at",
        "timestamp",
        "started_at",
        "finished_at",
    }

    def strip_ids(value):
        if isinstance(value, dict):
            return {k: strip_ids(v) for k, v in value.items() if k not in SHADOW_VOLATILE_KEYS}
        if isinstance(value, list):
            return [strip_ids(v) for v in value]
        return value

    monkeypatch.setattr("app.db.models.new_case_uid", lambda: "CASE_SOUNDNESS")
    # The soundness run requires every chapter provider response to carry the
    # complete request fingerprint in valid text fields.
    monkeypatch.setenv("SOUNDNESS_CHAPTER_PAYLOAD", "1")
    replay_trace = None
    if os.environ.get("SOUNDNESS_REPLAY"):
        from soundness_replay import install, install_phase5

        from app.db.models import Case, StoryVersion

        replay_trace = (install_phase5 if os.environ.get("SOUNDNESS_PHASE5") == "1" else install)(
            monkeypatch
        )
        case = db_session.query(Case).one()
        master = db_session.query(StoryVersion).filter_by(kind="master").one()
    elif os.environ.get("SOUNDNESS_FROM_ZERO") == "1":
        from app.db.models import Case
        from app.utils import slugify

        case = Case(
            canonical_title="BP SOUNDNESS RESEARCH",
            slug=slugify("BP SOUNDNESS RESEARCH"),
            language="en",
            case_uid="CASE_SOUNDNESS",
        )
        db_session.add(case)
        db_session.commit()
        master = None
    else:
        case, master = _story(db_session, title="BP SOUNDNESS")
    case.case_uid = "CASE_SOUNDNESS"
    db_session.commit()
    case.resolution_status = "UNSOLVED"
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", True)
    history_before = [
        {
            key: getattr(row, key)
            for key in (
                "track_id",
                "case_id",
                "film_key",
                "production_script_id",
                "language",
                "beat_id",
                "purpose",
                "mood",
                "start",
                "end",
                "why",
                "selection_reason",
            )
        }
        for row in db_session.query(MusicUsage).order_by(MusicUsage.id).all()
    ]
    initial_track_ids = {row.id for row in db_session.query(MusicTrack).all()}

    # The production fixture's normal fake writer is English-only. Keep the
    # real pipeline and replace only its mocked spoken output with target
    # scripts so the real fa/ar language gates and renderers execute.
    real_spoken = documentary_env.spoken.generate_text

    async def target_script(role, system, user):
        result = await real_spoken(role, system, user)
        if role == "spoken_writer":
            payload = json.loads(user.split("INPUT:\n", 1)[1])
            token = (
                "این ماجرا آرام ادامه یافت"
                if payload.get("language") == "fa"
                else "هذه القضية استمرت بهدوء"
                if payload.get("language") == "ar"
                else None
            )
            if token:
                lines = []
                for line in result.text.splitlines():
                    if line.startswith("[[ACT:"):
                        lines.append(line)
                    else:
                        words = token.split()
                        count = max(1, len(line.split()))
                        generated = [words[i % len(words)] for i in range(count)]
                        sentences = [" ".join(generated[i : i + 4]) for i in range(0, count, 4)]
                        lines.append(". ".join(sentences) + ".")
                result.text = "\n".join(lines)
        return result

    monkeypatch.setattr(documentary_env.spoken, "generate_text", target_script)
    languages = ["en", "de", "fa", "ar"]
    from_zero = os.environ.get("SOUNDNESS_FROM_ZERO") == "1"
    job = J.create_job(
        db_session, case, master, languages, "pilot", 80.0, "preview",
        from_zero=from_zero, target_minutes=20.0 if from_zero else None,
    )
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    if from_zero:
        from app.db.models import StoryVersion

        master = db_session.query(StoryVersion).filter_by(kind="master").one()
    stages = {s["name"]: s for s in json.loads(job.stages_json)}
    assert job.status in {"completed", "completed_warnings"}, (job.error, stages)
    assert "errors" not in json.loads(job.result_json), json.loads(job.result_json)
    result = json.loads(job.result_json)
    assert set(result["renders"]) == set(languages)
    for language in languages:
        script = json.loads(
            db_session.get(
                ProductionScript, result["renders"][language]["production_script_id"]
            ).script_json
        )
        assert script["language"] == language
        for shot in script.get("shots") or []:
            if shot.get("kind") == "timeline":
                xs = [label["x"] for label in shot["timeline"].get("labels") or []]
                assert xs == sorted(xs)
        assert stages[f"render:{language}"]["status"] == "done"
    # Shorts are a post-film worker and may still be running when the long
    # film's completion assertions execute; exclude those deliberately async
    # artifact types from the long-film shadow invariant.
    per_language = {
        name for name, scope in ARTIFACT_SCOPES.items()
        if scope == "per_language" and name not in {"short_script_captions", "short_render"}
    }
    rows = db_session.query(Artifact).filter(Artifact.stage_job_id == job.id).all()
    counts = {
        language: {row.type: 0 for row in rows if row.language == language}
        for language in languages
    }
    for row in rows:
        if row.language in counts:
            counts[row.language][row.type] += 1
    assert all(per_language <= set(values) for values in counts.values())
    assert all(all(n >= 1 for n in values.values()) for values in counts.values())
    print("FOUR_LANGUAGE_ARTIFACT_COUNTS", json.dumps(counts, sort_keys=True))
    hashes_before = {row.id: row.input_hash for row in rows if row.language in languages}
    assert len(hashes_before) == len([row for row in rows if row.language in languages])
    mismatches = registry_consistency(db_session, job.id)
    print("REGISTRY_MISMATCHES", json.dumps(mismatches, sort_keys=True))
    assert mismatches == []

    snapshot_roots = [
        Path(ai_config.documentary.storage_dir),
        Path(ai_config.voice.work_dir),
        Path(ai_config.music_library.dir),
        Path(ai_config.chapters.intro_dir),
    ]
    snapshot_started = time.perf_counter()
    baseline_snapshot = saved_output_snapshot(db_session, file_roots=snapshot_roots)
    baseline_seconds = time.perf_counter() - snapshot_started
    print("BASELINE_SNAPSHOT_SECONDS", round(baseline_seconds, 3))
    capture_path = os.environ.get("SOUNDNESS_CAPTURE")
    if capture_path:
        Path(capture_path).write_text(
            json.dumps(baseline_snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # The independent-run proof invokes this test in two fresh processes.
    # Returning here makes each child a single clean full pipeline run; the
    # old same-database resume comparison was not an independent-run test.
    if os.environ.get("SOUNDNESS_CHILD") == "1":
        if replay_trace is not None:
            from soundness_observation import observe

            run_root = Path(os.environ["SOUNDNESS_RUN_ROOT"])
            observation = observe(db_session, run_root)
            observation["executed_stages"], observation["source_applications"] = replay_trace
            Path(os.environ["SOUNDNESS_OBSERVATION"]).write_text(
                json.dumps(observation, sort_keys=True, ensure_ascii=False)
            )
        print(
            "INDEPENDENT_RUN",
            json.dumps(
                {
                    "artifacts": len(baseline_snapshot),
                    "seconds": round(time.perf_counter() - snapshot_started, 3),
                },
                sort_keys=True,
            ),
        )
        return

    def reachable_types(start):
        found = {start}
        changed = True
        while changed:
            changed = False
            for artifact_type, deps in ARTIFACT_DEPENDENCIES.items():
                if artifact_type in {"short_moment_selection", "short_script_captions", "short_render"}:
                    continue
                if artifact_type not in found and set(deps) & found:
                    found.add(artifact_type)
                    changed = True
        return found

    beat_row = next(row for row in rows if row.type == "beats")
    beat_affected = ArtifactRegistry(db_session).invalidate([beat_row.id], "beat text changed")
    assert set(beat_affected) == reachable_types("beats")
    fa_timing = next(row for row in rows if row.type == "audio_timing" and row.language == "fa")
    fa_take = next(
        edge.depends_on_id
        for edge in db_session.query(ArtifactDep)
        .filter(ArtifactDep.artifact_id == fa_timing.id)
        .all()
    )
    fa_affected = ArtifactRegistry(db_session).invalidate([fa_take], "fa voice duration changed")
    assert set(fa_affected) == {
        "voice_takes",
        "audio_timing",
        "music_cues",
        "captions",
        "production_script",
        "final_render",
    }
    assert all(
        row.language in (None, "fa")
        for row in db_session.query(Artifact).filter(
            Artifact.id.in_([item for values in fa_affected.values() for item in values])
        )
    )

    # A second full job with shadow disabled must preserve the film JSON once
    # generated IDs and volatile fields are removed.
    first_scripts = {
        language: strip_ids(
            json.loads(
                db_session.get(
                    ProductionScript, result["renders"][language]["production_script_id"]
                ).script_json
            )
        )
        for language in languages
    }
    # Reset the fixture's music-use history to the same declared pre-selection
    # snapshot used by the first run.  Selection reasons are part of the
    # identity comparison; they are no longer excluded.
    db_session.query(MusicUsage).delete(synchronize_session=False)
    db_session.query(MusicTrack).filter(~MusicTrack.id.in_(initial_track_ids)).delete(
        synchronize_session=False
    )
    db_session.add_all([MusicUsage(**row) for row in history_before])
    db_session.commit()
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", False)
    job_off = J.create_job(db_session, case, master, languages, "pilot", 80.0, "preview")
    asyncio.run(J.run_job(job_off.id))
    db_session.expire_all()
    off_job = db_session.get(DocumentaryJob, job_off.id)
    assert off_job.status in {"completed", "completed_warnings"}, (
        off_job.error,
        json.loads(off_job.stages_json),
    )
    assert "errors" not in json.loads(off_job.result_json), json.loads(off_job.result_json)
    off_result = json.loads(off_job.result_json)
    second_scripts = {
        language: strip_ids(
            json.loads(
                db_session.get(
                    ProductionScript, off_result["renders"][language]["production_script_id"]
                ).script_json
            )
        )
        for language in languages
    }

    def diff_values(left, right, path=""):
        if isinstance(left, dict) and isinstance(right, dict):
            differences = []
            for key in sorted(set(left) | set(right)):
                differences.extend(diff_values(left.get(key), right.get(key), f"{path}.{key}"))
            return differences
        if isinstance(left, list) and isinstance(right, list):
            differences = []
            for index, (l_item, r_item) in enumerate(zip(left, right, strict=False)):
                differences.extend(diff_values(l_item, r_item, f"{path}[{index}]"))
            if len(left) != len(right):
                differences.append(f"{path}: lengths {len(left)} != {len(right)}")
            return differences
        return [] if left == right else [f"{path}: {left!r} != {right!r}"]

    differences = diff_values(first_scripts["fa"], second_scripts["fa"])
    assert first_scripts == second_scripts, differences[:20]

    # Full-documentary mode-off golden captured from the pre-incremental ref
    # (05320d5), with only the Phase 2 mock commit applied there. Compare the
    # clean first-run snapshot, before the resume and second-job checks above.
    golden_path = Path(__file__).parent / "golden" / "documentary_pipeline_pre_incremental.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    authorized_path = Path(__file__).parent / "golden" / "authorized_deltas.json"
    authorized = json.loads(authorized_path.read_text(encoding="utf-8"))
    pilot_authorized_path = Path(__file__).parent / "golden" / "pilot_authorized_deltas.json"
    pilot_authorized = set(json.loads(pilot_authorized_path.read_text(encoding="utf-8")).get("keys", []))
    current = baseline_snapshot
    golden_diff = [
        key for key in sorted(set(golden) | set(current)) if golden.get(key) != current.get(key)
    ]
    # Prompt 18 changes the pilot's editorial target, so the intentionally
    # shorter spoken/performance/audio outputs have a separate exact-key
    # authorization list. The regular golden remains strict for everything
    # outside those declared pilot outputs.
    unauthorized_diff = [key for key in golden_diff
                         if key not in authorized and key not in pilot_authorized]
    print(
        "MODE_OFF_GOLDEN_DIFF",
        json.dumps(
            {
                "pre_ref": "05320d5",
                "different": len(golden_diff),
                "authorized": len(golden_diff) - len(unauthorized_diff),
                "unauthorized": len(unauthorized_diff),
                "keys": golden_diff[:20],
            },
            sort_keys=True,
        ),
    )
    assert unauthorized_diff == []
    # The real renderer's timeline layout is language-independent in direction.
    window = [{"id": "T1", "date": "1998"}, {"id": "T2", "date": "2007"}]
    for language in ("fa", "ar"):
        _, points = timeline_layout(window, language)
        assert [point["x"] for point in points] == sorted(point["x"] for point in points)


@needs_ffmpeg
def test_four_language_independent_run_determinism(tmp_path):
    """Compare two fresh-process, fresh-DB, fresh-output full runs."""
    from app.services.soundness import saved_request_snapshot

    root = Path(__file__).resolve().parents[1]
    runs = []
    for name in ("independent-run-a", "independent-run-b"):
        run_root = tmp_path / name
        run_root.mkdir()
        snapshot = run_root / "snapshot.json"
        payloads = run_root / "mock-payloads.jsonl"
        env = os.environ.copy()
        env.pop("TC_TEST_DATABASE_URL", None)
        env.update(
            {
                "TC_COPY_DATABASE_URL": f"sqlite:///{run_root / 'pipeline.db'}",
                "SOUNDNESS_CHILD": "1",
                "SOUNDNESS_CHAPTER_PAYLOAD": "1",
                "SOUNDNESS_RUN_ROOT": str(run_root),
                "SOUNDNESS_CAPTURE": str(snapshot),
                "SOUNDNESS_PAYLOAD_LOG": str(payloads),
                "SOUNDNESS_SNAPSHOT_VALUES": str(run_root / "snapshot-values.json"),
                "PYTHONPATH": str(root),
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                __file__ + "::test_four_language_shadow_pipeline_real_job",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        runs.append((json.loads(snapshot.read_text()), saved_request_snapshot(payloads)))
    first, second = runs
    assert first[0] == second[0]
    assert first[1] == second[1]
    print(
        "INDEPENDENT_DETERMINISM",
        json.dumps(
            {
                "runs": 2,
                "artifacts": len(first[0]),
                "provider_calls": len(first[1]),
                "snapshot_equal": True,
                "logical_request_snapshot_equal": True,
            },
            sort_keys=True,
        ),
    )


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
    r = client.post(
        f"/api/cases/{case.id}/documentary/jobs",
        json={"languages": ["en", "xx"], "mode": "pilot", "pilot_seconds": 60},
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["languages"] == ["en"] and launched == [job["id"]]
    assert job["stages"][0] == {"name": "master_approval", "status": "pending", "detail": None}
    assert job["stages"][1]["name"] == "blueprint"
    again = client.post(f"/api/cases/{case.id}/documentary/jobs", json={"mode": "pilot"})
    assert again.status_code == 409
    assert client.post(f"/api/documentary/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    # visual library: upload, list, review, file
    img = tmp_path / "up.jpg"
    _jpg(img)
    with open(img, "rb") as fh:
        up = client.post(
            f"/api/cases/{case.id}/visuals/upload",
            files={"file": ("up.jpg", fh, "image/jpeg")},
            data={"title": "Family photo", "role": "evidence", "rights": "owned"},
        )
    assert up.status_code == 200, up.text
    asset = up.json()
    assert asset["rights"] == "owned" and asset["usable_publish"] is True
    lst = client.get(f"/api/cases/{case.id}/visuals?q=family").json()
    assert [a["id"] for a in lst] == [asset["id"]]
    p = client.patch(
        f"/api/visuals/{asset['id']}", json={"verification": "verified", "role": "context"}
    ).json()
    assert p["verification"] == "verified" and p["human_override"] is True
    assert client.patch(f"/api/visuals/{asset['id']}", json={"rights": "stolen"}).status_code == 422
    f = client.get(asset["thumbnail_url"])
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/cases/{case.id}/documentary/visual-plan").status_code == 404
    assert client.get(f"/api/cases/{case.id}/documentary/production/en").status_code == 404
    music = client.get("/api/documentary/music").json()
    # config cues are imported as variant-1 library tracks
    assert {m["cue_id"] for m in music} >= {"bed_mystery", "sting_reveal"}


def test_ambiguous_place_names_resolve_near_the_case(tmp_path, monkeypatch):
    from app.documentary.visuals import maps as MAPS

    monkeypatch.setattr(MAPS, "cache_dir", lambda: tmp_path)
    calls = []

    async def fake_get(client, url, params=None):
        calls.append(params)

        class R:
            @staticmethod
            def json():
                return [
                    {"lat": "51.64", "lon": "11.13", "display_name": "Wilhelmshof, Harzgerode"},
                    {"lat": "52.53", "lon": "11.60", "display_name": "Wilhelmshof, Uchtspringe"},
                ]

        return R()

    monkeypatch.setattr(MAPS, "_polite_get", fake_get)
    far = asyncio.run(MAPS.geocode("Wilhelmshof"))
    near = asyncio.run(MAPS.geocode("Wilhelmshof", near=(52.54, 11.59)))
    assert far["display_name"].endswith("Harzgerode")
    assert near["display_name"].endswith("Uchtspringe")
    assert len(calls) == 1 and calls[0]["limit"] == 5  # cached
    # a result that is not that place is no map at all
    assert asyncio.run(MAPS.geocode("Wilhelmshof 188")) is None

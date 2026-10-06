"""Sentence-safe voice blocks: never cut a sentence or a quotation, act
boundaries are hard breaks, paragraph ends are preferred, sizes are
balanced, oversize sentences are flagged instead of cut."""
import json
import uuid

import pytest

from app.core.ai_config import VoiceBlocksConfig, ai_config
from app.documentary.voice_blocks import (
    _ends_sentence,
    _quote_open,
    plan_for_version,
    plan_voice_blocks,
    split_sentences,
)

CFG = VoiceBlocksConfig(
    min_seconds=30, target_seconds=60, max_seconds=90,
    sentence_break_penalty=0.35, short_block_penalty=4.0, context_chars=120,
)
WPM = 150  # 2.5 words per second


def _norm(t):
    return " ".join(t.split())


def _sentence(i, words=10):
    # `words` words, ending with a full stop.
    return " ".join([f"s{i}"] + ["word"] * (words - 1)) + "."


def _paragraph(start, n_sentences, words=10):
    return " ".join(_sentence(start + k, words) for k in range(n_sentences))


def _plan(sections, language="en", cfg=CFG):
    return plan_voice_blocks(sections, language, cfg=cfg, words_per_minute=WPM)


# ---------------------------------------------------------------------------
# sentence units (the language-specific traps)
# ---------------------------------------------------------------------------


def test_german_dates_and_abbreviations_are_not_sentence_ends():
    text = (
        "Am 3. März 2004 verschwand Sarah gegen 22 Uhr. Dr. Weber sagte "
        "später, er habe sie z. B. nie allein gesehen. Die Polizei fand das "
        "Auto ca. 4 km entfernt."
    )
    assert split_sentences(text, "de") == [
        "Am 3. März 2004 verschwand Sarah gegen 22 Uhr.",
        "Dr. Weber sagte später, er habe sie z. B. nie allein gesehen.",
        "Die Polizei fand das Auto ca. 4 km entfernt.",
    ]


def test_german_quote_spanning_two_sentences_stays_whole():
    text = "Er sagte: „Ich ging nach Hause. Dann war sie weg.“ Danach schwieg er."
    assert split_sentences(text, "de") == [
        "Er sagte: „Ich ging nach Hause. Dann war sie weg.“",
        "Danach schwieg er.",
    ]


def test_english_initials_and_reported_speech():
    text = (
        'On March 3rd, 2004, Sarah disappeared. "Where were you?" he asked. '
        'Det. J. Miller said: "We found the car." Nothing.'
    )
    assert split_sentences(text, "en") == [
        "On March 3rd, 2004, Sarah disappeared.",
        '"Where were you?" he asked.',
        'Det. J. Miller said: "We found the car."',
        "Nothing.",
    ]


def test_persian_speaker_intro_stays_with_its_quote():
    # Plain pysbd splits «پلیس می‌گوید:» from the quote and cuts inside
    # «…؟» — both would be mid-sentence cuts.
    text = (
        "سارا در شب سوم مارس ناپدید شد. پلیس می‌گوید: «ماشین او چهار "
        "کیلومتر دورتر پیدا شد.» او جوابی نداد."
    )
    units = split_sentences(text, "fa")
    assert units[0] == "سارا در شب سوم مارس ناپدید شد."
    assert units[1].startswith("پلیس می‌گوید: «ماشین")
    # pysbd glues the sentence after «…» onto the quote. We keep that
    # (coarser, but never a mid-sentence cut: «…» پرسید. continues).
    assert units[-1].endswith("او جوابی نداد.")
    assert not any(u.endswith(":") for u in units)
    assert all(not _quote_open(u, "fa") for u in units)


def test_persian_question_inside_quote_is_not_cut():
    units = split_sentences("«کجا بودی؟» پرسید. او جوابی نداد.", "fa")
    assert units[0].startswith("«کجا بودی؟» پرسید.")
    assert all(u.count("«") == u.count("»") for u in units)


def test_arabic_quote_and_doctor_abbreviation():
    text = (
        "سأل: «أين كنتِ؟» ولم تُجب. كانت الساعة 10.30 مساءً. "
        "قال الطبيب د. أحمد ذلك. وانتهى التحقيق."
    )
    assert split_sentences(text, "ar") == [
        "سأل: «أين كنتِ؟» ولم تُجب.",
        "كانت الساعة 10.30 مساءً.",
        "قال الطبيب د. أحمد ذلك.",
        "وانتهى التحقيق.",
    ]


def test_split_never_changes_text():
    for lang, text in {
        "en": 'He said: "Go." She went. Then  nothing.',
        "fa": "پلیس می‌گوید: «ماشین پیدا شد.» تمام.",
        "ar": "قال: «انتهى.» ثم صمت.",
        "de": "Er rief: „Halt!“ Dann Stille.",
    }.items():
        assert _norm(" ".join(split_sentences(text, lang))) == _norm(text)


# ---------------------------------------------------------------------------
# packing
# ---------------------------------------------------------------------------


def test_no_text_is_lost_and_order_is_kept():
    sections = [
        {"id": "act1", "text": "\n\n".join(
            _paragraph(p * 10, 6) for p in range(5))},
        {"id": "act2", "text": "\n\n".join(
            _paragraph(100 + p * 10, 4) for p in range(3))},
    ]
    plan = _plan(sections)
    joined = " ".join(b["text"] for b in plan["blocks"])
    assert _norm(joined) == _norm(" ".join(s["text"] for s in sections))


def test_every_block_ends_on_a_sentence_end():
    text = "\n\n".join(_paragraph(p * 20, 9, words=13) for p in range(6))
    plan = _plan([{"id": "act1", "text": text}])
    assert plan["block_count"] > 2
    for b in plan["blocks"]:
        assert _ends_sentence(b["text"]), b["text"][-40:]


def test_act_boundary_is_a_hard_break():
    sections = [
        {"id": "act1", "text": _paragraph(0, 3)},   # ~12 s, shorter than min
        {"id": "act2", "text": _paragraph(10, 3)},
    ]
    plan = _plan(sections)
    assert [b["section_id"] for b in plan["blocks"]] == ["act1", "act2"]
    assert [b["block_id"] for b in plan["blocks"]] == ["EN_act1_01", "EN_act2_01"]


def test_long_act_blocks_stay_within_limits():
    text = "\n\n".join(_paragraph(p * 10, 5) for p in range(12))  # 600 words
    plan = _plan([{"id": "act1", "text": text}])
    for b in plan["blocks"]:
        assert CFG.min_seconds <= b["est_seconds"] <= CFG.max_seconds


def test_blocks_prefer_paragraph_ends():
    # Each paragraph is 50 s (125 words): ideal blocks are whole paragraphs.
    text = "\n\n".join(_paragraph(p * 10, 5, words=25) for p in range(6))
    plan = _plan([{"id": "act1", "text": text}])
    assert all(b["ends_paragraph"] for b in plan["blocks"])


def test_no_tiny_leftover_block():
    # 100 s of text: 90 + 10 would leave a tiny tail; expect balanced halves.
    text = _paragraph(0, 25, words=10)  # 250 words = 100 s, one paragraph
    plan = _plan([{"id": "act1", "text": text}])
    secs = [b["est_seconds"] for b in plan["blocks"]]
    assert len(secs) == 2 and min(secs) >= CFG.min_seconds


def test_oversize_sentence_is_flagged_not_cut():
    long_sentence = _sentence(1, words=300)  # 120 s in one sentence
    text = _paragraph(10, 4) + " " + long_sentence + " " + _paragraph(20, 4)
    plan = _plan([{"id": "act1", "text": text}])
    oversize = [b for b in plan["blocks"] if b["oversize"]]
    assert len(oversize) == 1
    assert oversize[0]["text"] == long_sentence
    assert plan["oversize_block_ids"] == [oversize[0]["block_id"]]


def test_short_act_is_one_block():
    plan = _plan([{"id": "act1", "text": _paragraph(0, 2)}])
    assert plan["block_count"] == 1 and not plan["blocks"][0]["oversize"]


def test_tts_context_comes_from_neighbouring_blocks():
    text = "\n\n".join(_paragraph(p * 10, 5, words=25) for p in range(4))
    blocks = _plan([{"id": "act1", "text": text}])["blocks"]
    assert blocks[0]["previous_text"] == "" and blocks[-1]["next_text"] == ""
    for a, b in zip(blocks, blocks[1:]):
        assert b["previous_text"] and a["text"].endswith(b["previous_text"])
        assert a["next_text"] and b["text"].startswith(a["next_text"])
        assert len(b["previous_text"]) <= CFG.context_chars


def test_editing_one_block_changes_only_its_hash():
    paras = [_paragraph(p * 10, 5, words=25) for p in range(4)]
    before = _plan([{"id": "act1", "text": "\n\n".join(paras)}])["blocks"]
    paras[2] = paras[2].replace("word", "term", 1)  # same length
    after = _plan([{"id": "act1", "text": "\n\n".join(paras)}])["blocks"]
    assert [b["block_id"] for b in before] == [b["block_id"] for b in after]
    changed = [
        a["block_id"] for a, b in zip(before, after)
        if a["content_hash"] != b["content_hash"]
    ]
    assert len(changed) == 1


def test_language_speed_changes_duration_estimate():
    sections = [{"id": "act1", "text": _paragraph(0, 10)}]
    en = plan_voice_blocks(sections, "en", cfg=CFG)
    de = plan_voice_blocks(sections, "de", cfg=CFG)
    assert ai_config.words_per_minute_for("de") < ai_config.words_per_minute_for("en")
    assert de["total_est_seconds"] > en["total_est_seconds"]


def test_config_rejects_inconsistent_limits():
    with pytest.raises(Exception):
        VoiceBlocksConfig(min_seconds=60, target_seconds=30, max_seconds=90)


# ---------------------------------------------------------------------------
# stored versions + API
# ---------------------------------------------------------------------------


def _case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"VB Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _version(db, case, sections=None, story_text=None, fingerprint=None):
    from app.db.models import StoryVersion

    struct = {}
    if sections is not None:
        struct["sections"] = [
            dict(s, words=len(s["text"].split())) for s in sections
        ]
        story_text = "\n\n".join(s["text"] for s in sections)
    if fingerprint:
        struct["evidence_fingerprint"] = fingerprint
    v = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="{}", narrative_structure=json.dumps(struct),
        story_text=story_text, text_hash="h", engagement_score=80.0,
        status="ready",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def test_plan_for_version_uses_saved_acts(db_session):
    case = _case(db_session)
    v = _version(db_session, case, sections=[
        {"id": "act1", "text": _paragraph(0, 4)},
        {"id": "act2", "text": _paragraph(10, 4)},
    ])
    plan = plan_for_version(v)
    assert plan["structured"] is True
    assert {b["section_id"] for b in plan["blocks"]} == {"act1", "act2"}


def test_legacy_version_is_one_section(db_session):
    case = _case(db_session)
    v = _version(db_session, case, story_text=_paragraph(0, 4))
    plan = plan_for_version(v)
    assert plan["structured"] is False
    assert {b["section_id"] for b in plan["blocks"]} == {"full"}


def test_voice_blocks_endpoint_reports_evidence_freshness(client, db_session):
    from app.agents.story import current_evidence_fingerprint
    from app.db.models import Fact

    case = _case(db_session)
    db_session.add(Fact(case_id=case.id, claim="A fact.", confidence=0.9))
    db_session.commit()
    v = _version(
        db_session, case,
        sections=[{"id": "act1", "text": _paragraph(0, 4)}],
        fingerprint=current_evidence_fingerprint(db_session, case.id),
    )
    url = f"/api/cases/{case.id}/stories/{v.id}/voice-blocks"
    r = client.get(url)
    assert r.status_code == 200
    body = r.json()
    assert body["evidence_current"] is True
    assert body["blocks"][0]["block_id"] == "EN_act1_01"

    db_session.add(Fact(case_id=case.id, claim="New research.", confidence=0.9))
    db_session.commit()
    assert client.get(url).json()["evidence_current"] is False

    assert client.get(
        f"/api/cases/{case.id}/stories/999999/voice-blocks"
    ).status_code == 404

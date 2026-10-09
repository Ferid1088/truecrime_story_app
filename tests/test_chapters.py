"""Chapters and the running case timeline: chapter breaks at every act end
(audio plan), the card texts written for every language and approved by
an independent auditor (rejected -> rewritten with the reasons -> still
rejected: left out and reported), chapter/title cards at the end of the
chapter break, timeline cards that show only what the story has told,
and the renderer drawing them."""
import asyncio
import json
import uuid

import numpy as np

from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, Fact, StoryVersion
from app.documentary import chapters as CH
from app.documentary.audio_director import (
    _director_input,
    director_system_prompt,
    validate_audio_plan,
)
from app.documentary.production.critics import apply_fixes, describe_cut
from app.documentary.production.script import (
    compose,
    insert_chapter_cards,
    timeline_card,
)
from app.documentary.visuals.director import _fallback_shot
from app.providers.generation.base import GenerationResult

BP = {"beats": [
    {"id": "B01", "act_id": "act1", "purpose": "hook", "summary": "A car at the lake at dawn.",
     "words": 60, "reveals": []},
    {"id": "B02", "act_id": "act1", "purpose": "orientation", "summary": "The village.",
     "words": 120, "reveals": [], "relies_on": ["T001"]},
    {"id": "B03", "act_id": "act2", "purpose": "investigation", "summary": "The search.",
     "words": 150, "reveals": ["T002"]},
    {"id": "B04", "act_id": "act2", "purpose": "reveal", "summary": "Police arrest the brother.",
     "words": 150, "reveals": ["T003"]},
    {"id": "B05", "act_id": "act3", "purpose": "reflection", "summary": "What remains.",
     "words": 100, "reveals": []},
]}
TIMELINE = [{"id": "T001", "event_date": "1998-03-10", "claim": "Anna leaves work at 6 pm."},
            {"id": "T002", "event_date": "1998-03-12", "claim": "Anna is reported missing."},
            {"id": "T003", "event_date": "1998-04-02", "claim": "Her brother is arrested."},
            {"id": "T004", "event_date": "2001-01-01", "claim": "Never told in the film."}]


# ---------------------------------------------------------------------------
# audio plan: a chapter break at every act end
# ---------------------------------------------------------------------------


def _item(bid, kind, seconds=None):
    return {"beat_id": bid, "paragraph_breath": "normal", "bed": "none",
            "after": {"type": kind, "seconds": seconds, "mood": "investigation",
                      "why": "x"}}


def test_every_act_end_is_a_chapter_break_and_nothing_else_is():
    raw = {"beats": [_item("B01", "breath", 1.2), _item("B02", "breath", 1.2),
                     _item("B03", "chapter_break", 15), _item("B04", "sting", 3),
                     _item("B05", "end")]}
    plan, rep = validate_audio_plan(raw, BP)
    after = {pb["beat_id"]: pb["after"]["type"] for pb in plan["beats"]}
    assert after["B01"] == after["B02"] == after["B04"] == "chapter_break"
    assert after["B03"] != "chapter_break" and after["B05"] == "end"
    reasons = {(a["beat"], a["reason"]) for a in rep["adjustments"] if "reason" in a}
    assert ("B01", "title_after_cold_open") in reasons
    assert ("B02", "act_end_is_chapter_break") in reasons
    assert ("B03", "chapter_break_only_between_acts") in reasons
    lo, hi = ai_config.audio_direction.transitions["chapter_break"]
    assert all(lo <= pb["after"]["seconds"] <= hi for pb in plan["beats"]
               if pb["after"]["type"] == "chapter_break")
    view = {b["beat_id"]: b for b in _director_input(BP)["beats"]}
    assert view["B02"]["ends_act"] and view["B01"]["opening_title"]
    assert not view["B03"]["ends_act"]
    assert "ends_act" in director_system_prompt()


# ---------------------------------------------------------------------------
# what the cards are about
# ---------------------------------------------------------------------------


def test_acts_events_and_dates():
    acts = CH.acts_of(BP, {"acts": [{"id": "act2", "title": "The search"}]})
    assert [(a["act_id"], a["number"], a["first_beat"], a["last_beat"]) for a in acts] == [
        ("act1", 1, "B01", "B02"), ("act2", 2, "B03", "B04"), ("act3", 3, "B05", "B05")]
    assert acts[1]["plan_title"] == "The search"
    assert acts[1]["reveals"] == ["Police arrest the brother."]
    assert CH.cold_open(BP)
    ev = CH.told_events(BP, TIMELINE)
    assert [(e["id"], e["first_beat"]) for e in ev] == [("T001", "B02"), ("T002", "B03"),
                                                        ("T003", "B04")]  # T004: never told
    assert CH.parse_date_text("12 March 1998") == (1998, 3, 12)
    assert CH.parse_date_text("March 1998") == (1998, 3, None)
    assert CH.same_date("1998-03-12", "March 1998") and not CH.same_date("1998-03-12", "1999")
    per_beat = CH.beat_events(BP, ev, {"beats": [{"beat_id": "B05", "date_text": "March 1998"}]})
    assert per_beat["B03"]["id"] == "T002" and per_beat["B04"]["id"] == "T003"
    assert "B01" not in per_beat
    # a later beat returning to a date already told
    assert per_beat["B05"]["id"] in ("T001", "T002")
    assert CH.chapter_label(2, "de") == "Kapitel 2" and CH.chapter_label(2, "fa") == "فصل ۲"


# ---------------------------------------------------------------------------
# writer + auditor
# ---------------------------------------------------------------------------


def _setup(db):
    title = f"Chap {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en",
                resolution_status="SOLVED")
    db.add(case)
    db.commit()
    for t in TIMELINE:
        db.add(Fact(case_id=case.id, claim=t["claim"], event_date=t["event_date"],
                    category="event", confidence=0.9))
    master = StoryVersion(case_id=case.id, language="en", version=1, status="approved",
                          narrative_angle="chronological",
                          story_text="Story.", narrative_structure=json.dumps(
                              {"title": "The lake", "acts": [{"id": "act2",
                                                              "title": "The brother"}]}))
    db.add(master)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=master.id,
                            blueprint_json=json.dumps(BP), status="valid")
    db.add(bp)
    db.commit()
    return case, master, bp


class CardGen:
    """Round 1: the German title of chapter 2 gives the arrest away and
    an event label is far too long; round 2 fixes the title, never the
    label."""

    def __init__(self, fail=False):
        self.fail, self.writes, self.audits = fail, [], []

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        res = GenerationResult(text="{}", model=f"m/{role}", provider="fake")
        if self.fail:
            raise RuntimeError("model down")
        langs = data["languages"]
        if role == "chapter_writer":
            self.writes.append(data.get("fix"))
            fix = {f["key"] for f in data.get("fix") or []}
            r = len(self.writes)
            out = {"film_title": {lg: f"The lake ({lg})" for lg in langs},
                   "chapters": [], "events": []}
            for c in data["chapters"]:
                t = {lg: f"Part {c['number']}" for lg in langs}
                if c["act_id"] == "act2" and r == 1:
                    t["de"] = "Der Bruder war es"
                out["chapters"].append({"act_id": c["act_id"], "title": t})
            for e in data["events"]:
                lab = {lg: f"Event {e['id']}" for lg in langs}
                if e["id"] == "T003":
                    lab["en"] = "x" * 200
                out["events"].append({"id": e["id"], "label": lab})
            if r > 1:
                assert fix and all(k in fix for k in ("chapter:act2",))
            return out, res
        assert role == "chapter_auditor"
        self.audits.append(sorted(data["texts"]))
        verdicts = []
        for k, texts in data["texts"].items():
            if texts.get("de") == "Der Bruder war es":
                verdicts.append({"key": k, "ok": False, "languages": ["de"],
                                 "reason": "names the culprit before the reveal"})
            else:
                verdicts.append({"key": k, "ok": True})
        return {"verdicts": verdicts}, res


def test_texts_are_audited_redone_and_left_out(db_session):
    case, master, bp = _setup(db_session)
    gen = CardGen()
    row = asyncio.run(CH.ChapterWriter(gen=gen).create(db_session, case, bp, master,
                                                        ["en", "de"]))
    plan, audit = json.loads(row.plan_json), json.loads(row.audit_json)
    assert row.status == "partial"
    ch2 = next(c for c in plan["chapters"] if c["act_id"] == "act2")
    assert ch2["title"] == {"en": "Part 2", "de": "Part 2"}     # rewritten, approved
    # the fix carried the auditor's reason
    fix = next(f for f in gen.writes[1] if f["key"] == "chapter:act2")
    assert fix["languages"] == ["de"] and "culprit" in fix["auditor"]
    # only rejected items were audited again
    assert set(gen.audits[1]) == {"chapter:act2", "event:T003"}
    t3 = next(e for e in plan["events"] if e["id"] == "T003")
    assert t3["label"] == {"de": "Event T003"}                   # en left out
    assert audit["left_out"] == [{"key": "event:T003", "languages": ["en"],
                                  "reason": "en: longer than 48 characters"}]
    assert len(gen.writes) == ai_config.documentary.max_redos + 1
    assert plan["film_title"] == {"en": "The lake (en)", "de": "The lake (de)"}
    cards = CH.cards_for(row, "de")
    assert cards["chapters"][1]["label"] == "Kapitel 2" and cards["film_title"] == "The lake (de)"
    t2 = next(e for e in cards["events"] if e["id"] == "T002")
    assert t2["date_text"] == "12. März 1998" and t2["label"] == "Event T002"
    assert cards["beat_order"] == ["B01", "B02", "B03", "B04", "B05"]
    en3 = next(e for e in CH.cards_for(row, "en")["events"] if e["id"] == "T003")
    assert en3["label"] is None                                  # the date only
    assert CH.plan_covers(row, ["de"]) and not CH.plan_covers(row, ["fa"])


def test_a_failing_writer_leaves_numbers_and_dates(db_session):
    case, master, bp = _setup(db_session)
    row = asyncio.run(CH.ChapterWriter(gen=CardGen(fail=True)).create(
        db_session, case, bp, master, ["en"]))
    assert row.status == "no_texts"
    assert "model down" in json.loads(row.audit_json)["error"]
    cards = CH.cards_for(row, "en")
    assert cards["chapters"][1] == {**cards["chapters"][1], "label": "Chapter 2", "title": None}


# ---------------------------------------------------------------------------
# on the timeline
# ---------------------------------------------------------------------------

CARDS = {"cold_open": True, "film_title": "The lake",
         "beat_order": ["B01", "B02", "B03", "B04", "B05"],
         "chapters": [{"act_id": "act1", "number": 1, "first_beat": "B01", "last_beat": "B02",
                       "label": "Chapter 1", "title": "The village"},
                      {"act_id": "act2", "number": 2, "first_beat": "B03", "last_beat": "B04",
                       "label": "Chapter 2", "title": None}],
         "events": [{"id": "T001", "date": "1998-03-10", "first_beat": "B02",
                     "date_text": "10 March 1998", "year": "1998", "label": "Anna leaves work"},
                    {"id": "T002", "date": "1998-03-12", "first_beat": "B03",
                     "date_text": "12 March 1998", "year": "1998", "label": "Reported missing"},
                    {"id": "T003", "date": "1998-04-02", "first_beat": "B04",
                     "date_text": "2 April 1998", "year": "1998", "label": None}]}


def test_the_timeline_shows_only_what_was_told():
    tl = timeline_card(CARDS, "B03", "T002")
    assert [e["id"] for e in tl["events"]] == ["T001", "T002"]      # T003 not told yet
    assert tl["from_x"] == 0.0 and tl["to_x"] == 1.0 and tl["label"] == "Reported missing"
    assert timeline_card(CARDS, "B03", "T003") is None              # a later event: never
    tl4 = timeline_card(CARDS, "B04", "T003", previous="T002")
    assert tl4["from_x"] == next(e["x"] for e in tl4["events"] if e["id"] == "T002")
    assert tl4["date"] == "2 April 1998" and tl4["label"] is None


def _manifest():
    beats = [("B01", 0.0, 6.0), ("B02", 22.0, 40.0), ("B03", 58.0, 80.0), ("B04", 81.5, 95.0)]
    return {"duration_seconds": 100.0, "files": {"narration_wav": "n.wav"},
            "timeline": {"beats": [{"beat_id": b, "start": a, "end": e} for b, a, e in beats],
                         "blocks": [], "words": [], "sentences": []}}


def _shot(start, end, beat, kind="image", **kw):
    return {"beat_id": beat, "start": start, "end": end, "kind": kind, "command": "NEW_IMAGE",
            "asset_id": f"VIS_{int(start):06d}", "path": "x.jpg", "motion": "SLOW_PUSH",
            "transition_in": "CROSSFADE", **kw}


def test_cards_sit_at_the_end_of_the_chapter_break():
    spans = _manifest()["timeline"]["beats"]
    shots = [_shot(0, 21.0, "B01"), _shot(21.0, 40.0, "B02"),
             _shot(40.0, 59.0, "B02", kind="video", clip_start=5.0, clip_end=60.0),
             _shot(59.0, 100.0, "B03")]
    overlays = [{"kind": "date", "text": "1998", "start": 50.0, "end": 55.0},
                {"kind": "date", "text": "1998", "start": 30.0, "end": 33.0}]
    left = insert_chapter_cards(shots, CARDS, spans, overlays)
    lead = ai_config.chapters.lead_out_seconds
    kinds = [(s["kind"], s["start"], s["end"]) for s in shots if s["kind"] in ("title", "chapter")]
    # cold open: title + chapter 1 before B02's first word; chapter 2 before B03's
    assert [k for k, _, _ in kinds] == ["title", "chapter", "chapter"]
    assert kinds[1][2] == round(22.0 - lead, 3) and kinds[2][2] == round(58.0 - lead, 3)
    assert kinds[2][2] - kinds[2][1] == ai_config.chapters.card_seconds
    # the clip under the card was cut back; nothing of the old chapter after it
    clip = next(s for s in shots if s["kind"] == "video")
    assert clip["end"] == kinds[2][1]
    last_card = max(i for i, s in enumerate(shots) if s["kind"] == "chapter")
    after = shots[last_card + 1]
    assert after["beat_id"] == "B03" and after["start"] == kinds[2][2]
    assert [o["start"] for o in overlays] == [30.0]                 # no text over a card
    assert left == []
    # a gap too short for a card: reported, not squeezed
    short = {**CARDS, "cold_open": False}
    spans2 = [{"beat_id": "B02", "start": 0.0, "end": 10.0},
              {"beat_id": "B03", "start": 11.5, "end": 30.0}]
    shots2 = [_shot(0, 30.0, "B02")]
    left = insert_chapter_cards(shots2, short, spans2, [])
    assert left and "too short" in left[0]["why"] and len(shots2) == 1


def test_compose_puts_a_timeline_card_where_the_director_asked():
    from test_visual_production import _asset

    house = _asset("VIS_000011")
    plan = {"candidates": {}, "beats": [
        {"beat_id": "B03", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "VIS_000011", "share": 0.5},
            {"command": "SHOW_TIMELINE", "event": "T002", "share": 0.5,
             "overlay": {"kind": "date", "text_en": "12 March 1998"}}]},
        {"beat_id": "B04", "shots": [
            {"command": "SHOW_TIMELINE", "event": "T001", "share": 1.0}]}]}
    m = {"duration_seconds": 40.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B03", "start": 0.0, "end": 19.5},
                                {"beat_id": "B04", "start": 20.0, "end": 40.0}],
                      "blocks": [], "words": [], "sentences": []}}
    s = compose(m, plan, {"VIS_000011": house}, {"date|12 March 1998": "12 March 1998"}, "en",
                cards={**CARDS, "chapters": []})
    tl = [x for x in s["shots"] if x["kind"] == "timeline"]
    assert len(tl) == 2 and tl[0]["timeline"]["event"] == "T002"
    # the second card moves from where the first one stood
    assert tl[1]["timeline"]["to_x"] == next(e["x"] for e in tl[1]["timeline"]["events"]
                                             if e["id"] == "T001")
    assert tl[1]["timeline"]["from_x"] == next(e["x"] for e in tl[1]["timeline"]["events"]
                                               if e["id"] == "T002")
    # a card that cannot be made (event not told yet) falls back to the date
    plan["beats"][1]["shots"][0]["event"] = "T009"
    s2 = compose(m, plan, {"VIS_000011": house}, {}, "en", cards={**CARDS, "chapters": []})
    assert [x["kind"] for x in s2["shots"]].count("timeline") == 1


def test_director_fallback_and_critics_respect_cards():
    assert _fallback_shot({"timeline_event": {"id": "T002"}, "date_text": "1998"},
                          True)["command"] == "SHOW_TIMELINE"
    assert _fallback_shot({"date_text": "1998"}, True)["command"] == "SHOW_DATE"
    shots = [_shot(0, 10, "B01"), {"index": 1, "beat_id": "B02", "start": 10, "end": 14,
                                   "kind": "chapter", "command": "CHAPTER_CARD",
                                   "card": {"label": "Chapter 2", "title": "The search"}},
             _shot(14, 30, "B02")]
    for i, sh in enumerate(shots):
        sh["index"] = i
    script = {"duration": 30.0, "shots": shots, "candidates": {}, "overlays": []}
    done = apply_fixes(script, [{"shot": 1, "severity": "high", "fix": "black"}], {})
    assert done == [] and script["shots"][1]["kind"] == "chapter"
    assert "chapter card: Chapter 2 — The search" in describe_cut(script, {}, [])


def test_the_renderer_draws_the_cards():
    from app.documentary.render.engine import FrameMaker

    W, H = 320, 180
    script = {"language": "fa", "shots": []}
    fm = FrameMaker(script, W, H)
    ch = fm.shot_frame({"kind": "chapter", "start": 0, "end": 4,
                        "card": {"label": "فصل ۲", "title": "جست‌وجو"}}, 1.0)
    tl = timeline_card(CARDS, "B03", "T002")
    f0 = fm.shot_frame({"kind": "timeline", "start": 10, "end": 15, "timeline": tl}, 10.0)
    f1 = fm.shot_frame({"kind": "timeline", "start": 10, "end": 15, "timeline": tl}, 13.0)
    assert ch.shape == (H, W, 3) and ch.max() > 200          # text on the dark ground
    assert not np.array_equal(f0, f1)                         # the marker moves
    # right to left: the current (latest) date sits on the LEFT for Persian
    red = np.argwhere((f1[..., 0] > 180) & (f1[..., 1] < 80))
    assert len(red) and red[:, 1].mean() < W / 2

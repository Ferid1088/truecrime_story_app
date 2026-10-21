"""The ways, besides illustrations, a gap in the picture track is filled with.

They use only what the film and the case already hold, so nothing is invented and the story is
not touched:
  reframe  a real picture the film showed earlier comes back as a close-up (spaced apart, a limited
           number of times, and the visual auditor must approve it for the exact words);
  split    two opposing accounts side by side, in the beat that tells the contradiction;
  text     a spoken sentence with a quotation or a number, set large on a dark ground.
Every card is drawn from the film's own words or the case's own facts; headings and facts are in
the film's language. Each way keeps its own cap per film (visuals.fill_ways)."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, Contradiction, Fact, Source, VisualAsset
from app.documentary import storage
from app.documentary.visuals import crops
from app.documentary.visuals import images as IM
from app.documentary.visuals import scene_cards as SC
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import next_asset_code
from app.documentary.visuals.usage import asset_facts, category, limit_of
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

PROVIDER = "generated"
# Characters of the narrated words a close-up keeps as its own anchor.
ANCHOR_CHARS = 240
KIND = "fill_card"
SIDES_SYSTEM = prompt("documentary/visuals/fill/contradiction_sides_system")
HEADINGS = {"split": "Two accounts"}


def quote_worthy(text: str) -> bool:
    """A sentence worth setting large: it holds a quotation or a number, and has a fitting length."""
    cfg = ai_config.fill_ways
    text = " ".join(text.split())
    if not cfg.text_min_chars <= len(text) <= cfg.text_max_chars:
        return False
    return any(c in text for c in cfg.quote_marks) or bool(re.search(r"\d", text))


class FillWays:
    def __init__(self, db: Session, case: Case, script: dict, blueprint: dict, shots: list[dict],
                 auditor):
        self.db, self.case, self.script, self.blueprint, self.auditor = db, case, script, blueprint, auditor
        self.cfg = ai_config.fill_ways
        self.language = script.get("language") or "en"
        self.beats = {b["id"]: b for b in blueprint.get("beats") or []}
        self.done: dict[str, int] = defaultdict(int)
        self.splits: set[str] = set()
        self.appear: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self.returns: dict[str, int] = defaultdict(int)
        self.regions: dict[str, set[int]] = defaultdict(set)
        for s in shots:
            if s.get("kind") == "image" and s.get("asset_id"):
                self.appear[s["asset_id"]].append((s["start"], s["end"]))
                if s.get("crop_region") is not None:
                    self.regions[s["asset_id"]].add(s["crop_region"])
        self.assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == case.id, VisualAsset.asset_code.in_(list(self.appear)))}
        self._pack: dict | None = None
        self._headings: dict[str, str] = {}

    # -- the case's own facts and contradictions ---------------------------------------------
    def pack(self) -> dict:
        if self._pack is None:
            from app.agents.story import build_evidence_pack

            self._pack = build_evidence_pack(
                self.db.query(Fact).filter(Fact.case_id == self.case.id).all(),
                self.db.query(Contradiction).filter(Contradiction.case_id == self.case.id).all(),
                self.db.query(Source).filter(Source.case_id == self.case.id).all())
        return self._pack

    async def localized(self, items: dict[str, str]) -> dict[str, str]:
        from app.documentary.visuals.generated import localize_texts

        return await localize_texts(self.db, self.case.id, items, self.language)

    async def heading(self, kind: str) -> str:
        if kind not in self._headings:
            self._headings[kind] = (await self.localized({f"heading_{kind}": HEADINGS[kind]}))[f"heading_{kind}"]
        return self._headings[kind]

    # -- the way a slot is filled --------------------------------------------------------------
    def reserve(self, way: str) -> bool:
        if self.done[way] >= self.cfg.max_per_film.get(way, 0):
            return False
        self.done[way] += 1
        return True

    def release(self, way: str) -> None:
        self.done[way] -= 1

    async def try_slot(self, slot: tuple[float, float], beat: str | None, sentences: list[str],
                       story: dict, firewall, names: set[str]) -> dict | None:
        for way in self.cfg.order:
            if not self.reserve(way):
                continue
            got = await getattr(self, f"_{way}")(slot, beat, sentences, story, firewall, names)
            if got:
                return {**got, "way": way}
            self.release(way)
        return None

    async def _reframe(self, slot, beat, sentences, story, firewall, names) -> dict | None:
        gap = self.cfg.reuse_gap_seconds
        candidates = []
        for code, spans in self.appear.items():
            a = self.assets.get(code)
            if (a is None or a.provider == PROVIDER or a.asset_type != "photo"
                    or a.asset_role not in ("evidence", "context")
                    or self.returns[code] >= self.cfg.max_reuse_per_picture
                    or len(spans) >= limit_of(category(asset_facts(a)))
                    or json.loads(a.verification_json or "{}").get("watermark")
                    or self._region(a, code) is None):
                continue
            if any(slot[0] < e + gap and slot[1] > s - gap for s, e in spans):
                continue
            named = bool(names & set(json.loads(a.entities_json or "[]")))
            candidates.append((not named, self.returns[code], code))
        for _, _, code in sorted(candidates)[: ai_config.coverage.pool_tries]:
            a = self.assets[code]
            verdict, shown_as, reasons = await self.auditor.check(
                self.db, self.case, a, sentences, story, firewall)
            if verdict == "approved":
                region = self._region(a, code)
                assert region is not None, "a picture without a free region is not a candidate"
                self.returns[code] += 1
                self.appear[code].append(slot)
                self.regions[code].add(region)
                return {"asset": a, "as": shown_as, "reasons": reasons, "region": region,
                        "anchor": " ".join(sentences)[:ANCHOR_CHARS]}
        return None

    def _region(self, a: VisualAsset, code: str) -> int | None:
        """The part of the picture its next return shows: the face for a person, else a region it
        has not shown yet; None when nothing new is left to show."""
        if a.subject_type == "person":
            return None if crops.FACE in self.regions[code] or not self._face(a) else crops.FACE
        return crops.free_region(self.regions[code])

    @staticmethod
    def _face(a: VisualAsset):
        from app.documentary.production.script import _focus

        return _focus(a.local_path)

    async def _split(self, slot, beat, sentences, story, firewall, names) -> dict | None:
        revealed = [x for x in (self.beats.get(beat) or {}).get("reveals") or []
                    if re.fullmatch(r"C\d+", x) and x not in self.splits]
        by_id = {c["id"]: c for c in self.pack()["contradictions"]}
        for cid in revealed:
            if cid not in by_id:
                continue
            self.splits.add(cid)
            sides = await self._sides(by_id[cid])
            if not sides:
                continue
            img = SC.split(await self.heading("split"), (sides["left"]["who"], sides["left"]["says"]),
                           (sides["right"]["who"], sides["right"]["says"]), self.language)
            return {"asset": self._card(img, f"split|{cid}|{self.language}", beat, "Two accounts"),
                    "as": "context", "reasons": [f"two accounts of {by_id[cid]['topic'][:80]}"]}
        return None

    async def _sides(self, contradiction: dict) -> dict | None:
        payload = {"topic": contradiction["topic"], "description": contradiction["description"],
                   "language": self.language}
        with track_run(self.db, self.case.id, "Contradiction Sides", input_summary=contradiction["id"]) as run:
            data, res = await run_agent("visuals.contradiction_sides", get_generation_provider(),
                                        json.dumps(payload, ensure_ascii=False), system=SIDES_SYSTEM)
            stamp_run(run, res, "visual_planner")
        left, right = (data or {}).get("left"), (data or {}).get("right")
        if not (isinstance(left, dict) and isinstance(right, dict)
                and all(x.get("who") and x.get("says") for x in (left, right))):
            return None
        return {"left": {k: str(left[k]).strip() for k in ("who", "says")},
                "right": {k: str(right[k]).strip() for k in ("who", "says")}}

    async def _text(self, slot, beat, sentences, story, firewall, names) -> dict | None:
        if slot[1] - slot[0] < 3.0:
            return None
        said = " ".join(c["text"] for c in self.script.get("subtitles") or []
                        if c["start"] < slot[1] - 0.3 and c["end"] > slot[0] + 0.3)
        if not quote_worthy(said):
            return None
        img = SC.text_scene(said, self.language)
        return {"asset": self._card(img, f"text|{said}|{self.language}", beat, said[:60]),
                "as": "context", "reasons": ["the spoken sentence, set large"]}

    # -- the card as a library picture ---------------------------------------------------------
    def _card(self, img, key: str, beat: str | None, title: str) -> VisualAsset:
        from app.documentary.visuals.generated import _find_generated

        found = _find_generated(self.db, self.case.id, "photo", key)
        if found is not None:
            return found
        code = next_asset_code(self.db)
        out = storage.visuals_dir(self.case.id, "illustration") / f"{code}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out, "JPEG", quality=92)
        thumb = storage.thumbs_dir(self.case.id) / f"{code}.jpg"
        IM.save_thumbnail(img.convert("RGB"), thumb)
        tier, why = T.provisional_why(PROVIDER, "context", None)
        a = VisualAsset(
            case_id=self.case.id, asset_code=code, asset_type="photo", subject_type="other",
            title=f"Card: {title}"[:500], provider=PROVIDER, asset_role="context",
            rights_status="owned", rights_reason="typographic card made by the app from the film's own words and facts",
            verification_status="verified", verification_confidence=1.0,
            local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
            width=img.width, height=img.height, quality_score=0.7,
            relevance_tier=tier, case_relevance=T.tier_label(tier), found_during="generated",
            sha256=IM.sha256(out.read_bytes()),
            spec_json=json.dumps({"kind": KIND, "key": key, "beat_id": beat, "tier_reason": why}))
        self.db.add(a)
        self.db.commit()
        return a

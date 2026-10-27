"""The last pass over the film's pictures: a requirement that has an approved picture which is not on screen in its
beat gets it. The picture takes the place of a still of that beat that serves no requirement of its own (a filler, a
repeat, a photo that only covered a bare moment), within the film's repetition rules. A requirement whose approved
pictures are all shown as often as the rules allow stays unmet; the coverage check records it as a shortage."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.documentary.visuals.usage import UsageTracker

if TYPE_CHECKING:
    from app.documentary.mvp.direct import Item

PRIORITY = {"high": 0, "medium": 1, "low": 2}


class RequirementPass:
    def __init__(self, out_beats: list[dict], window_of: dict[str, dict], items: dict[str, list[Item]],
                 required: dict[str, list[dict]], tracker: UsageTracker, blocked: Callable[[str, Item], bool],
                 fits: Callable[[Item, float], bool], shot_of: Callable[..., dict]):
        self.beats, self.window_of, self.items, self.required = out_beats, window_of, items, required
        self.tracker, self.blocked, self.fits, self.shot_of = tracker, blocked, fits, shot_of
        self.placed: list[dict] = []

    def run(self) -> list[dict]:
        for beat in self.beats:
            self._beat(beat)
        return self.placed

    @staticmethod
    def _serves(item: Item, req: dict) -> bool:
        return req["key"] in item.serves and item.role in req["roles"]

    def _on_screen(self, beat: dict) -> set[str]:
        return {s["asset_id"] for s in beat["shots"] if s.get("asset_id") and not s.get("gap")}

    def _unmet(self, bid: str, pool: list[Item], shown: set[str]) -> list[dict]:
        return sorted((r for r in self.required.get(bid, [])
                       if not any(self._serves(i, r) and i.code in shown for i in pool)),
                      key=lambda r: PRIORITY.get(r["priority"], 1))

    def _beat(self, beat: dict) -> None:
        bid = beat["beat_id"]
        pool = self.items.get(bid, [])
        window = self.window_of[bid]
        length = window["end"] - window["start"]
        for req in self._unmet(bid, pool, self._on_screen(beat)):
            for item in sorted((i for i in pool if self._serves(i, req) and not self.blocked(bid, i)),
                               key=lambda i: (self.tracker.appearances(i.code), i.rank)):
                if self._swap(beat, window["start"], length, item, pool, req):
                    break

    def _swap(self, beat: dict, start: float, length: float, item: Item, pool: list[Item], req: dict) -> bool:
        at = start
        for index, shot in enumerate(beat["shots"]):
            seconds = shot["share"] * length
            a, b = at, at + seconds
            at = b
            if shot.get("command") != "NEW_IMAGE" or not shot.get("asset_id") or shot.get("gap") or shot.get("engine"):
                continue
            if shot["asset_id"] == item.code or not self.fits(item, seconds) or not self._expendable(beat, shot, pool):
                continue
            self.tracker.discard(shot["asset_id"], a, b)
            if not self.tracker.allows(item.code, a, b)[0]:
                self.tracker.add(shot["asset_id"], a, b)
                continue
            self.tracker.add(item.code, a, b)
            beat["shots"][index] = {**self.shot_of({"start": a, "end": b}, item, length, index,
                                                   f"the approved picture for requirement {req['key']}",
                                                   shot.get("said", "")), "requirement": req["key"]}
            self.placed.append({"beat": beat["beat_id"], "requirement": req["key"], "asset": item.code,
                                "replaced": shot["asset_id"], "start": round(a, 2), "end": round(b, 2)})
            return True
        return False

    def _expendable(self, beat: dict, shot: dict, pool: list[Item]) -> bool:
        """The picture of this shot serves no requirement that would be left without a picture on screen."""
        others = {s["asset_id"] for s in beat["shots"] if s is not shot and s.get("asset_id") and not s.get("gap")}
        mine = next((i for i in pool if i.code == shot["asset_id"]), None)
        return mine is None or not any(
            self._serves(mine, r) and not any(self._serves(i, r) and i.code in others for i in pool)
            for r in self.required.get(beat["beat_id"], []))

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


class ScriptRequirementPass:
    """The same last pass over the compiled film: the production script replaces pictures that its own spoiler and
    repetition rules do not allow, and can leave a requirement without its approved picture. A still of the beat
    that serves no requirement of its own gives way to an approved picture that the film's rules allow there."""

    def __init__(self, shots: list[dict], required: dict[str, list[dict]], assets: dict, tracker: UsageTracker,
                 blocked: Callable[[str, object], bool], usable: Callable[[object], bool],
                 asset_info: Callable[[object], dict]):
        self.shots, self.required, self.assets, self.tracker = shots, required, assets, tracker
        self.blocked, self.usable, self.asset_info = blocked, usable, asset_info
        self.placed: list[dict] = []

    def run(self) -> list[dict]:
        for beat in sorted({s["beat_id"] for s in self.shots if s.get("beat_id")}):
            mine = [s for s in self.shots if s.get("beat_id") == beat]
            shown = {s["asset_id"] for s in mine if s.get("asset_id")}
            for req in sorted((r for r in self.required.get(beat, []) if not r["approved"] & shown),
                              key=lambda r: PRIORITY.get(r["priority"], 1)):
                if self._place(beat, mine, req):
                    shown = {s["asset_id"] for s in mine if s.get("asset_id")}
        return self.placed

    def _place(self, beat: str, mine: list[dict], req: dict) -> bool:
        options = sorted((self.assets[c] for c in req["approved"] if c in self.assets
                          and self.assets[c].asset_type == "photo" and self.usable(self.assets[c])
                          and not self.blocked(beat, self.assets[c])),
                         key=lambda a: self.tracker.appearances(a.asset_code))
        for shot in mine:
            if shot.get("kind") != "image" or not shot.get("asset_id") or not self._expendable(beat, mine, shot):
                continue
            for asset in options:
                self.tracker.discard(shot["asset_id"], shot["start"], shot["end"])
                if self.tracker.allows(asset.asset_code, shot["start"], shot["end"])[0]:
                    self.tracker.add(asset.asset_code, shot["start"], shot["end"])
                    self.placed.append({"beat": beat, "requirement": req["key"], "asset": asset.asset_code,
                                        "replaced": shot["asset_id"], "start": shot["start"]})
                    for key in ("repeat", "repeat_justified", "repeat_reason", "fill_reason", "held"):
                        shot.pop(key, None)
                    shot.update(self.asset_info(asset))
                    shot["fill_reason"] = f"the approved picture for requirement {req['key']}"
                    return True
                self.tracker.add(shot["asset_id"], shot["start"], shot["end"])
        return False

    def _expendable(self, beat: str, mine: list[dict], shot: dict) -> bool:
        others = {s["asset_id"] for s in mine if s is not shot and s.get("asset_id")}
        return not any(shot["asset_id"] in r["approved"] and not r["approved"] & others
                       for r in self.required.get(beat, []))

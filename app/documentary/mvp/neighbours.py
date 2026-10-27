"""The owner-approved last resort for moments that are still bare after every picture, clip, graphic, repeat and
topical match: the neighbours cover them. Half of the moment goes to the picture before it (held longer), half to
the picture after it. A neighbour that is not a still picture (a video, a map, a graphic) is never stretched; its half
is covered by a photo of its own instead, one that spoils nothing: it belongs to this beat or an earlier one, the
reveal graph allows it here, and it is not shown elsewhere in the film at or after this moment. A still is never
held longer than `max_still_seconds`; what the neighbour cannot take is covered by a photo as well."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.core.ai_config import ai_config
from app.documentary.visuals.usage import UsageTracker

if TYPE_CHECKING:
    from app.documentary.mvp.direct import Item

STILL_TYPES = ("photo", "image")


class NeighbourCover:
    def __init__(self, out_beats: list[dict], window_of: dict[str, dict], items: dict[str, list[Item]],
                 tracker: UsageTracker, names: dict[str, set[str]], beat_order: dict[str, int],
                 blocked: Callable[[str, Item], bool], related: Callable[[Item, str, dict], bool],
                 shot_of: Callable[..., dict]):
        self.beats = out_beats
        self.window_of = window_of
        self.items = items
        self.tracker = tracker
        self.names = names
        self.order = beat_order
        self.blocked = blocked
        self.related = related
        self.shot_of = shot_of
        self.limit = ai_config.mvp().shots.max_still_seconds
        self.min_piece = ai_config.mvp().neighbour_cover.min_piece_seconds
        self.hold = ai_config.mvp().neighbour_cover.max_hold_seconds
        self.covered: list[dict] = []

    def run(self) -> list[dict]:
        """Cover every bare stretch; the stretches it could not cover stay bare (and are reported as gaps)."""
        for n, beat in enumerate(self.beats):
            window = self.window_of[beat["beat_id"]]
            length = window["end"] - window["start"]
            shots, at, k = list(beat["shots"]), window["start"], 0
            out: list[dict] = []
            while k < len(shots):
                if not shots[k].get("gap"):
                    out.append(shots[k])
                    at += shots[k]["share"] * length
                    k += 1
                    continue
                last = k
                while last + 1 < len(shots) and shots[last + 1].get("gap"):
                    last += 1
                seconds = sum(s["share"] for s in shots[k:last + 1]) * length
                before = out[-1] if out else self._edge(n - 1, -1)
                after = shots[last + 1] if last + 1 < len(shots) else self._edge(n + 1, 0)
                before_s = out[-1]["share"] * length if out else self._edge_seconds(n - 1, -1)
                after_s = shots[last + 1]["share"] * length if last + 1 < len(shots) else self._edge_seconds(n + 1, 0)
                said = " ".join(s.get("said", "") for s in shots[k:last + 1]).strip()
                pieces = self._cover(beat["beat_id"], at, seconds, length, before, after,
                                     out[-1] if out else None, shots[last + 1] if last + 1 < len(shots) else None, said,
                                     (before_s, after_s))
                out.extend(pieces)
                at += seconds
                k = last + 1
            beat["shots"] = out
        return self.covered

    def _edge(self, n: int, index: int) -> dict | None:
        """The first or last shot of a neighbouring beat that is not itself bare."""
        if not 0 <= n < len(self.beats):
            return None
        shots = [s for s in self.beats[n]["shots"] if not s.get("gap")]
        return shots[index] if shots else None

    def _edge_seconds(self, n: int, index: int) -> float:
        """How long the first or last shot of a neighbouring beat that is not bare is shown in its own beat."""
        shot = self._edge(n, index)
        if shot is None:
            return 0.0
        window = self.window_of[self.beats[n]["beat_id"]]
        return shot["share"] * (window["end"] - window["start"])

    @staticmethod
    def _grows(shot: dict | None) -> bool:
        return bool(shot) and shot.get("command") == "NEW_IMAGE" and bool(shot.get("asset_id"))

    def _split(self, seconds: float, before: dict | None, after: dict | None, lengths: tuple[float, float]) -> float:
        """The seconds of the stretch the picture before it takes. Two pictures that can be held longer share it so that
        the shorter takes more, until both are as long as the stretch allows; otherwise it is halved."""
        if before is None or after is None:
            return seconds if after is None else 0.0
        if not (self._grows(before) and self._grows(after)):
            return seconds / 2
        return min(max((lengths[1] - lengths[0] + seconds) / 2, 0.0), seconds)

    def _cover(self, bid: str, start: float, seconds: float, length: float, before: dict | None, after: dict | None,
               grow_before: dict | None, grow_after: dict | None, said: str,
               lengths: tuple[float, float] = (0.0, 0.0)) -> list[dict]:
        """The shots that replace one bare stretch. `grow_before` / `grow_after` are the neighbours inside this beat
        (held longer in place); a neighbour in another beat is continued by a shot of its own. `lengths` are how long the
        two neighbours are shown now."""
        end = start + seconds
        left = self._split(seconds, before, after, lengths)
        right = seconds - left
        take_left, rest_left = self._share(before, left, grow_before["share"] * length if grow_before else 0.0)
        take_right, rest_right = self._share(after, right, grow_after["share"] * length if grow_after else 0.0)
        beside = {self._code(before), self._code(after)}
        out: list[dict] = []
        if take_left:
            self.tracker.add(before["asset_id"], start, start + take_left)
            if grow_before is not None:
                grow_before["share"] += take_left / length
            else:
                out.append(self._hold(before, take_left, length, said))
        out.extend(self._photos(bid, start + take_left if rest_left else start + left, rest_left + rest_right, length,
                                said, beside))
        if take_right:
            self.tracker.add(after["asset_id"], end - take_right, end)
            if grow_after is not None:
                grow_after["share"] += take_right / length
            else:
                out.append(self._hold(after, take_right, length, said))
        bare = sum(s["share"] for s in out if s.get("gap")) * length
        self.covered.append({"beat": bid, "start": round(start, 2), "end": round(end, 2), "said": said[:240],
                             "before": self._how(before, take_left, rest_left),
                             "after": self._how(after, take_right, rest_right), "bare_seconds": round(bare, 2)})
        return out

    @staticmethod
    def _how(neighbour: dict | None, took: float, photo: float) -> str:
        if took and photo:
            return "held longer, then a photo"
        return "held longer" if took else ("a photo" if photo else "nothing to cover")

    @staticmethod
    def _code(shot: dict | None) -> str | None:
        return shot.get("asset_id") if shot else None

    def _share(self, neighbour: dict | None, wanted: float, held: float) -> tuple[float, float]:
        """(seconds the neighbouring still takes, seconds left for photos of their own). `held` is how long the picture
        is already shown in this beat (a neighbour of another beat is continued by a shot of its own: 0)."""
        if wanted <= 1e-9:
            return 0.0, 0.0
        if not self._grows(neighbour):
            return 0.0, wanted
        room = max(0.0, self.hold - held)
        take = min(wanted, room)
        rest = wanted - take
        if 0 < rest < self.min_piece:
            take = max(0.0, wanted - self.min_piece)
            rest = wanted - take
        return take, rest

    def _hold(self, neighbour: dict, seconds: float, length: float, said: str) -> dict:
        """The neighbour's picture continued into this beat (a still in the next or previous beat is not stretched
        across the cut; it is shown again, unbroken, for the seconds it is given here)."""
        return {**{k: v for k, v in neighbour.items() if k in ("command", "asset_id", "motion", "speed", "role")},
                "share": seconds / length, "transition_in": "CROSSFADE", "continuation": True, "said": said,
                "why": "the picture next to a bare moment, held longer (approved by the owner)"}

    def _photos(self, bid: str, start: float, seconds: float, length: float, said: str,
                beside: set[str | None]) -> list[dict]:
        if seconds <= 1e-9:
            return []
        count = max(1, math.ceil(seconds / self.limit - 1e-9))
        piece = seconds / count
        out: list[dict] = []
        previous: str | None = None
        for n in range(count):
            a, b = start + n * piece, start + (n + 1) * piece
            pick = self._pick(bid, a, b, said, beside | {previous})
            previous = pick.code if pick else None
            if pick is None:
                out.append({"command": "BLACK_SCREEN", "share": piece / length, "motion": "NONE", "speed": 1.0,
                            "transition_in": "CROSSFADE", "gap": True, "said": said,
                            "why": "no approved visual for this moment"})
                continue
            self.tracker.add(pick.code, a, b)
            out.append({**self.shot_of({"start": a, "end": b}, pick, length, n,
                                   "a photo that spoils nothing, covering a moment its neighbours cannot (approved by "
                                   "the owner)"), "neighbour_cover": True, "said": said})
        return out

    def _pick(self, bid: str, start: float, end: float, said: str, avoid: set[str | None]) -> Item | None:
        here = self.order.get(bid, 0)
        pool = [i for other, group in self.items.items() if self.order.get(other, 99) <= here for i in group
                if i.asset.asset_type in STILL_TYPES and i.code not in avoid and not self.blocked(bid, i)
                and self._spoils_nothing(i.code, start)]
        pool.sort(key=lambda i: (not self.related(i, said, self.names), self.tracker.appearances(i.code), i.rank))
        return next((i for i in pool if self.tracker.allows(i.code, start, end)[0]), None)

    def _spoils_nothing(self, code: str, start: float) -> bool:
        """Unused in the film, or only shown before this moment (never one the film shows later)."""
        spans = self.tracker.spans.get(self.tracker.key(code), [])
        return all(b <= start + 1e-6 for _a, b in spans)

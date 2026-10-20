"""How often a film may show a zoom-in map, and which place: the director asks here."""

from __future__ import annotations

from collections import Counter

from app.core.ai_config import ZoomMapConfig, ai_config


def head(place: str | None) -> str:
    """The name of a place without its region ("Fall River, Massachusetts" -> "Fall River")."""
    return (place or "").split(",")[0].strip()


class MapLimits:
    """The film-level rules of the zoom map beat: places the story has told, a cap per film and per act,
    a gap between two maps, and a place coming back at most as often as the config allows."""

    def __init__(self, cfg: ZoomMapConfig | None = None):
        self.cfg = cfg or ai_config.zoom()
        self.limits = self.cfg.limits
        self.per_place: Counter = Counter()
        self.per_act: Counter = Counter()
        self.total = 0
        self.last_start: float | None = None

    def reason_against(self, key: str, act: str, start: float, told: set[str]) -> str | None:
        lim = self.limits
        if key not in told:
            return "map_place_not_told"
        if self.total >= lim["max_per_film"]:
            return "map_film_limit"
        if self.per_act[act] >= lim["max_per_act"]:
            return "map_act_limit"
        if self.per_place[key] > lim["max_returns_per_place"]:
            return "place_already_mapped"
        if self.last_start is not None and start - self.last_start < lim["min_gap_seconds"]:
            return "map_too_close"
        return None

    def returns(self, key: str) -> int:
        """How often this place was shown before (0 = first time)."""
        return self.per_place[key]

    def register(self, key: str, act: str, start: float) -> int:
        n = self.per_place[key]
        self.per_place[key] += 1
        self.per_act[act] += 1
        self.total += 1
        self.last_start = start
        return n

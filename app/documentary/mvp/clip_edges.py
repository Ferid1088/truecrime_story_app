"""Where the footage of a clip or video starts and ends.

An intro or an end card (a logo screen, a QR code, credits) or a presenter's studio segment is not footage. Frames of the
first and last seconds are looked at by a vision model, which names the last image of the start and the first image of
the end that are not footage; the boundary is then moved to the scene cut between that image and its neighbour (the
cut an end card starts with), or to the neighbour itself when no cut is found, so no card frame is kept."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ai_config import ai_config


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    what: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start


def sample_times(start: float, end: float) -> tuple[list[float], list[float]]:
    """The seconds of the images shown for the start and for the end of the clip."""
    cfg = ai_config.mvp().overlays.edges
    edge = min(cfg.edge_seconds, (end - start) / 2)
    n = cfg.frames_per_edge
    head = [round(start + edge * (i + 0.5) / n, 3) for i in range(n)]
    tail = [round(end - edge + edge * (i + 0.5) / n, 3) for i in range(n)]
    return head, tail


def _index_of(second, times: list[float]) -> int | None:
    """The listed second the model means (the nearest one), None when it is not a number or is far from all."""
    try:
        value = float(second)
    except (TypeError, ValueError):
        return None
    index = min(range(len(times)), key=lambda i: abs(times[i] - value))
    gap = (times[1] - times[0]) if len(times) > 1 else 1.0
    return index if abs(times[index] - value) <= gap else None


def window_of(answer: dict | None, start: float, end: float, head: list[float], tail: list[float],
              cuts: list[float]) -> Window:
    """The footage window of a clip that lies between `start` and `end`, from the model's answer about its edges."""
    answer = answer if isinstance(answer, dict) else {}
    keep_start, keep_end = start, end
    spacing = (head[1] - head[0]) if len(head) > 1 else 1.0
    i = _index_of(answer.get("head_until"), head)
    if i is not None:
        after = head[i + 1] if i + 1 < len(head) else head[i] + spacing
        between = [c for c in cuts if head[i] < c <= after]
        keep_start = min(between) if between else after
    j = _index_of(answer.get("tail_from"), tail)
    if j is not None:
        before = tail[j - 1] if j > 0 else tail[0] - spacing
        between = [c for c in cuts if before < c <= tail[j]]
        keep_end = max(between) if between else before
    return Window(round(max(keep_start, start), 3), round(min(keep_end, end), 3), str(answer.get("what") or "")[:80])


def long_enough_to_check(start: float, end: float) -> bool:
    """A clip shorter than a few edge frames' worth is not looked at: the whole of it would be taken for an edge."""
    return end - start >= 2 * ai_config.mvp().overlays.edges.min_keep_seconds + 2.0


def trimmed(asset) -> bool:
    """True when the start or the end of this clip was trimmed away (an intro or an end card)."""
    import json

    done = json.loads(asset.spec_json or "{}").get("clip_edges") or {}
    return bool(done) and [round(x, 3) for x in done.get("keep") or []] != [round(x, 3) for x in done.get("was") or []]

"""The camera path of a zoom-in map shot: pure functions, no browser.

Every frame of a shot is a function of its time alone, so the same plan always gives the same
camera and the render is repeatable frame by frame."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.ai_config import ZoomMapConfig

TILE = 512.0  # MapLibre's world size at zoom 0 is one 512 px tile


@dataclass(frozen=True)
class Camera:
    lon: float
    lat: float
    zoom: float
    pitch: float = 0.0
    bearing: float = 0.0


@dataclass(frozen=True)
class Frame:
    camera: Camera
    line_progress: float = 0.0      # share of the route line drawn (route variant)
    pulse: float | None = None      # 0..1 phase of the marker pulse, None = no pulse yet


@dataclass(frozen=True)
class ZoomPlan:
    variant: str
    seconds: float
    target: tuple[float, float]               # lat, lon of the place
    zoom_start: float
    zoom_end: float
    bearing_start: float = 0.0
    other: tuple[float, float] | None = None  # route: the place the line starts at (lat, lon)
    pitch_end: float = 0.0
    line_share: float = 0.7
    ease_power: float = 3.2
    pulse_seconds: float = 1.6
    hold_seconds: float = 0.0       # the shot rests on the place this long after the camera has arrived
    attribution: list[str] = field(default_factory=list)


def ease_out(p: float, power: float) -> float:
    """Fast at first, slowing down towards the end (0 -> 0, 1 -> 1)."""
    p = min(max(p, 0.0), 1.0)
    return 1.0 - (1.0 - p) ** power


def ease_in_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3.0 - 2.0 * p)


def fit_zoom(a: tuple[float, float], b: tuple[float, float], width: int, height: int,
             padding: float = 0.35) -> float:
    """Zoom at which both places (lat, lon) are on screen with room around them."""
    def xy(p):
        lat = max(min(p[0], 85.0), -85.0)
        x = (p[1] + 180.0) / 360.0
        y = 0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)
        return x, y

    (x1, y1), (x2, y2) = xy(a), xy(b)
    dx, dy = max(abs(x1 - x2), 1e-9), max(abs(y1 - y2), 1e-9)
    usable = 1.0 - padding
    zx = math.log2(width * usable / (TILE * dx))
    zy = math.log2(height * usable / (TILE * dy))
    return max(0.0, min(zx, zy, 18.0))


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def move_seconds(plan: ZoomPlan) -> float:
    return plan.seconds - plan.hold_seconds


def frame_at(plan: ZoomPlan, t: float, width: int, height: int) -> Frame:
    p = min(max(t / move_seconds(plan), 0.0), 1.0)
    if plan.variant == "route" and plan.other is not None:
        return _route_frame(plan, p, t, width, height)
    return _zoom_frame(plan, p, t)


def _zoom_frame(plan: ZoomPlan, p: float, t: float) -> Frame:
    # the aim point is the centre of the screen in every frame; zoom and tilt follow the same
    # curve, so the whole move comes to rest together and the shot then holds on the place
    eased = ease_out(p, plan.ease_power)
    lat, lon = plan.target
    cam = Camera(lon=lon, lat=lat, zoom=_lerp(plan.zoom_start, plan.zoom_end, eased),
                 pitch=plan.pitch_end * eased, bearing=plan.bearing_start)
    return Frame(cam, 1.0, _pulse(plan, t))


def _route_frame(plan: ZoomPlan, p: float, t: float, width: int, height: int) -> Frame:
    assert plan.other is not None
    a, b = plan.other, plan.target
    fit = fit_zoom(a, b, width, height)
    settle = 1.0 - plan.line_share            # first part: from wide to the two places
    if p < settle:
        e = ease_out(p / settle, plan.ease_power)
        z = _lerp(plan.zoom_start, fit, e)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        return Frame(Camera(mid[1], mid[0], z, 0.0, plan.bearing_start), 0.0, None)
    q = (p - settle) / plan.line_share
    drawn = ease_in_out(q)
    head = (_lerp(a[0], b[0], drawn), _lerp(a[1], b[1], drawn))
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    follow = ease_in_out(q)
    z = fit + (plan.zoom_end - fit) * ease_out(q, plan.ease_power) * 0.5
    lat = _lerp(mid[0], head[0], follow)
    lon = _lerp(mid[1], head[1], follow)
    cam = Camera(lon, lat, z, plan.pitch_end * follow, plan.bearing_start)
    return Frame(cam, drawn, _pulse(plan, t) if drawn >= 1.0 else None)


def _pulse(plan: ZoomPlan, t: float) -> float | None:
    """The marker pulses once the camera has nearly arrived, and keeps pulsing while the shot holds."""
    start = move_seconds(plan) * 0.55
    if t < start:
        return None
    return ((t - start) / plan.pulse_seconds) % 1.0


def frames(plan: ZoomPlan, fps: int, width: int, height: int) -> list[Frame]:
    n = max(2, round(plan.seconds * fps))
    return [frame_at(plan, plan.seconds * i / (n - 1), width, height) for i in range(n)]   # first = start, last = arrived


def build_plan(cfg: ZoomMapConfig, variant: str, target: tuple[float, float], granularity: str,
               return_index: int = 0, other: tuple[float, float] | None = None,
               attribution: list[str] | None = None) -> ZoomPlan:
    """The plan of one shot. A place that comes back starts from another level and direction."""
    v = cfg.variants[variant]
    cam = cfg.camera
    starts, bearings = cam["start_zooms"], cam["bearings"]
    end = float(cam["end_zoom_by_granularity"].get(granularity, cam["street_zoom"]))
    if variant == "pitched_3d":
        end = max(end, 16.2)
    start = float(starts[return_index % len(starts)])
    bearing = float(bearings[return_index % len(bearings)])
    return ZoomPlan(
        variant=variant, seconds=v.seconds + v.hold_seconds, hold_seconds=v.hold_seconds, target=target, zoom_start=start, zoom_end=end,
        bearing_start=bearing, other=other, pitch_end=v.pitch_end,
        line_share=v.line_share, ease_power=float(cam["ease_power"]),
        pulse_seconds=float(cfg.marker["pulse_seconds"]), attribution=attribution or [])

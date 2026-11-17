"""The calendar of a publishing plan. The model proposes dates and times with reasons; the app makes them true: film before its Shorts, the daily limit, the spacing of films, the time
window, the order of the Shorts as the model ranked them. What the app moved is recorded next to the slot, so the owner sees where the calendar differs from the proposal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.publishing.config import PublishingConfig, SchedulingSettings


@dataclass
class Slot:
    destination: str
    asset: str
    day: date
    local_time: str
    reason: str
    moved: str | None = None


def _clock(value: object, settings: SchedulingSettings, fallback: str) -> tuple[str, str | None]:
    """A usable HH:MM inside the allowed window, and a note when the proposal had to be changed."""
    text = str(value or "")
    try:
        hour, minute = (int(p) for p in text.split(":"))
        parsed = time(hour, minute).strftime("%H:%M")
    except (ValueError, TypeError):
        return fallback, f"time {text!r} was not HH:MM: {fallback} used"
    if parsed < settings.earliest_local_time:
        return settings.earliest_local_time, f"{parsed} is before {settings.earliest_local_time}: moved"
    if parsed > settings.latest_local_time:
        return settings.latest_local_time, f"{parsed} is after {settings.latest_local_time}: moved"
    return parsed, None


def resolve(items: list[dict], *, plan_start: date, fallback_time: str, settings: SchedulingSettings, config: PublishingConfig, order: dict[str, list[str]],
            destinations: dict[str, list[str]], existing_films: dict[str, list[date]]) -> list[Slot]:
    """Slots for every (destination, asset) in `destinations` (destination -> assets). `order` is the ranked clip keys per platform; `existing_films` the dates of films already
    planned per platform (other cases)."""
    proposed = {(i.get("destination"), i.get("asset")): i for i in items}
    slots: dict[tuple[str, str], Slot] = {}
    film_day: dict[str, date] = {}
    for destination, assets in destinations.items():
        platform = destination.split("/")[0]
        if not destination.endswith("/film"):
            continue
        for asset in assets:
            item = proposed.get((destination, asset), {})
            clock, note = _clock(item.get("local_time"), settings, fallback_time)
            try:
                day = plan_start + timedelta(days=max(0, int(item.get("date_offset_days", 0))))
            except (TypeError, ValueError):
                day, note = plan_start, note or "no usable date: plan start used"
            moved = [note] if note else []
            while any(abs((day - other).days) < max(settings.min_days_between_films, 0) or day == other for other in existing_films.get(platform, [])):
                day += timedelta(days=1)
                if "spacing" not in " ".join(moved):
                    moved.append("moved for spacing from another documentary on this platform")
            film_day[platform] = day
            slots[(destination, asset)] = Slot(destination, asset, day, clock, str(item.get("reason") or ""), "; ".join(moved) or None)
    for destination, assets in destinations.items():
        platform = destination.split("/")[0]
        if not destination.endswith("/short"):
            continue
        targets = [r.target.split("/")[0] for r in config.routing_rules(destination)]
        floor = max([film_day[t] for t in {platform, *targets} if t in film_day], default=plan_start - timedelta(days=1)) + timedelta(days=1)
        ranked = [k for k in order.get(platform, []) if k in assets] + [a for a in assets if a not in order.get(platform, [])]
        wanted = []
        for asset in assets:
            item = proposed.get((destination, asset), {})
            try:
                wanted.append((plan_start + timedelta(days=max(0, int(item.get("date_offset_days", 1)))), asset))
            except (TypeError, ValueError):
                wanted.append((plan_start + timedelta(days=1), asset))
        days = sorted(d for d, _ in wanted)                                   # the proposed dates, handed out in the ranked order
        used: dict[date, int] = {}
        for rank, asset in enumerate(ranked):
            item = proposed.get((destination, asset), {})
            proposed_day = days[rank]
            day = max(proposed_day, floor)
            moved = []
            if day != proposed_day:
                moved.append("moved after the film" if floor > proposed_day else "")
            while used.get(day, 0) >= settings.max_shorts_per_platform_per_day:
                day += timedelta(days=1)
                moved.append("moved for the daily limit")
            used[day] = used.get(day, 0) + 1
            clock, note = _clock(item.get("local_time"), settings, fallback_time)
            if note:
                moved.append(note)
            own = (wanted[[a for _, a in wanted].index(asset)][0])
            if own != proposed_day and not moved:
                moved.append("dates follow the ranked order")
            slots[(destination, asset)] = Slot(destination, asset, day, clock, str(item.get("reason") or ""), "; ".join(dict.fromkeys(m for m in moved if m)) or None)
    return list(slots.values())


def to_utc(slot: Slot, timezone: str) -> datetime:
    hour, minute = (int(p) for p in slot.local_time.split(":"))
    return datetime.combine(slot.day, time(hour, minute), tzinfo=ZoneInfo(timezone)).astimezone(UTC)

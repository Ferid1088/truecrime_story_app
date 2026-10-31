"""What the audience must be able to follow in the pictures: a person the text names is shown (the first mention always), a
picture that comes again is about the text where it comes, and no picture stays on screen longer than the limit. Each rule is a
pass over the finished timeline; what cannot be shown is a bare moment (the drawing covers it), never a wrong picture."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

from app.core.ai_config import ai_config

PICTURE_KINDS = ("image",)
STOP = {"that", "this", "with", "from", "were", "have", "been", "their", "about", "which", "after", "before", "there"}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{4,}", text.lower()) if w not in STOP}


def names_of(slug: str) -> tuple[str, str | None]:
    """The full name and the last word of an entity slug ('alleigha_botner' -> 'alleigha botner', 'botner')."""
    parts = [p for p in slug.split("_") if len(p) > 2 and p != "ent"]
    return " ".join(parts), (parts[-1] if parts else None)


def named_by(text: str, slug: str) -> bool:
    """The text names this very person: the full name or the first name (a shared last name names a family, not a person)."""
    parts = [p for p in slug.split("_") if len(p) > 2 and p != "ent"]
    low = text.lower()
    return bool(parts and (" ".join(parts) in low or re.search(rf"\b{re.escape(parts[0])}", low)))


def mentions(text: str, slug: str, person: bool = True) -> bool:
    """The text names this entity: its full name, or for a person also the last word of it ('Smith' names todd_smith)."""
    full, last = names_of(slug)
    low = text.lower()
    return bool((full and full in low) or (person and last and re.search(rf"\b{re.escape(last)}", low)))


def bare(start: float, end: float, beat_id: str | None) -> dict:
    """A moment with no picture: black for now, the drawing covers it."""
    return {"kind": "black", "command": "BLACK_SCREEN", "beat_id": beat_id, "start": round(start, 3), "end": round(end, 3),
            "motion": "NONE", "speed": 1.0, "transition_in": "CROSSFADE", "black_filled": True, "bare": True}


def _text_between(sentences: list[dict], start: float, end: float) -> str:
    return " ".join((s.get("display") or s.get("speech") or "") for s in sentences if s["start"] < end and s["end"] > start)


def limit_picture_seconds(shots: list[dict]) -> list[dict]:
    """No picture is on screen longer than the limit: the rest of a longer hold becomes a bare moment. Returns what was cut."""
    limit = ai_config.motion.max_picture_seconds
    done = []
    for shot in list(shots):
        if shot.get("kind") not in PICTURE_KINDS or shot["end"] - shot["start"] <= limit + 1e-3:
            continue
        cut = round(shot["start"] + limit, 3)
        shots.append(bare(cut, shot["end"], shot.get("beat_id")))
        done.append({"fix": "picture_cut_at_the_limit", "asset": shot.get("asset_id"), "start": shot["start"],
                     "was": round(shot["end"] - shot["start"], 1)})
        shot["end"] = cut
    shots.sort(key=lambda s: (s["start"], s["end"]))
    return done


def drop_unrelated_repeats(shots: list[dict], sentences: list[dict], words_of: Callable[[str], set[str]]) -> list[dict]:
    """A picture that comes again must be about the text where it comes: a person the text names, or a word the picture's own
    description shares with it. Otherwise that appearance becomes a bare moment. `words_of(code)` is the picture's description."""
    seen: Counter = Counter()
    done = []
    for shot in sorted((s for s in shots if s.get("kind") in PICTURE_KINDS and s.get("asset_id")), key=lambda s: s["start"]):
        code = shot["asset_id"]
        seen[code] += 1
        if seen[code] == 1:
            continue
        text = _text_between(sentences, shot["start"], shot["end"])
        related = (any(mentions(text, e) for e in shot.get("entities") or [])
                   or bool(tokens(text) & words_of(code)))
        if related:
            continue
        shots[shots.index(shot)] = bare(shot["start"], shot["end"], shot.get("beat_id"))
        done.append({"fix": "unrelated_repeat_removed", "asset": code, "start": shot["start"]})
    return done


def cover_people(shots: list[dict], sentences: list[dict], persons: dict[str, list[str]],
                 pool_for: Callable[[str], list], make_shot: Callable[[dict, object, float, float, str], dict],
                 beat_at: Callable[[float], str | None], entities_of: Callable[[object], list[str]],
                 fit: Callable[[object, str], int] | None = None, preferred: dict[str, list[str]] | None = None) -> list[dict]:
    """A person the text names has a photo of that person on screen: at the first mention always, at later mentions up to the
    configured number of times. `persons` maps an entity slug to its photos' codes; `pool_for(beat)` gives the photos the story
    has earned and the firewall allows at that beat. Only a picture shot is replaced, and only by a photo of that person."""
    cfg = ai_config.motion
    first_seen: set[str] = set()
    replaced: Counter = Counter()
    done = []
    for sentence in sorted(sentences, key=lambda s: s["start"]):
        text = sentence.get("display") or sentence.get("speech") or ""
        for slug in persons:
            if not mentions(text, slug):
                continue
            first = slug not in first_seen
            first_seen.add(slug)
            a, b = sentence["start"], sentence["end"]
            on = [s for s in shots if s.get("kind") in ("image", "video") and s["start"] < b and s["end"] > a]
            if any(slug in (s.get("entities") or []) for s in on):
                continue
            if not first and replaced[slug] >= cfg.person_replacements:
                continue
            target = next((s for s in on if s["kind"] == "image"), None)
            if target is None:
                continue
            start, end = max(target["start"], a), min(target["end"], max(b, a + cfg.person_min_seconds))
            if start - target["start"] < 1.0:
                start = target["start"]
            if target["end"] - end < 1.0:
                end = target["end"]
            if end - start < cfg.person_min_seconds:
                continue
            beat = beat_at(start) or target.get("beat_id")
            shown = Counter(s.get("asset_id") for s in shots)
            ranked = (preferred or {}).get(slug, [])            # the inspector's photos for this person, best first
            options = sorted((p for p in pool_for(beat) if slug in entities_of(p) and p.asset_code in persons[slug]),
                             key=lambda p: (ranked.index(p.asset_code) if p.asset_code in ranked else len(ranked),
                                            -(fit(p, text) if fit else 0), shown[p.asset_code]))   # the inspector's choice, the fit, the least shown
            if not options:
                continue
            why = "the first time the text names this person" if first else "the person the text names"
            new = make_shot(target, options[0], start, end, why)
            shots.remove(target)
            if start - target["start"] > 1e-3:
                shots.append({**target, "end": round(start, 3)})
            if target["end"] - end > 1e-3:
                shots.append({**target, "start": round(end, 3), "transition_in": "CROSSFADE"})
            shots.append(new)
            shots.sort(key=lambda s: (s["start"], s["end"]))
            replaced[slug] += 0 if first else 1
            done.append({"fix": "person_shown", "person": slug, "asset": options[0].asset_code, "start": round(start, 1),
                         "first_mention": first})
    return done


CARD_KINDS = ("graphic", "detective_board", "chapter", "title")
HOLDS_LONGER = ("image", *CARD_KINDS)             # a shot that can be held longer than planned (a clip plays once, a map zoom has its length)


def merge_adjacent_black(shots: list[dict]) -> None:
    """Black stretches next to each other are one stretch (in place)."""
    shots.sort(key=lambda s: (s["start"], s["end"]))
    merged: list[dict] = []
    for shot in shots:
        if merged and shot["kind"] == "black" and merged[-1]["kind"] == "black" and shot["start"] - merged[-1]["end"] < 1e-3:
            merged[-1]["end"] = shot["end"]
            merged[-1]["bare"] = bool(merged[-1].get("bare") or shot.get("bare"))
        else:
            merged.append(shot)
    shots[:] = merged


def hold_pictures_between_gaps(shots: list[dict]) -> list[dict]:
    """A picture with a gap on both sides is held at least `picture_between_gaps_seconds`: the time is taken from the two gaps, half each
    (all of a gap that is shorter than its half, the rest from the other). Without it the portrait would grow, shrink to the corner for a
    flash of a picture and grow again. The gaps that are left are judged by the general rules afterwards. Returns what was changed."""
    minimum = ai_config.drawing_settings().picture_between_gaps_seconds
    merge_adjacent_black(shots)
    done = []
    i = 1
    while i + 1 < len(shots):
        pic, before, after = shots[i], shots[i - 1], shots[i + 1]
        length = pic["end"] - pic["start"]
        if pic["kind"] != "image" or before["kind"] != "black" or after["kind"] != "black" or length >= minimum - 1e-6:
            i += 1
            continue
        need = minimum - length
        take_before = min(need / 2, before["end"] - before["start"])
        take_after = min(need - take_before, after["end"] - after["start"])
        take_before = min(need - take_after, before["end"] - before["start"])
        pic["start"], pic["end"] = round(pic["start"] - take_before, 3), round(pic["end"] + take_after, 3)
        before["end"], after["start"] = round(before["end"] - take_before, 3), round(after["start"] + take_after, 3)
        done.append({"fix": "picture_held_between_gaps", "start": round(pic["start"], 1), "from": round(length, 1),
                     "to": round(pic["end"] - pic["start"], 1)})
        for gap in (after, before):                                   # a gap that was used up is gone
            if gap["end"] - gap["start"] < 0.05:
                shots.remove(gap)
        i = shots.index(pic) + 1
    return done


def cover_short_gaps(shots: list[dict], film_end: float | None = None) -> list[dict]:
    """A black stretch shorter than the drawing's gap length (8 s) is covered by its neighbours, whatever the limit a picture may stay:
    - the shot before it is held longer (a picture or a card, the same);
    - a card before it and a picture after it share the gap, half each (the card is held longer, the picture starts earlier);
    - a clip before it cannot be held, so the picture after it starts earlier;
    - when that is not possible either, the clip's last frame is held for the gap (`frozen_tail_seconds`).
    A stretch with no such neighbour stays black (it is reported, never drawn). Black stretches next to each other are one stretch first,
    and a stretch at the very start or end of the film (its fades) stays. A picture held longer for a gap carries `gap_cover_seconds`."""
    gap = ai_config.drawing_settings().gap_full_seconds
    merge_adjacent_black(shots)
    end = film_end if film_end is not None else max((s["end"] for s in shots), default=0.0)
    done = []
    i = 0
    while i < len(shots):
        g = shots[i]
        length = g["end"] - g["start"]
        before = shots[i - 1] if i else None
        after = shots[i + 1] if i + 1 < len(shots) else None
        if g["kind"] != "black" or not 0.05 < length < gap or g["start"] < 0.5 or g["end"] > end - 0.5:
            i += 1
            continue
        holds_before = before is not None and before["kind"] in HOLDS_LONGER and not before.get("zoom_video")
        starts_earlier = after is not None and after["kind"] == "image"
        freezes_before = before is not None and before["kind"] == "video" and not before.get("zoom_video")
        if holds_before and before["kind"] in CARD_KINDS and starts_earlier:
            mid = round(g["start"] + length / 2, 3)
            before["end"], after["start"] = mid, mid
            after["gap_cover_seconds"] = round(after.get("gap_cover_seconds", 0.0) + length / 2, 3)
        elif holds_before:
            before["end"] = g["end"]
            if before["kind"] == "image":
                before["gap_cover_seconds"] = round(before.get("gap_cover_seconds", 0.0) + length, 3)
        elif starts_earlier:
            after["start"] = g["start"]
            after["gap_cover_seconds"] = round(after.get("gap_cover_seconds", 0.0) + length, 3)
        elif freezes_before:
            before["end"] = g["end"]                                    # the renderer holds the clip's last frame once its shot outlasts it
            before["frozen_tail_seconds"] = round(before.get("frozen_tail_seconds", 0.0) + length, 3)
        else:
            done.append({"fix": "short_gap_left", "start": round(g["start"], 1), "seconds": round(length, 1),
                         "before": before["kind"] if before else None, "after": after["kind"] if after else None})
            i += 1
            continue
        done.append({"fix": "short_gap_covered", "start": round(g["start"], 1), "seconds": round(length, 1)})
        shots.pop(i)
    return done

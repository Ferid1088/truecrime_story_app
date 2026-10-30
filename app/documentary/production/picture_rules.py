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
                 fit: Callable[[object, str], int] | None = None) -> list[dict]:
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
            options = sorted((p for p in pool_for(beat) if slug in entities_of(p) and p.asset_code in persons[slug]),
                             key=lambda p: (-(fit(p, text) if fit else 0), shown[p.asset_code]))   # the photo that fits the words, then the least shown
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

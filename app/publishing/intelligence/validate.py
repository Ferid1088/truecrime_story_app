"""The deterministic checks of what the models answer. There is no second model that reviews the first: the app counts, compares and looks up. A finding is an `error` (the answer is
sent back once, and what stays wrong is shown) or a `warning` (shown)."""

from __future__ import annotations

import re

from app.documentary.host_firewall import reveal_issues
from app.publishing.config import MetadataLimit
from app.tags.validate import LATIN_WORD

SCORES = ("hook_3s", "audience_appeal", "curiosity", "spoiler_risk", "follower_conversion", "film_conversion")
ALLOWED_LATIN = {"youtube", "facebook", "instagram", "tiktok", "shorts", "short", "reels", "reel", "elevenlabs"}      # platform names; the music credit's provider (the app adds the credit itself)
HASHTAG = re.compile(r"^#[^\s#]+$")
MIN_PERSIAN_LETTERS = 80
PERSIAN_LETTER = re.compile("[\u0600-\u06ff]")
HALF_SPACE = "\u200c"
URL = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|org|net|ir|tv)\b", re.IGNORECASE)


def finding(severity: str, kind: str, message: str) -> dict:
    return {"severity": severity, "type": kind, "message": message}


def errors(findings: list[dict]) -> list[str]:
    return [f["message"] for f in findings if f["severity"] == "error"]


# --- the planner -------------------------------------------------------------------------------------------------------------------------------------------------

def planner_problems(raw: object, clip_keys: list[str], platforms: dict[str, list[str]]) -> list[str]:
    """What is structurally wrong with the planner's answer (sent back once). `platforms` maps each platform to the kinds it takes."""
    if not isinstance(raw, dict):
        return ["the answer is not a JSON object"]
    problems: list[str] = []
    clips = raw.get("clips")
    if not isinstance(clips, list):
        problems.append("clips must be a list")
    else:
        seen = [c.get("clip_key") for c in clips if isinstance(c, dict)]
        if sorted(seen) != sorted(clip_keys):
            problems.append(f"clips must judge exactly these clips, once each: {clip_keys}")
        for c in clips:
            scores = c.get("scores") if isinstance(c, dict) else None
            if not isinstance(scores, dict) or any(not isinstance(scores.get(k), int) or not 1 <= scores[k] <= 10 for k in SCORES):
                problems.append(f"every clip needs whole-number scores from 1 to 10 for {list(SCORES)}")
                break
    sequences = raw.get("sequences")
    for platform in (p for p, kinds in platforms.items() if "short" in kinds):
        order = [s.get("clip_key") for s in (sequences or {}).get(platform, []) if isinstance(s, dict)] if isinstance(sequences, dict) else []
        if sorted(order) != sorted(clip_keys):
            problems.append(f"sequences.{platform} must list every clip key exactly once: {clip_keys}")
    schedule = raw.get("schedule")
    if not isinstance(schedule, dict) or not isinstance(schedule.get("items"), list):
        problems.append("schedule.items must be a list")
    return problems


def clean_planner(raw: dict, clip_keys: list[str], platforms: dict[str, list[str]], routing_methods: dict[str, list[str]]) -> dict:
    """The planner's answer reduced to what the app uses: scores clamped, the texts as strings, routing only with allowed methods (anything else is dropped and noted)."""
    notes: list[str] = []
    clips = []
    for c in raw.get("clips", []):
        if not isinstance(c, dict) or c.get("clip_key") not in clip_keys:
            continue
        scores = {k: max(1, min(10, int(c["scores"][k]))) for k in SCORES}
        fit = {p: max(1, min(10, int(v))) for p, v in (c.get("platform_fit") or {}).items() if p in platforms and isinstance(v, int | float)}
        clips.append({"clip_key": c["clip_key"], "scores": scores, "platform_fit": fit, "strengths": str(c.get("strengths") or "")[:400], "risks": str(c.get("risks") or "")[:400]})
    sequences = {p: [{"clip_key": s["clip_key"], "reason": str(s.get("reason") or "")[:500]} for s in raw.get("sequences", {}).get(p, []) if isinstance(s, dict) and s.get("clip_key") in clip_keys]
                 for p, kinds in platforms.items() if "short" in kinds}
    routing = {}
    for destination, advice in (raw.get("routing") or {}).items():
        platform = destination.split("/")[0]
        if not isinstance(advice, dict) or advice.get("method") not in routing_methods.get(platform, []):
            notes.append(f"routing advice for {destination} dropped: {advice.get('method') if isinstance(advice, dict) else advice!r} is not a method of that platform")
            continue
        routing[destination] = {"method": advice["method"], "reason": str(advice.get("reason") or "")[:500]}
    schedule = raw.get("schedule") or {}
    return {"clips": clips, "sequences": sequences, "routing": routing, "notes": notes, "warnings": [str(w) for w in raw.get("warnings") or [] if isinstance(w, str)],
            "schedule_items": [i for i in schedule.get("items", []) if isinstance(i, dict)], "schedule_summary": str(schedule.get("summary") or "")[:800],
            "assumptions": [str(a) for a in schedule.get("assumptions") or [] if isinstance(a, str)]}


# --- the metadata ------------------------------------------------------------------------------------------------------------------------------------------------

def composed_text(text: str, hashtags: list[str]) -> str:
    """The text as it is posted: the body, then the hashtags (they count against the platform's limit together)."""
    return text + ("\n\n" + " ".join(hashtags) if hashtags else "")


def tidy(item: dict) -> tuple[dict, list[str]]:
    """The item in the shape the app stores (strings, lists of strings, a `#` on every hashtag, no duplicates), and what was changed."""
    fixes: list[str] = []
    hashtags: list[str] = []
    for tag in item.get("hashtags") or []:
        tag = str(tag).strip().replace(" ", "_")
        tag = tag if tag.startswith("#") else f"#{tag}"
        if tag in hashtags or tag == "#":
            fixes.append(f"duplicate or empty hashtag {tag!r} removed")
            continue
        hashtags.append(tag)
    keywords = list(dict.fromkeys(str(k).strip() for k in item.get("keywords") or [] if str(k).strip()))
    chapters = [{"time": str(c.get("time")), "title": str(c.get("title") or "").strip()} for c in item.get("chapters") or [] if isinstance(c, dict)]
    return {"title": str(item.get("title") or "").strip(), "text": str(item.get("text") or "").strip(), "hashtags": hashtags, "keywords": keywords, "chapters": chapters,
            "cta": str(item.get("cta") or "").strip(), "routing_note": str(item.get("routing_note") or "").strip(), "facts_used": [str(f) for f in item.get("facts_used") or []]}, fixes


def metadata_findings(item: dict, kind: str, limit: MetadataLimit, *, fact_ids: set[str] | None = None, clip_keys: set[str] | None = None, known_words: set[str] | None = None,
                      chapter_times: list[str] | None = None, story=None, cutoff: int = 0) -> list[dict]:
    """The findings on one destination's metadata (`item` is already tidy). Each context that is given adds its checks: the case's names and facts, the film's chapter times, the story
    for spoilers. Without any context only the platform's own limits and formats are checked (what the owner's edit and an approval need)."""
    out: list[dict] = []
    title, text = item["title"], item["text"]
    if not text:
        out.append(finding("error", "empty_text", "the text is empty"))
    if limit.title_max == 0 and title:
        out.append(finding("error", "title_not_supported", "this destination has no title: leave it empty"))
    if limit.title_max and not title:
        out.append(finding("error" if limit.title_required else "warning", "no_title", "a title is needed" if limit.title_required else "no title"))
    if title and len(title) > limit.title_max > 0:
        out.append(finding("error", "title_too_long", f"the title has {len(title)} characters, the limit is {limit.title_max}"))
    posted = composed_text(text, item["hashtags"])
    if len(posted) > limit.text_max:
        out.append(finding("error", "text_too_long", f"the text with its hashtags has {len(posted)} characters, the limit is {limit.text_max}"))
    if limit.text_max_bytes and len(posted.encode("utf-8")) > limit.text_max_bytes:
        out.append(finding("error", "text_too_long_bytes", f"the text with its hashtags has {len(posted.encode('utf-8'))} bytes, the limit is {limit.text_max_bytes} bytes (Persian letters take two bytes)"))
    bad = sorted({c for c in limit.forbidden_chars if c in f"{title}{posted}"})
    if bad:
        out.append(finding("error", "forbidden_characters", f"the characters {' '.join(bad)} are not allowed on this destination"))
    if len(item["hashtags"]) > limit.hashtags_max:
        out.append(finding("error", "too_many_hashtags", f"{len(item['hashtags'])} hashtags, the limit is {limit.hashtags_max}"))
    elif len(item["hashtags"]) > limit.hashtags_recommended:
        out.append(finding("warning", "many_hashtags", f"{len(item['hashtags'])} hashtags: {limit.hashtags_recommended} or fewer are recommended"))
    for tag in item["hashtags"]:
        if not HASHTAG.match(tag):
            out.append(finding("error", "bad_hashtag", f"{tag!r} is not a valid hashtag (no spaces, one #)"))
    if limit.keywords_max_chars == 0 and item["keywords"]:
        out.append(finding("error", "keywords_not_supported", "this destination has no keywords field: leave the list empty"))
    elif sum(len(k) + 1 for k in item["keywords"]) > limit.keywords_max_chars > 0:
        out.append(finding("error", "keywords_too_long", f"the keywords exceed {limit.keywords_max_chars} characters"))
    if kind == "film" and limit.chapters:
        if chapter_times is not None and [c["time"] for c in item["chapters"]] != chapter_times:
            out.append(finding("error", "chapters_changed", f"the chapters must be exactly the input's, with these times: {chapter_times}"))
        elif any(not c["title"] for c in item["chapters"]):
            out.append(finding("error", "chapter_without_title", "every chapter needs a title"))
    elif item["chapters"]:
        out.append(finding("error", "chapters_not_supported", "no chapters on this destination: leave the list empty"))
    if URL.search(posted) and not limit.web_address_allowed:
        out.append(finding("error", "web_address", "no web address in the text: a link in a caption is not a working route to the film"))
    unknown = sorted({w for w in LATIN_WORD.findall(' '.join(w for w in posted.split() if not URL.search(w))) if w.casefold().strip("'’-") not in known_words | ALLOWED_LATIN}) if known_words is not None else []
    if unknown:
        out.append(finding("error", "unknown_name", f"Latin-script words that are not in the case record: {', '.join(unknown[:6])}"))
    bad_refs = [f for f in item["facts_used"] if f != "film" and f not in fact_ids and not (f.startswith("clip:") and f[5:] in (clip_keys or set()))] if fact_ids is not None else []
    if bad_refs:
        out.append(finding("error", "unknown_fact", f"facts_used names ids that do not exist: {', '.join(bad_refs[:6])}"))
    if known_words is not None and len(PERSIAN_LETTER.findall(posted)) >= MIN_PERSIAN_LETTERS and HALF_SPACE not in f"{title}{posted}":
        out.append(finding("error", "no_half_space", "Persian text without a single half-space (U+200C): compounds, «می» verbs and plurals need it, e.g. روایت‌های، می‌کند"))
    if story is not None:
        for issue in reveal_issues(story, cutoff, f"{title}. {posted}"):
            out.append(finding("error", "spoiler", f"tells what comes after the clip's or film's last act: {issue['quote']}"))
    return out

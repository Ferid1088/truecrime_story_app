"""The creative pack of a case: exactly TWO creative model requests per complete pack.

1. GPT-6 reads the whole approved English master story (every act, every verified fact), finds the strongest situations, chooses five distinct Shorts and writes, for every language independently, the
   title and cover text of the film and the narration, title and cover text of each Short, with a plain-language factual anchor per Short.
2. GPT-Luna receives ONLY GPT-6's final texts and the compact anchors (never the story or any example library) and rewrites everything for all languages together in ONE request.

Afterwards only deterministic code runs (structure, script, platform limits, and a check that a rewrite introduces no number, quotation or name the original and the anchor do not hold): an item the
check cannot accept keeps GPT-6's wording; a Luna request that fails or does not fit keeps the validated GPT-6 result and says so. A pack whose input is unchanged is never paid for twice (the two
stages are cached separately). The pack makes no speech or picture and publishes nothing, and `store` never replaces anything that is approved, voiced or the owner's."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.shorts import CreativePack, CreativePolisher, extract_json
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.covers import proposals as P
from app.db.models import Case, CaseTitleCandidate, CoverProposal, ShortClip, StoryVersion
from app.documentary.blueprint import latest_blueprint
from app.documentary.host import _pack
from app.documentary.host_firewall import visible_facts
from app.documentary.host_story import HostStory, load_story
from app.documentary.spoken import latin_left
from app.identity.titles import title_budget
from app.naming import collision as COL
from app.naming import gates as G
from app.naming.agent import auto_select
from app.naming.context import build_context
from app.naming.corpus import get_corpus
from app.naming.normalize import norm_title
from app.providers.generation.base import GenerationError
from app.services.cost_model import model_cost_eur
from app.services.tracking import stamp_run, track_run
from app.shortform import clips as C

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {"fa": "Persian (Farsi)", "en": "English", "de": "German", "es": "Spanish", "tr": "Turkish", "fr": "French", "it": "Italian", "pt": "Brazilian Portuguese", "ar": "Arabic"}
ROOT = Path(__file__).resolve().parents[2]
MIN_NARRATION_WORDS = len(C.TEXT_PARTS)          # the compatibility adapter cuts a narration into five parts
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NUMBER = re.compile(r"\d+")
_QUOTE = re.compile(r"[«“\"„‹]([^»”\"“‹›]{3,})[»”\"“›]")
_CAPITAL = re.compile(r"\b[A-ZÀ-ÖØ-Þ][\w-]{2,}")
_SENTENCE_START = re.compile(r"(^|[.!?…:;»”\"“«]\s*)$")
Ledger = Callable[[str], AbstractContextManager]          # (label) -> the context of one booked request (the caller's `track_run`), yielding the run the request is stamped on
_locks: dict[str, asyncio.Lock] = {}


def system_text(agent: str) -> str:
    """The agent's own prompt: the themes and the technical contract, nothing else is ever added."""
    return prompt(f"agents/{agent}")


def story_of(db: Session, case: Case, version: StoryVersion) -> HostStory:
    """The story of a version as the creative agents read it."""
    blueprint = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
    return load_story(db, case, version, _pack(db, case.id), blueprint)


def dossier(story: HostStory, languages: list[str] | None = None, english_released: dict | None = None) -> dict:
    """Everything GPT-6 needs: the complete approved English master story, the verified facts in plain language, the five Shorts, and per language the platform limits of its titles. For the
    languages made after the owner released the English texts, `english_released` holds those texts: the other languages write native versions of them."""
    cfg = ai_config.creative_pack
    low, high = cfg.target_seconds
    return {**({"english_released": english_released} if english_released else {}),
            "case": story.case_title, "central_question": story.central_question, "acts": [{"title": a.title, "narration": a.text} for a in story.acts],
            "facts": [{"claim": f.get("claim"), "uncertain": bool(f.get("uncertain"))} for f in visible_facts(story, len(story.acts))], "names": [list(e.spellings) for e in story.entities],
            "clip_keys": cfg.clip_keys, "target_seconds": f"{low}-{high}", "min_words": cfg.min_words, "max_words": cfg.max_words, "situation_count": cfg.situation_count, "speculation_count": cfg.speculation_count,
            "languages": {code: {"name": LANGUAGE_NAMES.get(code, code), "film_title_max_characters": min(ai_config.case_naming.max_chars, title_budget(code)),
                                 "short_title_max_characters": cfg.short_title_max_characters} for code in (languages or cfg.languages)}}


def _text(value) -> str:
    return " ".join(str(value or "").split())


def items(raw) -> list[dict]:
    """A list of {theme, text} as a model answered it: the theme as its number (None when it is not one), the text trimmed; empty texts dropped."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and _text(item.get("text")):
            theme = item.get("theme")
            out.append({"theme": theme if isinstance(theme, int) and 1 <= theme <= ai_config.creative_pack.theme_count else None, "text": _text(item["text"])})
    return out


SPOKEN = ("hook", "story", "avatar")          # a Short is spoken as its hook, the interesting things and the avatar's closing line


def _invitation(code: str) -> str:
    return ai_config.creative_pack.avatar_invitation.get(code, "")


def _short(raw: dict, code: str) -> dict:
    """One Short as a model answered it: the three spoken parts, the title and the cover text, trimmed; the avatar line ends with the language's invitation to click the link (added here, once);
    `narration` is the three spoken parts together."""
    out = {f: _text(raw.get(f)) for f in (*SPOKEN, "title", "cover_text")}
    if out["avatar"] and _invitation(code) and not out["avatar"].endswith(_invitation(code)):
        out["avatar"] = f"{out['avatar']} {_invitation(code)}"
    out["narration"] = " ".join(out[f] for f in SPOKEN if out[f])
    return out


def normalize(data: dict, languages: list[str]) -> dict:
    """What a model answered as {anchors: {clip_key: text}, languages: {code: {film: {title, cover_text}, shorts: {clip_key: {narration, title, cover_text}}}}}: only the expected languages and
    clip keys, texts trimmed; anything else in the answer is dropped, nothing is invented."""
    keys = ai_config.creative_pack.clip_keys
    anchors = {a.get("clip_key"): _text(a.get("anchor")) for a in (data.get("anchors") or []) if isinstance(a, dict)}
    out: dict = {"anchors": {k: anchors.get(k, "") for k in keys}, "situations": items(data.get("situations")), "speculations": items(data.get("speculations")), "summary": _text(data.get("summary")), "languages": {}}
    for code in languages:
        body = (data.get("languages") or {}).get(code) if isinstance(data.get("languages"), dict) else None
        if not isinstance(body, dict):
            continue
        film = body.get("film") if isinstance(body.get("film"), dict) else {}
        shorts = {s.get("clip_key"): s for s in (body.get("shorts") or []) if isinstance(s, dict)}
        out["languages"][code] = {"film": {"title": _text(film.get("title")), "cover_text": _text(film.get("cover_text"))},
                                  "shorts": {k: _short(shorts[k], code) for k in keys if k in shorts}}
    return out


def _bounds() -> dict:
    """The production limits of the texts (config creative_pack): words of a title or cover text, the spoken seconds of a narration (target and tolerance)."""
    cfg = ai_config.creative_pack
    return {"words": (cfg.min_words, cfg.max_words), "seconds": (cfg.target_seconds[0] - cfg.duration_tolerance_seconds, cfg.max_seconds)}


def _named(text: str, names: set[str] | None) -> list[str]:
    """The words of a text that are a name of a person or place of the case (every text for publishing is anonymous: roles only)."""
    if not names:
        return []
    words = {w.casefold() for n in names for w in re.findall(r"[\w’-]+", n) if len(w) > 2}
    return sorted(w for w in {t.casefold() for t in re.findall(r"[\w’-]+", text)} if w in words)


def _item_problems(code: str, label: str, field: str, text: str, limit: int | None = None, kind: str | None = None, names: set[str] | None = None) -> list[dict]:
    """What is wrong with one text. `kind` "words" checks the word limits of a title or cover text, "seconds" the estimated spoken length of a narration; `names` that no name stays in it."""
    found = []
    if not text:
        found.append({"type": "empty", "item": f"{label}.{field}"})
        return found
    if named := _named(text, names):
        found.append({"type": "names_a_person_or_place", "item": f"{label}.{field}", "words": named})
    if kind == "words" and not _bounds()["words"][0] <= len(text.split()) <= _bounds()["words"][1]:
        found.append({"type": "word_count", "item": f"{label}.{field}", "words": len(text.split()), "limits": list(_bounds()["words"])})
    if kind == "seconds" and not _bounds()["seconds"][0] <= (seconds := C.narration_seconds(text, code)) <= _bounds()["seconds"][1]:
        found.append({"type": "duration", "item": f"{label}.{field}", "seconds": seconds, "limits": list(_bounds()["seconds"])})
    if limit is not None and len(text) > limit:
        found.append({"type": "too_long", "item": f"{label}.{field}", "characters": len(text), "limit": limit})
    if latin_left(text, code):
        found.append({"type": "wrong_script", "item": f"{label}.{field}", "words": latin_left(text, code)[:4]})
    return found


def validate(pack: dict, languages: list[str], names: set[str] | None = None) -> dict[str, list[dict]]:
    """What is wrong with a pack, per place ("<language>", "<language>/film", "<language>/<clip key>"): only correctness and production needs. Missing language or clip, an empty text, a text in the
    wrong script, a title over the platform's limit, a narration too short to cut into the five parts the voice and video steps read. Questions, calls to action, section lengths and word counts of
    titles and covers are never checked."""
    cfg = ai_config.creative_pack
    problems: dict[str, list[dict]] = {}
    if len(pack.get("situations") or []) != cfg.situation_count or any(s["theme"] is None for s in pack["situations"]) or not pack.get("summary"):
        problems["situations"] = [{"type": "situations", "count": len(pack.get("situations") or []), "expected": cfg.situation_count}]
    if found := [*forbidden_in(pack.get("situations") or []), *forbidden_in(pack.get("speculations") or [])]:
        problems["situations"] = [*problems.get("situations", []), *found]
    for code in languages:
        body = pack["languages"].get(code)
        if body is None:
            problems[code] = [{"type": "language_missing"}]
            continue
        film = [*_item_problems(code, "film", "title", body["film"]["title"], kind="words", names=names), *_item_problems(code, "film", "cover_text", body["film"]["cover_text"], kind="words", names=names)]
        if body["film"]["title"] and (reason := G.gate_length(body["film"]["title"], code)):
            film.append({"type": "too_long", "item": "film.title", "reason": reason})
        if film:
            problems[f"{code}/film"] = film
        for key in cfg.clip_keys:
            short = body["shorts"].get(key)
            if short is None:
                problems[f"{code}/{key}"] = [{"type": "clip_missing"}]
                continue
            found = [*(p for f in SPOKEN for p in _item_problems(code, key, f, short[f], names=names)), *_item_problems(code, key, "narration", short["narration"], kind="seconds"),
                     *_item_problems(code, key, "title", short["title"], cfg.short_title_max_characters, "words", names), *_item_problems(code, key, "cover_text", short["cover_text"], kind="words", names=names)]
            if short["narration"] and len(short["narration"].split()) < MIN_NARRATION_WORDS:
                found.append({"type": "narration_too_short", "item": f"{key}.narration"})
            if found:
                problems[f"{code}/{key}"] = found
    return problems


def forbidden_in(texts: list[dict]) -> list[dict]:
    """The items that use a word the situations, picks and speculations never use (config creative_pack.forbidden_words: no talk of courts and verdicts)."""
    banned = set(ai_config.creative_pack.forbidden_words)
    return [{"type": "forbidden_word", "item": i + 1, "words": sorted(hit)} for i, t in enumerate(texts) if (hit := banned & {w.casefold() for w in re.findall(r"[\w’-]+", t["text"])})]


def pick_problems(picks: list[dict], names: set[str], expected: int | None = None) -> list[dict]:
    """What is wrong with Luna's picks (or speculations, `expected` their count): not exactly the configured count, an item without one of the themes, an item that still names a person or place of the
    story (they are anonymous)."""
    expected = expected or ai_config.creative_pack.pick_count
    words = {w.casefold() for n in names for w in re.findall(r"[\w’-]+", n) if len(w) > 2}
    found = [] if len(picks) == expected else [{"type": "pick_count", "count": len(picks), "expected": expected}]
    found += forbidden_in(picks)
    for i, pick in enumerate(picks):
        if pick["theme"] is None:
            found.append({"type": "no_theme", "pick": i + 1})
        if named := sorted(w for w in {t.casefold() for t in re.findall(r"[\w’-]+", pick["text"])} if w in words):
            found.append({"type": "names_a_person_or_place", "pick": i + 1, "words": named})
    return found


def _numbers(text: str) -> set[str]:
    """The numbers of a text, without leading zeros (02:31 and 2:31 are the same time)."""
    return {str(int(n)) for n in _NUMBER.findall(text.translate(_DIGITS))}


def _quotes(text: str) -> list[str]:
    return [_text(q) for q in _QUOTE.findall(text)]


def _letters(text: str) -> str:
    """The letters and digits of a text only: quotations that differ in punctuation, quote marks or joining only are the same words."""
    return re.sub(r"[\W_]+", "", text.casefold())


def _names_in(text: str) -> list[str]:
    """The capitalized words inside sentences (a capital at the start of a sentence says nothing)."""
    return [m.group() for m in _CAPITAL.finditer(text) if not _SENTENCE_START.search(text[:m.start()])]


def introduces(original: str, rewrite: str, anchor: str, names: set[str] | None, *, known_text: str = "", lost_quotes: bool = True) -> list[str]:
    """What a rewrite brings in that GPT-6's texts of the language and the factual anchors do not hold (`known_text`, with the original and the anchor): a number (02:31 is 2:31), a quotation whose
    words are not found there (a quotation split or joined, or with other quote marks or punctuation, has the same words), a capitalized name inside a sentence (only when `names` is given: narration of
    a language that does not capitalize every noun); and, in narration, a quotation of the original that it lost. Empty means the rewrite may be taken; the check cannot read meaning, so a rewrite it
    cannot accept keeps the original's wording."""
    known = f"{original} {anchor} {known_text}"
    found = [f"number {n}" for n in sorted(_numbers(rewrite) - _numbers(known))]
    flat_known = _letters(known)
    found += [f"quotation {q!r} added" for q in _quotes(rewrite) if _letters(q) not in flat_known]
    if lost_quotes:
        flat_rewrite = _letters(rewrite)
        found += [f"quotation {q!r} lost" for q in _quotes(original) if _letters(q) not in flat_rewrite]
    if names is not None:
        known_words = {w.casefold() for w in re.findall(r"[\w’-]+", known)} | {w.casefold() for n in names for w in re.findall(r"[\w’-]+", n)}
        found += [f"name {w}" for w in dict.fromkeys(_names_in(rewrite)) if w.casefold() not in known_words]
    return found


def _flatten(body: dict) -> str:
    return " ".join([*body["film"].values(), *(t for short in body["shorts"].values() for t in short.values())])


def merge(gpt6: dict, luna: dict, languages: list[str], names: set[str]) -> tuple[dict, dict[str, str], dict[str, list[str]]]:
    """(the final pack, which model wrote each item, what made an item keep GPT-6's wording): a Luna item is taken when it is complete, passes the script and length checks and introduces nothing."""
    cfg = ai_config.creative_pack
    final = json.loads(json.dumps(gpt6))
    sources: dict[str, str] = {}
    refused: dict[str, list[str]] = {}
    anchors = " ".join(gpt6["anchors"].values())
    known = {code: anchors + " " + _flatten(body) for code, body in gpt6["languages"].items()}          # everything GPT-6 wrote in the language: a Luna text may reuse a number or quotation of it

    def choose(place: str, code: str, original: str, rewrite: str, anchor: str, limit: int | None = None, narration: bool = False, kind: str | None = None) -> str:
        reasons = [p["type"] for p in _item_problems(code, place, "x", rewrite, limit, kind, names)] or introduces(
            original, rewrite, anchor, names if narration and code in cfg.name_check_languages else None, known_text=known[code], lost_quotes=narration)
        if reasons:
            sources[place], refused[place] = "gpt6", reasons
            return original
        sources[place] = "luna" if rewrite != original else "gpt6"
        return rewrite

    for code in languages:
        body, new = final["languages"].get(code), luna["languages"].get(code)
        if body is None or new is None:
            continue
        for field in ("title", "cover_text"):
            limit = None if field == "cover_text" else min(ai_config.case_naming.max_chars, title_budget(code))
            body["film"][field] = choose(f"{code}/film/{field}", code, body["film"][field], new["film"][field], "", limit, kind="words")
        for key, short in body["shorts"].items():
            rewritten = new["shorts"].get(key)
            if rewritten is None:
                continue
            before = {f: short[f] for f in SPOKEN}
            for field in (*SPOKEN, "title", "cover_text"):
                limit = cfg.short_title_max_characters if field == "title" else None
                short[field] = choose(f"{code}/{key}/{field}", code, short[field], rewritten[field], gpt6["anchors"].get(key, ""), limit, narration=field in SPOKEN, kind=None if field in SPOKEN else "words")
            joined = " ".join(short[f] for f in SPOKEN)
            if joined != " ".join(before.values()) and _item_problems(code, key, "narration", joined, kind="seconds"):          # together the three parts must still fit the spoken length
                short.update(before)
                for f in SPOKEN:
                    sources[f"{code}/{key}/{f}"], refused[f"{code}/{key}/{f}"] = "gpt6", ["duration"]
                joined = " ".join(before.values())
            short["narration"] = joined
    return final, sources, refused


def _usage(result) -> dict:
    """What a model request used and cost: the gateway's own price when it reports one, else the reported tokens at the model's configured rate (config pricing.json), else unknown (None: an unknown cost
    is never booked as free)."""
    rate = ai_config.budget.usd_to_eur
    cost_usd, cost_eur, source = result.cost_usd, None, "gateway" if result.cost_usd is not None else None
    if cost_usd is not None:
        cost_eur = cost_usd * rate
    elif (cost_eur := model_cost_eur(result.model, result.input_tokens, result.output_tokens, cached_tokens=result.cached_tokens)) is not None:       # the same figure the case ledger books
        cost_usd, source = cost_eur / rate, "configured_pricing"
    return {"model": result.model, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cached_tokens": result.cached_tokens, "cost_source": source,
            "cost_usd": None if cost_usd is None else round(cost_usd, 6), "cost_eur": None if cost_eur is None else round(cost_eur, 6)}


async def _booked(ledger: Ledger | None, label: str, role: str, request):
    """The result of ONE model request, booked: with a `ledger` the request runs inside its own tracked run, stamped with the provider's usage so the case's cost ledger counts it (also when the
    answer turns out unusable); without one nothing is booked."""
    with (ledger(label) if ledger else nullcontext(None)) as run:
        answer = await request()
        if run is not None:
            stamp_run(run, answer[1] if isinstance(answer, tuple) else answer, role)
        return answer


def _fingerprint(given: dict, stage: str = "final") -> str:
    """What identifies a pack's input: GPT-6's stage depends on the dossier, its prompt and model only; the stages after it also on Luna's prompt and model."""
    luna = system_text("creative_polisher") if stage != "gpt6" else ""
    material = json.dumps({"dossier": given, "gpt6": system_text("creative_pack"), "luna": luna, "models": [ai_config.alias_for("creative_pack"),
                                                                                                                                       ai_config.alias_for("creative_polisher")]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _cache(case_key: str, fingerprint: str, stage: str) -> Path:
    return ROOT / ai_config.creative_pack.cache_dir / f"{case_key}_{fingerprint}_{stage}.json"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


async def generate(gen, story: HostStory, languages: list[str] | None = None, *, case_key: str = "case", ledger: Ledger | None = None, english_released: dict | None = None) -> dict:
    """The complete pack: {pack (the final one), gpt6 (its first version), sources, refused, problems, usage, cache, fallback, languages}. At most two creative requests, none when the input is
    unchanged and cached; a Luna request that fails keeps the GPT-6 result (`fallback` says why). A GPT-6 request that fails raises: nothing half-made is returned."""
    given = dossier(story, languages, english_released)
    codes = list(given["languages"])
    names = {n for group in given["names"] for n in group}
    fingerprint = _fingerprint(given)
    async with _locks.setdefault(fingerprint, asyncio.Lock()):                    # a second caller of the same input waits for the first and then reads its cache
        final = _read(_cache(case_key, fingerprint, "final"))
        if final is not None:
            return {**final, "cache": "final", "usage": {**final["usage"], "calls_made": 0}}
        usage: dict = {"calls_made": 0}
        first = _read(_cache(case_key, _fingerprint(given, "gpt6"), "gpt6"))
        if first is None:
            usage["calls_made"] += 1
            data, result = await _booked(ledger, "Creative pack: GPT-6", "creative_pack", lambda: CreativePack(gen).run(system=system_text("creative_pack"), dossier=given))
            first = {"pack": normalize(data, codes), "usage": _usage(result)}
            log.info("creative pack, GPT-6: %s", first["usage"])
            if validate(first["pack"], codes, names) and not first["pack"]["languages"]:
                raise GenerationError("invalid_output", "the creative pack came back without any language", provider=result.provider, model=result.model)
            _write(_cache(case_key, _fingerprint(given, "gpt6"), "gpt6"), first)
            cache = "miss"
        else:
            cache = "gpt6"
        usage["gpt6"] = first["usage"]
        pack, problems = first["pack"], validate(first["pack"], codes, names)
        sources, refused, fallback, picks, speculations = {}, {}, None, [], []
        request = {"languages": {c: {"film": b["film"], "shorts": [{"clip_key": k, **{f: s[f].removesuffix(_invitation(c)).strip() if f == "avatar" else s[f] for f in (*SPOKEN, "title", "cover_text")}} for k, s in b["shorts"].items()]} for c, b in pack["languages"].items()},
                   "anchors": [{"clip_key": k, "anchor": a} for k, a in pack["anchors"].items()], "target_seconds": given["target_seconds"], "min_words": given["min_words"], "max_words": given["max_words"], "situations": pack["situations"], "summary": pack["summary"], "pick_count": ai_config.creative_pack.pick_count, "speculation_count": ai_config.creative_pack.speculation_count,
                   "limits": {c: {"film_title_characters": v["film_title_max_characters"], "short_title_characters": v["short_title_max_characters"]} for c, v in given["languages"].items()}}
        size = len(json.dumps(request, ensure_ascii=False))
        polisher = ai_config.creative_pack.polisher
        if not polisher.enabled:
            fallback = {"stage": "luna", "reason": "disabled in the configuration"}
        elif not pack["languages"]:
            fallback = {"stage": "luna", "reason": "GPT-6 returned no language"}
        elif size > polisher.max_input_chars:
            fallback = {"stage": "luna", "reason": f"capacity: the request holds {size} characters, the configured limit is {polisher.max_input_chars}; nothing was cut and nothing was sent"}
        else:
            try:
                raw = _read(_cache(case_key, fingerprint, "luna"))
                data = extract_json(raw["text"]) if raw else {}                    # Luna's answer is kept as it came: a parsing fix is checked on it without paying again
                if not data:
                    usage["calls_made"] += 1                                       # an attempt counts: a failed request may be billed too
                    result = await _booked(ledger, "Creative pack: Luna", "creative_polisher", lambda: CreativePolisher(gen).run(system=system_text("creative_polisher"), request=request))
                    raw = {"text": result.text, "usage": _usage(result)}
                    _write(_cache(case_key, fingerprint, "luna"), raw)
                    log.info("creative pack, Luna: %s", raw["usage"])
                    data = extract_json(result.text)
                    if not data:
                        raise GenerationError("invalid_output", f"the polishing model returned no JSON object (it answered {len(result.text)} characters, {result.output_tokens} output tokens; kept in the cache)", provider=result.provider, model=result.model)
                usage["luna"] = raw["usage"]
                polished = normalize(data, codes)
                if lost := [c for c in codes if c in pack["languages"] and c not in polished["languages"]]:
                    fallback = {"stage": "luna", "reason": f"the answer had no readable block for {lost}: GPT-6's wording stands for them (delete the Luna cache file to ask again)"}
                pack, sources, refused = merge(pack, polished, codes, names)
                picks, speculations = items(data.get("picks")), items(data.get("speculations"))
            except GenerationError as exc:
                fallback = {"stage": "luna", "reason": str(exc)[:300]}
                log.warning("the Luna rewrite failed, the GPT-6 result stands: %s", exc)
        costs = [u["cost_usd"] for u in (usage.get("gpt6"), usage.get("luna")) if u]
        usage["total_cost_usd"] = None if None in costs else round(sum(costs), 6)             # unknown if any request's cost is unknown
        usage["total_cost_eur"] = None if None in costs else round(sum(costs) * ai_config.budget.usd_to_eur, 6)
        final_problems = validate(pack, codes, names) or problems
        if fallback is None and (bad := pick_problems(picks, names)):
            final_problems["picks"] = bad
        if fallback is None and (bad := pick_problems(speculations, names, ai_config.creative_pack.speculation_count)):
            final_problems["speculations"] = bad
        result_out = {"pack": pack, "picks": picks, "speculations": speculations, "gpt6": first["pack"], "sources": sources, "refused": refused, "problems": final_problems, "usage": usage, "fallback": fallback, "languages": codes}
        if fallback is None or fallback["reason"].startswith("capacity") or not polisher.enabled:
            _write(_cache(case_key, fingerprint, "final"), result_out)          # a failed Luna request is not cached: the next run asks Luna again, never GPT-6
        return {**result_out, "cache": cache}


def _voiced(db: Session, case_id: int, language: str, key: str) -> bool:
    """Whether a clip of this key already has a voice or a video: made (paid) assets are never replaced."""
    for row in db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language, ShortClip.clip_key == key):
        media = json.loads(row.media_json or "{}")
        if media.get("voice_seconds") or media.get("video"):
            return True
    return False


def store(db: Session, case: Case, version: StoryVersion, result: dict, only: list[str] | None = None, replace_voiced: bool = False, voiced: dict | None = None) -> dict:
    """The pack as proposals, never over anything approved, voiced or the owner's: a Short clip per language and slot (the English ones are the masters the others link to; a key that already has
    a voice or a video is skipped; the narration is also cut into the five legacy parts the voice, avatar and video steps read), the film title as a candidate (through the mechanical gates),
    the covers' texts as open proposals. Returns what was stored and what was skipped. `only` limits it to some languages; `voiced` {language: {clip key: {hook, story, avatar}}} are the texts written for the voice (what is spoken; the published words stay in the clip too); `replace_voiced` makes a new version of a clip that already has a voice or a
    video (the old version and its voice stay as they are; the newest version is the one in use and has to be voiced again)."""
    pack, problems = result["pack"], result["problems"]
    out = {"clips": 0, "skipped_clips": [], "titles": 0, "covers": 0, "skipped_covers": []}
    masters: dict[str, ShortClip] = {}
    for code in sorted((c for c in pack["languages"] if only is None or c in only), key=lambda c: c != "en"):
        own = db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.language == code).order_by(StoryVersion.id.desc()).first()
        body = pack["languages"][code]
        for key, short in body["shorts"].items():
            if f"{code}/{key}" in problems or (_voiced(db, case.id, code, key) and not replace_voiced):
                out["skipped_clips"].append(f"{code}/{key}")
                continue
            clip = C.with_estimate({"clip_key": key, "narration": short["narration"], "hook": short["hook"], "story": short["story"], "avatar": short["avatar"], "voice": (voiced or {}).get(code, {}).get(key), **C.parts_of(*(((voiced or {}).get(code, {}).get(key) or short)[f] for f in SPOKEN)), "idea": pack["anchors"].get(key), "concept": {"anchor": pack["anchors"].get(key)},
                                    "package": {"title": short["title"], "cover_text": short["cover_text"]},
                                    "pack": {"narration": result["sources"].get(f"{code}/{key}/hook", "gpt6"), "title": result["sources"].get(f"{code}/{key}/title", "gpt6"),
                                             "cover_text": result["sources"].get(f"{code}/{key}/cover_text", "gpt6")}}, code)
            count = db.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == code, ShortClip.clip_key == key).count()
            row = ShortClip(case_id=case.id, language=code, clip_key=key, kind="master" if code == "en" else "native", story_version_id=(own or version).id,
                            master_clip_id=masters[key].id if code != "en" and key in masters else (_latest(db, case.id, "en", key) if code != "en" else None), version=count + 1, status="valid",
                            script_json=json.dumps(clip, ensure_ascii=False), validation_json=json.dumps({"issues": [], "pack": True}), generation_model=ai_config.alias_for("creative_pack"))
            db.add(row)
            db.flush()
            if code == "en":
                masters[key] = row
            out["clips"] += 1
        if f"{code}/film" not in problems:
            out["titles"] += _store_title(db, case, code, body["film"]["title"])
            out["covers"] += _store_cover(db, case, code, "long", body["film"]["cover_text"], out["skipped_covers"])
        for key, short in body["shorts"].items():
            if f"{code}/{key}" not in problems:
                out["covers"] += _store_cover(db, case, code, key, short["cover_text"], out["skipped_covers"])
    db.commit()
    return out


def _latest(db: Session, case_id: int, language: str, key: str) -> int | None:
    row = db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language, ShortClip.clip_key == key).order_by(ShortClip.id.desc()).first()
    return row.id if row else None


def _store_title(db: Session, case: Case, language: str, title: str) -> int:
    """The film's title of one language as the top-ranked candidate, through the mechanical gates and the collision check. An owner's or a published title is never replaced (`auto_select` keeps it)."""
    if not title or db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language == language, CaseTitleCandidate.title_norm == norm_title(title)).first():
        return 0
    ctx, corpus = build_context(db, case), get_corpus(db)
    others: dict[str, list[str]] = {}
    for other in db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language != language, CaseTitleCandidate.status != "rejected"):
        others.setdefault(other.language, []).append(other.title)
    family = next((r.title_family_id for r in db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.title_family_id.isnot(None)).limit(1)), None)
    reason = G.run_gates(title, language, ctx, others) or next(iter(COL.check_lexical(title, language, case.id, corpus).reasons), None)
    db.add(CaseTitleCandidate(case_id=case.id, language=language, title=title, title_norm=norm_title(title), title_family_id=family, status="rejected" if reason else "candidate",
                              rejection_reason=reason, origin="pack", brevity_score=G.brevity_score(title), critic_json=json.dumps({"rank_score": 1.0, "angle": "pack"})))
    db.flush()
    auto_select(db, case, language)
    return 1


def _store_cover(db: Session, case: Case, language: str, key: str, text: str, skipped: list[str], replace_approved: bool = False) -> int:
    """A cover text as an open proposal (the scene picture is made later by the cover planner). A key whose current cover is approved or the owner's is left as it is."""
    if not text:
        return 0
    earlier = P.current(db, case.id, language).get(key)
    if earlier is not None and not replace_approved and (earlier.status == "approved" or earlier.source == "owner"):
        skipped.append(f"{language}/{key}")
        return 0
    db.add(CoverProposal(case_id=case.id, language=language, cover_key=key, kind="long" if key == "long" else "short", version=(earlier.version + 1) if earlier else 1, source="agent", status="open",
                         title=text, scene_prompt="", reason="from the creative pack: the picture is still to be planned",
                         validation_json=json.dumps({"issues": [], "format": "16:9" if key == "long" else "9:16", "pack": True})))
    return 1


def released_english(db: Session, case: Case) -> dict | None:
    """The English film title, cover texts and Short titles exactly as the owner released them, or None while he has not released them (config covers.text_review_languages; {} when English is not reviewed)."""
    from app.covers import text_gate as TG

    if not TG.required("en"):
        return {}                                          # no review is configured for English: nothing is held and nothing is handed on
    state = TG.status(db, case, "en")
    if not state["released"]:
        return None
    out: dict = {"film_title": "", "film_cover_text": "", "shorts": {}}
    for item in state["items"]:
        if item["kind"] == "film_title":
            out["film_title"] = item["text"]
        elif item["kind"] == "cover_text" and item["key"] == "long":
            out["film_cover_text"] = item["text"]
        elif item["kind"] == "short_title":
            out["shorts"].setdefault(item["key"], {})["title"] = item["text"]
        elif item["kind"] == "cover_text":
            out["shorts"].setdefault(item["key"], {})["cover_text"] = item["text"]
    return out


def has_pack(db: Session, case_id: int, language: str) -> bool:
    return db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case_id, CaseTitleCandidate.language == language, CaseTitleCandidate.origin == "pack").first() is not None


async def ensure(db: Session, case: Case, gen, languages: list[str] | None = None) -> dict | None:
    """The pack of a case from its approved English final story, when the autopilot reaches the titles (`languages`: the case's selected languages). English comes first, alone: the owner reviews and
    corrects its film title, cover texts and Short titles and releases them (the texts gate). The other languages are made only after that release, as native versions of the released English texts.
    Returns what was stored per stage, None when there is nothing to make now. A pack that cannot be made leaves the normal title writer to do its work (the failure is logged, never raised)."""
    version = (db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.kind == "final", StoryVersion.language == "en", StoryVersion.approved_at.isnot(None))
               .order_by(StoryVersion.id.desc()).first())
    if version is None:
        return None
    wanted = [c for c in (languages or ai_config.creative_pack.languages) if not has_pack(db, case.id, c)]
    english_first = "en" in (languages or ai_config.creative_pack.languages)
    stage = ["en"] if english_first and "en" in wanted else [c for c in wanted if c != "en"] if english_first else wanted
    released = None
    if english_first and stage != ["en"]:
        released = released_english(db, case)
        if released is None:
            return None                                          # English first: the others wait for the owner's release of the English texts
    if not stage:
        return None
    try:
        result = await generate(gen, story_of(db, case, version), stage, case_key=str(case.id), ledger=lambda label: track_run(db, case.id, label), english_released=released)
    except GenerationError as exc:
        log.warning("the creative pack of case %s could not be made, the title writer works alone: %s", case.id, exc)
        return None
    return {**store(db, case, version, result, only=stage), "languages": stage, "problems": sorted(result["problems"]), "usage": result["usage"], "fallback": result["fallback"]}

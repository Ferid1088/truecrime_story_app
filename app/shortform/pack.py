"""The creative pack of a case: every title, cover text, Short hook and Short script for ALL languages from ONE request to one model (GPT-6) that knows everything the application knows about the
case (all its acts, all its verified facts, nothing held back), scouts the concepts, writes each language independently and checks its own answer twice inside the call. The code then validates the
whole answer (limits of words and characters in every language, empty suspense phrases, fact ids that really exist, a concept that may be chosen, every slot present) and, when something is wrong,
asks once more with exactly what is wrong. The result is stored as proposals (see `store`): Short clips per language, film title candidates, cover texts. The pack makes no speech or picture and
publishes nothing, and it never replaces anything that is approved, voiced or the owner's."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agents.shorts import CATEGORIES, CreativePack
from app.core.ai_config import ai_config
from app.covers import proposals as P
from app.covers.compose import title_issues as cover_issues
from app.db.models import Case, CaseTitleCandidate, CoverProposal, ShortClip, StoryVersion
from app.documentary.blueprint import latest_blueprint
from app.documentary.host import _pack, punctuation_for_the_voice
from app.documentary.host_firewall import visible_facts
from app.documentary.host_story import HostStory, load_story
from app.identity.titles import title_budget
from app.naming import collision as COL
from app.naming import gates as G
from app.naming.agent import auto_select, title_languages
from app.naming.context import build_context
from app.naming.corpus import get_corpus
from app.naming.normalize import norm_title
from app.shortform import clips as C

log = logging.getLogger(__name__)

MEASURES = ("emotional_intensity", "specificity_originality", "question_strength", "case_connection", "reliability")
FILM_RANKS = {"winner": 1.0, "backup": 0.95, "candidate": 0.8}


def _number(value) -> float:
    try:
        return min(10.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def overall(scores: dict) -> float:
    """The weighted overall score (0 to 10) of a concept's five measures; a missing or unreadable measure counts 0."""
    return round(sum(w * _number(scores.get(k)) for k, w in ai_config.shorts_standard.scouting.weights.items()), 2)


def rate(found: list[dict], fact_ids: set[str], act_ids: set[str]) -> list[dict]:
    """Every concept with its measures, its weighted overall score, and whether it may be chosen (`choosable`, with the reason when not): the ids it cites must be verified ones that were given."""
    cfg = ai_config.shorts_standard.scouting
    rated = []
    for c in found:
        raw = c.get("scores") if isinstance(c.get("scores"), dict) else {}
        scores = {k: _number(raw.get(k)) for k in MEASURES}
        evidence = [str(e) for e in c.get("evidence_ids") or []]
        why = None
        if not evidence or any(e not in fact_ids for e in evidence):
            why = "it cites no verified fact id, or one that does not exist"
        elif scores["reliability"] < cfg.min_reliability:
            why = f"factual reliability {scores['reliability']:g} is below {cfg.min_reliability:g}"
        rated.append({**{k: c.get(k) for k in ("concept", "category", "angle", "proof", "status", "risk")}, "evidence_ids": evidence,
                      "act_ids": [str(a) for a in c.get("act_ids") or [] if str(a) in act_ids], "scores": scores, "overall": overall(scores), "choosable": why is None, "why_not": why})
    return sorted(rated, key=lambda r: r["overall"], reverse=True)


def story_of(db: Session, case: Case, version: StoryVersion) -> HostStory:
    """The story of a version as the creative agents read it."""
    blueprint = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
    return load_story(db, case, version, _pack(db, case.id), blueprint)


def dossier(story: HostStory, languages: list[str] | None = None) -> dict:
    """Everything the model needs, from the approved English master story: ALL its acts, all the verified facts with their ids (nothing is held back), the slots and, per language, its limits."""
    cfg = ai_config.short_clips_settings()
    cover = ai_config.covers_settings().title
    naming = ai_config.case_naming
    return {"case": story.case_title, "central_question": story.central_question,
            "acts": [{"act": a.id, "title": a.title, "narration": a.text} for a in story.acts], "facts": visible_facts(story, len(story.acts)), "names": [list(e.spellings) for e in story.entities],
            "slots": [{"clip_key": f"C{i + 1}", "angle": angle} for i, angle in enumerate(cfg.angles[:cfg.max_clips])],
            "languages": {code: {"words": {part: list(C.words_for(part, code, cfg)) for part in ("hook", "context", "detail", "reaction")}, "cta_max_words": cfg.cta_max_words,
                                 "title_words": [naming.min_words, naming.max_words], "film_title_characters": min(naming.max_chars, title_budget(code)),
                                 "cover_words": [cover["min_words"], cover["max_words"]],
                                 "punctuation": punctuation_for_the_voice(code) or None} for code in (languages or title_languages())},
            "minimum_concepts": ai_config.shorts_standard.scouting.min_concepts, "categories": CATEGORIES, "generic_phrases": cfg.generic_phrases}


def _words(text: str) -> int:
    return len(str(text or "").split())


def _range(kind: str, label: str, text: str, low: int, high: int) -> list[dict]:
    n = _words(text)
    return [] if low <= n <= high else [{"type": kind, "words": n, "fix": f"The {label} has {low} to {high} words ({n} now)."}]


def validate(pack: dict, given: dict) -> dict[str, list[dict]]:
    """What is wrong with a whole pack, per place: "scouting", "<language>", "<language>/film", "<language>/<clip key>". Nothing is accepted on trust: ids, limits and phrases are checked by the code."""
    cfg = ai_config.short_clips_settings()
    naming = ai_config.case_naming
    problems: dict[str, list[dict]] = {}

    def add(place: str, found: list[dict]) -> None:
        if found:
            problems.setdefault(place, []).extend(found)

    facts = {str(f.get("id")) for f in given["facts"]}
    acts = {str(a["act"]) for a in given["acts"]}
    rated = rate(pack["scouting"]["concepts"], facts, acts)
    if len(rated) < given["minimum_concepts"] or len({r["concept"] for r in rated}) < given["minimum_concepts"]:
        add("scouting", [{"type": "too_few_concepts", "fix": f"List at least {given['minimum_concepts']} genuinely different concepts."}])
    by_text = {r["concept"]: r for r in rated}
    chosen_by_slot = {c.get("clip_key"): c for c in pack["scouting"]["chosen"]}
    original = pack["scouting"]["concepts"]
    for slot in given["slots"]:
        pick = chosen_by_slot.get(slot["clip_key"])
        index = pick.get("index") if pick else None
        concept = original[index] if isinstance(index, int) and 0 <= index < len(original) else None
        if concept is None or not by_text.get(str(concept.get("concept") or ""), {}).get("choosable"):
            add("scouting", [{"type": "slot_concept", "clip_key": slot["clip_key"], "fix": f"Choose for {slot['clip_key']} a scouted concept that cites verified fact ids and is reliable."}])
    for code in given["languages"]:
        body = pack["languages"].get(code)
        if body is None:
            add(code, [{"type": "language_missing", "fix": f"Write everything for {code}."}])
            continue
        film = body.get("film") if isinstance(body.get("film"), dict) else {}
        for label, key in (("film title", "title"), ("film title backup", "title_backup")):
            add(f"{code}/film", _range("film_title_words", label, film.get(key), naming.min_words, naming.max_words))
        if reason := G.gate_length(str(film.get("title") or ""), code):
            add(f"{code}/film", [{"type": "film_title_length", "fix": reason}])
        for key in ("cover_text", "cover_backup"):
            add(f"{code}/film", [{"type": "film_cover_words", "fix": p} for p in cover_issues(str(film.get(key) or ""))])
        shorts = {s.get("clip_key"): s for s in body.get("shorts") or [] if isinstance(s, dict)}
        for slot in given["slots"]:
            short = shorts.get(slot["clip_key"])
            place = f"{code}/{slot['clip_key']}"
            if short is None:
                add(place, [{"type": "clip_missing", "fix": f"Write the Short {slot['clip_key']} in {code}."}])
                continue
            clip = {**{k: short.get(k) for k in ("clip_key", *C.TEXT_PARTS)}, "angle": slot["angle"], "idea": short.get("idea")}
            add(place, C.clip_issues(clip, code, cfg))
            for i in short.get("evidence_ids") or []:
                if str(i) not in facts:
                    add(place, [{"type": "unknown_fact_id", "quote": str(i), "fix": f"{i} is not one of the verified fact ids you were given."}])
            if not short.get("evidence_ids"):
                add(place, [{"type": "no_fact_ids", "fix": "Give the verified fact ids the Short rests on."}])
            add(place, _range("short_title_words", "short title", short.get("title"), naming.min_words, naming.max_words))
            for key in ("cover_text", "cover_alternative"):
                add(place, [{"type": f"short_{key}_words", "fix": p} for p in cover_issues(str(short.get(key) or ""))])
    return problems


async def generate(gen, story: HostStory, languages: list[str] | None = None) -> dict:
    """The pack and what is still wrong with it: {pack, problems, repairs, languages}. The model is asked once; while the code finds problems it is asked again with exactly those
    (`shorts_standard.pack.repairs` times at most). A request that fails raises: nothing half-made is returned."""
    given = dossier(story, languages)
    agent = CreativePack(gen)
    pack = await agent.run(dossier=given)
    problems = validate(pack, given)
    repairs = 0
    while problems and repairs < ai_config.shorts_standard.pack.repairs:
        repairs += 1
        pack = await agent.run(dossier=given, previous=pack, problems=problems)
        problems = validate(pack, given)
    return {"pack": pack, "problems": problems, "repairs": repairs, "languages": list(given["languages"])}


def _voiced(db: Session, case_id: int, language: str, key: str) -> bool:
    """Whether a clip of this key already has a voice or a video: made (paid) assets are never replaced."""
    for row in db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language, ShortClip.clip_key == key):
        media = json.loads(row.media_json or "{}")
        if media.get("voice_seconds") or media.get("video"):
            return True
    return False


def store(db: Session, case: Case, version: StoryVersion, result: dict) -> dict:
    """The pack as proposals, never over anything approved, voiced or the owner's: a Short clip per language and slot (the English ones are the masters the others link to; a key that already has a
    voice or a video is skipped), the film's title candidates (through the mechanical gates; the winner ranks first), the covers' texts as open proposals. Returns what was stored and what was skipped."""
    pack, problems = result["pack"], result["problems"]
    concepts = pack["scouting"]["concepts"]
    chosen = {c.get("clip_key"): concepts[c["index"]] for c in pack["scouting"]["chosen"] if isinstance(c.get("index"), int) and 0 <= c["index"] < len(concepts)}
    rated = {r["concept"]: r for r in rate(concepts, {f for f in _fact_ids(result)}, {a for a in _act_ids(result)})}
    out = {"clips": 0, "skipped_clips": [], "titles": 0, "covers": 0, "skipped_covers": []}
    languages = sorted(pack["languages"], key=lambda code: code != "en")
    masters: dict[str, ShortClip] = {}
    for code in languages:
        own = db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.language == code).order_by(StoryVersion.id.desc()).first()
        body = pack["languages"][code]
        for short in body.get("shorts") or []:
            key = short.get("clip_key")
            if _voiced(db, case.id, code, key):
                out["skipped_clips"].append(f"{code}/{key}")
                continue
            concept = rated.get(str((chosen.get(key) or {}).get("concept") or ""), {})
            clip = {**{k: short.get(k) for k in ("clip_key", "angle", "idea", *C.TEXT_PARTS, "evidence_ids")}, "concept": concept,
                    "selection": {"tease_question": short.get("tease_question"), "checks": pack["checks"]}, "package": {k: short.get(k) for k in ("title", "title_backup", "title_candidates", "cover_text", "cover_backup", "cover_alternative", "cover_candidates")}}
            clip = C.with_estimate(clip, code)
            issues = problems.get(f"{code}/{key}", [])
            count = db.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == code, ShortClip.clip_key == key).count()
            row = ShortClip(case_id=case.id, language=code, clip_key=key, kind="master" if code == "en" else "native", story_version_id=(own or version).id,
                            master_clip_id=masters[key].id if code != "en" and key in masters else None, version=count + 1, status="valid" if not issues else "needs_review",
                            script_json=json.dumps(clip, ensure_ascii=False), validation_json=json.dumps({"issues": issues, "pack": True}, ensure_ascii=False), generation_model=ai_config.alias_for("creative_pack"))
            db.add(row)
            db.flush()
            if code == "en":
                masters[key] = row
            out["clips"] += 1
        out["titles"] += _store_titles(db, case, code, body.get("film") if isinstance(body.get("film"), dict) else {})
        out["covers"] += _store_covers(db, case, code, body, out["skipped_covers"])
    db.commit()
    return out


def _fact_ids(result: dict) -> set[str]:
    return {i for body in result["pack"]["languages"].values() for s in body.get("shorts") or [] for i in s.get("evidence_ids") or []} | {i for c in result["pack"]["scouting"]["concepts"] for i in c.get("evidence_ids") or []}


def _act_ids(result: dict) -> set[str]:
    return {a for c in result["pack"]["scouting"]["concepts"] for a in c.get("act_ids") or []}


def _store_titles(db: Session, case: Case, language: str, film: dict) -> int:
    """The film's title candidates of one language: the winner, the backup and the other candidates, each through the mechanical gates and the collision check, ranked winner first. An owner's or a
    published title is never replaced (`auto_select` keeps it)."""
    ranked: dict[str, float] = {}
    for rank, titles in (("winner", [film.get("title")]), ("backup", [film.get("title_backup")]), ("candidate", film.get("title_candidates") or [])):
        for title in titles:
            if str(title or "").strip():
                ranked.setdefault(str(title).strip(), FILM_RANKS[rank])
    if not ranked:
        return 0
    ctx, corpus = build_context(db, case), get_corpus(db)
    family = next((r.title_family_id for r in db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.title_family_id.isnot(None)).limit(1)), None)
    others: dict[str, list[str]] = {}
    for other in db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language != language, CaseTitleCandidate.status != "rejected"):
        others.setdefault(other.language, []).append(other.title)
    stored = 0
    for title, rank in ranked.items():
        if db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language == language, CaseTitleCandidate.title_norm == norm_title(title)).first():
            continue
        reason = G.run_gates(title, language, ctx, others) or next(iter(COL.check_lexical(title, language, case.id, corpus).reasons), None)
        db.add(CaseTitleCandidate(case_id=case.id, language=language, title=title, title_norm=norm_title(title), title_family_id=family, editorial_concept=film.get("hook_justification"),
                                  status="rejected" if reason else "candidate", rejection_reason=reason, origin="pack", brevity_score=G.brevity_score(title),
                                  critic_json=json.dumps({"rank_score": rank, "angle": "pack", "hook": film.get("hook"), "hook_justification": film.get("hook_justification")}, ensure_ascii=False)))
        stored += 1
    db.flush()
    auto_select(db, case, language)
    return stored


def _store_covers(db: Session, case: Case, language: str, body: dict, skipped: list[str]) -> int:
    """The cover texts as open proposals (the scene picture is made later by the cover planner). A key whose current cover is approved or the owner's is left as it is."""
    current = P.current(db, case.id, language)
    film = body.get("film") if isinstance(body.get("film"), dict) else {}
    items = [("long", film.get("cover_text"), film.get("cover_backup"))] + [(s.get("clip_key"), s.get("cover_text"), s.get("cover_alternative") or s.get("cover_backup")) for s in body.get("shorts") or []]
    stored = 0
    for key, text, alternative in items:
        if not key or not str(text or "").strip():
            continue
        earlier = current.get(key)
        if earlier is not None and (earlier.status == "approved" or earlier.source == "owner"):
            skipped.append(f"{language}/{key}")
            continue
        db.add(CoverProposal(case_id=case.id, language=language, cover_key=key, kind="long" if key == "long" else "short", version=(earlier.version + 1) if earlier else 1, source="agent", status="open",
                             title=str(text).strip(), scene_prompt="", reason="from the creative pack: the picture is still to be planned",
                             validation_json=json.dumps({"issues": [], "alt_title": str(alternative or "").strip() or None, "format": "16:9" if key == "long" else "9:16", "pack": True}, ensure_ascii=False)))
        stored += 1
    return stored


async def ensure(db: Session, case: Case, gen) -> dict | None:
    """The pack of a case once, from its approved English final story, when the autopilot reaches the titles: None when the case has no approved English story or already has a pack; a pack that
    cannot be made leaves the normal title writer to do its work (the failure is logged, never raised)."""
    from app.providers.generation.base import GenerationError

    if db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.origin == "pack").first() is not None:
        return None
    version = (db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.kind == "final", StoryVersion.language == "en", StoryVersion.approved_at.isnot(None))
               .order_by(StoryVersion.id.desc()).first())
    if version is None:
        return None
    try:
        result = await generate(gen, story_of(db, case, version))
    except GenerationError as exc:
        log.warning("the creative pack of case %s could not be made, the title writer works alone: %s", case.id, exc)
        return None
    return {**store(db, case, version, result), "problems": sorted(result["problems"]), "repairs": result["repairs"]}

"""The narration of a prepared language (Persian) is made right BEFORE it is voiced, not repaired after.

Nothing is listened to afterwards (no speech-to-text, no phoneme recognizer, no retake for a score), so the text and its pronunciation must be
right when they reach ElevenLabs:

1. an editor model rewrites each sentence when needed so that it is correct in meaning against the English source, natural to speak and
   unambiguous to pronounce, and lists the pronunciation rules of the words a voice could misread (names, numbers, foreign words, ambiguous
   vowels, ezafe). It never guesses: an uncertain word is reported and the run stops;
2. deterministic preflight checks (every number and name of the draft survives, the length is sane, every rule belongs to a word of the text);
3. a checker model compares each beat's final text with the English source (meaning, names, numbers, facts);
4. the result is a NEW spoken version of the story (the old one is kept), whose text is the final text; its rules become one pronunciation
   dictionary of the film, sent with every block."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.agents.story import save_story_version, stored_sections
from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited
from app.db.models import Case, EditorialBlueprint, StoryVersion
from app.documentary import names as NS
from app.documentary.mvp import reference as REF
from app.documentary.voice_performance import speech_structure
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)
ZWNJ = "‌"
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
RULE_TYPES = ("phoneme", "alias")
# what the model is asked to do with the input that follows (the agents of this app always get a task line before the input)
EDIT_TASK = ("TASK: for every sentence of the input return its final Persian text and its pronunciation rules, exactly as your instructions say. "
             "Return JSON only, with the key \"sentences\" and one object for every input sentence.\n\nINPUT:\n")
CHECK_TASK = ("TASK: for every beat of the input say whether the Persian text is faithful to the English source, exactly as your instructions say. "
              "Return JSON only, with the key \"beats\".\n\nINPUT:\n")


class PreparationFailed(RuntimeError):
    """The text could not be prepared (an agent failed, no source to compare with, the budget is reached)."""


class PreparationUncertain(RuntimeError):
    """The editor or a check is not sure about items: the run stops before anything is voiced and reports them."""

    def __init__(self, items: list[dict]):
        self.items = items
        shown = "; ".join(f"{i.get('where', '')}: {i.get('what', '')}" for i in items[:8])
        super().__init__(f"{len(items)} item(s) need a person before the narration is voiced: {shown}")


@dataclass
class Sentence:
    i: int
    beat_id: str
    paragraph: int
    draft: str
    final: str = ""
    rules: list[dict] = field(default_factory=list)
    uncertain: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# the text
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Persian Unicode in one form: composed letters, single spaces, no control or zero-width mark except the half-space."""
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(c for c in text if c == ZWNJ or c in "\n " or (not unicodedata.category(c).startswith("C") and c != "​"))
    return re.sub(r"[ \t]+", " ", text).strip()


def numbers_of(text: str) -> list[str]:
    """The digit groups of a text, with Persian and Arabic-Indic digits read as ASCII."""
    return re.findall(r"\d+", (text or "").translate(DIGITS))


def whole_word(surface: str) -> re.Pattern:
    """`surface` as a whole word: not inside another word (letters, digits and the half-space join words)."""
    return re.compile(rf"(?<![\w{ZWNJ}]){re.escape(surface)}(?![\w{ZWNJ}])")


def sentences_of(version: StoryVersion) -> list[Sentence]:
    out: list[Sentence] = []
    for beat in speech_structure(version):
        for p, paragraph in enumerate(beat["paragraphs"]):
            for sent in paragraph:
                out.append(Sentence(len(out), beat["beat_id"], p, sent["speech"]))
    return out


def assemble(sentences: list[Sentence]) -> list[dict]:
    """The sections of the new version: the beats and paragraphs of the old one, the sentences of the final text."""
    sections: list[dict] = []
    for beat_id in dict.fromkeys(s.beat_id for s in sentences):
        paragraphs: list[str] = []
        for p in sorted({s.paragraph for s in sentences if s.beat_id == beat_id}):
            paragraphs.append(" ".join(s.final or s.draft for s in sentences if s.beat_id == beat_id and s.paragraph == p))
        sections.append({"id": beat_id, "text": "\n\n".join(paragraphs)})
    return sections


# ---------------------------------------------------------------------------
# the English source of every beat
# ---------------------------------------------------------------------------


def english_sources(db: Session, version: StoryVersion) -> dict[str, str]:
    """For each beat of this version, the English film's text of the same stretch of the story (the beats of two films are cut differently:
    they are matched by their place in the story). Empty when the case has no English film to compare with."""
    from app.documentary.blueprint import version_sections
    from app.documentary.spoken import beat_sections

    struct = json.loads(version.narrative_structure or "{}")
    mine = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
    english_plan = REF.english_needs_plan(db, version.case_id)
    theirs = db.get(EditorialBlueprint, english_plan.blueprint_id) if english_plan is not None else None
    master = db.get(StoryVersion, theirs.story_version_id) if theirs is not None else None
    if mine is None or theirs is None or master is None:
        return {}
    english_text = {s["id"]: s["text"] for s in beat_sections(version_sections(master), json.loads(theirs.blueprint_json or "{}"))}
    english_spans = REF.beat_spans(theirs.blueprint_json)
    limit = ai_config.voice_prepare.source_chars
    return {beat: " ".join(english_text.get(b, "") for b in REF.matching_beats(span, english_spans))[:limit]
            for beat, span in REF.beat_spans(mine.blueprint_json).items()}


def tones_of(db: Session, version: StoryVersion) -> dict[str, dict]:
    struct = json.loads(version.narrative_structure or "{}")
    row = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
    beats = json.loads(row.blueprint_json or "{}").get("beats") or [] if row else []
    return {b["id"]: {k: b.get(k) for k in ("purpose", "emotional_load") if b.get(k)} for b in beats}


# ---------------------------------------------------------------------------
# the preflight checks (before anything is voiced)
# ---------------------------------------------------------------------------


def check_sentence(sent: Sentence, names: "NS.NameForms | None" = None) -> list[dict]:
    """What is wrong with an edited sentence, in plain words: empty, a number or a name of the draft lost, a length far from the draft's, a
    rule that belongs to no word of the text or may match another word, a pronunciation that cannot be what its type says."""
    cfg = ai_config.voice_prepare
    where = f"sentence {sent.i} ({sent.beat_id})"
    issues: list[dict] = []
    final = sent.final
    if not final.strip():
        return [{"where": where, "what": "the final text is empty"}]
    draft_numbers, final_numbers = numbers_of(sent.draft), numbers_of(final)
    lost = [n for n in draft_numbers if n not in final_numbers]
    if lost:
        issues.append({"where": where, "what": f"the number {', '.join(lost)} of the draft is not in the final text"})
    if names is not None:
        for slug in names.forms:
            named = lambda text: names.mentions(text, slug) or names.named_by(text, slug)      # noqa: E731 - full, first or last name
            if named(sent.draft) and not named(final):
                issues.append({"where": where, "what": f"the name {slug} of the draft is not in the final text"})
    ratio = len(final.split()) / max(len(sent.draft.split()), 1)
    if not cfg.min_length_ratio <= ratio <= cfg.max_length_ratio:
        issues.append({"where": where, "what": f"the final text is {ratio:.2f} times as long as the draft"})
    ipa = re.compile(cfg.ipa_pattern)
    for rule in sent.rules:
        surface = rule["surface"]
        found = whole_word(surface).findall(final)
        if not found:
            issues.append({"where": where, "what": f"the rule for «{surface}» belongs to no word of the text"})
        elif len(re.findall(re.escape(surface), final)) > len(found):
            issues.append({"where": where, "what": f"the rule for «{surface}» would also match part of another word"})
        if rule["type"] == "phoneme" and not ipa.match(rule["pronunciation"]):
            issues.append({"where": where, "what": f"the phoneme of «{surface}» is not an IPA reading: {rule['pronunciation']}"})
    return issues


def clean_rules(raw) -> list[dict]:
    """The rules of one sentence as the editor gave them, those that have what a rule needs."""
    out = []
    for rule in raw if isinstance(raw, list) else []:
        if (isinstance(rule, dict) and isinstance(rule.get("surface"), str) and rule["surface"].strip()
                and rule.get("type") in RULE_TYPES and isinstance(rule.get("pronunciation"), str) and rule["pronunciation"].strip()):
            out.append({"surface": normalize(rule["surface"]), "type": rule["type"], "pronunciation": rule["pronunciation"].strip(),
                        "reason": str(rule.get("reason") or "")[:300]})
    return out


def dictionary_rules(sentences: list[Sentence]) -> tuple[list[dict], list[dict]]:
    """The rules of the whole film as ElevenLabs dictionary rules, one per word, and the conflicts: the same word that needs two different
    readings (a dictionary cannot choose by sentence)."""
    chosen: dict[str, dict] = {}
    conflicts: list[dict] = []
    for sent in sentences:
        for rule in sent.rules:
            known = chosen.get(rule["surface"])
            if known is None:
                chosen[rule["surface"]] = rule
            elif (known["type"], known["pronunciation"]) != (rule["type"], rule["pronunciation"]):
                conflicts.append({"where": f"sentence {sent.i} ({sent.beat_id})",
                                  "what": f"«{rule['surface']}» needs two readings: {known['pronunciation']} and {rule['pronunciation']}"})
    rules = [{"string_to_replace": r["surface"], "type": "phoneme", "alphabet": "ipa", "phoneme": r["pronunciation"]} if r["type"] == "phoneme"
             else {"string_to_replace": r["surface"], "type": "alias", "alias": r["pronunciation"]} for r in chosen.values()]
    return rules, conflicts


# ---------------------------------------------------------------------------
# the editor and the checker
# ---------------------------------------------------------------------------


def _budget_left(db: Session, case_id: int) -> bool:
    snapshot = BudgetService(db).snapshot(case_id)
    return snapshot.spent_eur < snapshot.cap_eur - 1e-9


async def _ask(db: Session, case_id: int, label: str, payload: dict, role: str, runner) -> dict:
    """One model call: `runner(provider, body)` makes the call (the agent's name stays in the code that asks), the run is tracked and costed."""
    try:
        with track_run(db, case_id, label, input_summary=label) as run:
            data, res = await runner(get_generation_provider(), json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, role)
    except GenerationError as e:
        raise PreparationFailed(f"{label} failed: {type(e).__name__}: {e}"[:300]) from e
    return data if isinstance(data, dict) else {}


async def edit(db: Session, case: Case, language: str, sentences: list[Sentence], sources: dict[str, str], tones: dict[str, dict]) -> None:
    """The editor over all sentences, a chunk at a time (its answer fills `final`, `rules` and `uncertain` of each sentence)."""
    size = ai_config.voice_prepare.sentences_per_call
    chunks = [sentences[k:k + size] for k in range(0, len(sentences), size)]

    async def one(chunk: list[Sentence]) -> dict:
        beats = []
        for sent in chunk:
            if not beats or beats[-1]["beat_id"] != sent.beat_id:
                beats.append({"beat_id": sent.beat_id, "english_source": sources.get(sent.beat_id, ""), "tone": tones.get(sent.beat_id, {}),
                              "sentences": []})
            before = sentences[sent.i - 1].draft if sent.i else ""
            after = sentences[sent.i + 1].draft if sent.i + 1 < len(sentences) else ""
            beats[-1]["sentences"].append({"i": sent.i, "draft": sent.draft, "previous": before, "next": after})
        return await _ask(db, case.id, f"Voice preparation ({language}): editor", {"language": language, "beats": beats}, "voice_prepare_editor",
                          lambda gen, body: run_agent("documentary.voice_prepare.edit", gen, EDIT_TASK + body))

    results = await gather_limited(None, [one(c) for c in chunks], return_exceptions=True)
    by_i = {s.i: s for s in sentences}
    for chunk, result in zip(chunks, results, strict=True):
        if isinstance(result, Exception):
            raise result if isinstance(result, PreparationFailed) else PreparationFailed(f"the editor failed: {type(result).__name__}: {result}"[:300])
        for item in result.get("sentences") or []:
            sent = by_i.get(item.get("i")) if isinstance(item, dict) else None
            if sent is None or sent not in chunk:
                continue
            sent.final = normalize(item.get("final_text") or "")
            sent.rules = [{**r, "surface": normalize(r["surface"])} for r in clean_rules(item.get("pronunciation_rules"))]
            sent.uncertain = [u for u in item.get("uncertain_items") or [] if isinstance(u, dict)]


async def verify(db: Session, case: Case, language: str, sentences: list[Sentence], sources: dict[str, str]) -> list[dict]:
    """The checker: each beat's final text against the English source. Returns the problems (the run stops on any)."""
    beats = [{"beat_id": b, "english_source": sources.get(b, ""),
              "persian_final": " ".join(s.final for s in sentences if s.beat_id == b)}
             for b in dict.fromkeys(s.beat_id for s in sentences)]
    size = 12
    chunks = [beats[k:k + size] for k in range(0, len(beats), size)]
    results = await gather_limited(None, [_ask(db, case.id, f"Voice preparation ({language}): check", {"language": language, "beats": c},
                                               "voice_prepare_checker", lambda gen, body: run_agent("documentary.voice_prepare.check", gen, CHECK_TASK + body))
                                          for c in chunks], return_exceptions=True)
    problems: list[dict] = []
    for chunk, result in zip(chunks, results, strict=True):
        if isinstance(result, Exception):
            raise result if isinstance(result, PreparationFailed) else PreparationFailed(f"the checker failed: {type(result).__name__}: {result}"[:300])
        answered = {b.get("beat_id"): b for b in result.get("beats") or [] if isinstance(b, dict)}
        for beat in chunk:
            verdict = answered.get(beat["beat_id"])
            if verdict is None:
                problems.append({"where": f"beat {beat['beat_id']}", "what": "the checker gave no verdict"})
            elif verdict.get("ok") is not True or verdict.get("problems"):
                for p in verdict.get("problems") or [{"kind": "unsure", "why": "not confirmed"}]:
                    problems.append({"where": f"beat {beat['beat_id']}", "what": f"{p.get('kind')}: {p.get('why')} ({str(p.get('quote') or '')[:80]})"})
    return problems


# ---------------------------------------------------------------------------
# the prepared version
# ---------------------------------------------------------------------------


def is_prepared(version: StoryVersion) -> bool:
    return bool(json.loads(version.narrative_structure or "{}").get("prepared"))


async def prepare_version(db: Session, case: Case, version: StoryVersion, language: str) -> StoryVersion:
    """The prepared version of a spoken version: a NEW spoken version with the final text and the film's pronunciation rules (the given one
    stays as it is). A version that is prepared already is returned. Raises PreparationUncertain when anything needs a person."""
    if is_prepared(version):
        return version
    if stored_sections(version) is None:
        raise PreparationFailed("the version has no stored sections to prepare")
    if not _budget_left(db, case.id):
        raise PreparationFailed("the case budget is reached before the narration was prepared")
    sentences = sentences_of(version)
    sources = english_sources(db, version)
    if not sources or not any(sources.values()):
        raise PreparationUncertain([{"where": "the film", "what": "there is no English source to compare the Persian text with"}])
    await edit(db, case, language, sentences, sources, tones_of(db, version))
    names = NS.load(db, case.id, language)
    issues: list[dict] = []
    for sent in sentences:
        issues += [{"where": f"sentence {sent.i} ({sent.beat_id})", "what": f"uncertain: {u.get('surface')}: {u.get('question')}"} for u in sent.uncertain]
        issues += check_sentence(sent, names)
    rules, conflicts = dictionary_rules(sentences)
    issues += conflicts
    if issues:
        raise PreparationUncertain(issues)
    issues = await verify(db, case, language, sentences, sources)
    if issues:
        raise PreparationUncertain(issues)
    sections = assemble(sentences)
    text = "\n\n".join(s["text"] for s in sections)
    struct = json.loads(version.narrative_structure or "{}")
    plan = {k: struct.get(k) for k in ("title", "central_question", "acts", "opening_strategy", "opening_reason", "case_status")}
    extra = {k: struct[k] for k in ("blueprint_id", "source_version_id", "beat_sections", "pilot_seconds", "pilot_beat_ids", "evidence_fingerprint")
             if k in struct}
    prepared = {"from_version_id": version.id, "rules": rules, "changed_sentences": sum(1 for s in sentences if s.final != s.draft),
                "sentences": len(sentences), "dictionary": None}
    prepared_version = save_story_version(
        db, case, plan, sections, text, 0.0, None, "not_evaluated", {"quality_gates": {"pass": True, "failures": []}, "prepared_from": version.id},
        language, None, kind="spoken", master_version_id=version.master_version_id,
        native_quality_score=version.native_quality_score, semantic_consistency_score=version.semantic_consistency_score,
        factual_consistency_score=version.factual_consistency_score, extra_structure={**extra, "prepared": prepared},
        writer_prompt="documentary/voice_prepare/editor_fa_system")
    carry_host(db, version, prepared_version)
    return prepared_version


def carry_host(db: Session, old: StoryVersion, new: StoryVersion) -> None:
    """The host of the old version is the host of the prepared one: its approved dialogue and its scenes (with the voices and avatar videos
    already made) are copied, so that nothing of the host is written, voiced or paid for again."""
    from app.db.models import HostScene, HostSegments

    row = (db.query(HostSegments).filter(HostSegments.story_version_id == old.id).order_by(HostSegments.version.desc()).first())
    if row is None:
        return
    keep = {c.name: getattr(row, c.name) for c in HostSegments.__table__.columns if c.name not in ("id", "story_version_id", "created_at")}
    copy = HostSegments(**keep, story_version_id=new.id)
    db.add(copy)
    db.flush()
    for scene in db.query(HostScene).filter(HostScene.host_segments_id == row.id).all():
        values = {c.name: getattr(scene, c.name) for c in HostScene.__table__.columns
                  if c.name not in ("id", "story_version_id", "host_segments_id", "created_at", "updated_at")}
        db.add(HostScene(**values, story_version_id=new.id, host_segments_id=copy.id))
    db.commit()


async def ensure_dictionary(db: Session, version: StoryVersion, language: str, provider=None) -> list[dict]:
    """The film's pronunciation dictionary at ElevenLabs, as the locators every block of the film is sent with: made once for these rules (a
    dictionary of the same rules is reused), and kept with the version. Nothing for a version without rules."""
    import hashlib

    from app.providers.voice import get_voice_provider

    struct = json.loads(version.narrative_structure or "{}")
    prepared = struct.get("prepared") or {}
    rules = prepared.get("rules") or []
    if not rules:
        return []
    fingerprint = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    known = prepared.get("dictionary") or {}
    if known.get("fingerprint") != fingerprint:
        name = ai_config.voice_prepare.dictionary_name.format(language=language, case_id=version.case_id, version_id=version.id)
        made = await (provider or get_voice_provider()).create_pronunciation_dictionary(name, rules)
        known = {"id": made["id"], "version_id": made["version_id"], "fingerprint": fingerprint, "name": name}
        prepared["dictionary"] = known
        struct["prepared"] = prepared
        version.narrative_structure = json.dumps(struct, ensure_ascii=False)
        db.commit()
    return [{"pronunciation_dictionary_id": known["id"], "version_id": known["version_id"]}]

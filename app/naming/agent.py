"""CaseNamingAgent, CaseTitleCritic, native check and the selection loop.

Per language the agent proposes native titles (never a translation of
another language's title); every proposal runs through the deterministic
gates (length, style, script, generic, spoiler, epistemic), the collision
check against stored titles, then the critics. Rejected proposals are
stored with their reason, so they are never proposed again. The loop
fills exactly `candidates_per_language` eligible titles, or reports how
many are missing when `max_rounds` is reached — it never pads.

LLM roles are routed centrally (case_naming_agent, case_title_critic,
native_title_critic); no model name appears here, and no search backend
is ever called."""

from __future__ import annotations

from app.core.prompts import prompt

import json
import secrets
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, CaseTitleCandidate
from app.identity.titles import clean_title, sync_identity
from app.naming import collision as COL
from app.naming.context import build_context
from app.naming.corpus import get_corpus
from app.naming.gates import brevity_score, run_gates
from app.naming.normalize import norm_title

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic"}
ORDER = ("en", "de", "fa", "ar")

NAMING_SYSTEM = prompt("naming/agent/naming_system")

CRITIC_SYSTEM = prompt("naming/agent/critic_system")

NATIVE_SYSTEM = prompt("naming/agent/native_system")


@dataclass
class Verdict:
    title: str
    reasons: list[str] = field(default_factory=list)
    col: COL.Collision | None = None
    scores: dict = field(default_factory=dict)
    angle: str | None = None

    @property
    def ok(self) -> bool:
        return not self.reasons


def _f(v, default=None):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def rank_score(scores: dict, title: str) -> float:
    w = ai_config.case_naming.weights
    vals = {"memorability": scores.get("memorability") or 0, "curiosity": scores.get("curiosity") or 0,
            "specificity": scores.get("specificity") or 0, "brevity": brevity_score(title),
            "documentary_tone": scores.get("documentary_tone") or 0}
    total = sum(w.values()) or 1.0
    return sum(w.get(k, 0) * v for k, v in vals.items()) / total


def _same_case_titles(db: Session, case_id: int, language: str) -> list[CaseTitleCandidate]:
    return (db.query(CaseTitleCandidate)
            .filter(CaseTitleCandidate.case_id == case_id,
                    CaseTitleCandidate.language == language)
            .order_by(CaseTitleCandidate.id).all())


def _others(db: Session, case_id: int, language: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in (db.query(CaseTitleCandidate)
              .filter(CaseTitleCandidate.case_id == case_id, CaseTitleCandidate.language != language,
                      CaseTitleCandidate.status != "rejected").all()):
        out.setdefault(c.language, []).append(c.title)
    return out


async def _critics(gen, ctx: dict, language: str, survivors: list[Verdict]) -> None:
    cfg = ai_config.case_naming
    if not survivors:
        return
    titles = [v.title for v in survivors]
    brief = {k: ctx.get(k) for k in (
        "canonical_case_name", "location", "central_question", "human_core", "key_facts",
        "hold_back", "claim_limits", "resolution_status")}
    data, _ = await gen.generate_structured(
        "case_title_critic", CRITIC_SYSTEM,
        json.dumps({"language": language, "case": brief, "titles": titles}, ensure_ascii=False))
    got = {norm_title(s.get("title")): s for s in (data or {}).get("scores", []) if isinstance(s, dict)}
    native = {}
    if language != "en":
        ndata, _ = await gen.generate_structured(
            "native_title_critic", NATIVE_SYSTEM.format(language=LANG_NAMES.get(language, language)),
            json.dumps({"titles": titles}, ensure_ascii=False))
        native = {norm_title(s.get("title")): s for s in (ndata or {}).get("scores", [])
                  if isinstance(s, dict)}
    for v in survivors:
        s = got.get(norm_title(v.title))
        if s is None:
            v.reasons.append("critic returned no score")
            continue
        v.scores = {k: _f(s.get(k)) for k in (
            "specificity", "memorability", "curiosity", "documentary_tone",
            "sensationalism_risk", "spoiler_risk", "epistemic_risk")}
        v.scores["reason"] = str(s.get("reason") or "")[:300]
        if language != "en":
            n = native.get(norm_title(v.title)) or {}
            v.scores["native_quality"] = _f(n.get("native_quality"))
            v.scores["literal_translation"] = bool(n.get("literal_translation"))
            v.scores["native_reason"] = str(n.get("reason") or "")[:300]
        sc = v.scores
        if any(sc[k] is None for k in ("specificity", "spoiler_risk", "epistemic_risk",
                                       "sensationalism_risk")):
            v.reasons.append("critic scores incomplete")
        elif sc["specificity"] < cfg.min_specificity:
            v.reasons.append(f"not case-specific (specificity {sc['specificity']:.2f})")
        elif sc["spoiler_risk"] > cfg.max_spoiler_risk:
            v.reasons.append(f"spoiler risk {sc['spoiler_risk']:.2f}")
        elif sc["epistemic_risk"] > cfg.max_epistemic_risk:
            v.reasons.append(f"epistemic risk {sc['epistemic_risk']:.2f}")
        elif sc["sensationalism_risk"] > cfg.max_sensationalism_risk:
            v.reasons.append(f"sensationalism risk {sc['sensationalism_risk']:.2f}")
        if language != "en" and not v.reasons:
            if sc.get("literal_translation"):
                v.reasons.append("literal translation, not a native title")
            elif (sc.get("native_quality") is None or sc["native_quality"] < cfg.min_native_quality):
                v.reasons.append(f"native quality {sc.get('native_quality')}")


async def evaluate_titles(db: Session, case: Case, language: str, titles: list[str], ctx: dict,
                          gen, embedder=None, angles: dict[str, str] | None = None
                          ) -> list[Verdict]:
    """Gates -> collision -> critics for a batch of proposed titles."""
    corpus = get_corpus(db)
    others = _others(db, case.id, language)
    verdicts: list[Verdict] = []
    seen: set[str] = set()
    for raw in titles:
        title = (raw or "").strip().strip("\"'«»“”„").strip()
        n = norm_title(title)
        if not n or n in seen:
            continue
        seen.add(n)
        v = Verdict(title, angle=(angles or {}).get(raw))
        reason = run_gates(title, language, ctx, others)
        if reason:
            v.reasons.append(reason)
        v.col = COL.check_lexical(title, language, case.id, corpus)
        v.reasons.extend(v.col.reasons)
        verdicts.append(v)
    from rapidfuzz import fuzz

    kept: list[Verdict] = []
    for v in verdicts:        # siblings in one batch must differ from each other
        if v.ok:
            twin = next((k for k in kept if fuzz.token_sort_ratio(
                norm_title(k.title), norm_title(v.title)) >= ai_config.case_naming.near_token_threshold),
                None)
            if twin:
                v.reasons.append(f"near-duplicate of its sibling '{twin.title}'")
            else:
                kept.append(v)
    live = [v for v in verdicts if v.ok]
    if embedder is not None and live:
        results = {v.title: v.col for v in live}
        try:
            await COL.add_semantic(results, {v.title: v.title for v in live}, {}, case.id,
                                   corpus, embedder)
        except Exception as e:  # noqa: BLE001 - semantic check is best-effort
            for v in live:
                v.col.reasons_note = f"semantic check unavailable: {e}"
        for v in live:
            v.reasons.extend(r for r in v.col.reasons if r not in v.reasons)
    await _critics(gen, ctx, language, [v for v in verdicts if v.ok])
    return verdicts


def _store(db: Session, case: Case, language: str, v: Verdict, *, round_no: int, family: str,
           concept: str | None, origin: str = "generated") -> CaseTitleCandidate:
    sc, col = v.scores, v.col
    row = CaseTitleCandidate(
        case_id=case.id, language=language, title=v.title, title_norm=norm_title(v.title),
        title_family_id=family, editorial_concept=concept, generation_round=round_no,
        status="candidate" if v.ok else "rejected",
        rejection_reason="; ".join(v.reasons)[:1000] or None,
        exact_collision=bool(col and col.exact),
        near_collision_score=col.near_score if col else None,
        semantic_collision_score=col.semantic_score if col else None,
        collision_with=(col.describe(col.exact_with or col.near_with or col.semantic_with)
                        if col else None),
        memorability_score=sc.get("memorability"), curiosity_score=sc.get("curiosity"),
        specificity_score=sc.get("specificity"), brevity_score=brevity_score(v.title),
        sensationalism_risk=sc.get("sensationalism_risk"), spoiler_risk=sc.get("spoiler_risk"),
        epistemic_risk=sc.get("epistemic_risk"),
        critic_json=json.dumps({**sc, "angle": v.angle, "rank_score": rank_score(sc, v.title)
                                if v.ok else None}, ensure_ascii=False),
        origin=origin)
    db.add(row)
    return row


def _family(db: Session, case: Case) -> tuple[str, str | None]:
    row = (db.query(CaseTitleCandidate)
           .filter(CaseTitleCandidate.case_id == case.id,
                   CaseTitleCandidate.title_family_id.isnot(None))
           .order_by(CaseTitleCandidate.id).first())
    return (row.title_family_id, row.editorial_concept) if row else ("TF_" + secrets.token_hex(4), None)


def _eligible(db: Session, case_id: int, language: str) -> list[CaseTitleCandidate]:
    return [c for c in _same_case_titles(db, case_id, language) if c.status != "rejected"]


def _enforce_exact(db: Session, case_id: int, language: str, keep: int) -> int:
    """Keep the best `keep` eligible titles; the rest are surplus."""
    rows = _eligible(db, case_id, language)
    selected = [r for r in rows if r.status == "selected"]
    rest = sorted((r for r in rows if r.status != "selected"),
                  key=lambda r: json.loads(r.critic_json or "{}").get("rank_score") or 0,
                  reverse=True)
    for r in rest[max(keep - len(selected), 0):]:
        r.status, r.rejection_reason = "rejected", "surplus (not among the best)"
    db.flush()
    return len(_eligible(db, case_id, language))


def mark_recommended(db: Session, case_id: int, language: str) -> None:
    rows = sorted(_eligible(db, case_id, language),
                  key=lambda r: json.loads(r.critic_json or "{}").get("rank_score") or 0,
                  reverse=True)
    for i, r in enumerate(rows):
        d = json.loads(r.critic_json or "{}")
        d["rank"], d["recommended"] = i + 1, i == 0
        r.critic_json = json.dumps(d, ensure_ascii=False)


async def fill_language(db: Session, case: Case, language: str, ctx: dict, gen, embedder=None,
                        ) -> dict:
    cfg = ai_config.case_naming
    target = cfg.candidates_per_language
    family, concept = _family(db, case)
    rounds = 0
    while len(_eligible(db, case.id, language)) < target and rounds < cfg.max_rounds:
        rounds += 1
        need = target - len(_eligible(db, case.id, language))
        avoid = [c.title for c in _same_case_titles(db, case.id, language)]
        corpus = get_corpus(db)
        avoid += [e.text for e in corpus.entries if e.kind in ("episode_title", "video_title")
                  and e.case_id != case.id][:60]
        payload = {"language": LANG_NAMES.get(language, language), "count": need + cfg.extra_per_round,
                   "case": {k: v for k, v in ctx.items() if k not in ("reveal_terms",
                                                                      "existing_research_titles")},
                   "family_concept": concept, "avoid": avoid[:120],
                   "other_language_titles": _others(db, case.id, language)}
        data, _ = await gen.generate_structured(
            "case_naming_agent", NAMING_SYSTEM, json.dumps(payload, ensure_ascii=False))
        data = data if isinstance(data, dict) else {}
        concept = concept or (str(data.get("editorial_concept") or "").strip()[:300] or None)
        cands = [c for c in data.get("candidates", []) if isinstance(c, dict) and c.get("title")]
        angles = {c["title"]: c.get("angle") for c in cands}
        verdicts = await evaluate_titles(db, case, language, [c["title"] for c in cands], ctx, gen,
                                         embedder, angles)
        for v in verdicts:
            _store(db, case, language, v, round_no=_next_round(db, case.id, language),
                   family=family, concept=concept)
        db.flush()
        _enforce_exact(db, case.id, language, target)
    mark_recommended(db, case.id, language)
    db.commit()
    n = len(_eligible(db, case.id, language))
    return {"language": language, "eligible": n, "rounds": rounds,
            "short_by": max(target - n, 0)}


def _next_round(db: Session, case_id: int, language: str) -> int:
    rows = _same_case_titles(db, case_id, language)
    return max((r.generation_round for r in rows), default=0) + 1


async def generate_case_titles(db: Session, case: Case, gen, embedder=None,
                               languages: list[str] | None = None) -> dict:
    """Exactly 7 eligible titles per language (or the honest shortfall)."""
    ctx = build_context(db, case)
    out = {}
    for lang in [l for l in ORDER if l in (languages or ORDER)]:
        out[lang] = await fill_language(db, case, lang, ctx, gen, embedder)
    fam, concept = _family(db, case)
    return {"case_id": case.id, "title_family_id": fam, "editorial_concept": concept,
            "status_known": ctx["resolution_status"] != "unknown", "languages": out}


async def evaluate_manual(db: Session, case: Case, language: str, title: str, gen,
                          embedder=None) -> CaseTitleCandidate:
    """A hand-written or edited title: the same gates, collision and
    critics as generated ones, before it can be approved."""
    ctx = build_context(db, case)
    title = clean_title(title)
    (v,) = await evaluate_titles(db, case, language, [title], ctx, gen, embedder) or [None]
    if v is None:
        raise ValueError("empty title")
    family, concept = _family(db, case)
    row = _store(db, case, language, v, round_no=_next_round(db, case.id, language),
                 family=family, concept=concept, origin="manual")
    db.commit()
    return row


class ApprovalRefused(ValueError):
    pass


def approve_candidate(db: Session, case: Case, candidate_id: int, *, revise: bool = False):
    """Select a title and write it to the episode identity. Deterministic
    checks run again first; a rejected candidate cannot be approved."""
    cand = db.get(CaseTitleCandidate, candidate_id)
    if cand is None or cand.case_id != case.id:
        raise ApprovalRefused("unknown candidate")
    if cand.status == "rejected":
        raise ApprovalRefused(f"rejected: {cand.rejection_reason}")
    ctx = build_context(db, case)
    reason = run_gates(cand.title, cand.language, ctx, _others(db, case.id, cand.language))
    col = COL.check_lexical(cand.title, cand.language, case.id, get_corpus(db),
                            skip_own_candidates=True)
    reason = reason or (col.reasons[0] if col.reasons else None)
    if reason:
        raise ApprovalRefused(reason)
    for other in _same_case_titles(db, case.id, cand.language):
        if other.status == "selected" and other.id != cand.id:
            other.status = "candidate"
    cand.status = "selected"
    ident = sync_identity(db, case, cand.language, title=cand.title, revise=revise)
    ident.title_family_id = cand.title_family_id
    ident.title_approved = True
    db.commit()
    return ident


def default_embedder():
    """The existing embedding client (bge-class model via the generation
    provider), or None when it is not configured — lexical checks still run."""
    try:
        from app.providers import get_research_provider

        emb = getattr(get_research_provider(), "embedder", None)
        return emb if emb is not None and emb.is_configured() else None
    except Exception:  # noqa: BLE001
        return None

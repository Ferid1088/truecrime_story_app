"""The one-pass evidence audit of the English draft.

An independent agent (Sol, a different model from the draft's writer) reads the draft in groups of
acts against the supplied facts and source excerpts ONLY and reports every claim they do not
support. It runs once per draft, its answer is stored with the draft version and shown, and it is a
report: it blocks nothing, repairs nothing, starts no further model call and changes no text. A
call that fails is recorded as failed and the pipeline goes on."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt, prompt_sha256
from app.db.models import Case, ProviderCall, StoryVersion
from app.language_pipeline.materials import SharedMaterial
from app.language_pipeline.stages import LanguageStages
from app.language_pipeline.store import structure_of
from app.providers.generation.base import GenerationError
from app.utils import utc_now

log = logging.getLogger(__name__)
AUDIT_STAGE = "english_audit"
AUDIT_PROMPT = "language_pipeline/audit/evidence_audit"
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
NOTE = "Report only: nothing was changed."


class AuditItem(BaseModel):
    id: int
    severity: Literal["high", "medium", "low"]
    kind: Literal["unsupported", "wrong_detail", "distortion", "certainty", "attribution",
                  "quote", "contradiction", "omission"]
    act: str = ""
    quote: str = ""
    claim: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: str = ""
    problem: str
    correction: str

    @field_validator("act", "quote", "claim", "evidence", mode="before")
    @classmethod
    def _blank(cls, value):
        return "" if value is None else str(value)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _ids(cls, value):
        return [] if value is None else [str(v) for v in value]


class AuditAnswer(BaseModel):
    verdict: Literal["pass", "issues"]
    items: list[AuditItem]
    summary: str

    @model_validator(mode="after")
    def _verdict_follows_items(self):
        if (self.verdict == "issues") != bool(self.items):
            raise ValueError('verdict is "issues" exactly when there are items')
        return self


def message(material: SharedMaterial, acts: list[dict]) -> str:
    """The user message: the stable keys first (identical in every call, so the provider caches
    them), the chunk last. Insertion order is kept; the keys are not sorted."""
    cfg = ai_config.language_pipeline.evidence_audit
    return json.dumps({
        "evidence": material.audit_evidence(),
        "key_fact_ids": material.key_fact_ids(cfg.key_fact_count),
        "chunk": {"acts": [{"act_id": a["id"], "text": a["text"]} for a in acts]},
    }, ensure_ascii=False, separators=(",", ":"))


def stable_part(user_message: str) -> str:
    """The leading part of the message that is the same in every call of one audit."""
    return user_message[:user_message.index(',"chunk":')]


def groups_of(sections: list[dict]) -> list[list[dict]]:
    size = ai_config.language_pipeline.evidence_audit.acts_per_call
    return [sections[i:i + size] for i in range(0, len(sections), size)]


def _window(db: Session, case_id: int, since) -> dict:
    row = db.query(
        func.count(ProviderCall.id).filter(ProviderCall.host == "budget-ledger"),
        func.coalesce(func.sum(ProviderCall.tokens_in), 0),
        func.coalesce(func.sum(ProviderCall.tokens_cached), 0),
        func.coalesce(func.sum(ProviderCall.tokens_reasoning), 0),
        func.coalesce(func.sum(ProviderCall.tokens_out), 0),
        func.coalesce(func.sum(ProviderCall.cost_eur), 0.0),
    ).filter(ProviderCall.case_id == case_id, ProviderCall.stage == AUDIT_STAGE,
             ProviderCall.is_test_data.is_(False), ProviderCall.created_at >= since).one()
    return {"calls": int(row[0]), "tokens_in": int(row[1]), "tokens_cached": int(row[2]),
            "tokens_reasoning": int(row[3]), "tokens_out": int(row[4]), "cost_eur": round(float(row[5]), 6)}


class EvidenceAudit:
    def __init__(self, stages: LanguageStages):
        self.stages = stages

    async def run(self, db: Session, case: Case, material: SharedMaterial,
                  draft: StoryVersion, sections: list[dict] | None = None) -> dict:
        """Audit the draft once: a chunk that is already audited (status ok) is not called again,
        a chunk that failed is tried again. Returns the merged report, stored with the version
        audited. `sections` are the acts of a text that keeps them only in its markers (a final)."""
        notes = json.loads(draft.critic_notes or "{}")
        sections = sections or [{"id": s["id"], "text": s["text"]} for s in structure_of(draft)["sections"]]
        done = {tuple(c["acts"]): c for c in (notes.get("evidence_audit") or {}).get("chunks", [])
                if c["status"] == "ok"}
        chunks = []
        for group in groups_of(sections):
            acts = tuple(s["id"] for s in group)
            chunk = done.get(acts) or await self._audit_chunk(db, case, material, group)
            chunks.append(chunk)
            self._store(db, draft, self.report(material, chunks, len(groups_of(sections))))
        return json.loads(draft.critic_notes or "{}")["evidence_audit"]

    async def _audit_chunk(self, db: Session, case: Case, material: SharedMaterial,
                           group: list[dict]) -> dict:
        cfg = ai_config.language_pipeline.evidence_audit
        acts = [s["id"] for s in group]
        user = message(material, group)
        system = prompt(AUDIT_PROMPT).format(max_items=cfg.max_items_per_call)
        began = utc_now()
        error = None
        for _ in range(cfg.invalid_output_retries + 1):
            try:
                data, res = await self.stages.call(
                    db, case, lambda: run_agent(
                        "english.evidence_audit", self.stages.gen, user, system=system,
                        cache_key=material.cache_key()),
                    "english_evidence_auditor", "English Evidence Auditor", AUDIT_STAGE, None,
                    f"acts {', '.join(acts)}")
                answer = AuditAnswer.model_validate(data)
            except ValidationError as e:
                error = f"the answer is not valid: {e}"
                continue
            except GenerationError as e:
                # a cut-off, refused or failed call: recorded, the pipeline goes on
                return self._chunk(acts, "failed", began, db, case.id, error=f"{e.kind}: {e}"[:500])
            return self._chunk(acts, "ok", began, db, case.id, answer=answer, model=res.model,
                               effort=res.reasoning_effort)
        return self._chunk(acts, "failed", began, db, case.id, error=(error or "")[:500])

    @staticmethod
    def carry_over(db: Session, new_version: StoryVersion, previous_report: dict, changed_acts: set[str],
                   material: SharedMaterial, sections: list[dict]) -> None:
        """Start the audit of a corrected text from the audit of the text it came from: the groups of
        acts the correction did not touch keep their audited result (and their items), so a run on the
        new version calls the model only for the groups that changed."""
        kept = [c for c in previous_report.get("chunks") or []
                if c["status"] == "ok" and not set(c["acts"]) & changed_acts]
        if kept:
            expected = len(groups_of(sections))
            EvidenceAudit._store(db, new_version, EvidenceAudit.report(material, kept, expected))

    @staticmethod
    def _chunk(acts, status, began, db, case_id, answer: AuditAnswer | None = None, model=None,
               effort=None, error=None) -> dict:
        return {"acts": acts, "status": status, "error": error, "model": model,
                "reasoning_effort": effort, "prompt_sha256": prompt_sha256(AUDIT_PROMPT),
                "verdict": answer.verdict if answer else None,
                "summary": answer.summary if answer else None,
                "items": [i.model_dump() for i in answer.items] if answer else [],
                "usage": _window(db, case_id, began)}

    @staticmethod
    def report(material: SharedMaterial, chunks: list[dict], expected: int) -> dict:
        """The chunks merged into one report: items most severe first and numbered again, counts by
        severity and kind, the models, the cost and the cached tokens. Each item carries the
        supplied facts it cites, so the page shows the evidence next to the finding."""
        facts = {f["id"]: f.get("claim") for f in material.draft_pack.get("facts") or []}
        items = sorted((i for c in chunks for i in c["items"]),
                       key=lambda i: (SEVERITY_RANK[i["severity"]], i["id"]))
        items = [{**i, "id": n, "evidence_facts": [{"id": e, "text": facts.get(e)} for e in i["evidence_ids"]]}
                 for n, i in enumerate(items, 1)]
        ok = [c for c in chunks if c["status"] == "ok"]
        status = "ok" if len(ok) == expected else ("failed" if not ok else "partial")
        usage: Counter = Counter()
        for c in chunks:
            usage.update(c["usage"])
        return {
            "status": status, "report_only": True, "note": NOTE,
            "verdict": ("issues" if items else "pass") if ok else "failed",
            "items": items,
            "counts": {"severity": dict(Counter(i["severity"] for i in items)),
                       "kind": dict(Counter(i["kind"] for i in items))},
            "chunks": chunks, "chunks_expected": expected,
            "models": sorted({c["model"] for c in ok if c["model"]}),
            "usage": {**usage, "cost_eur": round(usage["cost_eur"], 6)},
            "summary": " ".join(c["summary"] for c in ok if c["summary"]),
        }

    @staticmethod
    def _store(db: Session, draft: StoryVersion, report: dict) -> None:
        notes = json.loads(draft.critic_notes or "{}")
        notes["evidence_audit"] = report
        draft.critic_notes = json.dumps(notes, ensure_ascii=False)
        db.commit()

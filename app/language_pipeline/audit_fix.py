"""The correction of factual findings in the English text. The evidence audit only reports; a finding is
not true because it was reported. This step gives a model the findings, the evidence they cite, the
facts nearest to each quote, every contradiction and the timeline, and the paragraphs that carry or
repeat each claim. The model decides per finding (confirmed, false alarm, unclear) and gives the
smallest edit for the confirmed ones. The code applies an edit only where its wording occurs exactly
once in its act, so nothing else in the text moves."""

from __future__ import annotations

import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case
from app.language_pipeline.materials import SharedMaterial, ordered_dump
from app.language_pipeline.stages import (
    AUDIT_FIX_STAGE,
    LanguageStages,
    StageFailed,
)
from app.services.writer_metrics import words


class FixEdit(BaseModel):
    act: str
    old: str
    new: str = ""


class FixDecision(BaseModel):
    finding_id: int
    decision: Literal["confirmed", "false_alarm", "unclear"]
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    edits: list[FixEdit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _edits_only_when_confirmed(self):
        if self.edits and self.decision != "confirmed":
            raise ValueError("only a confirmed finding may carry edits")
        return self


class FixAnswer(BaseModel):
    decisions: list[FixDecision]
    summary: str = ""

    @model_validator(mode="after")
    def _one_decision_per_finding(self):
        ids = [d.finding_id for d in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("every finding gets exactly one decision")
        return self


def paragraphs_of(sections: list[dict]) -> list[dict]:
    """The paragraphs of the text with their act and position."""
    out = []
    for section in sections:
        for index, text in enumerate(p for p in re.split(r"\n\s*\n", section["text"]) if p.strip()):
            out.append({"act": section["id"], "index": index, "text": text.strip()})
    return out


def _trigrams(text: str) -> set[tuple]:
    w = words(text)
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def carries(quote: str, paragraph: str, threshold: float) -> bool:
    """The paragraph holds the quote, or enough of its word sequences to be a repeat of the claim."""
    if quote and " ".join(quote.split()) in " ".join(paragraph.split()):
        return True
    grams = _trigrams(quote)
    return bool(grams) and len(grams & _trigrams(paragraph)) / len(grams) >= threshold


class AuditFix:
    def __init__(self, stages: LanguageStages, embedder=None):
        self.stages = stages
        self.embedder = embedder

    # -- what goes to the model -----------------------------------------------------------
    @staticmethod
    def findings(report: dict) -> list[dict]:
        cfg = ai_config.language_pipeline.audit_fix
        rank = {"high": 0, "medium": 1, "low": 2}
        wanted = [i for i in report.get("items") or [] if i["severity"] in cfg.severities]
        wanted.sort(key=lambda i: (rank[i["severity"]], i["id"]))
        return [{"id": i["id"], "severity": i["severity"], "kind": i["kind"], "act": i["act"],
                 "quote": i["quote"], "claim": i["claim"], "problem": i["problem"],
                 "suggested_correction": i["correction"], "evidence_ids": i["evidence_ids"]}
                for i in wanted[:cfg.max_findings]]

    @staticmethod
    def paragraphs_for(findings: list[dict], sections: list[dict]) -> list[dict]:
        cfg = ai_config.language_pipeline.audit_fix
        paragraphs = paragraphs_of(sections)
        chosen: dict[tuple, dict] = {}
        for finding in findings:
            hits = [p for p in paragraphs if carries(finding["quote"], p["text"], cfg.repeat_overlap)]
            for p in hits[:cfg.max_paragraphs_per_finding]:
                chosen[(p["act"], p["index"])] = p
        return sorted(chosen.values(), key=lambda p: (p["act"], p["index"]))

    async def evidence_for(self, material: SharedMaterial, findings: list[dict]) -> dict:
        """The facts the findings cite, the facts nearest to each quote, and all contradictions and the
        timeline (small, and the place the audit's false alarms came from)."""
        cfg = ai_config.language_pipeline.audit_fix
        evidence = material.audit_evidence()
        by_id = {f["id"]: f for f in evidence["facts"]}
        picked: dict[str, dict] = {}
        for finding in findings:
            for fact_id in finding["evidence_ids"]:
                if fact_id in by_id:
                    picked[fact_id] = by_id[fact_id]
        if self.embedder is not None and cfg.evidence_per_finding and findings:
            facts = evidence["facts"]
            matrix = _unit(await self.embedder.embed([f["text"] or "" for f in facts]))
            queries = _unit(await self.embedder.embed([f["quote"] or f["claim"] for f in findings]))
            for row in queries @ matrix.T:
                for index in np.argsort(-row)[:cfg.evidence_per_finding]:
                    picked[facts[int(index)]["id"]] = facts[int(index)]
        return {"facts": [{"id": f["id"], "text": f["text"], "excerpt": f["excerpt"], "uncertain": f["uncertain"]}
                          for f in picked.values()],
                "contradictions": evidence["contradictions"], "timeline": evidence["timeline"]}

    @staticmethod
    def message(findings: list[dict], evidence: dict, paragraphs: list[dict]) -> str:
        return ordered_dump({"findings": findings, "evidence": evidence, "paragraphs": paragraphs})

    # -- the call -------------------------------------------------------------------------
    async def run(self, db: Session, case: Case, material: SharedMaterial, report: dict,
                  sections: list[dict]) -> dict:
        """Decide the audit's findings and apply the edits of the confirmed ones. Returns the fix report;
        its `sections` are the text after the edits (the input sections when nothing changed)."""
        findings = self.findings(report)
        if not findings:
            return {"status": "nothing_to_fix", "decisions": [], "sections": sections, "acts_changed": [],
                    "counts": {}, "applied": 0, "skipped": 0}
        evidence = await self.evidence_for(material, findings)
        paragraphs = self.paragraphs_for(findings, sections)
        message = self.message(findings, evidence, paragraphs)
        answer: FixAnswer | None = None
        for attempt in range(ai_config.limits_settings().language_pipeline.inspector_attempts):
            data, res = await self.stages.audit_fix(db, case, material, message)
            try:
                answer = FixAnswer.model_validate(data)
                break
            except ValidationError as e:
                if attempt + 1 == ai_config.limits_settings().language_pipeline.inspector_attempts:
                    raise StageFailed(AUDIT_FIX_STAGE, f"the audit fix answer is not valid after "
                                      f"{ai_config.limits_settings().language_pipeline.inspector_attempts} attempts: {e}") from e
        assert answer is not None   # the loop above returns or raises
        known = {f["id"] for f in findings}
        decisions = [d for d in answer.decisions if d.finding_id in known]
        new_sections, applied, skipped = self.apply(sections, decisions)
        by_id = {d.finding_id: d for d in decisions}
        report_rows = []
        for finding in findings:
            d = by_id.get(finding["id"])
            report_rows.append({
                "finding_id": finding["id"], "severity": finding["severity"], "kind": finding["kind"],
                "act": finding["act"], "quote": finding["quote"],
                "decision": d.decision if d else "unclear", "reason": d.reason if d else "no decision given",
                "evidence_ids": d.evidence_ids if d else [],
                "applied": [e for e in applied if e["finding_id"] == finding["id"]],
                "skipped": [e for e in skipped if e["finding_id"] == finding["id"]]})
        counts: dict[str, int] = {}
        for row in report_rows:
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
        changed = sorted({e["act"] for e in applied})
        return {"status": "ok", "decisions": report_rows, "summary": answer.summary, "counts": counts,
                "applied": len(applied), "skipped": len(skipped), "acts_changed": changed,
                "sections": new_sections, "result": res, "model": res.model, "model_calls": 1}

    @staticmethod
    def apply(sections: list[dict], decisions: list[FixDecision]) -> tuple[list[dict], list[dict], list[dict]]:
        """The edits of the confirmed findings, in order. An edit whose wording is not found in its act, or
        is found more than once, is skipped (and reported); every other character of the text stays."""
        texts = {s["id"]: s["text"] for s in sections}
        applied: list[dict] = []
        skipped: list[dict] = []
        for decision in decisions:
            for edit in decision.edits:
                record = {"finding_id": decision.finding_id, "act": edit.act, "old": edit.old, "new": edit.new}
                text = texts.get(edit.act)
                if text is None or not edit.old or text.count(edit.old) != 1:
                    skipped.append({**record, "reason": "act unknown" if text is None else
                                    "wording not found exactly once"})
                    continue
                texts[edit.act] = _tidy(text.replace(edit.old, edit.new, 1))
                applied.append(record)
        return [{**s, "text": texts[s["id"]]} for s in sections], applied, skipped


def _tidy(text: str) -> str:
    """After a removal: no double space and no empty paragraph left behind."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _unit(matrix) -> np.ndarray:
    m = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)

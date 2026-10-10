"""The NarrativeDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.blueprint."""

from __future__ import annotations

import json
from sqlalchemy.orm import Session
from app.agents.story import build_evidence_pack, evidence_fingerprint, is_structured, stored_evidence_fingerprint
from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, EditorialBlueprint, Fact, Source, StoryVersion
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.blueprint import (
    _call_director,
    _director_input,
    director_system_prompt,
    validate_blueprint,
    version_sections,
)


class NarrativeDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    async def create(self, db: Session, case: Case, version: StoryVersion
                     ) -> EditorialBlueprint:
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all(),
        )
        fingerprint = evidence_fingerprint(pack)
        stored_fp = stored_evidence_fingerprint(version)
        if stored_fp and stored_fp != fingerprint:
            raise RuntimeError(
                f"Story v{version.version} was written from an older evidence "
                "set (research changed since); its evidence ids no longer match. "
                "Regenerate the story before designing its blueprint."
            )
        sections = version_sections(version)
        system = director_system_prompt()
        payload = _director_input(sections, pack)

        with track_run(db, case.id, "Narrative Director",
                       input_summary=f"story_v{version.version} acts={len(sections)}") as run:
            raw, res = await _call_director(self.gen, system, payload)
            stamp_run(run, res, "narrative_director")
            run.output_summary = f"beats={len((raw or {}).get('beats') or [])}"
        blueprint, report = validate_blueprint(raw, sections, pack)

        repairs = 0
        while report["errors"] and repairs < ai_config.blueprint.max_repair_iterations:
            repairs += 1
            repair_payload = {
                **payload,
                "previous_blueprint": raw,
                "errors_to_fix": report["errors"],
                "instruction": "Return the corrected complete blueprint JSON. "
                               "Fix every listed error; keep everything else.",
            }
            with track_run(db, case.id, "Narrative Director",
                           input_summary=f"repair {repairs}: "
                                         f"{len(report['errors'])} errors") as run:
                raw, res = await _call_director(self.gen, system, repair_payload)
                stamp_run(run, res, "narrative_director")
                run.output_summary = f"beats={len((raw or {}).get('beats') or [])}"
            blueprint, report = validate_blueprint(raw, sections, pack)

        # One improvement round for listener-load warnings; kept only if
        # it is error-free and strictly better — never makes it worse.
        revisions = 0
        revise_codes = set(ai_config.blueprint.revise_on_warnings)

        def revisable(rep: dict) -> list[dict]:
            return [w for w in rep["warnings"] if w["code"] in revise_codes]

        while (not report["errors"] and revisable(report)
               and revisions < ai_config.blueprint.max_revision_iterations):
            revisions += 1
            revise_payload = {
                **payload,
                "previous_blueprint": raw,
                "warnings_to_improve": revisable(report),
                "instruction": "Return the improved complete blueprint JSON. "
                               "Resolve the listed listener-load problems "
                               "(split long beats, close or merge questions, "
                               "add recovery) without changing anything that "
                               "is already right.",
            }
            with track_run(db, case.id, "Narrative Director",
                           input_summary=f"revision {revisions}: "
                                         f"{len(revisable(report))} warnings") as run:
                cand_raw, cand_res = await _call_director(
                    self.gen, system, revise_payload
                )
                stamp_run(run, cand_res, "narrative_director")
            cand_bp, cand_rep = validate_blueprint(cand_raw, sections, pack)
            better = not cand_rep["errors"] and (
                len(revisable(cand_rep)) < len(revisable(report))
            )
            report.setdefault("revision_log", []).append({
                "revision": revisions, "accepted": better,
                "warnings_before": len(revisable(report)),
                "warnings_after": len(revisable(cand_rep)),
                "errors_after": len(cand_rep["errors"]),
            })
            if better:
                log = report["revision_log"]
                raw, res, blueprint, report = cand_raw, cand_res, cand_bp, cand_rep
                report["revision_log"] = log
        report["repair_iterations"] = repairs
        report["revision_iterations"] = revisions
        report["structured_story"] = is_structured(sections)

        count = (
            db.query(EditorialBlueprint)
            .filter(EditorialBlueprint.story_version_id == version.id)
            .count()
        )
        row = EditorialBlueprint(
            case_id=case.id, story_version_id=version.id, version=count + 1,
            status=report["status"], evidence_fingerprint=fingerprint,
            story_text_hash=version.text_hash,
            central_question=blueprint["central_question"] or None,
            editorial_thesis=blueprint["editorial_thesis"] or None,
            human_thread=blueprint["human_thread"] or None,
            blueprint_json=json.dumps(blueprint, ensure_ascii=False),
            validation_json=json.dumps(report, ensure_ascii=False),
            generation_provider=getattr(res, "provider", None),
            generation_model=getattr(res, "model", None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

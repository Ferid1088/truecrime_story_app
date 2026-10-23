"""The model stages of the shared draft and of each language's film version. Each is one
agent call: it builds the request (a stable shared prefix first, the variable part last), runs
the agent through the cost ledger and returns the text with what the provider reported. Storing
and gating live elsewhere. Stages 2-4 run the agents the language's entry in
language_pipeline.language_agents names (the generic ones without an entry)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.agents.story import (
    _MARKER_INSTRUCTION,
    _mark_sections,
    _parse_sections,
    realign_sections,
    strip_section_markers,
)
from app.core.ai_config import ai_config
from app.core.prompts import prompt, prompt_sha256
from app.db.models import Case
from app.language_pipeline.film_schema import FidelityReport, FilmInspection
from app.language_pipeline.lengths import (
    LengthRange,
    act_word_targets,
    draft_min_words,
    draft_words,
)
from app.language_pipeline.materials import SharedMaterial, dump, ordered_dump
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationResult
from app.services.tracking import documentary_run_context, stamp_run, track_run

DRAFT_STAGE = "language_draft"
REWRITE_STAGE = "language_rewrite"
INSPECT_STAGE = "language_inspect"
DRAFT_REVIEW_STAGE = "language_draft_review"
FINAL_STAGE = "language_final"
LENGTH_CORRECTION_STAGE = "length_correction"
REVIEW_STAGE = "language_review"
FIDELITY_STAGE = "language_fidelity"
AUDIT_FIX_STAGE = "english_audit_fix"
# After audit_fix.max_fix_rounds LLM rounds still leave a confirmed finding open: its flagged quote
# is removed deterministically (no model call) instead of looping or publishing it unresolved.
DETERMINISTIC_FIX_STAGE = "deterministic_fix"


class StageFailed(RuntimeError):
    """A stage returned something the pipeline cannot use."""

    def __init__(self, stage: str, reason: str, detail: dict | None = None):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason, self.detail = stage, reason, detail or {}


@dataclass
class StageOutput:
    stage: str
    language: str
    text: str
    sections: list[dict]
    markers_ok: bool
    result: GenerationResult
    prompt_name: str
    prompt_sha256: str
    data: dict = field(default_factory=dict)

    @property
    def usage(self) -> dict:
        r = self.result
        return {"tokens_in": r.input_tokens, "tokens_cached": r.cached_tokens,
                "tokens_reasoning": r.reasoning_tokens, "tokens_out": r.output_tokens,
                "reasoning_effort": r.reasoning_effort, "model": r.model}


def language_name(code: str) -> str:
    return ai_config.multilingual.language_catalog[code].name


def style_guide(code: str) -> str:
    return prompt(f"language_pipeline/guides/{code}")


def stage_name(stage: str, language: str | None) -> str:
    return f"{stage}:{language}" if language else stage


def normalize_markers(text: str) -> tuple[str, str]:
    """A film text starts at its first act marker and every marker stands alone on its line. A model that
    puts a remark about its own work before the first marker, or a marker in the middle of a line, breaks
    the format and nothing else: the text from the first marker on is the narration, the remark is
    returned to be recorded. A text without a marker is returned unchanged (and fails the marker gate)."""
    prefix = ai_config.story_quality.section_marker_prefix
    first = text.find(prefix)
    if first < 0:
        return text, ""
    lead, body = text[:first].strip(), text[first:]
    marker = re.escape(prefix) + r"[^\]\n]+\]\]"
    body = re.sub(rf"(?<!\n)({marker})", r"\n\n\1", body)
    body = re.sub(rf"({marker})[ \t]*(?=[^\s])", r"\1\n\n", body)
    return body.strip(), lead


def marked(sections: list[dict]) -> str:
    """The text in act markers, as the writers and inspectors receive and return it."""
    return _mark_sections(sections)


class LanguageStages:
    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _sections(raw: str, material: SharedMaterial) -> tuple[list[dict], bool]:
        previous = [{"id": i, "text": ""} for i in material.act_ids]
        return realign_sections(previous, raw, allow_paragraph_fallback=False)

    async def call(self, db: Session, case: Case, call, role: str, label: str,
                   stage: str, language: str | None, summary: str):
        """`call` is a coroutine function that runs the agent; it is tracked in the ledger
        under this stage and language."""
        with documentary_run_context(
                case_id=case.id, language=language, pipeline_stage=stage_name(stage, language)), \
                track_run(db, case.id, label, input_summary=summary) as run:
            out = await call()
            result = out[1] if isinstance(out, tuple) else out
            stamp_run(run, result, role)
            run.output_summary = (f"tokens_in={result.input_tokens} cached={result.cached_tokens} "
                                  f"reasoning={result.reasoning_tokens} out={result.output_tokens}")
        return out

    def _output(self, stage: str, language: str, text: str, material: SharedMaterial,
                result: GenerationResult, prompt_name: str, data: dict | None = None) -> StageOutput:
        text, lead_in = normalize_markers(text)
        sections, ok = self._sections(text, material)
        return StageOutput(
            stage=stage, language=language, text=text, sections=sections, markers_ok=ok,
            result=result, prompt_name=prompt_name, prompt_sha256=prompt_sha256(prompt_name),
            data={**(data or {}), **({"removed_lead_in": lead_in} if lead_in else {})})

    # -- stage 1 -------------------------------------------------------------
    @staticmethod
    def scenes(act: dict) -> list[str | None]:
        """The scenes of an act: one per key beat (an act without key beats is one scene)."""
        return list(act.get("key_beats") or [None])

    @classmethod
    def scene_target_words(cls, material: SharedMaterial, act: dict, count: int = 1) -> int:
        """The words of `count` consecutive beats of the act (its share of the story over its beats)."""
        act_words = material.act_shares()[str(act["id"])] * draft_words()
        return round(act_words / len(cls.scenes(act)) * count)

    @classmethod
    def scene_groups(cls, act: dict, size: int) -> list[tuple[int, int]]:
        """(first beat, number of beats) of each call: `size` consecutive beats of the act at a time."""
        total = len(cls.scenes(act))
        return [(first, min(size, total - first)) for first in range(0, total, size)]

    async def draft_scene(self, db: Session, case: Case, material: SharedMaterial, tone: str,
                          act: dict, index: int, story_so_far: str, told_so_far: dict,
                          extra_evidence_ids: list[str], correction: str | None = None, count: int = 1,
                          prefix: str | None = None) -> StageOutput:
        """One scene of the shared draft (`count` consecutive key beats told as one part), told after
        everything already written and with the ledger of what that told. The call carries the same shared
        prefix every time (cached): the whole evidence block, or the package of its act when `prefix` is given."""
        cfg = ai_config.language_pipeline
        language = cfg.draft_language
        act_id = str(act["id"])
        beats = self.scenes(act)
        order = material.act_ids
        task = {
            "case": case.canonical_title, "language": language, "target_words": draft_words(),
            "target_minutes": ai_config.language_pipeline.long_draft_minutes,
            "act": {**{k: act.get(k) for k in ("id", "title", "purpose", "key_beats", "open_loops",
                                               "resolved_loops", "do_not_reveal", "evidence_ids")
                       if act.get(k) is not None},
                    "extra_evidence_ids": extra_evidence_ids},
            "beat": " | ".join(b for b in beats[index:index + count] if b) or None,
            "beat_index": index + 1, "beats_in_act": len(beats),
            "beats_after_in_this_act": [b for b in beats[index + count:] if b],
            "unit_target_words": self.scene_target_words(material, act, count),
            "story_so_far": story_so_far, "told_so_far": told_so_far,
            "acts_after": [{k: a.get(k) for k in ("id", "title", "purpose")}
                           for a in material.acts if order.index(str(a["id"])) > order.index(act_id)],
        }
        if correction:
            task["correction"] = correction
        res = await self.call(
            db, case, lambda: run_agent(
                "language.draft", self.gen, dump(task), prefix=prefix or material.draft_block(),
                cache_key=f"{material.cache_key()}:{act_id}" if prefix else material.cache_key(),
                tone=tone, target_words=draft_words(),
                marker_instruction=_MARKER_INSTRUCTION),
            "language_draft_writer", "Language Draft Writer", DRAFT_STAGE, None,
            f"shared draft, act {act_id} scene {index + 1}-{index + count}/{len(beats)}")
        text = strip_section_markers(res.text)
        return StageOutput(
            stage=DRAFT_STAGE, language=language, text=text, sections=[{"id": act_id, "text": text}],
            markers_ok=True, result=res, prompt_name="language_pipeline/draft_writer",
            prompt_sha256=prompt_sha256("language_pipeline/draft_writer"))

    # -- stages 2-4: the agents of the language's entry in language_agents ---------------------
    def _runner(self, table: dict, agent: str):
        try:
            return table[agent]
        except KeyError:
            raise StageFailed(agent, f"no stage code runs the agent {agent!r}") from None

    @property
    def _rewriters(self) -> dict:
        return {"language.rewrite.en": self._rewrite_en,
                "language.rewrite.fa": self._rewrite_fa, "language.rewrite.de": self._rewrite_de,
                "language.rewrite.ar": self._rewrite_ar}

    @property
    def _inspectors(self) -> dict:
        return {"language.inspect.en": self._inspect_en,
                "language.inspect.fa": self._inspect_fa, "language.inspect.de": self._inspect_de,
                "language.inspect.ar": self._inspect_ar}

    @property
    def _finalizers(self) -> dict:
        return {"language.final.en": self._final_en,
                "language.final.fa": self._final_fa, "language.final.de": self._final_de,
                "language.final.ar": self._final_ar}

    # -- stage 2 -------------------------------------------------------------
    async def rewrite(self, db: Session, case: Case, material: SharedMaterial, source_text: str,
                      language: str, span: LengthRange, review: list[dict] | None = None) -> StageOutput:
        """The film text of one language from the approved English master (`source_text`). `review` is
        the editorial review of the draft (its items), handed only to the language told from the draft."""
        agent = ai_config.language_pipeline.agents_for(language).rewrite
        return await self._runner(self._rewriters, agent)(
            db, case, material, source_text, language, span, review)

    @classmethod
    def word_counts(cls, text: str, material: SharedMaterial) -> dict[str, int]:
        """Words per act of a text in act markers."""
        sections, _ = cls._sections(text, material)
        return {s["id"]: len(s["text"].split()) for s in sections}

    @staticmethod
    def act_targets(material: SharedMaterial, language: str, span: LengthRange) -> dict[str, int]:
        """The words each act of this language's film text is written to (see lengths)."""
        return act_word_targets(material.act_plan_words(), span,
                                ai_config.language_pipeline.stage_config_for(language).act_target_cap_ratio)

    def _prompt_of(self, agent: str, **fmt) -> str:
        return prompt(ai_config.agents[agent].prompt).format(**fmt)

    def _native_rewrite_request(self, agent: str, material, source_text, language, span,
                                review: list[dict] | None = None) -> tuple[str, str]:
        """The user message (stable keys first, in the contract's order) and the formatted system
        prompt of a native film language's stage 2: the master and the plan, no evidence; the draft's
        editorial review last, when there is one."""
        task = ordered_dump({"language": language, "plan": material.film_plan_view(),
                             "target_minutes_min": span.minutes_min, "target_minutes_max": span.minutes_max,
                             "target_words_min": span.words_min, "target_words_max": span.words_max,
                             "act_word_targets": self.act_targets(material, language, span),
                             "english_master": source_text,
                             **({"draft_review": review} if review is not None else {})})
        system = self._prompt_of(agent, marker_instruction=_MARKER_INSTRUCTION,
                                 style_guide=style_guide(language), target_words_min=span.words_min,
                                 target_words_max=span.words_max)
        return task, system

    async def _native_rewrite(self, db, case, material, language, span, run, agent: str) -> StageOutput:
        res = await self.call(db, case, run, ai_config.agents[agent].role, "Language Rewrite Writer",
                              REWRITE_STAGE, language, f"{language} {span.minutes_min:g}-{span.minutes_max:g}min")
        return self._output(REWRITE_STAGE, language, res.text.strip(), material, res,
                            ai_config.prompt_of(agent))

    async def _rewrite_en(self, db, case, material, source_text, language, span, review=None):
        task, system = self._native_rewrite_request("language.rewrite.en", material, source_text, language,
                                                    span, review)
        return await self._native_rewrite(
            db, case, material, language, span, lambda: run_agent(
                "language.rewrite.en", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.rewrite.en")

    async def _rewrite_fa(self, db, case, material, source_text, language, span, review=None):
        task, system = self._native_rewrite_request("language.rewrite.fa", material, source_text, language,
                                                    span, review)
        return await self._native_rewrite(
            db, case, material, language, span, lambda: run_agent(
                "language.rewrite.fa", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.rewrite.fa")

    async def _rewrite_de(self, db, case, material, source_text, language, span, review=None):
        task, system = self._native_rewrite_request("language.rewrite.de", material, source_text, language,
                                                    span, review)
        return await self._native_rewrite(
            db, case, material, language, span, lambda: run_agent(
                "language.rewrite.de", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.rewrite.de")

    async def _rewrite_ar(self, db, case, material, source_text, language, span, review=None):
        task, system = self._native_rewrite_request("language.rewrite.ar", material, source_text, language,
                                                    span, review)
        return await self._native_rewrite(
            db, case, material, language, span, lambda: run_agent(
                "language.rewrite.ar", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.rewrite.ar")

    # -- stage 3 -------------------------------------------------------------
    async def inspect(self, db: Session, case: Case, material: SharedMaterial, text: str,
                      language: str, span: LengthRange, stage: str = INSPECT_STAGE) -> StageOutput:
        """The language's inspector on a text. `stage` names the ledger line: the inspection of the
        rewrite (language_inspect) or the review of the delivered text (language_review)."""
        agent = ai_config.language_pipeline.agents_for(language).inspect
        return await self._runner(self._inspectors, agent)(db, case, material, text, language, span, stage)

    def _native_inspect_request(self, agent: str, material, text, language, span) -> tuple[str, str]:
        """Stage 3 of a native film language: the text, the plan and the counts; never the master,
        never evidence."""
        task = ordered_dump({"language": language, "plan": material.inspector_plan_view(),
                             "word_count": len(text.split()),
                             "allowed_word_range": {"min": span.words_min, "max": span.words_max},
                             "story": text})
        return task, self._prompt_of(agent, style_guide=style_guide(language))

    async def _native_inspect(self, db, case, material, text, language, run, agent: str,
                              stage: str = INSPECT_STAGE) -> StageOutput:
        """One call; an answer that does not validate is asked for once more, a second one fails the stage."""
        for attempt in range(ai_config.limits_settings().language_pipeline.inspector_attempts):
            data, res = await self.call(db, case, run, "language_inspector", "Language Inspector",
                                        stage, language, f"{language} {len(text.split())} words")
            try:
                inspection = FilmInspection.model_validate(data)
                break
            except ValidationError as e:
                if attempt + 1 == ai_config.limits_settings().language_pipeline.inspector_attempts:
                    raise StageFailed(stage, f"the {language} inspector's answer is not valid "
                                      f"after {ai_config.limits_settings().language_pipeline.inspector_attempts} attempts: {e}") from e
        return self._output(stage, language, text, material, res, ai_config.prompt_of(agent),
                            data=self.film_critique(inspection))

    async def _inspect_en(self, db, case, material, text, language, span, stage):
        task, system = self._native_inspect_request("language.inspect.en", material, text, language, span)
        return await self._native_inspect(
            db, case, material, text, language, lambda: run_agent(
                "language.inspect.en", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.inspect.en", stage)

    async def _inspect_fa(self, db, case, material, text, language, span, stage):
        task, system = self._native_inspect_request("language.inspect.fa", material, text, language, span)
        return await self._native_inspect(
            db, case, material, text, language, lambda: run_agent(
                "language.inspect.fa", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.inspect.fa", stage)

    async def _inspect_de(self, db, case, material, text, language, span, stage):
        task, system = self._native_inspect_request("language.inspect.de", material, text, language, span)
        return await self._native_inspect(
            db, case, material, text, language, lambda: run_agent(
                "language.inspect.de", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.inspect.de", stage)

    async def _inspect_ar(self, db, case, material, text, language, span, stage):
        task, system = self._native_inspect_request("language.inspect.ar", material, text, language, span)
        return await self._native_inspect(
            db, case, material, text, language, lambda: run_agent(
                "language.inspect.ar", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.inspect.ar", stage)

    @staticmethod
    def film_critique(inspection: FilmInspection) -> dict:
        """The validated answer in the stored shape; items sorted high first and capped."""
        rank = {"high": 0, "medium": 1, "low": 2}
        items = sorted((i.model_dump() for i in inspection.items), key=lambda i: rank[i["severity"]])
        return {"verdict": inspection.verdict, "scores": inspection.scores,
                "items": items[:ai_config.language_pipeline.max_inspector_items],
                "summary": inspection.summary}

    # -- the meaning check of a translated language against the approved English text ----------
    @property
    def _fidelity_checkers(self) -> dict:
        return {"language.fidelity": self._fidelity}

    async def fidelity(self, db: Session, case: Case, material: SharedMaterial, language: str,
                       english_text: str, text: str) -> tuple[FidelityReport, GenerationResult]:
        """Does the film text tell what the approved English text tells? A comparison of two texts:
        no evidence goes in. An answer that does not validate is asked for once more, like the
        inspector's; a second failure fails the check."""
        agent = ai_config.language_pipeline.agents_for(language).fidelity
        if not agent:
            raise StageFailed(FIDELITY_STAGE, f"language_pipeline.language_agents.{language} has no fidelity agent")
        return await self._runner(self._fidelity_checkers, agent)(db, case, material, language, english_text, text)

    async def _fidelity(self, db, case, material, language, english_text, text):
        task = ordered_dump({"language": language_name(language), "english_master": english_text, "story": text})
        system = self._prompt_of("language.fidelity")
        for attempt in range(ai_config.limits_settings().language_pipeline.inspector_attempts):
            data, res = await self.call(
                db, case, lambda: run_agent("language.fidelity", self.gen, task, system=system,
                                            cache_key=material.cache_key()),
                "english_evidence_auditor", "Language Fidelity Check", FIDELITY_STAGE, language,
                f"{language} {len(text.split())} words")
            try:
                return FidelityReport.model_validate(data), res
            except ValidationError as e:
                if attempt + 1 == ai_config.limits_settings().language_pipeline.inspector_attempts:
                    raise StageFailed(FIDELITY_STAGE, f"the {language} fidelity answer is not valid "
                                      f"after {ai_config.limits_settings().language_pipeline.inspector_attempts} attempts: {e}") from e

    async def audit_fix(self, db: Session, case: Case, material: SharedMaterial, message: str):
        """The findings of the evidence audit checked against the evidence, with the edit for each
        confirmed one. Returns (data, result); the caller validates the answer."""
        system = self._prompt_of("english.audit_fix")
        return await self.call(
            db, case, lambda: run_agent("english.audit_fix", self.gen, message, system=system,
                                        cache_key=material.cache_key()),
            "english_evidence_auditor", "English Audit Fix", AUDIT_FIX_STAGE, None, "audit findings")

    # -- stage 4 -------------------------------------------------------------
    async def finalize(self, db: Session, case: Case, material: SharedMaterial, language: str,
                       span: LengthRange, last_version: str, critique: dict,
                       source_text: str) -> StageOutput:
        """Stage 4: the last version and the critique, the English master as a read-only reference."""
        agent = ai_config.language_pipeline.agents_for(language).final
        return await self._runner(self._finalizers, agent)(
            db, case, material, language, span, last_version, critique, source_text)

    def _native_final_request(self, agent: str, material, last_version, critique, language, span,
                              source_text) -> tuple[str, str]:
        """Stage 4 of a native film language: the stable keys (plan, range, the master as a read-only
        reference) first, the last version and the critique last."""
        task = ordered_dump({"language": language, "plan": material.film_plan_view(),
                             "target_words_min": span.words_min, "target_words_max": span.words_max,
                             "act_word_targets": self.act_targets(material, language, span),
                             "english_master": source_text, "last_version": last_version,
                             "last_version_word_counts": self.word_counts(last_version, material),
                             "critique": critique})
        system = self._prompt_of(agent, marker_instruction=_MARKER_INSTRUCTION,
                                 style_guide=style_guide(language), target_words_min=span.words_min,
                                 target_words_max=span.words_max)
        return task, system

    async def _native_final(self, db, case, material, language, run, agent: str) -> StageOutput:
        res = await self.call(db, case, run, ai_config.agents[agent].role, "Language Final Writer",
                              FINAL_STAGE, language, f"{language} final")
        return self._output(FINAL_STAGE, language, res.text.strip(), material, res,
                            ai_config.prompt_of(agent))

    async def _final_en(self, db, case, material, language, span, last_version, critique,
                        source_text):
        task, system = self._native_final_request("language.final.en", material, last_version, critique,
                                                  language, span, source_text)
        return await self._native_final(
            db, case, material, language, lambda: run_agent(
                "language.final.en", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.final.en")

    async def _final_fa(self, db, case, material, language, span, last_version, critique,
                        source_text):
        task, system = self._native_final_request("language.final.fa", material, last_version, critique,
                                                  language, span, source_text)
        return await self._native_final(
            db, case, material, language, lambda: run_agent(
                "language.final.fa", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.final.fa")

    async def _final_de(self, db, case, material, language, span, last_version, critique,
                        source_text):
        task, system = self._native_final_request("language.final.de", material, last_version, critique,
                                                  language, span, source_text)
        return await self._native_final(
            db, case, material, language, lambda: run_agent(
                "language.final.de", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.final.de")

    async def _final_ar(self, db, case, material, language, span, last_version, critique,
                        source_text):
        task, system = self._native_final_request("language.final.ar", material, last_version, critique,
                                                  language, span, source_text)
        return await self._native_final(
            db, case, material, language, lambda: run_agent(
                "language.final.ar", self.gen, task, system=system, cache_key=material.cache_key()),
            "language.final.ar")

    # -- stage 5: the bounded length correction (at most once per chain) ---------------------
    @property
    def _correctors(self) -> dict:
        return {"language.length_correction": self._correct,
                "language.length_correction.fa_ar": self._correct_fa_ar}

    async def correct_length(self, db: Session, case: Case, material: SharedMaterial, language: str,
                             acts: list[dict]) -> StageOutput:
        """Expand only the listed acts (each: act_id, current_words, target_words, shortfall_words,
        current_text, master_text); the answer holds those acts, with their markers, and nothing else."""
        agent = ai_config.language_pipeline.agents_for(language).length_correction
        return await self._runner(self._correctors, agent)(db, case, material, language, acts)

    async def _correct(self, db, case, material, language, acts) -> StageOutput:
        return await self._correction(
            db, case, material, language, acts, "language.length_correction",
            lambda task, system: run_agent("language.length_correction", self.gen, task, system=system,
                                           cache_key=material.cache_key()))

    async def _correct_fa_ar(self, db, case, material, language, acts) -> StageOutput:
        return await self._correction(
            db, case, material, language, acts, "language.length_correction.fa_ar",
            lambda task, system: run_agent("language.length_correction.fa_ar", self.gen, task, system=system,
                                           cache_key=material.cache_key()))

    async def _correction(self, db, case, material, language, acts, agent: str, call) -> StageOutput:
        requested = [a["act_id"] for a in acts]
        task = ordered_dump({"language": language, "plan_for_acts": material.correction_plan_view(requested),
                             "acts_to_expand": acts})
        system = self._prompt_of(agent, marker_instruction=_MARKER_INSTRUCTION,
                                 style_guide=style_guide(language))
        res = await self.call(
            db, case, lambda: call(task, system), ai_config.agents[agent].role, "Length Correction",
            LENGTH_CORRECTION_STAGE, language, f"{language} acts {', '.join(requested)}")
        text, lead_in = normalize_markers(res.text.strip())
        sections = _parse_sections(text)
        return StageOutput(
            stage=LENGTH_CORRECTION_STAGE, language=language, text=text, sections=sections,
            markers_ok=[s["id"] for s in sections] == requested, result=res,
            prompt_name=ai_config.prompt_of(agent),
            prompt_sha256=prompt_sha256(ai_config.prompt_of(agent)),
            data={"requested": requested, **({"removed_lead_in": lead_in} if lead_in else {})})


def check_draft(sections: list[dict], requested_minutes: float | None = None) -> list[str]:
    """Why the assembled draft cannot feed the four languages (empty when it can)."""
    words = sum(len(s["text"].split()) for s in sections)
    minimum = draft_min_words(requested_minutes)
    if words >= minimum:
        return []
    return [f"draft is {words} words; at least {minimum} are needed (as long as the longest "
            f"final text it feeds); the target is {draft_words()}"]





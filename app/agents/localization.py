import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, Fact, Source, StoryVersion
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.utils import language_quality, text_hash
from app.agents.story import (
    _MARKER_INSTRUCTION,
    EngagementCritic,
    StoryPipeline,
    _join_sections,
    _mark_sections,
    build_evidence_pack,
    evidence_fingerprint,
    is_structured,
    realign_sections,
    stored_evidence_fingerprint,
    stored_sections,
    strip_section_markers,
    structure_without_text,
)

# Native-style critic guidance per target language. Prompts are evaluated
# IN the target language's critical vocabulary, not translated from English.
_NATIVE_CRITERIA = {
    "fa": (
        "ارزیابی را به زبان فارسی انجام بده و این معیارها را بسنج:\n"
        "- آیا متن واقعاً طبیعی و بومی است؟\n"
        "- آیا لحن شبیه ترجمه ماشینی است؟\n"
        "- آیا جمله‌بندی فارسی طبیعی است؟\n"
        "- آیا ریتم داستان مناسب روایت ویدئویی است؟\n"
        "- آیا تعلیق در فارسی کار می‌کند؟\n"
        "- آیا واژه‌ها و اصطلاحات طبیعی هستند؟\n"
        "- آیا متن بیش از حد رسمی یا کتابی شده؟\n"
        "- آیا متن حس نویسنده فارسی‌زبان دارد؟"
    ),
    "ar": (
        "قيّم النص بالعربية وراعِ هذه المعايير:\n"
        "- انسياب السرد العربي الأصيل\n"
        "- غياب تراكيب الترجمة الحرفية\n"
        "- اتساق المستوى اللغوي (الفصحى المعيارية ما لم يُطلب غير ذلك)\n"
        "- إيقاع السرد المناسب للرواية المرئية\n"
        "- الطبيعية الثقافية"
    ),
    "de": (
        "Bewerte den Text auf Deutsch nach diesen Kriterien:\n"
        "- natürliche deutsche Erzählstimme\n"
        "- keine englische Satzstruktur, keine Übersetzungsartefakte\n"
        "- passender True-Crime-Ton\n"
        "- natürliches Erzähltempo"
    ),
}


class LocalizationPipeline:
    """English Master -> native transcreation -> multi-validator gating.

    Localizations never reinterpret research: they derive ONLY from a
    specific ready Master version plus its protected fact/uncertainty map.
    Facts, dates, names, chronology and uncertainty are invariant; rhythm,
    idiom, transitions and suspense cadence are free.
    """

    def __init__(self):
        self.gen = get_generation_provider()
        self.critic = EngagementCritic()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def localize(
        self,
        db: Session,
        case: Case,
        master: StoryVersion,
        language: str,
        target_minutes: int | None = None,
    ) -> StoryVersion:
        loc = ai_config.localization
        if language == ai_config.multilingual.canonical_language:
            raise ValueError("Canonical language is the Master, not a localization.")
        if language not in ai_config.multilingual.localization_languages:
            raise ValueError(f"Language {language!r} is not a configured localization target.")
        if loc.require_master_ready and master.status != "ready":
            raise RuntimeError(
                f"Master v{master.version} is '{master.status}' — localization "
                "requires a ready Master."
            )
        if master.kind != "master":
            raise ValueError("Localization must derive from a master StoryVersion.")

        wpm = ai_config.words_per_minute_for(language)
        minutes = target_minutes or ai_config.story.default_target_minutes
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all(),
        )
        fingerprint = evidence_fingerprint(pack)
        master_fp = stored_evidence_fingerprint(master)
        if loc.require_current_evidence and master_fp and master_fp != fingerprint:
            # Evidence IDs in the master's plan (F012, …) would now point
            # at different facts, and grounding would judge the story
            # against evidence it was never written from.
            raise RuntimeError(
                f"Master v{master.version} was written from an older evidence "
                "set (research changed since). Regenerate the master before "
                "localizing."
            )
        protected = self._protected_payload(master, pack)

        # Act structure: localize WITH [[ACT:id]] markers so every act of
        # the master maps to the same act in every language.
        master_sections = stored_sections(master)
        structured = bool(master_sections) and is_structured(master_sections)
        if not structured:
            master_sections = [{"id": "full", "text": master.story_text}]
        structure_lost_at: list[str] = []
        per_section_fallback = False

        with track_run(
            db, case.id, "Localization Writer",
            input_summary=f"lang={language} master_v{master.version}",
        ) as run:
            raw, writer_res = await self._write(
                case, master, master_sections, protected, language, wpm,
                minutes, marked=structured,
            )
            stamp_run(run, writer_res, "localization_writer")
            run.output_summary = f"words={len(raw.split())}"
        # Across languages paragraph counts legitimately differ, so only
        # the markers themselves can prove which text belongs to which act.
        sections, kept = realign_sections(
            master_sections, raw, allow_paragraph_fallback=False
        )
        if not kept:
            # The one-call transcreation dropped markers — localize act by
            # act instead (more calls, guaranteed structure).
            sections, writer_res = await self._write_per_section(
                db, case, master_sections, protected, language, wpm, minutes
            )
            per_section_fallback = True

        native: dict = {}
        semantic: dict = {}
        grounding: dict = {}
        engagement = 0.0

        for attempt in range(1 + loc.max_localization_repair_iterations):
            text = _join_sections(sections)
            native = await self._native_critic(db, case, text, language)
            semantic = await self._semantic_check(
                db, case, master.story_text, text, protected, language
            )
            grounding = await self._localized_grounding(
                db, case, text, pack, language
            )
            gates = self._evaluate_gates(
                text, language, minutes, wpm, native, semantic,
                grounding, engagement=None,
            )
            if gates["pass"]:
                break
            if attempt < loc.max_localization_repair_iterations:
                marked_now = is_structured(sections)
                with track_run(
                    db, case.id, "Localization Repair",
                    input_summary=f"lang={language} failures={gates['failures']}",
                ) as run:
                    raw, res = await self._repair(
                        _mark_sections(sections) if marked_now else text,
                        master, protected, gates, language, marked=marked_now,
                    )
                    stamp_run(run, res, "localization_writer")
                    run.output_summary = f"words={len(raw.split())}"
                # Same language now: paragraph-count realignment is valid.
                sections, kept = realign_sections(sections, raw)
                if not kept:
                    structure_lost_at.append("localization_repair")
                text = _join_sections(sections)

        # Final editor runs only when the main gates pass, then is
        # re-validated and kept only if it did not break anything —
        # including the act structure.
        final_edit_note = None
        if gates["pass"]:
            marked_now = is_structured(sections)
            edited, edit_res = await self._final_edit(
                db, case, _mark_sections(sections) if marked_now else text,
                language, marked=marked_now,
            )
            if edited:
                edit_sections, kept = realign_sections(sections, edited)
                if not kept:
                    final_edit_note = "rejected_structure_lost"
                edit_text = _join_sections(edit_sections)
                edit_semantic = (
                    await self._semantic_check(
                        db, case, master.story_text, edit_text, protected,
                        language,
                    )
                    if kept else {}
                )
                edit_gates = (
                    self._evaluate_gates(
                        edit_text, language, minutes, wpm, native,
                        edit_semantic, grounding, engagement=None,
                    )
                    if kept else {"pass": False}
                )
                if edit_gates["pass"]:
                    text = edit_text
                    sections = edit_sections
                    semantic = edit_semantic
                    gates = edit_gates
                    writer_res = edit_res

        # Independent engagement score per language — never inherited.
        h = text_hash(text)
        with track_run(db, case.id, "Localized Engagement Critic") as run:
            critique, res = await self.critic.critique(
                text, minutes, role="localized_engagement_critic"
            )
            stamp_run(run, res, "localized_engagement_critic", text_hash=h)
            engagement = float(critique.get("score", 0))
            run.output_summary = f"score={engagement} hash={h[:8]}"
        if engagement < loc.minimum_engagement_score:
            gates.setdefault("failures", []).append("engagement_below_threshold")
            gates["pass"] = False
        gates["pass"] = not gates["failures"]

        critique["quality_gates"] = gates
        critique["native_quality"] = native
        critique["semantic_consistency"] = semantic
        critique["localized_grounding"] = grounding
        critique["text_hash"] = h
        critique["localization"] = {
            "language": language,
            "derived_from_master_version": master.version,
            "structured": is_structured(sections),
            "per_section_fallback": per_section_fallback,
            "final_edit": final_edit_note,
        }

        row = StoryPipeline()._new_version(
            db, case,
            structure_without_text(json.loads(master.narrative_structure or "{}")),
            sections,
            text, engagement, None, "not_evaluated", critique, language,
            writer_res,
            kind="localized",
            master_version_id=master.id,
            derived_from_master_version=master.version,
            native_quality_score=self._native_score(native),
            semantic_consistency_score=float(
                semantic.get("semantic_consistency_score") or 0
            ),
            factual_consistency_score=float(
                grounding.get("grounding_score") or 0
            ),
            extra_structure={
                "evidence_fingerprint": fingerprint,
                "structure_lost_at": structure_lost_at,
                "localized_per_section": per_section_fallback,
            },
        )
        return row

    # ------------------------------------------------------------------
    # protected payload + writer
    # ------------------------------------------------------------------

    def _protected_payload(self, master: StoryVersion, pack: dict) -> dict:
        """The invariant contract every localization must preserve."""
        try:
            structure = json.loads(master.narrative_structure or "{}")
        except (ValueError, TypeError):
            structure = {}
        # Section TEXT is the master story itself — already sent as
        # master_story. Keep only ids/lengths here (no double tokens).
        structure = structure_without_text(structure)
        return {
            "narrative_structure": structure,
            "verified_facts": [
                f["claim"] for f in pack["facts"] if not f["uncertain"]
            ],
            "uncertainty_map": [
                {"claim": f["claim"], "must_remain_uncertain": True}
                for f in pack["disputed_facts"]
            ],
            "timeline": [
                {"date": t["event_date"], "event": t["claim"]}
                for t in pack["timeline"]
            ],
            "contradictions": [
                {"topic": c["topic"], "description": c["description"]}
                for c in pack["contradictions"]
            ],
        }

    @staticmethod
    def _writer_rules(language: str) -> str:
        return f"""
You are an excellent native {language} true-crime storyteller.

Rewrite the supplied English master story as if it had been ORIGINALLY
written by you in {language} — narrative localization, not translation.
Adapt freely: sentence structure, paragraph rhythm, transitions, idioms,
rhetorical style, suspense cadence, sentence order within a meaning unit.

You may NOT change: facts, dates, names, chronology, evidence,
contradictions, uncertainty, legal status, meaning of quotations,
outcome. Items marked must_remain_uncertain MUST stay uncertain.
No new facts. No removed critical facts. No invented dialogue, inner
thoughts, or scene details. No citations, URLs, or meta-commentary.
If a quotation appears, render it faithfully — never turn a paraphrase
into a direct quote.
"""

    async def _write(
        self,
        case: Case,
        master: StoryVersion,
        master_sections: list[dict],
        protected: dict,
        language: str,
        wpm: int,
        minutes: int,
        marked: bool = False,
    ) -> tuple[str, object]:
        target_words = int(minutes * wpm)
        system = f"""{self._writer_rules(language)}
{_MARKER_INSTRUCTION if marked else ""}
Length: aim for about {target_words} words (narration duration target).
Output only the story text.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "master_story": (
                    _mark_sections(master_sections) if marked
                    else master.story_text
                ),
                "protected": protected,
                "target_language": language,
            },
            ensure_ascii=False,
        )
        res = await self.gen.generate_text("localization_writer", system, user)
        return res.text, res

    async def _write_per_section(
        self,
        db: Session,
        case: Case,
        master_sections: list[dict],
        protected: dict,
        language: str,
        wpm: int,
        minutes: int,
    ) -> tuple[list[dict], object]:
        """Fallback: transcreate one act per call. The previous localized
        act's ending and the next master act's opening are passed as
        context so transitions still read as one story."""
        master_words = sum(len(s["text"].split()) for s in master_sections) or 1
        target_total = int(minutes * wpm)
        out: list[dict] = []
        last_res = None
        for i, s in enumerate(master_sections):
            share = len(s["text"].split()) / master_words
            target = max(1, int(target_total * share))
            system = f"""{self._writer_rules(language)}
You are localizing ONE act of a longer story. Continue naturally from
the previous act's ending; do not summarize other acts.
Length: aim for about {target} words.
Output only this act's text.
"""
            nxt = master_sections[i + 1]["text"] if i + 1 < len(master_sections) else ""
            user = json.dumps(
                {
                    "case": case.canonical_title,
                    "act_id": s["id"],
                    "master_act": s["text"],
                    "previous_localized_act_ending": (
                        " ".join(out[-1]["text"].split()[-60:]) if out else ""
                    ),
                    "next_master_act_opening": " ".join(nxt.split()[:40]),
                    "protected": protected,
                    "target_language": language,
                },
                ensure_ascii=False,
            )
            with track_run(
                db, case.id, "Localization Writer",
                input_summary=f"lang={language} act={s['id']} (per-act)",
            ) as run:
                res = await self.gen.generate_text(
                    "localization_writer", system, user
                )
                stamp_run(run, res, "localization_writer")
                run.output_summary = f"words={len(res.text.split())}"
            out.append(
                {"id": s["id"], "text": strip_section_markers(res.text)}
            )
            last_res = res
        return out, last_res

    # ------------------------------------------------------------------
    # validators
    # ------------------------------------------------------------------

    async def _native_critic(
        self, db: Session, case: Case, text: str, language: str
    ) -> dict:
        criteria = _NATIVE_CRITERIA.get(
            language,
            "Evaluate whether the text reads as natively written in the "
            "target language (no translation artifacts, natural register, "
            "storytelling rhythm).",
        )
        system = f"""
You are a native-language story editor evaluating a localized true-crime
narration. Judge it IN THE TARGET LANGUAGE as a native storyteller would —
do not compare phrasing to English.

{criteria}

Return JSON only:
{{
  "naturalness": 0-100,
  "storytelling_flow": 0-100,
  "translation_artifact_score": 0-100,
  "pacing": 0-100,
  "curiosity": 0-100,
  "emotional_effect": 0-100,
  "word_choice": 0-100,
  "sentence_rhythm": 0-100,
  "overall_native_quality": 0-100,
  "problems": ["..."],
  "rewrite_instructions": ["..."]
}}

For "translation_artifact_score" a high score means FEW artifacts.
"""
        with track_run(
            db, case.id, "Native Language Critic",
            input_summary=f"lang={language}",
        ) as run:
            data, res = await self.gen.generate_structured(
                "native_language_critic", system,
                json.dumps({"language": language, "story": text},
                           ensure_ascii=False),
            )
            stamp_run(run, res, "native_language_critic")
            run.output_summary = (
                f"native={data.get('overall_native_quality', 0)}"
            )
        return data

    async def _semantic_check(
        self,
        db: Session,
        case: Case,
        master_text: str,
        localized_text: str,
        protected: dict,
        language: str,
    ) -> dict:
        system = """
You are a bilingual semantic-consistency auditor. A localized narration
must be semantically equivalent to its English master: same facts, same
dates, same names, same chronology, same uncertainty, same outcome.

Compare the two texts and report:
- semantic_consistency_score: 0-100 (100 = fully equivalent)
- missing_information: master content absent from the localization
- added_information: content in the localization NOT in the master
- meaning_changes: passages where meaning shifted
- uncertainty_changes: claims whose certainty strengthened/weakened
- name_date_number_errors: any changed name, date, number or measurement

Return JSON only. Be strict — every factual divergence counts.
"""
        with track_run(
            db, case.id, "Semantic Consistency Checker",
            input_summary=f"lang={language}",
        ) as run:
            data, res = await self.gen.generate_structured(
                "semantic_consistency_checker", system,
                json.dumps(
                    {
                        "master": master_text,
                        "localized": localized_text,
                        "protected": protected,
                        "target_language": language,
                    },
                    ensure_ascii=False,
                ),
            )
            stamp_run(run, res, "semantic_consistency_checker")
            run.output_summary = (
                f"score={data.get('semantic_consistency_score', 0)}"
            )
        return data

    async def _localized_grounding(
        self, db: Session, case: Case, text: str, pack: dict, language: str
    ) -> dict:
        """Defense in depth: the localized story must not introduce
        factual detail absent from the canonical evidence pack."""
        system = """
You are a grounding validator. Compare the localized story (any language)
against the canonical English evidence pack. A claim is supported if it
is semantically contained in the evidence, regardless of language.

Return JSON only:
{
  "supported_claims": [{"claim": "...", "evidence_id": "F001"}],
  "unsupported_claims": [{"claim": "...", "severity": "fatal|minor"}],
  "uncertainty_errors": [{"claim": "...", "evidence_id": "F001"}],
  "grounding_score": 0.0-1.0
}
"""
        with track_run(
            db, case.id, "Localized Grounding Validator",
            input_summary=f"lang={language}",
        ) as run:
            data, res = await self.gen.generate_structured(
                "localized_grounding_validator", system,
                json.dumps({"story": text, "evidence": pack},
                           ensure_ascii=False),
            )
            stamp_run(run, res, "localized_grounding_validator")
            run.output_summary = (
                f"score={data.get('grounding_score', 0)}"
            )
        return data

    # ------------------------------------------------------------------
    # repair + final edit
    # ------------------------------------------------------------------

    async def _repair(
        self,
        text: str,
        master: StoryVersion,
        protected: dict,
        gates: dict,
        language: str,
        marked: bool = False,
    ) -> tuple[str, object]:
        system = f"""
You are an excellent native {language} true-crime storyteller revising
your own localized narration. Fix ONLY the reported problems. All
factual invariants still apply: no fact changes, no new facts, no
removed facts, uncertainty stays intact.
{_MARKER_INSTRUCTION if marked else ""}
Output only the story text.
"""
        user = json.dumps(
            {
                "current_text": text,
                "master_story": master.story_text,
                "protected": protected,
                "gate_failures": gates["failures"],
                "details": {
                    "native": gates.get("native"),
                    "semantic": gates.get("semantic"),
                    "grounding": gates.get("grounding"),
                },
            },
            ensure_ascii=False,
        )
        res = await self.gen.generate_text("localization_writer", system, user)
        return res.text, res

    async def _final_edit(
        self, db: Session, case: Case, text: str, language: str,
        marked: bool = False,
    ) -> tuple[str | None, object]:
        system = f"""
You are a native {language} line editor doing a final polish pass on a
localized true-crime narration. Improve phrasing, transitions, clarity
and rhythm ONLY. You must NOT: add facts, remove facts, invent dialogue,
turn uncertain claims into facts, shorten the story materially, add
citations, or change meaning.
{_MARKER_INSTRUCTION if marked else ""}
Output only the story text.
"""
        with track_run(
            db, case.id, "Localized Final Editor",
            input_summary=f"lang={language}",
        ) as run:
            try:
                res = await self.gen.generate_text(
                    "localized_final_editor", system, text
                )
            except Exception as e:
                run.error = str(e)[:300]
                run.status = "failed"
                return None, None
            stamp_run(run, res, "localized_final_editor",
                      text_hash=text_hash(res.text))
            run.output_summary = f"words={len(res.text.split())}"
        return res.text, res

    # ------------------------------------------------------------------
    # gates
    # ------------------------------------------------------------------

    @staticmethod
    def _native_score(native: dict) -> float:
        return float(native.get("overall_native_quality") or 0)

    def _evaluate_gates(
        self,
        text: str,
        language: str,
        minutes: int,
        wpm: int,
        native: dict,
        semantic: dict,
        grounding: dict,
        engagement: float | None,
    ) -> dict:
        loc = ai_config.localization
        failures = []

        lang = language_quality(
            text, language, ai_config.language_quality_for(language)
        )
        if not lang["pass"]:
            failures.append("language_quality")

        if re.search(r"https?://|www\.", text):
            failures.append("output_purity")
        if ai_config.story_quality.strip_markdown_artifacts and re.search(
            r"^\s*#{1,6}\s|^\s*[-*_]{3,}\s*$", text, re.M
        ):
            failures.append("markdown_artifacts")

        # Duration band, not word parity — languages differ in density.
        est_minutes = len(text.split()) / max(wpm, 1)
        if not (
            minutes * loc.minimum_duration_ratio
            <= est_minutes
            <= minutes * loc.maximum_duration_ratio
        ):
            failures.append("duration_out_of_range")

        native_score = self._native_score(native)
        if native_score < loc.minimum_native_quality_score:
            failures.append("native_quality")

        semantic_score = float(semantic.get("semantic_consistency_score") or 0)
        semantic_fatal = (
            semantic.get("added_information")
            or semantic.get("missing_information")
            or semantic.get("name_date_number_errors")
            or semantic.get("meaning_changes")
            or semantic.get("uncertainty_changes")
        )
        if (
            semantic_score < loc.minimum_semantic_consistency_score
            or semantic_fatal
        ):
            failures.append("semantic_consistency")

        factual_score = float(grounding.get("grounding_score") or 0)
        if factual_score < loc.minimum_factual_consistency_score:
            failures.append("factual_consistency")

        if (
            engagement is not None
            and engagement < loc.minimum_engagement_score
        ):
            failures.append("engagement_below_threshold")

        failures = list(dict.fromkeys(failures))
        return {
            "pass": not failures,
            "failures": failures,
            "words": len(text.split()),
            "estimated_minutes": round(est_minutes, 1),
            "language_quality": lang,
            "native_quality_score": native_score,
            "semantic_consistency_score": semantic_score,
            "factual_consistency_score": factual_score,
            "native": native,
            "semantic": semantic,
            "grounding": grounding,
        }

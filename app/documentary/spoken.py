"""Spoken storytelling adaptation — the script as a person TELLS it.

A documentary script that reads well on paper can sound like a news
bulletin when spoken. This stage rewrites the approved story, beat by
beat, the way a gifted storyteller talks to one listener: shorter
sentences, everyday words, active verbs, natural signposting and room
to breathe — natively in English, German, Persian and Arabic, each with
its own house style. Facts, names, dates, numbers, quotations and the
level of certainty never change.

Pipeline per language:
  1. writer (role spoken_writer): whole story in one call, beat markers
     kept; if markers get lost: chunks of beats, then single beats;
  2. meaning check (role spoken_meaning_checker, a different model):
     per beat — missing / added / changed facts, certainty changes;
  3. style critic (role spoken_style_critic, a different model, judging
     as a native listener): storyteller | mixed | newsreader, with the
     exact phrases that sound read-aloud and spoken alternatives — plus a
     deterministic ear check (sentence length, stiff written phrases);
  4. repair rounds (spoken.max_repair_iterations) for the beats that
     failed 2 or 3, re-checked each time.
The writer and its critics are independent (review_independence group
"spoken_adaptation", strict): the critics never use the writer's model,
and the writer never falls back to a critic's model.
The result is a StoryVersion (kind "spoken") whose sections are the
blueprint's beats — so every later stage (voice, pauses, music, visuals)
lines up beat by beat in every language.
"""

from __future__ import annotations

from app.core.prompts import prompt

import json
import re

from sqlalchemy.orm import Session

from app.agents.story import (
    _MARKER_INSTRUCTION,
    StoryPipeline,
    _join_sections,
    _mark_sections,
    _paragraphs,
    build_evidence_pack,
    evidence_fingerprint,
    realign_sections,
    stored_sections,
    strip_section_markers,
    structure_without_text,
)
from app.core.ai_config import ai_config
from app.db.models import (
    Case, Contradiction, EditorialBlueprint, Fact, Source, StoryVersion,
)
from app.core.concurrency import gather_limited
from app.documentary.blueprint import latest_blueprint, version_sections
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.utils import language_quality

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic"}

# House style per language, written for (and partly in) that language.
STYLE_GUIDES = {
    "en": prompt("documentary/spoken/style_guides_en"),
    "de": prompt("documentary/spoken/style_guides_de"),
    "fa": prompt("documentary/spoken/style_guides_fa"),
    "ar": prompt("documentary/spoken/style_guides_ar"),
}


# Before -> after pairs that show the move from "read aloud" to "told".
# They are deliberately unrelated to any case and fact-preserving
# themselves (nothing added, certainty kept): style illustrations only.
STYLE_EXAMPLES = {
    "en": [
        ("Following an extensive investigation, authorities determined that "
         "the vehicle had been abandoned in the early hours of the morning, "
         "although the identity of the driver remained unestablished.",
         "Police looked into it for a long time. And in the end, they were "
         "sure of one thing: someone left that car there in the early hours "
         "of the morning. But who was behind the wheel? That, nobody could "
         "say."),
        ("She exercised considerable influence over the household's "
         "financial affairs.",
         "When it came to money in that house, she had a big say."),
        ("It was subsequently reported that the man had been observed in "
         "the vicinity of the station on the evening in question.",
         "Later, a report came in. Someone had seen the man near the "
         "station. That same evening."),
    ],
    "de": [
        ("Im Zuge der umfangreichen Ermittlungen wurde seitens der Behörden "
         "festgestellt, dass das Fahrzeug in den frühen Morgenstunden "
         "abgestellt worden war, wobei die Identität des Fahrers ungeklärt "
         "blieb.",
         "Die Polizei ermittelte lange. Und am Ende stand eines fest: "
         "Jemand hatte den Wagen in den frühen Morgenstunden dort "
         "abgestellt. Aber wer saß am Steuer? Das blieb offen."),
        ("Sie übte erheblichen Einfluss auf die finanziellen Angelegenheiten "
         "des Haushalts aus.",
         "Wenn es in diesem Haus ums Geld ging, hatte sie viel zu sagen."),
        ("In der Folge wurde berichtet, dass der Mann am fraglichen Abend in "
         "der Nähe des Bahnhofs beobachtet worden sei.",
         "Später kam ein Hinweis. Jemand will den Mann in der Nähe des "
         "Bahnhofs gesehen haben. Am selben Abend."),
    ],
    "fa": [
        ("در پی تحقیقات گسترده، از سوی مقامات مشخص گردید که خودروی مذکور در "
         "ساعات اولیه‌ی بامداد رها شده بود، هرچند هویت راننده همچنان نامعلوم "
         "باقی ماند.",
         "پلیس مدت‌ها روی این ماجرا کار کرد. و آخرش، فقط یه چیز روشن شد: "
         "یه نفر ماشین رو صبحِ خیلی زود همون‌جا ول کرده بود. ولی پشت فرمون کی "
         "نشسته بود؟ اینو هیچ‌کس نتونست بگه."),
        ("او نفوذ قابل‌توجهی بر امور مالی خانواده اعمال می‌کرد.",
         "تو اون خونه، وقتی پای پول وسط بود، حرفِ اون وزن زیادی داشت."),
        ("متعاقباً گزارش شد که فرد مذکور در شب مورد نظر در حوالی ایستگاه "
         "مشاهده شده است.",
         "بعدها یه خبر رسید. یه نفر گفته بود اون مرد رو نزدیک ایستگاه دیده. همون "
         "شب."),
    ],
    "ar": [
        ("وفي إطار التحقيقات الموسّعة، تمّ التوصّل من قِبَل الجهات المختصة "
         "إلى أنّ المركبة قد تُركت في الساعات الأولى من الصباح، في حين ظلّت "
         "هوية السائق مجهولة.",
         "حقّقت الشرطة طويلًا. وفي النهاية، تأكّدت من أمر واحد: أحدهم ترك "
         "السيارة هناك في ساعات الفجر الأولى. لكن من كان خلف المقود؟ هذا ما "
         "لم يستطع أحد أن يقوله."),
        ("كانت تمارس نفوذًا كبيرًا على الشؤون المالية للأسرة.",
         "وحين يتعلّق الأمر بالمال في ذلك البيت، كان لكلمتها وزن كبير."),
        ("وأفادت التقارير لاحقًا بأنّ الرجل شوهد بالقرب من المحطة في "
         "المساء المذكور.",
         "ثم وصلت معلومة. أحدهم قال إنه رأى الرجل قرب المحطة. في المساء "
         "نفسه."),
    ],
}

# Read-aloud markers a native editor would cut (hints for the repair
# round, never a gate on their own).
STIFF_PATTERNS = {
    "en": [r"\bit is understood\b", r"\baccording to reports\b",
           r"\bsubsequently\b", r"\bprior to\b", r"\bin the vicinity of\b",
           r"\bcommenced\b", r"\bwas conducted\b", r"\bthe residence\b",
           r"\bthe individual\b", r"\bin question\b", r"\bthe aforementioned\b",
           r"\bdomestic circle\b", r"\bexercised\b"],
    "de": [r"\bseitens\b", r"\bim Zuge\b", r"\bLiegenschaft\b",
           r"\bdurchgeführt\b", r"\berfolgte\b", r"\bbezüglich\b",
           r"\bhinsichtlich\b", r"\bdiesbezüglich\b", r"\bin der Folge\b",
           r"\bfraglichen\b"],
    "fa": [r"مذکور", r"نامبرده", r"مزبور", r"(?<!\S)توسط(?!\S)", r"مورد \S+ قرار",
           r"صورت گرفت", r"علی‌رغم", r"معهذا", r"می‌باشد", r"گردید", r"متعاقباً"],
    "ar": [r"(?<!\S)و?تمّ?(?!\S)", r"(?<!\S)قام(?:ت)? بـ?", r"من قِبَل", r"من قبل",
           r"حيث إنّ?", r"الجهات المختصة", r"الجهات الأمنية", r"المذكور"],
}


CONNECTORS = {
    "en": '"So", "But", "And then", "Now", "Here\'s the thing"',
    "de": '"Also", "Aber", "Und dann", "Jetzt", "Und genau hier wird es seltsam"',
    "fa": "«خب»، «ولی»، «بعدش»، «حالا»، «نکته این‌جاست»",
    "ar": "«إذن»، «لكن»، «ثم»، «والآن»، «وهنا المفارقة»",
}


def _examples_block(language: str) -> str:
    pairs = STYLE_EXAMPLES.get(language) or STYLE_EXAMPLES["en"]
    return "\n".join(f"  READ:  {a}\n  TOLD:  {b}\n" for a, b in pairs)


def read_aloud_metrics(text: str, language: str) -> dict:
    """Deterministic ear-friendliness: sentence lengths and stiff phrases."""
    from app.documentary.voice_blocks import split_sentences

    limit = ai_config.spoken.max_sentence_words.get(language, 24)
    sentences = [x for para in _paragraphs(text) for x in split_sentences(para, language)]
    lengths = [len(x.split()) for x in sentences]
    long = [x for x, n in zip(sentences, lengths) if n > limit]
    stiff = []
    for pat in STIFF_PATTERNS.get(language, []):
        for m in re.finditer(pat, text, re.IGNORECASE):
            stiff.append(m.group(0).strip())
    return {
        "sentences": len(sentences),
        "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_sentences": long,
        "long_sentence_share": round(len(long) / len(sentences), 3) if sentences else 0.0,
        "stiff_phrases": sorted(set(stiff)),
    }


def beat_sections(source_sections: list[dict], blueprint: dict) -> list[dict]:
    """Source text per beat (beat id = section id)."""
    paras = {s["id"]: _paragraphs(s["text"]) for s in source_sections}
    out = []
    for b in blueprint.get("beats") or []:
        first, last = b["paragraphs"]
        out.append({"id": b["id"],
                    "text": "\n\n".join(paras[b["act_id"]][first - 1:last])})
    return out


def _words_minutes(text: str, language: str) -> float:
    return len((text or "").split()) / max(ai_config.words_per_minute_for(language), 1)


def writer_system_prompt(language: str, source_language: str = "en") -> str:
    name = LANG_NAMES.get(language, language)
    limit = ai_config.spoken.max_sentence_words.get(language, 24)
    source_note = (
        f"The script is in {LANG_NAMES.get(source_language, source_language)}. "
        f"Tell it directly in {name}, the way a native {name} storyteller "
        "would tell it — never as a translation."
        if language != source_language else ""
    )
    style = STYLE_GUIDES.get(language, STYLE_GUIDES["en"])
    connectors = CONNECTORS.get(language, CONNECTORS["en"])

    return prompt("documentary/spoken/writer_system_prompt").format(name=name, source_note=source_note, limit=limit, connectors=connectors, style=style, v0=_examples_block(language), _MARKER_INSTRUCTION=_MARKER_INSTRUCTION)


MEANING_SYSTEM = prompt("documentary/spoken/meaning_system")


def critic_system_prompt(language: str) -> str:
    name = LANG_NAMES.get(language, language)
    house = STYLE_GUIDES.get(language, STYLE_GUIDES["en"])
    return prompt("documentary/spoken/critic_system_prompt").format(name=name, house=house)


_RULE_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$", re.M)
_META = re.compile(r"\*\*|^\s*#{1,6}\s|^\s*[-*] \*\*", re.M)


def clean_writer_output(raw: str) -> tuple[str, list[str]]:
    """Narration only: drops a lead-in before the first marker and any
    trailing notes after a horizontal rule (models sometimes answer the
    'user' with comments or a change log). Returns (text, issues)."""
    issues: list[str] = []
    text = raw or ""
    prefix = re.escape(ai_config.story_quality.section_marker_prefix)
    m = re.compile(rf"^\s*{prefix}[^\]]+\]\]\s*$", re.M).search(text)
    if m and text[:m.start()].strip():
        issues.append("lead_in_removed")
        text = text[m.start():]
    r = _RULE_LINE.search(text)
    if r:
        issues.append("trailing_notes_removed")
        text = text[:r.start()]
    return text.strip(), issues


def has_meta_text(text: str) -> bool:
    """Markdown or notes that must never reach the narrator."""
    return bool(_META.search(text or "") or _RULE_LINE.search(text or ""))


def _task_line(language: str, beats: int, repair: bool = False) -> str:
    name = LANG_NAMES.get(language, language)
    if repair:
        return (f"TASK: revise the {beats} spoken {name} beat(s) below exactly as your "
                "instructions say. Output ONLY those beats, each under its marker "
                "line — no comments, notes, headings or list of changes.")
    return (f"TASK: tell the script below ({beats} beat(s)) as spoken {name} "
            "narration, exactly as your instructions say. Output ONLY the "
            "narration under its marker lines — no comments to me, no notes, no "
            "headings, no list of changes. 'if_mentioned_keep_uncertain' lists "
            "claims that must stay uncertain IF this part of the script mentions "
            "them; never add them otherwise.")


def _meaning_ok(entry: dict) -> bool:
    return not any(entry.get(k) for k in ("missing", "added", "changed", "certainty"))


class SpokenNarrator:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.spoken

    # ------------------------------------------------------------------
    # model calls
    # ------------------------------------------------------------------

    async def _text(self, db, case_id, label, role, system, payload: dict,
                    task: str = ""):
        user = json.dumps(payload, ensure_ascii=False)
        if task:
            user = f"{task}\n\nINPUT:\n{user}"
        with track_run(db, case_id, label, input_summary=label) as run:
            res = await self.gen.generate_text(role, system, user)
            stamp_run(run, res, role)
            run.output_summary = f"words={len(res.text.split())}"
        return res

    async def _json(self, db, case_id, label, role, system, payload: dict):
        with track_run(db, case_id, label, input_summary=label) as run:
            data, res = await self.gen.generate_structured(
                role, system, json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, role)
        return data if isinstance(data, dict) else {}, res

    async def _write(self, db, case_id, language, source: list[dict],
                     uncertain: list[str]) -> tuple[list[dict], object, list[str]]:
        """Whole story in one call; on lost markers chunks, then beats."""
        system = writer_system_prompt(language)
        log: list[str] = []

        async def call(beats: list[dict], before: str = ""):
            payload = {"language": language, "script": _mark_sections(beats),
                       "if_mentioned_keep_uncertain": uncertain}
            if before:
                payload["story_so_far_ends_with"] = before
            task = _task_line(language, len(beats))
            res = await self._text(
                db, case_id, f"Spoken Writer ({language})", "spoken_writer", system,
                payload, task,
            )
            text, issues = clean_writer_output(res.text)
            if issues:
                # The model talked to us instead of telling the story:
                # ask once more, plainly.
                log.append(f"meta_output_retry_{beats[0]['id']}")
                payload["previous_answer_rejected"] = (
                    "Your previous answer contained comments, notes or a list of "
                    "changes. Output only the spoken narration this time.")
                res = await self._text(
                    db, case_id, f"Spoken Writer ({language})", "spoken_writer",
                    system, payload, task,
                )
                text, _ = clean_writer_output(res.text)
            res.text = text
            return res

        size = self.cfg.beats_per_call
        if not size:
            res = await call(source)
            sections, ok = realign_sections(source, res.text,
                                            allow_paragraph_fallback=False)
            if ok and [s["id"] for s in sections] == [s["id"] for s in source]:
                return sections, res, log
            log.append("markers_lost_whole_story")
            size = 6
        out: list[dict] = []
        for i in range(0, len(source), size):
            chunk = source[i:i + size]
            # Continuity: the next chunk hears how the last one ended.
            tail = " ".join(out[-1]["text"].split()[-60:]) if out else ""
            res = await call(chunk, tail)
            got, ok = realign_sections(chunk, res.text, allow_paragraph_fallback=False)
            if ok and [s["id"] for s in got] == [s["id"] for s in chunk]:
                out.extend(got)
                continue
            log.append(f"markers_lost_chunk_{chunk[0]['id']}")
            for beat in chunk:
                tail = " ".join(out[-1]["text"].split()[-60:]) if out else ""
                res = await call([beat], tail)
                got, ok = realign_sections([beat], res.text,
                                           allow_paragraph_fallback=False)
                text = got[0]["text"] if ok else strip_section_markers(res.text)
                out.append({"id": beat["id"], "text": text})
        return out, res, log

    async def _check(self, db, case_id, language, source: list[dict],
                     spoken: list[dict], beat_ids: set[str] | None = None):
        """Meaning check + native style critic per chunk of beats (all
        chunks and both critics in parallel)."""
        src = {s["id"]: s["text"] for s in source}
        subset = [s for s in spoken if beat_ids is None or s["id"] in beat_ids]
        size = self.cfg.beats_per_call or len(subset) or 1
        chunks = [subset[i:i + size] for i in range(0, len(subset), size)]

        def meaning_payload(chunk):
            return {"source_language": "en", "spoken_language": language,
                    "beats": [{"beat_id": s["id"], "source": src[s["id"]],
                               "spoken": s["text"]} for s in chunk]}

        def style_payload(chunk):
            return {"language": language, "narration": _mark_sections(chunk)}

        calls = []
        for chunk in chunks:
            calls.append(self._json(db, case_id, f"Spoken Meaning Check ({language})",
                                    "spoken_meaning_checker", MEANING_SYSTEM,
                                    meaning_payload(chunk)))
            calls.append(self._json(db, case_id, f"Spoken Style Critic ({language})",
                                    "spoken_style_critic", critic_system_prompt(language),
                                    style_payload(chunk)))
        results = await gather_limited(None, calls)
        by_id_m: dict[str, dict] = {}
        by_id_s: dict[str, dict] = {}
        overall, notes = [], []
        for k in range(0, len(results), 2):
            meaning, _ = results[k]
            style, _ = results[k + 1]
            for b in meaning.get("beats") or []:
                if isinstance(b, dict):
                    by_id_m[str(b.get("beat_id"))] = b
            for b in style.get("beats") or []:
                if isinstance(b, dict):
                    by_id_s[str(b.get("beat_id"))] = b
            if style.get("overall"):
                overall.append(style["overall"])
            if style.get("notes"):
                notes.append(str(style["notes"]))
        result = {}
        for s in subset:
            m = by_id_m.get(s["id"])
            st = by_id_s.get(s["id"]) or {}
            verdict = st.get("verdict") if st.get("verdict") in (
                "storyteller", "mixed", "newsreader") else "unrated"
            result[s["id"]] = {
                # A beat the checker did not return is NOT assumed fine.
                "meaning": m if m is not None else {"unchecked": True},
                "meaning_ok": m is not None and _meaning_ok(m),
                "verdict": verdict,
                "problems": [p for p in st.get("problems") or [] if isinstance(p, dict)][:4],
                "ear": read_aloud_metrics(s["text"], language),
            }
        worst = next((v for v in ("newsreader", "mixed", "storyteller") if v in overall), None)
        return result, worst, " ".join(notes)[:800]

    @staticmethod
    def _needs_repair(entry: dict) -> bool:
        ear = entry.get("ear") or {}
        return (not entry["meaning_ok"]) or (
            entry["verdict"] in ("mixed", "newsreader") and entry["problems"]
        ) or bool(ear.get("long_sentences") or ear.get("stiff_phrases"))

    async def _repair(self, db, case_id, language, source, spoken, checks,
                      uncertain) -> list[dict]:
        targets = [s for s in spoken if self._needs_repair(checks[s["id"]])]
        if not targets:
            return spoken
        size = self.cfg.beats_per_call or len(targets)
        # chunks repair different beats: all at once
        results = await gather_limited(None, [
            self._repair_chunk(db, case_id, language, source, spoken, checks, uncertain,
                               targets[i:i + size])
            for i in range(0, len(targets), size)])
        new: dict[str, str] = {}
        for fixed in results:
            new.update(fixed)
        return [{"id": s["id"], "text": new.get(s["id"], s["text"])} for s in spoken]

    async def _repair_chunk(self, db, case_id, language, source, spoken, checks,
                            uncertain, targets) -> dict[str, str]:
        src = {s["id"]: s["text"] for s in source}
        system = writer_system_prompt(language) + """
You are now revising ONLY the beats given. For each beat you get its
source script, your current spoken version and the problems found:
fact problems (restore lost facts, remove added ones, fix changed
values or certainty), style problems from a native editor (follow the
spoken suggestions), sentences that are too long for the ear (split
them into short spoken sentences) and stiff written phrases (say them
the way people talk). Fix exactly those problems and keep everything
else. Return only these beats, each under its marker line.
"""
        payload = {
            "language": language,
            "if_mentioned_keep_uncertain": uncertain,
            "beats": [
                {"beat_id": s["id"], "source": src[s["id"]], "current": s["text"],
                 "fact_problems": {k: v for k, v in checks[s["id"]]["meaning"].items()
                                   if k in ("missing", "added", "changed", "certainty")},
                 "style_problems": checks[s["id"]]["problems"],
                 "too_long_for_the_ear": (checks[s["id"]].get("ear") or {}).get(
                     "long_sentences", []),
                 "stiff_phrases": (checks[s["id"]].get("ear") or {}).get(
                     "stiff_phrases", [])}
                for s in targets
            ],
            "output_template": _mark_sections(
                [{"id": s["id"], "text": "…"} for s in targets]),
        }
        res = await self._text(db, case_id, f"Spoken Repair ({language})",
                               "spoken_writer", system, payload,
                               _task_line(language, len(targets), repair=True))
        text, _ = clean_writer_output(res.text)
        fixed, ok = realign_sections(
            [{"id": s["id"], "text": s["text"]} for s in targets], text,
            allow_paragraph_fallback=False)
        if not ok:
            return {}  # keep current text; checks stay honest
        return {s["id"]: s["text"] for s in fixed}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def create(self, db: Session, case: Case, master: StoryVersion,
                     language: str) -> StoryVersion:
        if language not in self.cfg.languages:
            raise ValueError(f"Language {language!r} is not configured for spoken narration.")
        row = latest_blueprint(db, master.id)
        if not row or row.status == "invalid" or (
            row.story_text_hash and row.story_text_hash != master.text_hash
        ):
            raise RuntimeError(
                "This story needs a valid editorial blueprint for its current "
                "text first (POST …/blueprint)."
            )
        blueprint = json.loads(row.blueprint_json or "{}")
        source = beat_sections(version_sections(master), blueprint)
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all(),
        )
        if row.evidence_fingerprint and row.evidence_fingerprint != evidence_fingerprint(pack):
            raise RuntimeError(
                "Research changed after the blueprint was made; regenerate the "
                "story and its blueprint first."
            )
        uncertain = [f["claim"] for f in pack["disputed_facts"]]

        spoken, writer_res, write_log = await self._write(
            db, case.id, language, source, uncertain)
        checks, overall, notes = await self._check(db, case.id, language, source, spoken)
        repairs = 0
        while (repairs < self.cfg.max_repair_iterations
               and any(self._needs_repair(c) for c in checks.values())):
            repairs += 1
            before = {s["id"]: s["text"] for s in spoken}
            spoken = await self._repair(db, case.id, language, source, spoken,
                                        checks, uncertain)
            changed = {s["id"] for s in spoken if s["text"] != before[s["id"]]}
            if not changed:
                break
            rechecked, overall, notes = await self._check(
                db, case.id, language, source, spoken, changed)
            checks.update(rechecked)

        text = _join_sections(spoken)
        failures = []
        if any(not c["meaning_ok"] for c in checks.values()):
            failures.append("meaning_changed")
        if any(c["verdict"] == "newsreader" for c in checks.values()):
            failures.append("newsreader_tone")
        n = max(len(checks), 1)
        storyteller = sum(1 for c in checks.values() if c["verdict"] == "storyteller")
        if storyteller / n < self.cfg.min_storyteller_share:
            failures.append("not_enough_storytelling")
        ear = read_aloud_metrics(text, language)
        if ear["long_sentence_share"] > self.cfg.max_long_sentence_share:
            failures.append("sentences_too_long")
        lq = language_quality(text, language, ai_config.language_quality_for(language))
        if not lq["pass"]:
            failures.append("language_quality")
        if re.search(r"https?://|www\.", text) or has_meta_text(text):
            failures.append("output_purity")
        ratio = _words_minutes(text, language) / max(
            _words_minutes(_join_sections(source), "en"), 1e-6)
        if not (self.cfg.min_duration_ratio <= ratio <= self.cfg.max_duration_ratio):
            failures.append("duration_out_of_range")

        meaning_ok = sum(1 for c in checks.values() if c["meaning_ok"])
        critique = {
            "quality_gates": {"pass": not failures, "failures": failures},
            "spoken": {
                "language": language, "overall": overall, "notes": notes,
                "storyteller_beats": storyteller, "beats": n,
                "meaning_ok_beats": meaning_ok, "duration_ratio": round(ratio, 3),
                "repair_iterations": repairs, "write_log": write_log,
                "language_quality": lq,
                "ear": {k: v for k, v in ear.items() if k != "long_sentences"}
                | {"long_sentences": ear["long_sentences"][:20]},
                "per_beat": checks,
            },
        }
        try:
            plan = structure_without_text(json.loads(master.narrative_structure or "{}"))
        except (ValueError, TypeError):
            plan = {}
        version = StoryPipeline()._new_version(
            db, case, plan, spoken, text, 0.0, None, "not_evaluated", critique,
            language, writer_res, kind="spoken",
            master_version_id=master.id,
            derived_from_master_version=master.version,
            native_quality_score=round(100 * storyteller / n, 1),
            semantic_consistency_score=round(100 * meaning_ok / n, 1),
            factual_consistency_score=round(meaning_ok / n, 3),
            extra_structure={
                "blueprint_id": row.id, "source_version_id": master.id,
                "beat_sections": True,
                "evidence_fingerprint": row.evidence_fingerprint,
            },
        )
        return version


def spoken_blueprint(db: Session, version: StoryVersion) -> dict | None:
    """Blueprint for a spoken version: its sections ARE the beats, so each
    beat becomes its own 'act' covering all of its paragraphs."""
    try:
        struct = json.loads(version.narrative_structure or "{}")
    except (ValueError, TypeError):
        return None
    row = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
    if row is None or row.status == "invalid":
        return None
    blueprint = json.loads(row.blueprint_json or "{}")
    sections = stored_sections(version)
    beats = blueprint.get("beats") or []
    if not sections or [s["id"] for s in sections] != [b["id"] for b in beats]:
        return None
    adapted = []
    for b, s in zip(beats, sections):
        adapted.append({**b, "act_id": s["id"],
                        "paragraphs": [1, max(len(_paragraphs(s["text"])), 1)]})
    return {**blueprint, "beats": adapted, "blueprint_id": row.id}

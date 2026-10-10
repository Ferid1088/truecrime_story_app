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
    _paragraphs,
    stored_sections,
)
from app.core.ai_config import ai_config
from app.db.models import (
    EditorialBlueprint, StoryVersion,
)

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

    return prompt("documentary/spoken/writer_system_prompt").format(name=name, source_note=source_note, limit=limit, connectors=connectors, style=style, examples_block=_examples_block(language), _MARKER_INSTRUCTION=_MARKER_INSTRUCTION)


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
        return (prompt("documentary/spoken/task_line_2").format(beats=beats, name=name))
    return (prompt("documentary/spoken/task_line").format(beats=beats, name=name))


def _meaning_ok(entry: dict) -> bool:
    return not any(entry.get(k) for k in ("missing", "added", "changed", "certainty"))


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


def __getattr__(name: str):
    # the agent class lives in app/agents/spoken.py (imported when first asked for)
    if name == "SpokenNarrator":
        from app.agents.spoken import SpokenNarrator

        return SpokenNarrator
    raise AttributeError(name)

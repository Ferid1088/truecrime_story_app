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

import json
import re

from sqlalchemy.orm import Session

from app.agents.story import (
    _MARKER_INSTRUCTION,
    _paragraphs,
    stored_sections,
)
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import (
    EditorialBlueprint,
    StoryVersion,
)

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic", "es": "Mexican Spanish", "it": "Italian", "fr": "French", "pt": "Brazilian Portuguese", "tr": "Turkish"}

# House style per language, written for (and partly in) that language.
STYLE_GUIDES = {
    "en": prompt("documentary/spoken/style_guides_en"),
    "de": prompt("documentary/spoken/style_guides_de"),
    "fa": prompt("documentary/spoken/style_guides_fa"),
    "ar": prompt("documentary/spoken/style_guides_ar"),
    "es": prompt("documentary/spoken/style_guides_es"),
    "it": prompt("documentary/spoken/style_guides_it"),
    "fr": prompt("documentary/spoken/style_guides_fr"),
    "pt": prompt("documentary/spoken/style_guides_pt"),
    "tr": prompt("documentary/spoken/style_guides_tr"),
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
    "es": [
        ("Tras una extensa investigación, las autoridades determinaron que el vehículo había sido abandonado en la madrugada, sin que se lograra establecer la identidad del conductor.",
         "La policía investigó durante mucho tiempo. Y al final quedó una sola certeza: alguien dejó esa camioneta ahí, de madrugada. Pero ¿quién iba al volante? Eso nadie lo supo."),
        ("Ella ejercía una influencia considerable sobre los asuntos financieros del hogar.",
         "Cuando se trataba del dinero de esa casa, ella tenía mucho que decir."),
        ("Posteriormente se informó de que el hombre había sido visto en las inmediaciones de la estación la noche en cuestión.",
         "Más tarde llegó un aviso. Alguien dijo que había visto al hombre cerca de la estación. Esa misma noche."),
    ],
    "it": [
        ("Nel corso delle approfondite indagini, da parte delle autorità è stato accertato che il veicolo era stato abbandonato nelle prime ore del mattino, mentre l'identità del conducente è rimasta non accertata.",
         "La polizia indagò a lungo. E alla fine una cosa era certa: qualcuno aveva lasciato l'auto lì nelle prime ore del mattino. Ma chi era al volante? Quello rimase un mistero."),
        ("La donna esercitava un'influenza considerevole sulla gestione delle questioni finanziarie del nucleo familiare.",
         "Quando in quella casa si parlava di soldi, era lei a dire l'ultima parola."),
        ("Successivamente è stato riferito che il suddetto uomo era stato notato nei pressi della stazione la sera in oggetto.",
         "Più tardi arrivò una segnalazione. Qualcuno aveva visto l'uomo vicino alla stazione. Quella stessa sera."),
    ],
    "fr": [
        ("À l'issue de longues investigations, les autorités établirent que le véhicule avait été abandonné aux premières heures du matin, tandis que l'identité du conducteur demeurait indéterminée.",
         "La police enquêta longtemps. Et à la fin, une seule chose était sûre : quelqu'un avait laissé la voiture là, aux premières heures du matin. Mais qui était au volant ? Cela, personne ne put le dire."),
        ("Elle exerçait une influence considérable sur les affaires financières du foyer.",
         "Dans cette maison, quand il était question d'argent, c'était elle qui avait le dernier mot."),
        ("Il fut par la suite rapporté que l'homme avait été aperçu à proximité de la gare le soir en question.",
         "Plus tard, un témoignage arriva. Quelqu'un disait avoir vu l'homme près de la gare. Le même soir."),
    ],
    "pt": [
        ("No decorrer das extensas investigações, foi apurado pelas autoridades que o veículo havia sido abandonado nas primeiras horas da manhã, permanecendo desconhecida a identidade do condutor.",
         "A polícia investigou por um bom tempo. E no fim, uma coisa ficou clara: alguém tinha deixado o carro ali nas primeiras horas da manhã. Mas quem estava ao volante? Isso continuou sem resposta."),
        ("Ela exercia considerável influência sobre os assuntos financeiros do domicílio.",
         "Quando o assunto era dinheiro naquela casa, a opinião dela pesava muito."),
        ("Posteriormente, foi noticiado que o homem teria sido avistado nas imediações da estação na noite em questão.",
         "Mais tarde, chegou uma informação. Alguém disse ter visto o homem perto da estação. Naquela mesma noite."),
    ],
    "tr": [
        ("Kapsamlı soruşturmanın ardından yetkili makamlarca, aracın sabahın erken saatlerinde terk edilmiş olduğu tespit edilmiş, ancak sürücünün kimliği belirlenememiştir.",
         "Polis uzun süre araştırdı. Sonunda bir şey kesinleşti: Biri o arabayı sabahın erken saatlerinde oraya bırakmıştı. Peki direksiyonda kim vardı? Onu kimse söyleyemedi."),
        ("Hane halkının mali işleri üzerinde önemli ölçüde nüfuz sahibiydi.",
         "O evde iş paraya gelince, en çok onun sözü geçerdi."),
        ("Daha sonra, söz konusu şahsın ilgili akşam istasyon civarında görüldüğü bildirilmiştir.",
         "Sonra bir ihbar geldi. Biri, adamı istasyonun yakınında gördüğünü söyledi. Aynı akşam."),
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
    "es": [r"\ben base a\b", r"\ba nivel de\b", r"\bllev(?:ó|o|aron) a cabo\b", r"\btuvo lugar\b", r"\bacto seguido\b", r"\bmencionad[oa]s?\b", r"\bdicho (?:vehículo|individuo|sujeto|inmueble)\b", r"\bdel cual\b|\bla cual\b|\blo cual\b", r"\bposteriormente\b", r"\bse procedió a\b", r"\bel referido\b", r"\bhabría(?:n)? sido\b", r"\bel individuo\b"],
    "it": [r"\bsi è proceduto\b", r"\bsi procedette\b", r"\bin data\b", r"\bnella giornata di\b", r"\bil sopracitato\b", r"\bsuddett[oaie]\b", r"\bpresso l'abitazione\b", r"\beffettu(?:ò|ato|ata|ati|ate|are)\b", r"\bin merito a\b", r"\bsuccessivamente\b", r"\bil predetto\b", r"\bin ordine a\b"],
    "fr": [r"\bsuite à\b", r"\bau niveau de\b", r"\bà l'issue de\b", r"\bl'intéressée?\b", r"\bce dernier\b", r"\bladite?\b|\bledit\b", r"\bpar le biais de\b", r"\bprocéd(?:a|èrent|er) à\b", r"\bdans le cadre de\b", r"\bà l'encontre de\b", r"\bles faits en question\b", r"\bsusmentionné"],
    "pt": [r"\bsupra(?:citad|mencionad)[oa]s?\b", r"\bem quest[ãa]o\b", r"\bpor parte d[aeo]s?\b", r"\bno [âa]mbito d[aeo]s?\b", r"\bem face d[aeo]s?\b", r"\bsubsequentemente\b", r"\b(?:foi|foram) realizad[oa]s?\b", r"\bacerca d[aeo]s?\b", r"\bdilig[êe]ncias\b", r"\b(?:o|a|os|as) referid[oa]s?\b", r"\bindiv[íi]duo\b", r"\btranscurso\b"],
    "tr": [r"\bgerçekleştir\w*", r"\bbulunmakta\w*", r"\bsöz konusu\b", r"\bmevcut\b", r"\bilgili şahıs\b", r"\bşahıs\w*", r"\btarafından\b", r"\bmüteak[ıi]\w*", r"\bakabinde\b", r"\w+(?:mekte|makta)(?:dır|dir|dur|dür)\b", r"\bbahse konu\b", r"\bzikredilen\b", r"\bnezdinde\b"],
}


CONNECTORS = {
    "en": '"So", "But", "And then", "Now", "Here\'s the thing"',
    "de": '"Also", "Aber", "Und dann", "Jetzt", "Und genau hier wird es seltsam"',
    "fa": "«خب»، «ولی»، «بعدش»، «حالا»، «نکته این‌جاست»",
    "ar": "«إذن»، «لكن»، «ثم»، «والآن»، «وهنا المفارقة»",
    "es": '«Bueno», «Pero», «Y entonces», «Ahora», «Y aquí viene lo raro»',
    "it": '"Allora", "Ma", "E poi", "Adesso", "Ed è qui che la storia si fa strana"',
    "fr": "« Alors », « Mais », « Et puis », « Maintenant », « Et c'est là que ça devient étrange »",
    "pt": '"Então", "Mas", "E então", "Agora", "E é justamente aqui que fica estranho"',
    "tr": '"Yani", "Ama", "Sonra", "Şimdi", "Asıl tuhaf olan da burada"',
}


DIGIT_TOKEN = re.compile(r"\S*[0-9٠-٩۰-۹]\S*")
EDGE = ".,;:!?؟،؛«»„“”‚‘’\"'()[]…-–—"


def digits_left(text: str, language: str) -> list[str]:
    """The numbers still written as digits in a language whose numbers are written out in words (config spoken.numbers_as_words): none for another."""
    if language not in ai_config.spoken.numbers_as_words:
        return []
    spoken = re.sub(r"\[[^\[\]]*\]", " ", text or "")          # a tag ([calm], a marker) is not spoken: its characters are not numbers
    return list(dict.fromkeys(m.group().strip(EDGE) for m in DIGIT_TOKEN.finditer(spoken)))


LATIN_WORD = re.compile(r"[A-Za-z]+")


def latin_left(text: str, language: str) -> list[str]:
    """The Latin-letter words left in a text of a language that is written only in its own script (config spoken.own_script_only): none for another."""
    if language not in ai_config.spoken.own_script_only:
        return []
    spoken = re.sub(r"\[[^\[\]]*\]", " ", text or "")           # a tag ([calm]) is not spoken
    return list(dict.fromkeys(LATIN_WORD.findall(spoken)))


def latin_fix(word: str) -> str:
    """The repair note sent back for a Latin word in a text of an own-script language."""
    return prompt("shortform/foreign_script_fix").format(word=repr(word))


def numbers_rule(language: str) -> str:
    """The writer's rule for numbers in a language whose numbers are written out in words ("" for another)."""
    return prompt(f"documentary/spoken/numbers_{language}") + "\n\n" if language in ai_config.spoken.numbers_as_words else ""


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
        "digits": digits_left(text, language),
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


def writer_system_prompt(language: str) -> str:
    name = LANG_NAMES.get(language, language)
    limit = ai_config.spoken.max_sentence_words.get(language, 24)
    style = STYLE_GUIDES.get(language, STYLE_GUIDES["en"])
    connectors = CONNECTORS.get(language, CONNECTORS["en"])

    return prompt("documentary/spoken/writer_system_prompt").format(name=name, limit=limit, connectors=connectors, style=style, examples_block=_examples_block(language), numbers_rule=numbers_rule(language), _MARKER_INSTRUCTION=_MARKER_INSTRUCTION)


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
    sections = stored_sections(version) or []
    beats = blueprint.get("beats") or []
    section_ids = {s["id"] for s in sections}
    # Pilot spoken versions intentionally contain only the opening beats. The
    # stored source sections therefore form a strict ordered subset of the
    # master blueprint, not a malformed full blueprint.
    beats = [b for b in beats if b.get("id") in section_ids]
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

"""The deterministic checks of a YouTube tag list: formatting that can be repaired without changing a word's meaning (spaces, a leading `#`, commas inside a tag, duplicates,
Unicode form) and the checks that only report (length, categories, evidence, years, accusations, claims about a fictional case, the script of the language). No model
call: a tag the model wrote is not right because the model wrote it. A list with an `error` issue cannot be approved; a `warning` asks for a look."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.ai_config import TagsConfig

ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
OTHER_SCRIPT = re.compile(r"[Ͱ-ϿЀ-ӿ֐-׿ऀ-෿฀-࿿぀-鿿가-힯]")
LATIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'’-]{2,}")
DIGIT_FORMS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
YEAR = re.compile(r"(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)")
SEPARATORS = re.compile(r"[,،;]")
RTL = {"fa", "ar"}


@dataclass
class Tag:
    text: str
    category: str = "manual"
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"text": self.text, "category": self.category, "evidence": list(self.evidence)}


def clean(text: str) -> str:
    """One tag in its plain form: Unicode NFC, no `#`, no separator inside, single spaces."""
    text = unicodedata.normalize("NFC", str(text)).replace("#", " ")
    return re.sub(r"\s+", " ", SEPARATORS.sub(" ", text)).strip()


def same_tag_key(text: str) -> str:
    """Tags that differ only by case, width forms or spacing are one tag. A zero-width non-joiner is kept: «مک‌کان» and «مک کان» are two real spellings."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def payload_length(texts: list[str]) -> int:
    """What YouTube counts against the 500 characters: every tag, two more for a tag with a space (it is sent in quotes) and one comma between tags."""
    return sum(len(t) + (2 if " " in t else 0) for t in texts) + max(len(texts) - 1, 0)


def as_text(tags: list[Tag]) -> str:
    """The tags as the owner copies them into YouTube Studio."""
    return ", ".join(t.text for t in tags)


def repair(tags: list[Tag]) -> tuple[list[Tag], list[str]]:
    """The formatting fixes: clean every tag, drop empty ones and duplicates (the first stays). Returns the list and what was done, so nothing changes silently."""
    out, seen, done = [], set(), []
    for tag in tags:
        text = clean(tag.text)
        if text != tag.text and text:
            done.append(f"cleaned «{tag.text}» to «{text}»")
        if not text:
            done.append("dropped an empty tag")
            continue
        key = same_tag_key(text)
        if key in seen:
            done.append(f"dropped the duplicate «{text}»")
            continue
        seen.add(key)
        out.append(Tag(text, tag.category, list(tag.evidence)))
    return out, done


def trim_to_fit(tags: list[Tag], limit: int) -> tuple[list[Tag], list[str]]:
    """A generated list that is too long loses its last tags (the model orders them by priority); what was dropped is named. Never used on the owner's own list."""
    kept = list(tags)
    while kept and payload_length([t.text for t in kept]) > limit:
        kept.pop()
    return kept, [f"dropped «{t.text}» to stay within {limit} characters" for t in tags[len(kept):]]


def _issue(kind: str, severity: str, message: str, tag: str | None = None) -> dict:
    return {"type": kind, "severity": severity, "message": message, **({"tag": tag} if tag else {})}


def _words(text: str) -> set[str]:
    return {w.casefold().strip("'’-") for w in LATIN_WORD.findall(unicodedata.normalize("NFC", text))}


def _names(text: str) -> set[str]:
    """The capitalised Latin words of a tag, the ones that can be names (a generic word such as «crime» is not checked against the evidence)."""
    return {w.casefold().strip("'’-") for w in LATIN_WORD.findall(unicodedata.normalize("NFC", text)) if w[0].isupper()}


def _term_in(text: str, terms: list[str]) -> str | None:
    folded = unicodedata.normalize("NFC", text).casefold()
    for term in terms:
        if re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", folded):
            return term
    return None


def check(tags: list[Tag], dossier: dict, cfg: TagsConfig) -> dict:
    """The report of a list for the dossier of its case and language: {ok, char_count, tag_count, issues[{type, severity, message, tag?}]}. `ok` means no error."""
    language, issues = dossier["language"], []
    texts = [t.text for t in tags]
    length = payload_length(texts)
    categories = {c["key"]: c for c in cfg.categories}
    refs = dossier["evidence_refs"]
    evidence_text = dossier["evidence_text"]
    low, high = cfg.target_tags
    if not tags:
        issues.append(_issue("empty_set", "error", "There is no tag."))
    if length > cfg.max_chars:
        issues.append(_issue("too_long", "error", f"The tags are {length} characters long; YouTube takes {cfg.max_chars}."))
    if tags and len(tags) < low:
        issues.append(_issue("few_tags", "warning", f"Only {len(tags)} tags; about {low} to {high} are usual."))
    if len(tags) > high:
        issues.append(_issue("many_tags", "warning", f"{len(tags)} tags; about {low} to {high} are usual, more looks like stuffing."))
    seen: dict[str, str] = {}
    for tag in tags:
        if not tag.text.strip():
            issues.append(_issue("empty_tag", "error", "A tag is empty.", tag.text))
        if "#" in tag.text:
            issues.append(_issue("hashtag", "error", "A video tag has no `#` (hashtags are separate).", tag.text))
        if SEPARATORS.search(tag.text):
            issues.append(_issue("separator_in_tag", "error", "A tag has a comma inside; it would be two tags.", tag.text))
        key = same_tag_key(tag.text)
        if key in seen:
            issues.append(_issue("duplicate", "error", f"Same tag as «{seen[key]}».", tag.text))
        seen[key] = tag.text
        category = categories.get(tag.category)
        if category is None:
            issues.append(_issue("unknown_category", "error", f"Unknown category «{tag.category}».", tag.text))
            continue
        unknown = [r for r in tag.evidence if r not in refs]
        if unknown:
            issues.append(_issue("invented_evidence", "error", f"Evidence not in the case record: {', '.join(unknown)}.", tag.text))
        if category["needs_evidence"] and not tag.evidence:
            issues.append(_issue("no_evidence", "error", f"A {category['label'].lower()} tag needs evidence from the case record.", tag.text))
        cited = " ".join(evidence_text.get(r, "") for r in tag.evidence if r in refs)
        for year in YEAR.findall(tag.text.translate(DIGIT_FORMS)):
            if year not in cited.translate(DIGIT_FORMS):
                issues.append(_issue("year_unsupported", "error", f"The year {year} is not in the evidence this tag cites.", tag.text))
        if tag.category in ("geography_history", "identity") and tag.evidence:
            missing = sorted(w for w in _names(tag.text) if w not in _words(cited) and w not in dossier["known_words"])
            if missing:
                issues.append(_issue("name_unverified", "warning", f"Not found in the cited evidence: {', '.join(missing)}. Check the spelling.", tag.text))
        if tag.category == "channel" and (not dossier["channel_name"] or same_tag_key(tag.text) != same_tag_key(dossier["channel_name"])):
            issues.append(_issue("wrong_channel", "error", "A channel tag is exactly the channel's name.", tag.text))
        term = _term_in(tag.text, cfg.accusation_terms.get(language, []))
        if term and not (dossier["case"]["resolution_status"] == "SOLVED" and tag.evidence):
            issues.append(_issue("unsupported_accusation", "error", f"«{term}» names or condemns someone; the case record does not support it.", tag.text))
        term = _term_in(tag.text, cfg.real_claim_terms.get(language, []))
        if term and dossier["case"]["fictional"]:
            issues.append(_issue("fictional_real_claim", "error", f"«{term}» claims a real case; this case is fictional.", tag.text))
        if language not in RTL and (ARABIC_SCRIPT.search(tag.text) or OTHER_SCRIPT.search(tag.text)):
            issues.append(_issue("wrong_script", "error", f"The script does not belong to the language {language}.", tag.text))
    if language in RTL and tags and not any(ARABIC_SCRIPT.search(t.text) for t in tags):
        issues.append(_issue("no_native_script", "error", f"No tag is written in the script of {language}."))
    native = [t.text for t in tags if language in RTL and t.category in ("identity", "search_phrases") and ARABIC_SCRIPT.search(t.text) and t.evidence]
    if native:
        issues.append(_issue("native_names_to_review", "warning", "Names in the native script are written by the model: " + ", ".join(native[:6]) + ". A native speaker should check them."))
    return {"ok": not any(i["severity"] == "error" for i in issues), "char_count": length, "tag_count": len(tags), "issues": issues}

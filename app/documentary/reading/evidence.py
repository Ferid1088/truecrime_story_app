"""Lexical evidence for the reading of a Persian word: the entries of English Wiktionary (Kaikki extract) with their Tehrani pronunciations and
romanisations, written in the scheme. It is EVIDENCE, not ground truth: it is community-edited, it knows lemmas and a few forms, its IPA is narrow
and noisy, and a word it does not know says nothing. Every candidate keeps where it came from (entry page, kind, tags)."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.reading.transcription import Transcription
from app.documentary.reading.words import key_of

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Candidate:
    latin: str                  # a reading in the scheme
    kind: str                   # "romanization" | "ipa"
    tags: tuple[str, ...] = ()  # the entry's own tags for this sound ("Tehrani"...)


@dataclass
class Entry:
    word: str
    pos: str
    gloss: list[str]
    colloquial: bool
    candidates: list[Candidate]
    form_of: str = ""           # for a colloquial or inflected entry: the word it is a form of
    page: str = ""


@dataclass
class Evidence:
    """What the lexicon says about one written word."""

    key: str
    entries: list[Entry] = field(default_factory=list)

    def readings(self) -> set[str]:
        return {c.latin for e in self.entries for c in e.candidates}


def ipa_to_latin(ipa: str) -> str:
    cfg = ai_config.reading.evidence
    text = re.sub(cfg.ipa_drop, "", ipa)
    pairs = sorted(cfg.ipa_to_latin.items(), key=lambda kv: -len(kv[0]))
    out, i = [], 0
    while i < len(text):
        for src, dst in pairs:
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def romanization_to_latin(roman: str) -> str:
    cfg = ai_config.reading.evidence
    return "".join(cfg.romanization_to_latin.get(ch, ch) for ch in roman.strip().lower())


class Lexicon:
    """The index of the extract: key of a written word → its entries. Built once from the Kaikki file (scripts/build_persian_lexicon.py)."""

    def __init__(self, index: dict[str, list[dict]] | None = None, transcription: Transcription | None = None, verbs: dict[str, list[dict]] | None = None):
        self.index = index or {}
        self.verbs = verbs or {}
        self.tr = transcription or Transcription()

    @staticmethod
    def path() -> Path:
        folder = Path(ai_config.reading.evidence.folder)
        return (folder if folder.is_absolute() else ROOT / folder) / "wiktionary_fa_index.json"

    @classmethod
    def load(cls) -> Lexicon:
        data = json.loads(cls.path().read_text(encoding="utf-8"))
        return cls(data["entries"], verbs=data.get("verbs"))

    def usable(self, latin: str) -> bool:
        return bool(latin) and self.tr.check(latin).status == "valid"

    def entry_of(self, raw: dict) -> dict | None:
        cfg = ai_config.reading.evidence
        cands: list[Candidate] = []
        for f in raw.get("forms", []):
            if "romanization" in (f.get("tags") or []) and not any(ch in cfg.classical_marks for ch in f.get("form", "")):
                latin = romanization_to_latin(f["form"])
                if self.usable(latin):
                    cands.append(Candidate(latin, "romanization", ()))
        for s in raw.get("sounds", []):
            tags = tuple(s.get("tags") or ())
            if "ipa" not in s or any(t in cfg.foreign_tags for t in tags):
                continue
            latin = ipa_to_latin(s["ipa"])
            if self.usable(latin):
                cands.append(Candidate(latin, "ipa", tags))
        if not cands:
            return None
        senses = raw.get("senses") or [{}]
        glosses = [g for s in senses for g in s.get("glosses", [])][:3]
        colloquial = any("colloquial" in (s.get("tags") or []) for s in senses)
        form_of = next((fo.get("word", "") for s in senses for fo in s.get("form_of", [])), "")
        return {"word": raw["word"], "pos": raw.get("pos", ""), "gloss": glosses, "colloquial": colloquial,
                "candidates": [asdict(c) for c in dict.fromkeys(cands)], "form_of": form_of}

    def verb_forms(self, raw: dict, entry: dict) -> dict[str, list[dict]]:
        """The past stem and the past participle of a verb entry, with their readings: the infinitive's reading minus its ending."""
        cfg = ai_config.reading.morphology.verb
        forms = raw.get("forms", [])

        def written(tags: list[str]) -> list[str]:
            return [f["form"] for f in forms if all(t in (f.get("tags") or []) for t in tags)]

        out: dict[str, list[dict]] = {}
        for c in entry["candidates"]:
            if c["kind"] != "romanization" or not c["latin"].endswith(cfg["infinitive_ending"]):
                continue
            base = c["latin"][: -len(cfg["infinitive_ending"])]
            for surface in written(cfg["stem_tags"]):
                out.setdefault(key_of(surface), []).append({"latin": base, "kind": "past stem", "infinitive": raw["word"]})
            for surface in written(cfg["participle_tags"]):
                out.setdefault(key_of(surface), []).append({"latin": base + cfg["participle_reading"], "kind": "past participle", "infinitive": raw["word"]})
        return out

    def build(self, jsonl: Path) -> int:
        index: dict[str, list[dict]] = {}
        verbs: dict[str, list[dict]] = {}
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                raw = json.loads(line)
                entry = self.entry_of(raw)
                if entry:
                    index.setdefault(key_of(raw["word"]), []).append(entry)
                    if raw.get("pos") == "verb":
                        for k, v in self.verb_forms(raw, entry).items():
                            verbs.setdefault(k, []).extend(v)
        self.index, self.verbs = index, verbs
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"attribution": ai_config.reading.evidence.attribution, "entries": index, "verbs": verbs}, ensure_ascii=False),
                        encoding="utf-8")
        return len(index)

    @staticmethod
    def download() -> Path:
        cfg = ai_config.reading.evidence
        folder = Path(cfg.folder) if Path(cfg.folder).is_absolute() else ROOT / cfg.folder
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "kaikki_persian.jsonl"
        urllib.request.urlretrieve(cfg.source_url, target)
        return target

    def lookup(self, surface: str) -> Evidence:
        key = key_of(surface)
        out = Evidence(key)
        for raw in self.index.get(key, []):
            cands = [Candidate(c["latin"], c["kind"], tuple(c["tags"])) for c in raw["candidates"]]
            out.entries.append(Entry(raw["word"], raw["pos"], raw["gloss"], raw["colloquial"], cands, raw["form_of"],
                                     ai_config.reading.evidence.page_url.format(word=raw["word"])))
        return out

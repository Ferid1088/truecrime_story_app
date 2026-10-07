"""Identity of a real-world case — duplicate detection across the system.

A case must never be suggested twice, whatever it is called. Titles are
weak evidence ("The Murder of Inga Gehricke" vs "Inga Gericke case"), so
identity compares what makes an incident unique: the people involved
(victim, suspect), the place, the dates, the source URLs, aliases and
known identifiers — with spelling-tolerant name matching (accents,
ß/ss, ü/ue, double letters, ck/k ...).

Everything here is pure data: an IdentityIndex is built from the database
(every Case in any state, every suggestion that was shown) and can be
passed to background jobs as plain dicts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz

from app.core.ai_config import ai_config

# Words that say WHAT happened, not WHICH case — never identity evidence.
_STOP = {
    "the", "a", "an", "of", "in", "on", "at", "and", "or", "for", "to", "from", "by", "with",
    "case", "cases", "murder", "murders", "murdered", "killing", "killings", "killer", "death",
    "deaths", "disappearance", "disappeared", "missing", "mystery", "unsolved", "solved",
    "cold", "trial", "verdict", "story", "crime", "crimes", "true", "investigation", "body",
    "found", "girl", "boy", "woman", "man", "family", "affair", "homicide", "kidnapping",
    "abduction", "der", "die", "das", "des", "dem", "den", "ein", "eine", "und", "von", "vom",
    "im", "am", "fall", "mord", "mordfall", "tod", "verschwinden", "vermisst", "prozess",
    "le", "la", "les", "de", "du", "el", "los", "las",
}
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def fold(text: str | None) -> str:
    """Lower case, accents removed, special letters spelled out,
    punctuation to spaces."""
    t = (text or "").replace("ß", "ss").replace("ẞ", "ss")
    t = t.replace("æ", "ae").replace("Æ", "ae").replace("ø", "o").replace("Ø", "o")
    t = t.replace("œ", "oe").replace("ł", "l").replace("Ł", "l").replace("đ", "d")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.casefold())
    return re.sub(r"\s+", " ", t).strip()


_DIGRAPHS = (("sch", "sh"), ("ue", "u"), ("oe", "o"), ("ae", "a"), ("ph", "f"), ("ck", "k"),
             ("th", "t"), ("dt", "t"), ("tz", "z"), ("y", "i"))


def skeleton(word: str) -> str:
    """A spelling-tolerant key: Müller = Mueller = Muller,
    Gehricke ≈ Gericke (compared fuzzily)."""
    w = fold(word)
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", w)


def tokens(text: str | None) -> list[str]:
    """Distinctive words of a title or name (no stop words, no years)."""
    return [t for t in fold(text).split()
            if len(t) >= 3 and t not in _STOP and not t.isdigit()]


def years(*texts: str | None) -> set[int]:
    out: set[int] = set()
    for t in texts:
        out |= {int(y) for y in _YEAR.findall(t or "")}
    return out


def canonical_url(url: str | None) -> str | None:
    from app.research_engine.urlnorm import canonicalize_url

    canon = canonicalize_url(url or "")
    if not canon:
        return None
    host, _, path = canon.partition("/")
    if not path.strip("/"):
        return None          # a homepage is not a case-specific source
    if any(g in canon for g in ai_config.case_selection.generic_url_hosts):
        return None
    return canon


def _list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value] if value.strip() else []
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _names(items) -> list[str]:
    out = []
    for p in _list(items):
        name = p.get("name") if isinstance(p, dict) else p
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


@dataclass
class Identity:
    kind: str                     # case | candidate | new
    id: int | None
    title: str
    state: str | None = None      # case status / suggestion state
    titles: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, kind: str, id: int | None, title: str, *, state: str | None = None,
              aliases=None, people=None, location: str | None = None,
              places=None, dates=None, urls=None, identifiers=None) -> "Identity":
        titles = [t for t in [title, *_names(aliases)] if t]
        place_list: list[str] = []
        for p in [location, *_names(places)]:
            for part in re.split(r"[,;/]", p or ""):
                f = fold(part)
                if f and f not in place_list:
                    place_list.append(f)
        return cls(
            kind=kind, id=id, title=title, state=state,
            titles=titles,
            people=[n for n in _names(people) if len(tokens(n)) >= 1],
            places=place_list,
            years=sorted(years(*[d for d in _list(dates) if isinstance(d, str)])),
            urls=sorted({u for u in (canonical_url(x) for x in _list(urls)) if u}),
            identifiers=sorted({fold(i) for i in _list(identifiers) if isinstance(i, str)
                                and fold(i)}),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Identity":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Verdict:
    duplicate: bool
    matched_kind: str | None = None
    matched_id: int | None = None
    matched_title: str | None = None
    matched_state: str | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.duplicate:
            return "; ".join(self.reasons) or "no match"
        if self.matched_kind == "batch":
            return (f"same real-world case as \"{self.matched_title}\" found in this "
                    "discovery run: " + "; ".join(self.reasons))
        where = {"case": "existing case", "candidate": "earlier suggestion"}.get(
            self.matched_kind or "", self.matched_kind or "record")
        state = f", {self.matched_state}" if self.matched_state else ""
        return (f"same real-world case as {where} #{self.matched_id} "
                f"\"{self.matched_title}\"{state}: " + "; ".join(self.reasons))

    def to_dict(self) -> dict:
        return {**asdict(self), "reason": self.reason}


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def _token_overlap(a: list[str], b: list[str], threshold: int) -> list[tuple[str, str]]:
    """Distinctive word pairs that are the same word (spelling-tolerant)."""
    pairs, used = [], set()
    for x in a:
        sx = skeleton(x)
        for y in b:
            if y in used:
                continue
            sy = skeleton(y)
            if sx == sy or (min(len(sx), len(sy)) >= 4 and fuzz.ratio(sx, sy) >= threshold):
                pairs.append((x, y))
                used.add(y)
                break
    return pairs


def _title_match(a: Identity, b: Identity, cfg) -> tuple[bool, str]:
    for ta in a.titles:
        for tb in b.titles:
            fa, fb = fold(ta), fold(tb)
            if fa and fa == fb:
                return True, f"same title \"{ta}\""
            da, db_ = tokens(ta), tokens(tb)
            if not da or not db_:
                continue
            if sorted(map(skeleton, da)) == sorted(map(skeleton, db_)):
                return True, f"title \"{ta}\" = \"{tb}\" (same names)"
            pairs = _token_overlap(da, db_, cfg.person_threshold)
            small = min(len(set(da)), len(set(db_)))
            if len(pairs) >= 2 and len(pairs) >= -(-2 * small // 3):
                score = fuzz.token_set_ratio(" ".join(map(skeleton, da)),
                                             " ".join(map(skeleton, db_)))
                if score >= cfg.title_threshold:
                    return True, (f"title \"{ta}\" ~ \"{tb}\" (shared "
                                  + ", ".join(x for x, _ in pairs) + f"; {score:.0f})")
    return False, ""


def _person_matches(a: Identity, b: Identity, cfg) -> list[str]:
    """Full names (two or more distinctive words) shared by both,
    spelling-tolerant. A person named in the other side's TITLE counts."""
    names_b = list(b.people) + [t for t in b.titles if len(tokens(t)) >= 2]
    out = []
    for pa in a.people + [t for t in a.titles if len(tokens(t)) >= 2]:
        ta = tokens(pa)
        if len(ta) < 2:
            continue
        for pb in names_b:
            tb = tokens(pb)
            if len(tb) < 2:
                continue
            pairs = _token_overlap(ta, tb, cfg.person_threshold)
            # every word of the shorter name matches (first + last name)
            if len(pairs) >= min(len(ta), len(tb)) and len(pairs) >= 2:
                if pa in a.people or pb in b.people:
                    out.append(f"{pa} ≈ {pb}")
                    break
    return sorted(set(out))


def _place_match(a: Identity, b: Identity) -> list[str]:
    """Shared places below country level (a place list ends with the
    widest unit; the last of several parts is skipped)."""
    def local(places: list[str]) -> list[str]:
        return places[:-1] if len(places) > 1 else places

    out = []
    for x in local(a.places):
        for y in local(b.places):
            if x == y or (min(len(x), len(y)) >= 4 and fuzz.ratio(skeleton(x), skeleton(y)) >= 92):
                out.append(x)
    return sorted(set(out))


def _years_close(a: Identity, b: Identity, window: int) -> bool:
    return any(abs(x - y) <= window for x in a.years for y in b.years)


def compare(a: Identity, b: Identity) -> Verdict:
    """Is `a` the same real-world case as `b`? Explainable rules:
      * a shared identifier;
      * a shared case-specific source URL + any shared name;
      * the same title / the same distinctive names in the titles;
      * a shared person's full name + the same place or a close year (or
        nothing that contradicts it: no place and no year known);
      * the same place + the same year + a shared distinctive title word.
    """
    cfg = ai_config.case_selection
    reasons: list[str] = []
    match_to = dict(matched_kind=b.kind, matched_id=b.id, matched_title=b.title,
                    matched_state=b.state)

    ids = set(a.identifiers) & set(b.identifiers)
    if ids:
        return Verdict(True, score=1.0, reasons=[f"same identifier {sorted(ids)[0]}"], **match_to)

    places = _place_match(a, b)
    close = _years_close(a, b, cfg.date_window_years)
    # both place AND dates known and both different: another incident
    # (a namesake), whatever the titles say
    contradicted = bool(a.places and b.places and not places
                        and a.years and b.years and not close)

    ok, why = _title_match(a, b, cfg)
    if ok and not contradicted:
        return Verdict(True, score=0.95, reasons=[why], **match_to)
    if ok:
        reasons.append(f"{why}, but different place and dates")

    people = _person_matches(a, b, cfg)
    title_words = _token_overlap(
        [w for t in a.titles for w in tokens(t)], [w for t in b.titles for w in tokens(t)],
        cfg.person_threshold)
    shared_urls = sorted(set(a.urls) & set(b.urls))

    if shared_urls and (people or title_words):
        return Verdict(True, score=0.9, reasons=[
            f"same source {shared_urls[0]}",
            *(f"same person {p}" for p in people[:2]),
            *([f"shared name {title_words[0][0]}"] if title_words and not people else [])],
            **match_to)
    if people:
        conflict_place = bool(a.places and b.places and not places)
        conflict_year = bool(a.years and b.years and not close)
        corroborated = places or close
        if corroborated or not (conflict_place or conflict_year):
            extra = ([f"same place {places[0]}"] if places else []) + (
                ["close dates"] if close else []) + (
                [] if corroborated else ["no place or date known that contradicts it"])
            return Verdict(True, score=0.85, reasons=[f"same person {people[0]}", *extra],
                           **match_to)
        reasons.append(f"namesake {people[0]} but different "
                       + ("place" if conflict_place else "dates"))
    if places and close and title_words:
        same_year = bool(set(a.years) & set(b.years))
        if same_year:
            return Verdict(True, score=0.75, reasons=[
                f"same place {places[0]}", "same year",
                f"shared name {title_words[0][0]}"], **match_to)
    return Verdict(False, reasons=reasons)


# ---------------------------------------------------------------------------
# the index of everything the system has held
# ---------------------------------------------------------------------------

# Suggestion states that count as "used": shown to the user (or older
# rows from before states existed). Filtered/duplicate rows were never
# offered and must not block a later suggestion.
USED_SUGGESTION_STATES = ("suggested", "accepted", "ignored")


class IdentityIndex:
    def __init__(self, items: list[Identity] | None = None):
        self.items: list[Identity] = list(items or [])

    def add(self, item: Identity) -> None:
        self.items.append(item)

    def check(self, candidate: Identity) -> Verdict:
        """The strongest duplicate match, or a non-duplicate verdict."""
        best: Verdict | None = None
        notes: list[str] = []
        for item in self.items:
            if item.kind == candidate.kind and item.id is not None and item.id == candidate.id:
                continue
            v = compare(candidate, item)
            if v.duplicate and (best is None or v.score > best.score
                                or (v.score == best.score and item.kind == "case"
                                    and best.matched_kind != "case")):
                best = v
            elif not v.duplicate and v.reasons:
                notes.extend(v.reasons)
        if best:
            return best
        return Verdict(False, reasons=notes[:3] or [
            f"checked against {self.count('case')} cases and "
            f"{self.count('candidate')} earlier suggestions"])

    def count(self, kind: str) -> int:
        return sum(1 for i in self.items if i.kind == kind)

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self.items]

    @classmethod
    def from_list(cls, rows: list[dict] | None) -> "IdentityIndex":
        return cls([Identity.from_dict(r) for r in rows or []])

    @classmethod
    def from_db(cls, db, exclude_candidate_ids: set[int] | None = None) -> "IdentityIndex":
        from app.db.models import Case, DiscoveryCandidate

        index = cls()
        for case in db.query(Case).all():
            index.add(case_identity(db, case))
        for cand in db.query(DiscoveryCandidate).all():
            if exclude_candidate_ids and cand.id in exclude_candidate_ids:
                continue
            if (cand.state or "suggested") not in USED_SUGGESTION_STATES:
                continue
            if cand.case_id:          # already represented by its case
                continue
            index.add(candidate_identity(cand))
        return index


def case_identity(db, case) -> Identity:
    """Everything that identifies a case: its own identity fields plus the
    most frequent people/places of its facts and its source URLs."""
    from collections import Counter

    from app.db.models import Fact, Source

    people = _names(case.people_json)
    places = [case.location] if case.location else []
    dates = [case.incident_date, case.latest_development_date]
    if db is not None:
        pc, lc = Counter(), Counter()
        for f in db.query(Fact.people_json, Fact.locations_json).filter(
                Fact.case_id == case.id).all():
            pc.update(_names(f[0]))
            lc.update(_names(f[1]))
        people += [n for n, _ in pc.most_common(8) if n not in people]
        places += [n for n, _ in lc.most_common(5) if n not in places]
        urls = [u for (u,) in db.query(Source.url).filter(Source.case_id == case.id).all()]
    else:
        urls = []
    return Identity.build(
        "case", case.id, case.canonical_title, state=case.status,
        aliases=case.aliases_json, people=people, places=places, dates=dates, urls=urls,
        identifiers=case.identifiers_json)


def candidate_identity(cand) -> Identity:
    return Identity.build(
        "candidate", cand.id, cand.title, state=cand.state or "suggested",
        aliases=cand.aliases_json, people=cand.people_json, location=cand.location,
        dates=[cand.incident_date, cand.latest_development_date],
        urls=cand.source_urls_json)


def raw_identity(raw: dict) -> Identity:
    """A discovery result (LLM extraction) before it is stored."""
    return Identity.build(
        "new", None, (raw.get("title") or "").strip(),
        aliases=raw.get("aliases"), people=raw.get("key_people"),
        location=raw.get("location"),
        dates=[raw.get("incident_date"), raw.get("approximate_date"),
               raw.get("latest_development_date")],
        urls=raw.get("source_urls"), identifiers=raw.get("identifiers"))

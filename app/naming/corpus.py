"""TitleCollisionCorpus — every title the application already holds.

Built only from stored rows: cases (all states, with aliases), discovery
suggestions, approved/published episode titles, films, every title
candidate ever proposed (rejected ones included), research source titles
and the video titles the research engine found. Cached by a cheap
fingerprint of those tables, so it rebuilds itself when a case is added,
a title is approved or edited, or research imports new titles — and
never refreshes from the outside."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (Case, CaseTitleCandidate, DiscoveryCandidate, EpisodeIdentity,
                           Source, Video, VideoSource)
from app.naming.normalize import norm_title

# kinds that name an episode identity (strongest collisions)
IDENTITY_KINDS = ("case_title", "episode_title", "video_title", "candidate", "discovery_title")


@dataclass(frozen=True)
class Entry:
    text: str
    norm: str
    kind: str
    case_id: int | None
    language: str | None = None
    status: str | None = None     # candidate rows: candidate | selected | rejected
    ref: int | None = None


class TitleCollisionCorpus:
    def __init__(self, entries: list[Entry]):
        self.entries = entries
        self.by_norm: dict[str, list[Entry]] = {}
        for e in entries:
            self.by_norm.setdefault(e.norm, []).append(e)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def __len__(self) -> int:
        return len(self.entries)


_TABLES = (
    (Case, Case.canonical_title), (DiscoveryCandidate, DiscoveryCandidate.title),
    (EpisodeIdentity, EpisodeIdentity.editorial_title), (Video, Video.title),
    (CaseTitleCandidate, CaseTitleCandidate.title), (Source, Source.title),
    (VideoSource, VideoSource.title),
)
_cache: dict[int, tuple[tuple, TitleCollisionCorpus]] = {}


def _fingerprint(db: Session) -> tuple:
    parts = []
    for model, col in _TABLES:
        n, top, size = db.query(func.count(model.id), func.max(model.id),
                                func.sum(func.length(col))).one()
        parts.append((n, top, size))
    parts.append(db.query(func.sum(func.length(EpisodeIdentity.published_title))).scalar())
    parts.append(db.query(func.sum(func.length(CaseTitleCandidate.status))).scalar())
    return tuple(parts)


def _add(out: list[Entry], text, kind, case_id, language=None, status=None, ref=None):
    text = (text or "").strip()
    n = norm_title(text)
    if n:
        out.append(Entry(text, n, kind, case_id, language, status, ref))


def build_corpus(db: Session) -> TitleCollisionCorpus:
    out: list[Entry] = []
    for c in db.query(Case).all():
        _add(out, c.canonical_title, "case_title", c.id, c.language, ref=c.id)
        for alias in json.loads(c.aliases_json or "[]"):
            _add(out, alias if isinstance(alias, str) else str(alias), "case_title", c.id,
                 ref=c.id)
    for d in db.query(DiscoveryCandidate).all():
        _add(out, d.title, "discovery_title", d.case_id, ref=d.id)
    for i in db.query(EpisodeIdentity).all():
        _add(out, i.editorial_title, "episode_title", i.case_id, i.language, ref=i.id)
        _add(out, i.published_title, "episode_title", i.case_id, i.language, ref=i.id)
    for v in db.query(Video).all():
        _add(out, v.title, "video_title", v.case_id, v.language, ref=v.id)
        _add(out, v.youtube_title, "video_title", v.case_id, v.language, ref=v.id)
    for t in db.query(CaseTitleCandidate).all():
        _add(out, t.title, "candidate", t.case_id, t.language, t.status, t.id)
    for s in db.query(Source).all():
        _add(out, s.title, "source_title", s.case_id, s.language, ref=s.id)
    for vs in db.query(VideoSource).all():
        _add(out, vs.title, "video_source_title", getattr(vs, "case_id", None),
             getattr(vs, "language", None), ref=vs.id)
    return TitleCollisionCorpus(out)


def get_corpus(db: Session) -> TitleCollisionCorpus:
    key = id(db.get_bind())
    fp = _fingerprint(db)
    hit = _cache.get(key)
    if hit and hit[0] == fp:
        return hit[1]
    corpus = build_corpus(db)
    _cache[key] = (fp, corpus)
    return corpus

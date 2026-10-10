import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    Case,
    Source,
    SourceChunk,
    Fact,
    Contradiction,
    StoryVersion,
    AgentRun,
    ResearchQuery,
    ResearchResult,
    ResearchSourceLink,
)
from app.schemas import (
    CreateCaseRequest,
    UpdateCaseRequest,
    AddSourceRequest,
)


from app.lifecycle.api import resolution_dict
from app.api.deps import duplicate_or_409, get_case_or_404, unique_slug
from app.api.serializers import case_stats, contradiction_dict, fact_dict, source_dict
from app.api import deps

router = APIRouter()


@router.post("/api/cases")
def create_case(payload: CreateCaseRequest, db: Session = Depends(get_db)):
    from app.lifecycle.identity import Identity
    from app.lifecycle.status import set_resolution

    identity = Identity.build("new", None, payload.canonical_title, aliases=payload.aliases,
                              people=payload.people, location=payload.location,
                              dates=[payload.incident_date])
    duplicate_or_409(db, identity, payload.force)
    row = Case(
        canonical_title=payload.canonical_title,
        slug=unique_slug(db, payload.canonical_title),
        language=payload.language,
        summary=payload.summary,
        aliases_json=json.dumps(payload.aliases, ensure_ascii=False),
        people_json=json.dumps(payload.people, ensure_ascii=False),
        location=payload.location,
        incident_date=payload.incident_date,
        origin="manual",
    )
    db.add(row)
    db.flush()
    set_resolution(db, row, payload.resolution_status, changed_by="user",
                   reason="set when the case was created", commit=False)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "canonical_title": row.canonical_title,
        "slug": row.slug,
        **resolution_dict(row),
    }


@router.get("/api/cases")
def list_cases(status: str | None = None, q: str | None = None,
               resolution: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Case)
    if status:
        query = query.filter(Case.status == status)
    if resolution and resolution.upper() != "ALL":
        query = query.filter(Case.resolution_status == resolution.upper())
    if q:
        query = query.filter(Case.canonical_title.ilike(f"%{q}%"))
    rows = query.order_by(Case.created_at.desc()).all()
    out = []
    for r in rows:
        last_run = (
            db.query(AgentRun)
            .filter(AgentRun.case_id == r.id)
            .order_by(AgentRun.started_at.desc())
            .first()
        )
        last_story = (
            db.query(StoryVersion)
            .filter(StoryVersion.case_id == r.id)
            .order_by(StoryVersion.created_at.desc())
            .first()
        )
        timestamps = [r.created_at]
        if last_run and last_run.started_at:
            timestamps.append(last_run.started_at)
        if last_story and last_story.created_at:
            timestamps.append(last_story.created_at)
        out.append(
            {
                "id": r.id,
                "title": r.canonical_title,
                "status": r.status,
                **resolution_dict(r),
                "language": r.language,
                "created_at": r.created_at,
                "last_activity": max(timestamps),
                **case_stats(db, r.id),
            }
        )
    return out


@router.get("/api/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    latest_story = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )
    return {
        "id": case.id,
        "title": case.canonical_title,
        "slug": case.slug,
        "status": case.status,
        **resolution_dict(case),
        "aliases": json.loads(case.aliases_json or "[]"),
        "people": json.loads(case.people_json or "[]"),
        "location": case.location,
        "incident_date": case.incident_date,
        "latest_development_date": case.latest_development_date,
        "origin": case.origin,
        "language": case.language,
        "summary": case.summary,
        "created_at": case.created_at,
        "languages_found": sorted({s.language for s in sources if s.language}),
        "narrative_angle": latest_story.narrative_angle if latest_story else None,
        **case_stats(db, case_id),
    }


@router.patch("/api/cases/{case_id}")
def update_case(case_id: int, payload: UpdateCaseRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    if payload.status is not None:
        case.status = payload.status
    if payload.canonical_title is not None:
        case.canonical_title = payload.canonical_title
    if payload.summary is not None:
        case.summary = payload.summary
    if payload.aliases is not None:
        case.aliases_json = json.dumps(payload.aliases, ensure_ascii=False)
    if payload.people is not None:
        case.people_json = json.dumps(payload.people, ensure_ascii=False)
    if payload.location is not None:
        case.location = payload.location or None
    if payload.incident_date is not None:
        case.incident_date = payload.incident_date or None
    db.commit()
    return {"id": case.id, "status": case.status, **resolution_dict(case)}


@router.post("/api/cases/{case_id}/sources")
def add_source(case_id: int, payload: AddSourceRequest, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)

    row = Source(
        case_id=case_id,
        title=payload.title,
        url=payload.url,
        source_type=payload.source_type,
        language=payload.language,
        publisher=payload.publisher,
        raw_text=payload.raw_text,
        notes=payload.notes,
        reliability_score=payload.reliability_score,
        is_authorized_text=payload.is_authorized_text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@router.get("/api/cases/{case_id}/sources")
def list_sources(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (
        db.query(Source)
        .filter(Source.case_id == case_id)
        .order_by(Source.created_at.desc())
        .all()
    )
    return [source_dict(s) for s in rows]


@router.delete("/api/cases/{case_id}/sources/{source_id}")
def delete_source(case_id: int, source_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    db.commit()
    return {"deleted": source_id}


@router.get("/api/cases/{case_id}/sources/{source_id}")
def get_source_detail(
    case_id: int, source_id: int, db: Session = Depends(get_db)
):
    """Source detail with full discovery provenance (Part 25): every
    query — across languages and jobs — that surfaced this source."""
    s = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    links = (
        db.query(ResearchSourceLink, ResearchResult, ResearchQuery)
        .join(
            ResearchResult,
            ResearchResult.id == ResearchSourceLink.research_result_id,
        )
        .join(
            ResearchQuery,
            ResearchQuery.id == ResearchResult.research_query_id,
        )
        .filter(ResearchSourceLink.source_id == source_id)
        .all()
    )
    seen_queries: set[int] = set()
    discovered_by = []
    for link, res, q in links:
        if q.id in seen_queries:
            continue
        seen_queries.add(q.id)
        discovered_by.append(
            {
                "query": q.query_text,
                "language": q.language,
                "purpose": q.purpose,
                "round": q.round,
                "link_type": link.link_type,
            }
        )
    d = source_dict(s)
    d["discovered_by"] = discovered_by
    return d


@router.get("/api/cases/{case_id}/sources/{source_id}/chunks")
def source_chunks(case_id: int, source_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    chunks = (
        db.query(SourceChunk)
        .filter(SourceChunk.source_id == source.id)
        .order_by(SourceChunk.chunk_index)
        .all()
    )
    return [
        {
            "id": c.id,
            "chunk_index": c.chunk_index,
            "text": c.text,
            "token_count": c.token_count,
            "section_title": c.section_title,
            "page_or_location": c.page_or_location,
            "language": c.language,
        }
        for c in chunks
    ]


@router.get("/api/cases/{case_id}/corpus-search")
async def corpus_search(
    case_id: int,
    q: str,
    language: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Hybrid corpus search (Part 49): BM25 + dense-embedding cosine fused
    over this case's downloaded research chunks. Multilingual — a Persian
    query matches English evidence semantically via the embedding model."""
    get_case_or_404(db, case_id)
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    limit = max(1, min(int(limit or 10), 50))

    from app.research_engine.index import (
        CorpusIndex,
        IndexedChunk,
        INDEX_CACHE,
    )

    idx = INDEX_CACHE.get(case_id)
    if idx is None:
        provider = deps.research_provider()
        embedder = getattr(provider, "embedder", None)
        if embedder is not None and not embedder.is_configured():
            embedder = None
        idx = CorpusIndex(embedder=embedder)
        rows = (
            db.query(SourceChunk, Source)
            .join(Source, Source.id == SourceChunk.source_id)
            .filter(Source.case_id == case_id)
            .all()
        )
        await idx.build(
            [
                IndexedChunk(
                    chunk_id=c.id,
                    source_id=s.id,
                    source_title=s.title or "",
                    language=c.language or s.language,
                    text=c.text or "",
                    location=c.page_or_location or c.section_title,
                )
                for c, s in rows
            ],
            embed=True,
        )
        INDEX_CACHE.put(case_id, idx)

    hits = await idx.search(q, limit=limit, language=language)
    src_urls = {
        s.id: s.url
        for s in db.query(Source)
        .filter(Source.id.in_({h.source_id for h in hits}))
        .all()
    } if hits else {}
    return {
        "query": q,
        "language": language,
        "case_id": case_id,
        "chunks_indexed": len(idx.chunks),
        "dense_enabled": idx._matrix is not None,
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "source_id": h.source_id,
                "source_title": h.source_title,
                "source_url": src_urls.get(h.source_id),
                "language": h.language,
                "text": h.text,
                "location": h.location,
                "score": h.score,
                "bm25_score": h.bm25_score,
                "dense_score": h.dense_score,
            }
            for h in hits
        ],
    }


@router.get("/api/cases/{case_id}/facts")
def list_facts(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = db.query(Fact).filter(Fact.case_id == case_id).all()
    return [fact_dict(f) for f in rows]


@router.get("/api/cases/{case_id}/timeline")
def case_timeline(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (
        db.query(Fact)
        .filter(Fact.case_id == case_id, Fact.event_date.isnot(None))
        .all()
    )
    items = [fact_dict(f) for f in rows]
    items.sort(key=lambda f: f["event_date"])
    return items


@router.get("/api/cases/{case_id}/contradictions")
def list_contradictions(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    return [contradiction_dict(c) for c in rows]


@router.get("/api/cases/{case_id}/dossier")
def case_dossier(case_id: int, db: Session = Depends(get_db)):
    """The CanonicalResearchDossier — the merged view of web research +
    verified video claims that feeds story generation."""
    from app.services.dossier import build_dossier

    case = get_case_or_404(db, case_id)
    return build_dossier(db, case)

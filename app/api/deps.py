
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    StoryVersion,
)
from app.utils import slugify
from app.providers import get_research_provider
from app.providers.generation import get_generation_provider




def get_case_or_404(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def generation_provider_status() -> dict:
    """Classified generation-provider status — one probe, reused by the
    status endpoint, the readiness report and the generation preflight."""
    provider = get_generation_provider()
    if hasattr(provider, "provider_status"):
        return await provider.provider_status()
    configured = provider.is_configured()
    return {
        "provider": provider.name,
        "configured": configured,
        "reachable": (
            await provider.check_health() if configured else False
        ),
        "authorized": True,
        "status": "unknown",
    }


async def require_generation_authorized() -> dict:
    """Part 23: no expensive generation while the provider cannot authorize.

    Checks configured → reachable → authorized; fails with a classified
    503 rather than letting an expensive pipeline die mid-run."""
    st = await generation_provider_status()
    if not st.get("configured"):
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_not_configured",
                    "provider_status": st,
                    "message": "Generation provider is not configured."},
        )
    if not st.get("reachable"):
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_unreachable",
                    "provider_status": st,
                    "message": "Generation provider is unreachable."},
        )
    if st.get("authorized") is False:
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_unauthorized",
                    "provider_status": st,
                    "message": (
                        "Generation provider has a permission/quota problem "
                        f"({st.get('status')}). Fix the provider before "
                        "running expensive generation."
                    )},
        )
    return st


def story_or_404(db: Session, case_id: int, version_id: int) -> StoryVersion:
    get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    return story


def best_master(db: Session, case_id: int) -> StoryVersion | None:
    return (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id, StoryVersion.kind == "master")
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )


def duplicate_or_409(db: Session, identity, force: bool, exclude_candidate: int | None = None):
    """The duplicate checker runs before any case is created (identity:
    people, places, dates, URLs, aliases — not just the title)."""
    from app.lifecycle.identity import IdentityIndex

    index = IdentityIndex([i for i in IdentityIndex.from_db(db).items
                           if i.kind == "case"])
    verdict = index.check(identity)
    if verdict.duplicate and not force:
        raise HTTPException(status_code=409, detail={
            "message": "This case already exists in the system.", **verdict.to_dict()})
    return verdict


def unique_slug(db: Session, title: str) -> str:
    base_slug = slugify(title)
    slug, i = base_slug, 2
    while db.query(Case).filter(Case.slug == slug).first():
        slug = f"{base_slug}-{i}"
        i += 1
    return slug


def research_provider():
    """The search-engine provider (a function so tests and tools can replace it in one place)."""
    return get_research_provider()


def generation_provider():
    return get_generation_provider()

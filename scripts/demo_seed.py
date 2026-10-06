from app.db.base import Base, engine, SessionLocal
from app.db.models import Case, Source
from app.utils import slugify

Base.metadata.create_all(bind=engine)

db = SessionLocal()

title = "Example Case"
case = Case(
    canonical_title=title,
    slug=slugify(title),
    language="fa",
    summary="Demo case for testing the pipeline.",
)
db.add(case)
db.commit()
db.refresh(case)

db.add(
    Source(
        case_id=case.id,
        title="Example public source",
        url="https://example.com",
        source_type="article",
        language="en",
        publisher="Example",
        raw_text="Replace this with text you are authorized to use.",
        reliability_score=0.5,
        is_authorized_text=True,
    )
)

db.commit()
print(f"Created demo case id={case.id}")
db.close()

import asyncio
from app.db.base import SessionLocal
from app.documentary.detective.review import review, persist_findings


async def main():
    db = SessionLocal()
    try:
        raw = await review(db, 11, mode="plan_audit", language="en", text_version=1)
        db.commit()
        print("OUTCOME", raw.get("outcome"))
        print("INSPECTION_MODES", raw.get("inspection_modes"))
        print("SUMMARY", raw.get("summary"))
        findings = persist_findings(db, 11, raw)
        db.commit()
        print("FINDINGS", len(findings))
        for f in findings:
            print(" -", f.severity, f.repair_owner, f.observed_problem[:120])
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

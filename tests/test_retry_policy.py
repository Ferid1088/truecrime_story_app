import asyncio
import json

from app.db.models import PolicyEvent
from app.services.retry_policy import ExhaustedAction, RetryAuditPolicy


def test_retry_policy_passes_exact_reasons_and_ids_and_omits_after_two_redos():
    feedback_seen = []

    async def produce(feedback):
        feedback_seen.append(feedback)
        return {"attempt": len(feedback_seen)}

    async def audit(_produced):
        return {"approved": False, "reasons": ["wrong cut"],
                "failed_item_ids": ["piece-7"]}

    outcome = asyncio.run(RetryAuditPolicy().run_with_audit(
        produce, audit, 2, ExhaustedAction.OMIT, stage="video_pieces"))

    assert outcome.status == "omitted"
    assert outcome.attempts == 3
    assert outcome.reasons == ("wrong cut",)
    assert outcome.failed_item_ids == ("piece-7",)
    assert feedback_seen == [
        {"reasons": [], "failed_item_ids": []},
        {"reasons": ["wrong cut"], "failed_item_ids": ["piece-7"]},
        {"reasons": ["wrong cut"], "failed_item_ids": ["piece-7"]},
    ]


def test_retry_policy_returns_approved_outcome_before_exhaustion():
    calls = []

    def produce(feedback):
        calls.append(feedback)
        return len(calls)

    def audit(value):
        return {"approved": value == 2, "reasons": [] if value == 2 else ["retry"]}

    outcome = asyncio.run(RetryAuditPolicy().run_with_audit(
        produce, audit, 2, ExhaustedAction.STOP, stage="master_story"))
    assert outcome.status == "approved" and outcome.attempts == 2


def test_retry_policy_records_attempts_and_final_outcome(db_session):
    async def produce(_feedback):
        return "bad"

    async def audit(_produced):
        return {"approved": False, "reasons": ["not faithful"],
                "failed_item_ids": ["chapter:2/de"]}

    before = db_session.query(PolicyEvent).count()
    outcome = asyncio.run(RetryAuditPolicy(
        db_session, case_id=9, language="de").run_with_audit(
            produce, audit, 1, ExhaustedAction.OMIT, stage="chapter_texts"))
    rows = db_session.query(PolicyEvent).order_by(PolicyEvent.id).all()[before:]
    assert outcome.status == "omitted" and len(rows) == 3
    assert rows[-1].decision == "omitted"
    assert json.loads(rows[-1].context_json)["failed_item_ids"] == ["chapter:2/de"]

"""Seed the isolated stack for real approval, naming and Shorts browser tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
from sqlalchemy import inspect, text

from app.core.test_database_guard import assert_safe_test_database
from app.db.base import SessionLocal
from app.db.models import (
    Case,
    DocumentaryJob,
    EditorialBlueprint,
    EpisodeIdentity,
    StoryVersion,
    Thumbnail,
    Video,
    VisualAsset,
)
from app.documentary import storage
from app.utils import text_hash

ROOT = Path(__file__).resolve().parents[1]


def main(*, include_short_languages: bool = False) -> None:
    with SessionLocal() as db:
        bind = db.get_bind()
        assert_safe_test_database(bind.url.render_as_string(hide_password=False))
        if bind.dialect.name != "postgresql" or bind.url.database != "truecrime_e2e":
            raise RuntimeError("approval E2E seed is restricted to PostgreSQL database truecrime_e2e")
        tables = [name for name in inspect(bind).get_table_names() if name != "alembic_version"]
        if tables:
            quoted = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in tables)
            db.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            db.commit()

        case = Case(canonical_title="Approval Flow E2E Case", slug="approval-flow-e2e",
                    language="en", status="story_ready", resolution_status="UNSOLVED",
                    summary="A recorded provider fixture for approval flow testing.")
        db.add(case)
        db.flush()
        master_text = "The lighthouse log records one final signal. The evidence remains disputed."
        master = StoryVersion(case_id=case.id, version=1, kind="master", language="en",
                              narrative_angle=json.dumps({"title": "The Final Signal",
                                                          "central_question": "What happened after the signal?"}),
                              story_text=master_text, status="approved", is_best=True,
                              text_hash=text_hash(master_text))
        db.add(master)
        db.flush()
        localized_text = "آخرین سیگنال در دفتر فانوس ثبت شد."
        localization = StoryVersion(case_id=case.id, version=1, kind="localized", language="fa",
                                    master_version_id=master.id, narrative_angle="روایت مستند",
                                    story_text=localized_text, status="ready", is_best=True,
                                    text_hash=text_hash(localized_text), native_quality_score=0.95,
                                    semantic_consistency_score=0.95, factual_consistency_score=0.95,
                                    engagement_score=0.8)
        db.add(localization)
        if include_short_languages:
            arabic_text = "سُجِّلت الإشارة الأخيرة في دفتر المنارة."
            db.add(StoryVersion(case_id=case.id, version=1, kind="localized", language="ar",
                                master_version_id=master.id, narrative_angle="رواية وثائقية",
                                story_text=arabic_text, status="approved", is_best=True,
                                text_hash=text_hash(arabic_text), native_quality_score=0.95,
                                semantic_consistency_score=0.95, factual_consistency_score=0.95,
                                engagement_score=0.8))
        db.flush()
        db.add(EpisodeIdentity(case_id=case.id, case_uid=case.case_uid, channel_id="en",
                               language="en", editorial_title="The Final Signal",
                               resolution_status="UNSOLVED", resolution_label="Unsolved",
                               youtube_title="The Final Signal (Unsolved) | ClueVera",
                               host_outfit_id="navy_cord_blazer_vest",
                               host_reference_asset="HOST_navy_cord_blazer_vest_front"))
        short_languages = ["en", "fa", "ar"] if include_short_languages else ["en"]
        if include_short_languages:
            localization.status = "approved"
        job = DocumentaryJob(case_id=case.id, master_version_id=master.id, status="completed",
                             mode="full", languages_json=json.dumps(short_languages), stages_json="[]")
        db.add(job)
        db.flush()
        db.add(EditorialBlueprint(
            case_id=case.id, story_version_id=master.id, status="valid",
            blueprint_json=json.dumps({"beats": [
                {"id": "B1", "text": "The last signal", "start_seconds": 0, "end_seconds": 4},
                {"id": "B2", "text": "The disputed log", "start_seconds": 4, "end_seconds": 8},
            ]}),
        ))
        source_video = ROOT / "frontend" / "e2e" / "assets" / "short-101.mp4"
        for language in short_languages:
            db.add(Video(case_id=case.id, job_id=job.id, language=language, state="rendered",
                         mode="full", file_path=storage.rel(source_video), title="The Final Signal"))
        image_dir = ROOT / "data" / "e2e" / "approval-flow"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / "thumbnail.jpg"
        Image.new("RGB", (1600, 900), (26, 33, 48)).save(image_path, quality=90)
        image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        asset = VisualAsset(
            case_id=case.id, asset_code="E2E_001", asset_type="photo", subject_type="place",
            title="Lighthouse exterior", description="Verified lighthouse photograph",
            asset_role="context", provider="upload", rights_status="public_domain",
            rights_reason="E2E fixture", verification_status="verified", verification_confidence=1.0,
            verification_json=json.dumps({"matches_claim": "yes", "depicts": "lighthouse"}),
            relevance_tier=2, local_path=storage.rel(image_path), thumbnail_path=storage.rel(image_path),
            width=1600, height=900, sha256=image_hash, found_during="upload",
        )
        db.add(asset)
        db.flush()
        thumb_path = image_dir / "thumbnail-en.jpg"
        Image.new("RGB", (1600, 900), (40, 45, 60)).save(thumb_path, quality=90)
        db.add(Thumbnail(
            case_id=case.id, language="en", version=1, status="draft",
            brief_json=json.dumps({"channel": "ClueVera", "resolution_label": "Unsolved",
                                   "thumbnail_text": "", "host": {"person": "Fereidoun",
                                   "side": "left", "size": "large", "pose": "front",
                                   "outfit_id": "navy_cord_blazer_vest"},
                                   "case_visuals": [{"asset_id": asset.id, "kind": "place",
                                                     "role": "primary"}]}),
            critic_json=json.dumps({"verdict": "pass", "vision_checked": True,
                                    "failures": [], "checks": [], "scorecard": {}}),
            layout_json="{}", file_path=storage.rel(thumb_path), sha256=hashlib.sha256(thumb_path.read_bytes()).hexdigest(),
            outfit_id="navy_cord_blazer_vest", host_asset_id="HOST_navy_cord_blazer_vest_front",
            primary_asset_id=asset.id, status_label="Unsolved",
        ))
        db.commit()
        print(json.dumps({"case_id": case.id, "job_id": job.id, "database": bind.url.database}))


if __name__ == "__main__":
    main()

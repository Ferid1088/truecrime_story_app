"""Write the case-6 visual-asset rights review list without changing the DB."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.base import SessionLocal
from app.db.models import VisualAsset


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "shortform"
JSON_PATH = OUT_DIR / "case6_visual_asset_review.json"
MARKDOWN_PATH = OUT_DIR / "case6_visual_asset_review.md"


def main() -> None:
    with SessionLocal() as db:
        assets = db.query(VisualAsset).filter_by(case_id=6).order_by(VisualAsset.id).all()
        rows = []
        for asset in assets:
            local = (ROOT / asset.local_path).resolve() if asset.local_path else None
            rows.append({
                "asset_id": asset.asset_code,
                "source": asset.source_name,
                "source_url": asset.source_url,
                "local_path": str(local) if local else None,
                "local_exists": bool(local and local.exists()),
                "rights_status": asset.rights_status,
                "rights_reason": asset.rights_reason,
                "editorial_note": {
                    "title": asset.title,
                    "caption": asset.caption,
                    "license": asset.license,
                    "credit": asset.credit,
                    "page_url": asset.page_url,
                },
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Case 6 Visual Asset Rights Review",
        "",
        f"Source: live `visual_assets` rows for `case_id=6`; asset count: **{len(rows)}**.",
        "Rights statuses and notes are copied as stored. No database values were changed.",
        "",
        "| Asset ID | Source | Source URL | Local path | Exists | Rights status | Rights reason | Editorial note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        note = row["editorial_note"]
        note_text = "; ".join(
            f"{key}={value}" for key, value in note.items() if value
        ) or "(none stored)"
        source = row["source"] or "(not stored)"
        source_url = row["source_url"] or "(not stored)"
        local = row["local_path"] or "(not stored)"
        reason = row["rights_reason"] or "(not stored)"
        lines.append(
            f"| `{row['asset_id']}` | {source} | {source_url} | `{local}` | "
            f"{str(row['local_exists']).lower()} | `{row['rights_status']}` | "
            f"{reason} | {note_text} |"
        )
    lines.extend([
        "",
        "`local_exists=false` means the database path is recorded but the file is not currently present at that absolute path.",
    ])
    MARKDOWN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"assets={len(rows)}")
    print(f"markdown={MARKDOWN_PATH}")
    print(f"json={JSON_PATH}")
    print(f"local_exists={sum(row['local_exists'] for row in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()

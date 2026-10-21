"""Contact sheets of the visual funnel: a grid of stored pictures with the verdict and its
confidence under each, so a person can judge whether the vision check was right."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import VisualAsset
from app.documentary import storage
from app.documentary.visuals.funnel import GENERATED, rejection_reason

CAPTION_LINES = 3
CAPTION_PX = 14
PAD = 6


def spread(items: list, count: int) -> list:
    """`count` items spread evenly over the list (all of them when there are fewer)."""
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


def sample_rejected(assets: list[VisualAsset], count: int) -> list[VisualAsset]:
    """Rejected pictures spread over the rejection reasons: the same share of each reason,
    the rest taken from the largest."""
    by_reason: dict[str, list[VisualAsset]] = defaultdict(list)
    for a in sorted(assets, key=lambda a: a.id):
        by_reason[rejection_reason(a)].append(a)
    quota = {r: min(len(v), count // len(by_reason)) for r, v in by_reason.items()} if by_reason else {}
    left = count - sum(quota.values())
    for reason in sorted(by_reason, key=lambda r: -len(by_reason[r])):
        extra = min(left, len(by_reason[reason]) - quota[reason])
        quota[reason] += extra
        left -= extra
    return [a for reason in sorted(by_reason) for a in spread(by_reason[reason], quota[reason])]


def sample_case(db: Session, case_id: int) -> dict[str, list[VisualAsset]]:
    cfg = ai_config.visual_report_settings().contact_sheet
    photos = [a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == case_id, VisualAsset.asset_type == "photo",
        VisualAsset.provider != GENERATED) if storage.resolve(a.thumbnail_path)]
    of = lambda status: sorted((a for a in photos if a.verification_status == status), key=lambda a: a.id)  # noqa: E731
    return {"rejected": sample_rejected(of("rejected"), cfg.rejected),
            "verified": spread(of("verified"), cfg.verified),
            "needs_review": spread(of("needs_review"), cfg.needs_review)}


def _caption(a: VisualAsset) -> list[str]:
    conf = f"{a.verification_confidence:.2f}" if a.verification_confidence is not None else "-"
    verdict = a.verification_status if a.verification_status != "rejected" else rejection_reason(a)
    return [f"{a.asset_code} {a.provider}", f"{verdict} conf {conf}",
            f"{a.subject_type or '?'} / tier {a.relevance_tier}"]


def render_sheet(assets: list[VisualAsset], path: Path) -> Path:
    """One PNG with the assets in a grid (columns from the config)."""
    cfg = ai_config.visual_report_settings().contact_sheet
    cell_w, cell_h = cfg.thumbnail_px, cfg.thumbnail_px * 3 // 4 + CAPTION_LINES * (CAPTION_PX + 2)
    rows = -(-len(assets) // cfg.columns)
    sheet = Image.new("RGB", (cfg.columns * (cell_w + PAD) + PAD, rows * (cell_h + PAD) + PAD), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=CAPTION_PX)
    for i, a in enumerate(assets):
        x, y = PAD + (i % cfg.columns) * (cell_w + PAD), PAD + (i // cfg.columns) * (cell_h + PAD)
        thumbnail = storage.resolve(a.thumbnail_path)
        assert thumbnail is not None, "only pictures with a thumbnail are sampled"
        with Image.open(thumbnail) as img:
            thumb = ImageOps.fit(img.convert("RGB"), (cell_w, cfg.thumbnail_px * 3 // 4))
        sheet.paste(thumb, (x, y))
        for n, line in enumerate(_caption(a)):
            draw.text((x, y + thumb.height + 1 + n * (CAPTION_PX + 2)), line, fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


def render_all(db: Session, case_id: int, out_dir: Path) -> dict[str, list[Path]]:
    """Sheets of contact_sheet.per_sheet pictures each: out_dir/<verdict>_<n>.png."""
    per_sheet = ai_config.visual_report_settings().contact_sheet.per_sheet
    out: dict[str, list[Path]] = {}
    for verdict, assets in sample_case(db, case_id).items():
        out[verdict] = [render_sheet(assets[i:i + per_sheet], out_dir / f"{verdict}_{i // per_sheet + 1}.png")
                        for i in range(0, len(assets), per_sheet)]
    return out

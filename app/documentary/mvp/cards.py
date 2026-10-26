"""Step 7 - verified factual graphics. Typographic cards drawn from the film's own words and the case's
validated evidence (a verified quotation, what the story has established, two conflicting accounts, a
quotable spoken sentence). Nothing here is invented: every card names the evidence it came from."""

from __future__ import annotations

import json

from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy.orm import Session

from app.db.models import Case, VisualAsset, VisualPlan
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals.generated import _find_generated, localize_texts
from app.documentary.visuals.research import next_asset_code
from app.documentary.visuals.typography import RTL, _draw_lines, font, text_width, wrap

W, H = 1920, 1080
GROUND, WARM, GREY, RED = (14, 14, 16), (245, 242, 235), (170, 168, 160), (206, 44, 40)
PROVIDER = "generated"
KINDS = ("quote", "conflict", "facts", "text")
HEADINGS = {"facts": "What we know so far", "conflict": "The accounts conflict"}


def _ground() -> Image.Image:
    img = Image.new("RGB", (W, H), GROUND)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-W * 0.2, -H * 0.3, W * 1.2, H * 1.3), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    return Image.composite(Image.new("RGB", (W, H), (34, 33, 36)), img, glow)


def _block(lines: list[str], size: float, spacing: float) -> int:
    return int(len(lines) * size * spacing)


def _centered(img: Image.Image, text: str, language: str, size: int, family: str = "serif",
              top: int | None = None, fill=WARM, width: float = 0.72, lines_max: int = 7) -> int:
    d = ImageDraw.Draw(img)
    f = font(language, family, "regular", size)
    box = int(W * width)
    lines = wrap(" ".join(text.split()), f, language, box)[:lines_max]
    y = top if top is not None else (H - _block(lines, f.size, 1.38)) // 2
    _draw_lines(d, lines, f, language, (W - box) // 2, y, box, "center", fill, 1.38)
    return y + _block(lines, f.size, 1.38)


def text_card(text: str, language: str) -> Image.Image:
    """One spoken sentence, large and centred, under a short red rule."""
    img = _ground()
    f = font(language, "serif", "regular", 66)
    lines = wrap(" ".join(text.split()), f, language, int(W * 0.72))[:6]
    top = (H - _block(lines, f.size, 1.38)) // 2
    ImageDraw.Draw(img).rectangle(((W - 90) // 2, top - 60, (W + 90) // 2, top - 54), fill=RED)
    _centered(img, text, language, 66, top=top, lines_max=6)
    return img


def quote_card(text: str, speaker: str | None, language: str) -> Image.Image:
    img = _ground()
    f = font(language, "serif", "regular", 62)
    lines = wrap(f"“{' '.join(text.split())}”", f, language, int(W * 0.7))[:6]
    top = (H - _block(lines, f.size, 1.38)) // 2 - 30
    ImageDraw.Draw(img).rectangle(((W - 90) // 2, top - 60, (W + 90) // 2, top - 54), fill=RED)
    end = _centered(img, f"“{' '.join(text.split())}”", language, 62, top=top, width=0.7, lines_max=6)
    if speaker:
        d = ImageDraw.Draw(img)
        fs = font(language, "sans", "bold", 36)
        w = text_width(speaker, fs, language)
        _draw_lines(d, [speaker], fs, language, (W - w) // 2, end + 40, w, "center", GREY, 1.2)
    return img


def facts_card(heading: str, items: list[str], language: str) -> Image.Image:
    img = _ground()
    d = ImageDraw.Draw(img)
    fh = font(language, "sans", "bold", 40)
    hw = text_width(heading, fh, language)
    _draw_lines(d, [heading], fh, language, (W - hw) // 2, 120, hw, "center", RED, 1.2)
    f = font(language, "serif", "regular", 44)
    col = int(W * 0.7)
    blocks = [wrap(" ".join(x.split()), f, language, col)[:4] for x in items[:4]]
    total = sum(_block(b, f.size, 1.3) + 36 for b in blocks)
    y = max(220, (H - total) // 2 + 40)
    x = (W - col) // 2
    for lines in blocks:
        d.rectangle((x - 40, y + 12, x - 28, y + 24), fill=RED)
        _draw_lines(d, lines, f, language, x, y, col, "end" if language in RTL else "start", WARM, 1.3)
        y += _block(lines, f.size, 1.3) + 36
    return img


def conflict_card(heading: str, topic: str, description: str, language: str) -> Image.Image:
    img = _ground()
    d = ImageDraw.Draw(img)
    fh = font(language, "sans", "bold", 40)
    hw = text_width(heading, fh, language)
    _draw_lines(d, [heading], fh, language, (W - hw) // 2, 110, hw, "center", RED, 1.2)
    end = _centered(img, topic, language, 50, top=220, fill=WARM, width=0.74, lines_max=3)
    _centered(img, description, language, 36, top=end + 50, fill=GREY, width=0.74, lines_max=9)
    return img


def render(card: dict, language: str, headings: dict[str, str]) -> Image.Image:
    kind = card["kind"]
    if kind == "quote":
        return quote_card(card["text"], card.get("speaker"), language)
    if kind == "facts":
        return facts_card(headings["facts"], card["items"], language)
    if kind == "conflict":
        return conflict_card(headings["conflict"], card["topic"], card["text"], language)
    return text_card(card["text"], language)


def card_key(card: dict, language: str) -> str:
    return f"card|{card['kind']}|{json.dumps(card, sort_keys=True, ensure_ascii=False)}|{language}"


def store_card(db: Session, case: Case, img: Image.Image, key: str, title: str) -> VisualAsset:
    found = _find_generated(db, case.id, "photo", key)
    if found is not None:
        return found
    code = next_asset_code(db)
    out = storage.visuals_dir(case.id, "illustration") / f"{code}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92)
    thumb = storage.thumbs_dir(case.id) / f"{code}.jpg"
    IM.save_thumbnail(img.convert("RGB"), thumb)
    asset = VisualAsset(
        case_id=case.id, asset_code=code, asset_type="photo", subject_type="other", title=f"Card: {title}"[:500],
        provider=PROVIDER, asset_role="evidence", rights_status="owned",
        rights_reason="typographic card made by the app from the film's own words and validated evidence",
        verification_status="verified", verification_confidence=1.0, local_path=storage.rel(out),
        thumbnail_path=storage.rel(thumb), width=img.width, height=img.height, quality_score=0.7,
        found_during="generated", sha256=IM.sha256(out.read_bytes()),
        spec_json=json.dumps({"kind": "mvp_card", "key": key}))
    db.add(asset)
    db.commit()
    return asset


async def materialize_cards(db: Session, case: Case, plan_row: VisualPlan, language: str) -> dict:
    """Turn every SHOW_CARD shot into a stored card picture (NEW_IMAGE with that asset)."""
    plan = json.loads(plan_row.plan_json or "{}")
    stats = {kind: 0 for kind in KINDS}
    headings: dict[str, str] = {}
    need = {f"heading_{k}": v for k, v in HEADINGS.items()}
    headings = {k[len("heading_"):]: v for k, v in (await localize_texts(db, case.id, need, language)).items()}
    for beat in plan.get("beats") or []:
        for shot in beat["shots"]:
            if shot.get("command") != "SHOW_CARD":
                continue
            card = shot.pop("card")
            if card["kind"] == "facts" and language != "en":
                local = await localize_texts(db, case.id, {f"f{i}": t for i, t in enumerate(card["items"])},
                                             language)
                card = {**card, "items": [local[f"f{i}"] for i in range(len(card["items"]))]}
            asset = store_card(db, case, render(card, language, headings), card_key(card, language),
                               card["kind"])
            shot.update({"command": "NEW_IMAGE", "asset_id": asset.asset_code, "role": "evidence"})
            stats[card["kind"]] += 1
    plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return stats

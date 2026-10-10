"""ThumbnailCritic — measurable checks first, then a vision opinion.

Deterministic checks (host size, badge size/contrast/placement, element
count, overlaps, font count, file size, brief rules) decide hard failures.
The vision critic (role `thumbnail_critic`) answers the key question —
"does this look like a professional documentary thumbnail rather than an
AI-generated YouTube collage?" — and scores curiosity, authenticity and
automation feel. Both feed the human-review scorecard; a human approves.

Scorecard values are 0-1; for the fields marked RISK, 0 is best."""

from __future__ import annotations


from pathlib import Path

import numpy as np
from PIL import Image

from app.core.ai_config import ai_config
from app.thumbnails.brief import validate_brief

RISK = ("crowding", "spoiler_risk", "misleading_risk", "automation_feel")



def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def _sharpness(path: Path, rect) -> float:
    x, y, w, h = rect
    g = np.asarray(Image.open(path).convert("L").crop((x, y, x + w, y + h)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(min(lap.var() / 200.0, 1.0))


def deterministic(brief: dict, layout: dict, image_path: Path) -> dict:
    cfg = ai_config.thumbnail
    W, H = layout["canvas"]
    area = W * H
    checks: list[dict] = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    errs = validate_brief(brief)
    check("brief_rules", not errs, "; ".join(errs))
    hx, hy, hw, hh = layout["host"]
    h_share, w_share = hh / H, hw / W
    check("host_present_and_large",
          h_share >= cfg.min_host_height and w_share >= cfg.min_host_width,
          f"{h_share:.2f} of the height, {w_share:.2f} of the width")
    check("host_side_matches_brief", layout["side"] == brief["host"]["side"])
    bx, by, bw, bh = layout["badge"]
    badge_share = bw * bh / area
    check("badge_small_not_dominant", badge_share <= cfg.badge_max_area, f"{badge_share:.3f}")
    check("badge_readable_size", bh >= cfg.badge_min_height_px, f"{bh}px")
    check("badge_contrast", layout["badge_ink_contrast"] >= cfg.min_contrast,
          str(layout["badge_ink_contrast"]))
    check("badge_inside_canvas", bx >= 0 and by >= 0 and bx + bw <= W and by + bh <= H)
    n = len(layout["elements"])
    check("not_crowded", n <= cfg.max_elements, f"{n} elements")
    over = _overlap(layout["host"], layout["panel"]) / (layout["panel"][2] * layout["panel"][3])
    check("host_does_not_cover_picture", over <= 0.08, f"{over:.2f}")
    check("one_font_family", len(set(layout["fonts"])) <= 1, ", ".join(sorted(set(layout["fonts"]))))
    check("file_size", layout["bytes"] <= cfg.max_file_bytes, f"{layout['bytes']} bytes")
    pics = [e for e in layout["elements"] if e.endswith("picture")]
    check("case_picture_count", 1 <= len(pics) <= cfg.max_case_images, str(len(pics)))
    check("text_length", len((brief.get("thumbnail_text") or "").split()) <= cfg.max_text_words)

    crowding = max(0.0, (n - 3) / max(cfg.max_elements - 2, 1)) + max(0.0, over - 0.02) * 4
    sharp = _sharpness(image_path, layout["panel"])
    score = {
        "host_visibility": round(min(h_share / 0.9, 1.0) * min(w_share / 0.2, 1.0), 2),
        "case_visual_clarity": round(0.5 + 0.5 * sharp, 2),
        "brand_consistency": round(0.6 + (0.2 if layout.get("background") == "studio" else 0)
                                   + (0.2 if layout.get("badge") else 0), 2),
        "cleanliness": round(max(0.0, 1.0 - crowding), 2),
        "readability": round(min(layout["badge_ink_contrast"] / 7.0, 1.0)
                             * (1.0 if bh >= cfg.badge_min_height_px else 0.5), 2),
        "crowding": round(min(crowding, 1.0), 2),
        "spoiler_risk": 0.0,          # assets passed the spoiler gates at selection
        "misleading_risk": 0.0,       # pair/suspect rules passed at selection
        "curiosity": None, "authenticity": None, "automation_feel": None,
    }
    return {"checks": checks, "scorecard": score}


async def vision(gen, image_path: Path, brief: dict) -> dict | None:
    if gen is None:
        return None
    from app.agents.thumbnail import ThumbnailCritic

    return await ThumbnailCritic(gen).run(
        image_path=image_path, language=brief["language"], episode_title=brief["episode_title"],
        status_label=brief["resolution_label"])


def _f(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


async def review(brief: dict, layout: dict, image_path: Path, gen=None) -> dict:
    cfg = ai_config.thumbnail
    rep = deterministic(brief, layout, image_path)
    sc = rep["scorecard"]
    llm = await vision(gen, image_path, brief)
    rep["vision_checked"] = llm is not None
    if llm:
        for k in ("curiosity", "authenticity", "automation_feel"):
            sc[k] = _f(llm.get(k))
        for k in ("brand_consistency", "cleanliness"):       # the stricter opinion wins
            v = _f(llm.get(k))
            if v is not None:
                sc[k] = round(min(sc[k], v), 2)
        for k in ("misleading_risk", "spoiler_risk"):
            v = _f(llm.get(k))
            if v is not None:
                sc[k] = round(max(sc[k], v), 2)
        rep["problems"] = [str(p)[:200] for p in (llm.get("problems") or [])][:8]
        rep["reason"] = str(llm.get("reason") or "")[:400]
        rep["looks_professional"] = bool(llm.get("looks_professional", True))
    failures = [f"{c['name']} ({c['detail']})" if c["detail"] else c["name"]
                for c in rep["checks"] if not c["ok"]]
    for k, floor in cfg.min_scores.items():
        if sc.get(k) is not None and sc[k] < floor:
            failures.append(f"{k} {sc[k]:.2f} < {floor}")
    if sc["spoiler_risk"] > cfg.max_spoiler_risk:
        failures.append(f"spoiler_risk {sc['spoiler_risk']:.2f}")
    if sc["misleading_risk"] > cfg.max_misleading_risk:
        failures.append(f"misleading_risk {sc['misleading_risk']:.2f}")
    if sc["automation_feel"] is not None and sc["automation_feel"] > cfg.max_automation_feel:
        failures.append(f"automation_feel {sc['automation_feel']:.2f}")
    if llm and not rep["looks_professional"]:
        failures.append("vision critic: does not look like a professional documentary thumbnail")
    rep["failures"] = failures
    rep["verdict"] = "fail" if failures else ("pass" if llm else "needs_review")
    return rep

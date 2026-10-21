"""Crops of a real picture: every later appearance of a picture in a film shows a different region
of it (config fill_ways.crop_regions), never the same crop twice."""

from __future__ import annotations

from app.core.ai_config import ai_config

# The region of a person's picture is the face the picture was found to show: a later appearance
# settles on it instead of an arbitrary quarter of the photograph.
FACE = -1


def region_box(width: int, height: int, region: int) -> list[int]:
    """Pixels (x0, y0, x1, y1) of region number `region` of a picture."""
    x0, y0, x1, y1 = ai_config.fill_ways.crop_regions[region]
    return [int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)]


def free_region(used: set[int]) -> int | None:
    """The first region not used yet, None when every one is."""
    return next((i for i in range(len(ai_config.fill_ways.crop_regions)) if i not in used), None)


def apply(shot: dict, region: int) -> None:
    """Show this shot as a close-up of `region` (the camera settles on it)."""
    shot["crop_region"] = region
    shot["focus"] = region_box(int(shot.get("width") or 0), int(shot.get("height") or 0), region)
    shot["motion"] = "CROP_FOCUS"


def apply_to(shot: dict, used: set[int]) -> bool:
    """Give a later appearance its crop: the face for a person's picture (once), else the first
    free region. False when there is nothing left that has not been shown."""
    if shot.get("subject_type") == "person" and shot.get("focus") and FACE not in used:
        shot["crop_region"], shot["motion"] = FACE, "CROP_FOCUS"
        return True
    region = free_region(used)
    if region is None or shot.get("subject_type") == "person":
        return False
    apply(shot, region)
    return True

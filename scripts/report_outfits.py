#!/usr/bin/env python
"""Read-only report of outfit PNG dimensions, bytes, and alpha-edge halos."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outfits import OutfitRegistry


def halo_pixels(image: Image.Image) -> int:
    rgba = image.convert("RGBA")
    count = 0
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            if 0 < alpha < 220 and min(red, green, blue) >= 220:
                count += 1
    return count


def main() -> None:
    report = []
    for outfit in OutfitRegistry().all():
        poses = []
        for pose in outfit.poses.values():
            with Image.open(pose.path) as image:
                count = halo_pixels(image)
                poses.append({
                    "role": pose.role,
                    "file": str(pose.path),
                    "size": list(image.size),
                    "bytes": pose.path.stat().st_size,
                    "alpha_edge_glow_halo": count >= 3,
                    "halo_pixels": count,
                })
        report.append({"outfit_id": outfit.outfit_id, "label": outfit.label, "poses": poses})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

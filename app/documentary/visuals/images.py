"""Image files: safe download, normalization, thumbnails, perceptual hash,
face focus points. Pure helpers — no editorial logic."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from app.core.ai_config import ai_config

MAX_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageError(RuntimeError):
    pass


async def download_image(url: str, client: httpx.AsyncClient | None = None) -> bytes:
    cfg = ai_config.visual_search
    limit = int(cfg.max_download_mb * 1024 * 1024)
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=cfg.request_timeout_s, follow_redirects=True,
        headers={"User-Agent": cfg.user_agent})
    try:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if ctype and not ctype.startswith("image/"):
                raise ImageError(f"not an image ({ctype})")
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > limit:
                    raise ImageError("image too large")
            return bytes(data)
    except httpx.HTTPError as e:
        raise ImageError(f"download failed: {type(e).__name__}") from e
    finally:
        if own:
            await client.aclose()


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # PIL raises many types
        raise ImageError(f"unreadable image: {type(e).__name__}") from e
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    return img.convert("RGB")


def dhash(img: Image.Image, size: int = 8) -> str:
    """64-bit difference hash: near-duplicate detection across sources."""
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            a = px[row * (size + 1) + col]
            b = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if a > b else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def save_master(img: Image.Image, path: Path, max_side: int = 3200) -> Path:
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)
    return path


def save_thumbnail(img: Image.Image, path: Path, px: int | None = None) -> Path:
    px = px or ai_config.visual_verification.thumbnail_px
    t = img.copy()
    t.thumbnail((px, px), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    t.save(path, "JPEG", quality=85)
    return path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """sha256 of a file read in chunks (video clips are not held in memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_url(path: Path) -> str:
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def face_boxes(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Frontal faces (x, y, w, h) — used to focus slow pushes on people
    and to keep parallax away from faces. Empty when OpenCV is missing."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    arr = np.asarray(img.convert("L"))
    scale = 800 / max(arr.shape) if max(arr.shape) > 800 else 1.0
    if scale != 1.0:
        arr = cv2.resize(arr, (int(arr.shape[1] * scale), int(arr.shape[0] * scale)))
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    found = cascade.detectMultiScale(arr, scaleFactor=1.1, minNeighbors=6,
                                     minSize=(28, 28))
    return [tuple(int(v / scale) for v in box) for box in found]

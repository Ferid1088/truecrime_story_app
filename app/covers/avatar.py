"""The avatar on a cover: the host cropped out of the film's outfit image around his face.

The outfit images show the host in his studio (there is no transparent cut-out), so the cover gets a framed portrait of him. The face is found with OpenCV's
YuNet detector (config covers.avatar); the studio's wall holds many small photographs of faces, so the host is the largest face (and, among faces nearly as
large, the one the detector is surest of)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary import storage


class AvatarError(RuntimeError):
    pass


def _model() -> Path:
    cfg = ai_config.covers_settings().avatar
    path = storage.ROOT / cfg["face_model"]
    if not path.exists():
        raise AvatarError(f"the face model {cfg['face_model']} is missing: download it from {cfg['face_model_source']}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != cfg["face_model_sha256"]:
        raise AvatarError(f"the face model {cfg['face_model']} is not the one the configuration names")
    return path


def host_face(image: Path) -> tuple[float, float, float, float]:
    """(x, y, width, height) in pixels of the host's face in an outfit image."""
    cfg = ai_config.covers_settings().avatar
    frame = cv2.imread(str(image))
    if frame is None:
        raise AvatarError(f"the outfit image {image} cannot be read")
    height, width = frame.shape[:2]
    found = cv2.FaceDetectorYN.create(str(_model()), "", (width, height), cfg["min_score"], 0.3, 5000).detect(frame)[1]
    if found is None or len(found) == 0:
        raise AvatarError(f"no face in the outfit image {image.name}")
    largest = max(f[2] * f[3] for f in found)
    near = [f for f in found if f[2] * f[3] >= cfg["host_area_share"] * largest]
    x, y, w, h = max(near, key=lambda f: f[14])[:4]
    return float(x), float(y), float(w), float(h)


def portrait(image: Path, aspect: float, face_widths: float) -> Image.Image:
    """The host's head and shoulders: a crop `face_widths` face widths wide with the width/height `aspect`, the face a little above its middle (moved inside the image when it would leave it)."""
    x, y, w, h = host_face(image)
    picture = Image.open(image).convert("RGB")
    crop_w = min(w * face_widths, picture.width, picture.height * aspect)
    crop_h = crop_w / aspect
    left = min(max(x + w / 2 - crop_w / 2, 0), picture.width - crop_w)
    top = min(max(y + h / 2 - crop_h * 0.34, 0), picture.height - crop_h)
    return picture.crop((round(left), round(top), round(left + crop_w), round(top + crop_h)))

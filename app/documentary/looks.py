"""The host's look library (Part 7b).

request -> generate (image model, the locked studio master as reference) ->
owner approves / rejects / regenerates with a prompt -> HeyGen look id stored
after the upload. Nothing is generated in bulk and nothing reaches HeyGen
before approval; the stage director may only choose APPROVED looks."""

from __future__ import annotations

import base64
import hashlib
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.core.prompts import prompt
from app.db.models import HostLook
from app.documentary import stage_direction as SD
from app.documentary import storage
from app.documentary import studio as ST
from app.utils import utc_now

# Framings that are plain crops of an approved front or wide picture: they
# need no picture of their own (the room never changes).
_CROPS = {"WIDE": ("MEDIUM", "MEDIUM_CLOSE", "CLOSE"), "MEDIUM": ("MEDIUM_CLOSE", "CLOSE")}


class LookError(ValueError):
    pass


class ImageProvider(Protocol):
    model: str

    async def generate(self, prompt: str, reference_png: bytes, outfit_png: bytes) -> bytes: ...


class ApimasterImageProvider:
    """OpenAI-compatible image edit (POST {base}/images/edits) with the studio
    master and the outfit picture as references. The response is read as
    b64_json or url; the first real call is where this gets verified."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 240.0):
        self.base_url, self.api_key, self.model, self.timeout_s = (
            base_url.rstrip("/"), api_key, model, timeout_s)

    async def generate(self, prompt: str, reference_png: bytes, outfit_png: bytes) -> bytes:
        files = [("image[]", ("studio.png", reference_png, "image/png")),
                 ("image[]", ("outfit.png", outfit_png, "image/png"))]
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/images/edits",
                                  headers={"Authorization": f"Bearer {self.api_key}"},
                                  data={"model": self.model, "prompt": prompt, "n": "1"},
                                  files=files)
            r.raise_for_status()
            item = (r.json().get("data") or [{}])[0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"])
            if item.get("url"):
                got = await client.get(item["url"])
                got.raise_for_status()
                return got.content
        raise LookError("the image model returned neither b64_json nor a url")


def default_prompt(look: HostLook) -> str:
    return prompt("documentary/host/look_image").format(
        camera=look.camera, framing=look.framing, pose=look.pose)


def request_look(db: Session, language: str, camera: str, framing: str, outfit_id: str,
                 pose: str, prompt: str | None = None) -> HostLook:
    if camera not in SD.CAMERAS or framing not in SD.FRAMINGS:
        raise LookError("unknown camera or framing")
    profile = ST.channel_profile(language)["studio_profile_id"]
    open_look = db.query(HostLook).filter(
        HostLook.studio_profile_id == profile, HostLook.camera == camera,
        HostLook.framing == framing, HostLook.outfit_id == outfit_id, HostLook.pose == pose,
        HostLook.status != "rejected").first()
    if open_look is not None:
        raise LookError(f"this look already exists (#{open_look.id}, {open_look.status})")
    look = HostLook(studio_profile_id=profile, language=language, camera=camera, framing=framing,
                    outfit_id=outfit_id, pose=pose, prompt=prompt)
    db.add(look)
    db.commit()
    db.refresh(look)
    return look


async def generate(db: Session, look: HostLook, provider: ImageProvider,
                   studio_png: bytes, outfit_png: bytes) -> HostLook:
    if look.status not in ("requested", "rejected", "generated"):
        raise LookError(f"a {look.status} look cannot be generated again")
    png = await provider.generate(look.prompt or default_prompt(look), studio_png, outfit_png)
    path = storage.ROOT / "data" / "looks" / look.studio_profile_id / f"look_{look.id}_{look.attempts + 1}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    look.image_path = str(path.relative_to(storage.ROOT))
    look.image_sha256 = hashlib.sha256(png).hexdigest()
    look.image_model = provider.model
    look.attempts += 1
    look.status = "generated"
    look.approved_by = look.approved_at = look.rejected_reason = None
    db.commit()
    return look


def approve(db: Session, look: HostLook, by: str) -> HostLook:
    if look.status != "generated":
        raise LookError("only a generated look can be approved")
    look.status, look.approved_by, look.approved_at = "approved", by, utc_now()
    db.commit()
    return look


def reject(db: Session, look: HostLook, reason: str) -> HostLook:
    if look.status not in ("generated", "approved"):
        raise LookError("only a generated or approved look can be rejected")
    look.status, look.rejected_reason, look.approved_at, look.approved_by = (
        "rejected", reason, None, None)
    db.commit()
    return look


def regenerate_with_prompt(db: Session, look: HostLook, prompt: str) -> HostLook:
    """Back to requested with a new prompt; generate() makes the new picture."""
    if look.status not in ("generated", "rejected"):
        raise LookError("only a generated or rejected look is regenerated")
    look.prompt, look.status = prompt, "requested"
    db.commit()
    return look


def require_approved_for_upload(look: HostLook) -> None:
    """Nothing is uploaded to HeyGen before the owner approved the picture."""
    if look.status != "approved":
        raise LookError("the look must be approved before it is uploaded to HeyGen")


def mark_uploaded(db: Session, look: HostLook, heygen_look_id: str) -> HostLook:
    require_approved_for_upload(look)
    look.heygen_look_id = heygen_look_id
    db.commit()
    return look


def approved_looks(db: Session, studio_profile_id: str) -> set[tuple[str, str]]:
    """(camera, framing) the stage director may choose: approved looks, plus
    the plain crops of an approved front or wide picture."""
    out: set[tuple[str, str]] = set()
    for look in db.query(HostLook).filter(HostLook.studio_profile_id == studio_profile_id,
                                          HostLook.status == "approved"):
        out.add((look.camera, look.framing))
        if look.camera in ("CAM01_FRONT", "CAM06_WIDE"):
            out.update((look.camera, f) for f in _CROPS.get(look.framing, ()))
    return out


def look_dict(look: HostLook) -> dict:
    return {"id": look.id, "studio_profile_id": look.studio_profile_id, "language": look.language,
            "camera": look.camera, "framing": look.framing, "outfit_id": look.outfit_id,
            "pose": look.pose, "prompt": look.prompt, "image_path": look.image_path,
            "image_sha256": look.image_sha256, "image_model": look.image_model,
            "status": look.status, "approved_by": look.approved_by,
            "approved_at": look.approved_at, "rejected_reason": look.rejected_reason,
            "heygen_look_id": look.heygen_look_id, "attempts": look.attempts}

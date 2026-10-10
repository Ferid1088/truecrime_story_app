"""Channel studios: which studio image belongs to which channel, which
camera angles exist, where the host may sit, and the framing presets the
host scenes use.

Source of truth:
  * config/ (connections.json, models.json, parameters/) → channels.<lang>: channel name, studio folder,
    studio profile id, avatar env names (the voice stays in
    voice.languages — one place per fact);
  * config/studio_registry.json: every studio image (deterministic id,
    sha256, size, camera angle, shot size, approval, safe zones) and one
    profile per channel (primary background, HOST_CLOSE / HOST_MEDIUM /
    HOST_WIDE presets);
  * data/studio/<lang>/: the image files themselves (never copied).

A channel never resolves another channel's studio: every asset carries its
language and a profile may only use assets of its own language.
Coordinates are normalized (0..1) to the image, so they hold at any size.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.ai_config import ai_config

REPO = Path(__file__).resolve().parents[2]

ASSET_TYPES = ("background", "camera_angle", "detail_reference", "design_board")
CAMERA_ANGLES = ("front_wide", "front_medium", "front_close", "three_quarter", "side",
                 "over_shoulder", "detail", "overhead", "unknown")
SHOT_SIZES = ("close", "medium", "wide", "full_room", "plan", "unknown")
PRESETS = ("HOST_CLOSE", "HOST_MEDIUM", "HOST_WIDE")
BACKGROUND_MODES = ("static_studio", "transparent_avatar_over_studio", "provider_composited")


class StudioError(ValueError):
    pass


class Zone(BaseModel):
    """A rectangle in normalized image coordinates (0..1, origin top-left)."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside(self):
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError(f"zone leaves the image: {self.model_dump()}")
        return self

    def contains(self, other: "Zone") -> bool:
        return (other.x >= self.x - 1e-6 and other.y >= self.y - 1e-6
                and other.x + other.width <= self.x + self.width + 1e-6
                and other.y + other.height <= self.y + self.height + 1e-6)

    def overlaps(self, other: "Zone") -> bool:
        return not (other.x >= self.x + self.width or other.x + other.width <= self.x
                    or other.y >= self.y + self.height or other.y + other.height <= self.y)


class SafeZones(BaseModel):
    host: Zone | None = None          # where the host's body may be (seated, in the armchair)
    head: Zone | None = None          # where the head should land (seated)
    logo: Zone | None = None          # the channel logo — never covered
    lower_third: Zone | None = None   # name/title cards
    host_standing: Zone | None = None  # where the host stands, full body
    head_standing: Zone | None = None  # where the standing host's head lands


class StudioAsset(BaseModel):
    id: str
    language: str
    file: str                          # file name inside data/studio/<lang>/
    camera: int | None = None          # camera number of the design board
    shot: str                          # the shot list's name (manifest.json)
    asset_type: str = "background"
    camera_angle: str = "unknown"
    shot_size: str = "unknown"
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    approved_for_host: bool = False
    approved_for_avatar: bool = False
    lighting_style: str | None = None
    studio_style: str | None = None
    notes: str | None = None
    safe_zones: SafeZones = Field(default_factory=SafeZones)

    @field_validator("asset_type")
    @classmethod
    def _type(cls, v):
        if v not in ASSET_TYPES:
            raise ValueError(f"asset_type must be one of {ASSET_TYPES}")
        return v

    @field_validator("camera_angle")
    @classmethod
    def _angle(cls, v):
        if v not in CAMERA_ANGLES:
            raise ValueError(f"camera_angle must be one of {CAMERA_ANGLES}")
        return v

    @field_validator("shot_size")
    @classmethod
    def _size(cls, v):
        if v not in SHOT_SIZES:
            raise ValueError(f"shot_size must be one of {SHOT_SIZES}")
        return v

    @property
    def aspect_ratio(self) -> float | None:
        return round(self.width / self.height, 4) if self.width and self.height else None

    @property
    def orientation(self) -> str | None:
        if not (self.width and self.height):
            return None
        return "landscape" if self.width > self.height else (
            "portrait" if self.height > self.width else "square")


class FramingPreset(BaseModel):
    """How a host shot is built: the studio image (optionally cropped and
    softened for depth), and where the host sits — height as a share of
    the frame, horizontal centre, the line the host's frame stands on."""

    asset_id: str
    crop: Zone | None = None            # part of the studio image shown
    background_blur: float = Field(default=0.0, ge=0.0, le=1.0)
    host_height_ratio: float = Field(gt=0.0, le=2.0)
    host_center_x: float = Field(ge=0.0, le=1.0)
    host_bottom: float = Field(ge=0.0, le=1.5)
    camera_safe: Zone = Field(default_factory=lambda: Zone(x=0.05, y=0.05, width=0.9,
                                                            height=0.9))
    subtitle_safe: Zone = Field(default_factory=lambda: Zone(x=0.1, y=0.82, width=0.8,
                                                              height=0.13))


class StudioProfile(BaseModel):
    id: str
    language: str
    primary_background: str
    presets: dict[str, FramingPreset]
    alternate_angles: list[str] = []
    background_mode: str = "transparent_avatar_over_studio"
    # who classified the shots and whether a person confirmed them
    review: dict = Field(default_factory=lambda: {"by": None, "confirmed": False})

    @field_validator("background_mode")
    @classmethod
    def _mode(cls, v):
        if v not in BACKGROUND_MODES:
            raise ValueError(f"background_mode must be one of {BACKGROUND_MODES}")
        return v


class StudioRegistry(BaseModel):
    version: int = 1
    root: str = "data/studio"
    note: str | None = None
    assets: list[StudioAsset] = []
    profiles: dict[str, StudioProfile] = {}

    def asset(self, asset_id: str) -> StudioAsset | None:
        return next((a for a in self.assets if a.id == asset_id), None)

    def assets_of(self, language: str) -> list[StudioAsset]:
        return [a for a in self.assets if a.language == language]

    def profile_for(self, language: str) -> StudioProfile | None:
        ch = ai_config.channels.get(language)
        pid = ch.profile_id(language) if ch else f"STUDIO_{language.upper()}"
        p = self.profiles.get(language)
        return p if p is not None and p.id == pid else None


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict[str, tuple[float, StudioRegistry]] = {}


def registry_path() -> Path:
    p = Path(ai_config.studio.registry_path)
    return p if p.is_absolute() else REPO / p


def load_registry(path: Path | None = None) -> StudioRegistry:
    path = path or registry_path()
    if not path.exists():
        return StudioRegistry()
    mtime = path.stat().st_mtime
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1].model_copy(deep=True)
        reg = StudioRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
        _cache[key] = (mtime, reg)
        return reg.model_copy(deep=True)


def save_registry(reg: StudioRegistry, path: Path | None = None) -> None:
    """Atomic: a crash mid-write never leaves a half-written registry."""
    path = path or registry_path()
    data = reg.model_dump(mode="json", exclude_none=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with _lock:
        _cache.pop(str(path), None)


def asset_path(reg: StudioRegistry, a: StudioAsset) -> Path:
    root = Path(reg.root)
    root = root if root.is_absolute() else REPO / root
    return root / a.language / a.file


def file_facts(path: Path) -> dict:
    """Technical facts read from the file itself (no model involved)."""
    from PIL import Image

    data = path.read_bytes()
    with Image.open(path) as im:
        w, h = im.size
    return {"width": w, "height": h, "sha256": hashlib.sha256(data).hexdigest()}


# ---------------------------------------------------------------------------
# channel mapping (one place) and resolution
# ---------------------------------------------------------------------------


def channel_profile(language: str) -> dict:
    """Everything a language's channel needs, assembled from the single
    sources: channel name and studio profile (channels), voice
    (voice.languages), avatar env names (channel, else avatar section)."""
    ch = ai_config.channels.get(language)
    if ch is None:
        raise StudioError(f"no channel configured for language {language!r}")
    voice = ai_config.voice.languages.get(language)
    return {
        "language": language,
        "channel_name": ch.name,
        "elevenlabs_voice_id": voice.voice_id if voice else None,
        "studio_profile_id": ch.profile_id(language),
        "heygen_avatar_env": ch.avatar_id_env or ai_config.avatar.avatar_id_env,
        "heygen_key_env": ch.avatar_key_env or ai_config.avatar.secret_env,
    }


def resolve(language: str, reg: StudioRegistry | None = None) -> dict:
    """The studio a language's host scenes use. Raises StudioError when the
    channel has no usable studio — never falls back to another channel."""
    reg = reg or load_registry()
    cp = channel_profile(language)
    prof = reg.profile_for(language)
    if prof is None:
        raise StudioError(f"no studio profile {cp['studio_profile_id']} for {language}")
    if prof.language != language:
        raise StudioError(f"profile {prof.id} belongs to {prof.language}, not {language}")
    used = {"primary": prof.primary_background,
            **{k: v.asset_id for k, v in prof.presets.items()}}
    resolved = {}
    for role, aid in used.items():
        a = reg.asset(aid)
        if a is None:
            raise StudioError(f"{prof.id}: {role} asset {aid} does not exist")
        if a.language != language:
            raise StudioError(f"{prof.id}: {role} asset {aid} belongs to {a.language}")
        resolved[role] = a
    return {**cp, "profile": prof, "primary": resolved["primary"],
            "presets": {k: (v, resolved[k]) for k, v in prof.presets.items()}}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def background_upscale(asset: StudioAsset, preset: FramingPreset) -> float | None:
    """How much the film (1920 wide) enlarges the shown part of the image."""
    if not asset.width:
        return None
    shown = asset.width * (preset.crop.width if preset.crop else 1.0)
    return round(ai_config.render.width / shown, 2)


def validate_language(language: str, reg: StudioRegistry | None = None,
                      check_files: bool = True) -> dict:
    reg = reg or load_registry()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        cp = channel_profile(language)
    except StudioError as e:
        return {"language": language, "ok": False, "errors": [str(e)], "warnings": []}
    if not cp["elevenlabs_voice_id"]:
        errors.append("no ElevenLabs voice configured (voice.languages)")
    assets = reg.assets_of(language)
    if not assets:
        errors.append("no studio assets")
    prof = reg.profile_for(language)
    if prof is None:
        errors.append(f"no studio profile {cp['studio_profile_id']}")
        return {"language": language, "ok": False, "errors": errors, "warnings": warnings}
    if check_files:
        for a in assets:
            p = asset_path(reg, a)
            if not p.exists():
                (errors if a.id == prof.primary_background else warnings).append(
                    f"{a.id}: file {a.language}/{a.file} is missing")
    primary = reg.asset(prof.primary_background)
    if primary is None or primary.language != language:
        errors.append(f"primary background {prof.primary_background} is not a {language} asset")
    elif not (primary.approved_for_host and primary.safe_zones.host):
        errors.append(f"primary background {primary.id} is not approved for the host "
                      "or has no host safe zone")
    host_ok = [a for a in assets if a.approved_for_host and a.safe_zones.host]
    if not host_ok:
        errors.append("no usable host background")
    for a in assets:
        sz = a.safe_zones
        if sz.host and sz.head and not sz.host.contains(sz.head):
            errors.append(f"{a.id}: head safe zone lies outside the host safe zone")
        if sz.head and sz.logo and sz.head.overlaps(sz.logo):
            warnings.append(f"{a.id}: the host's head would cover the logo")
        if sz.host_standing and sz.head_standing and not sz.host_standing.contains(
                sz.head_standing):
            errors.append(f"{a.id}: standing head zone lies outside the standing host zone")
        if sz.head_standing and sz.logo and sz.head_standing.overlaps(sz.logo):
            warnings.append(f"{a.id}: the standing host's head would cover the logo")
    for name in PRESETS:
        pr = prof.presets.get(name)
        if pr is None:
            (warnings if name != "HOST_MEDIUM" else errors).append(f"no {name} preset")
            continue
        a = reg.asset(pr.asset_id)
        if a is None or a.language != language:
            errors.append(f"{name}: asset {pr.asset_id} is not a {language} asset")
            continue
        if not a.approved_for_host:
            errors.append(f"{name}: {a.id} is not approved for the host")
        up = background_upscale(a, pr)
        if up and up > ai_config.studio.max_background_upscale:
            warnings.append(f"{name}: background enlarged {up}x (soft)")
        if name == "HOST_CLOSE" and a.shot_size != "close":
            warnings.append(f"no native close shot — {name} crops {a.id}")
    if not any(a.shot_size in ("wide", "full_room") and a.approved_for_host for a in assets):
        warnings.append("no approved wide shot")
    for aid in prof.alternate_angles:
        a = reg.asset(aid)
        if a is None or a.language != language:
            errors.append(f"alternate angle {aid} is not a {language} asset")
    if not prof.review.get("confirmed"):
        warnings.append(f"shot classification by {prof.review.get('by') or 'nobody'} — "
                        "not yet confirmed")
    return {"language": language, "ok": not errors, "errors": errors, "warnings": warnings}


def validate_all(reg: StudioRegistry | None = None, check_files: bool = True) -> dict:
    reg = reg or load_registry()
    langs = list(dict.fromkeys(list(ai_config.channels) + list(reg.profiles)))
    out = {l: validate_language(l, reg, check_files) for l in langs}
    seen: dict[str, str] = {}
    for a in reg.assets:
        if a.id in seen:
            out.setdefault(a.language, {"errors": []})["errors"].append(f"duplicate id {a.id}")
        seen[a.id] = a.language
        if a.sha256:
            dup = [b.id for b in reg.assets if b.sha256 == a.sha256 and b.id != a.id]
            if dup:
                out[a.language]["warnings"].append(f"{a.id}: same image as {', '.join(dup)}")
    for v in out.values():
        v["ok"] = not v["errors"]
    return out


# ---------------------------------------------------------------------------
# views (no filesystem paths)
# ---------------------------------------------------------------------------


def asset_view(a: StudioAsset, reg: StudioRegistry) -> dict:
    return {
        "id": a.id, "language": a.language, "file_name": a.file, "camera": a.camera,
        "shot": a.shot, "asset_type": a.asset_type, "camera_angle": a.camera_angle,
        "shot_size": a.shot_size, "width": a.width, "height": a.height,
        "aspect_ratio": a.aspect_ratio, "orientation": a.orientation,
        "approved_for_host": a.approved_for_host, "approved_for_avatar": a.approved_for_avatar,
        "lighting_style": a.lighting_style, "studio_style": a.studio_style, "notes": a.notes,
        "safe_zones": a.safe_zones.model_dump(exclude_none=True),
        "file_present": asset_path(reg, a).exists(),
        "image_url": f"/api/studios/assets/{a.id}/image",
    }


def studios_view(reg: StudioRegistry | None = None) -> dict:
    reg = reg or load_registry()
    checks = validate_all(reg)
    out = []
    for lang in ai_config.channels:
        cp = channel_profile(lang)
        prof = reg.profile_for(lang)
        out.append({
            **cp,
            "profile": prof.model_dump(mode="json") if prof else None,
            "assets": [asset_view(a, reg) for a in reg.assets_of(lang)],
            "validation": checks.get(lang),
            "upscale": ({k: background_upscale(reg.asset(v.asset_id), v)
                         for k, v in prof.presets.items() if reg.asset(v.asset_id)}
                        if prof else {}),
        })
    return {"channels": out, "presets": list(PRESETS),
            "background_modes": list(BACKGROUND_MODES)}


def thumbnail(asset_id: str, reg: StudioRegistry | None = None) -> Path:
    """A cached JPEG preview (data/studio_thumbs, keyed by the image hash)."""
    from PIL import Image

    reg = reg or load_registry()
    a = reg.asset(asset_id)
    if a is None:
        raise StudioError(f"unknown studio asset {asset_id}")
    src = asset_path(reg, a)
    if not src.exists():
        raise StudioError(f"{asset_id}: image file missing")
    d = Path(ai_config.studio.thumbs_dir)
    d = d if d.is_absolute() else REPO / d
    d.mkdir(parents=True, exist_ok=True)
    tag = (a.sha256 or hashlib.sha256(src.read_bytes()).hexdigest())[:16]
    out = d / f"{a.id}_{tag}_{ai_config.studio.thumb_width}.jpg"
    if not out.exists():
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((ai_config.studio.thumb_width, ai_config.studio.thumb_width))
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=82)
            os.replace(tmp, out)
    return out


def update_profile(language: str, primary_background: str | None = None,
                   presets: dict[str, str] | None = None, confirm: bool | None = None,
                   reviewer: str | None = None) -> dict:
    """Change the primary background, which asset a preset uses, or confirm
    the review. Refuses anything that would make the channel invalid."""
    reg = load_registry()
    prof = reg.profile_for(language)
    if prof is None:
        raise StudioError(f"no studio profile for {language}")
    if primary_background is not None:
        a = reg.asset(primary_background)
        if a is None or a.language != language:
            raise StudioError(f"{primary_background} is not a {language} studio asset")
        prof.primary_background = primary_background
    for name, aid in (presets or {}).items():
        if name not in PRESETS:
            raise StudioError(f"unknown preset {name}")
        a = reg.asset(aid)
        if a is None or a.language != language:
            raise StudioError(f"{aid} is not a {language} studio asset")
        cur = prof.presets.get(name)
        if cur is None:
            raise StudioError(f"{name} has no framing yet — define it in the registry")
        if cur.asset_id != aid:
            # another image: keep the host framing, drop a crop made for the old one
            prof.presets[name] = cur.model_copy(update={"asset_id": aid, "crop": None})
    if confirm is not None:
        prof.review = {**prof.review, "confirmed": bool(confirm),
                       **({"confirmed_by": reviewer} if reviewer else {})}
    reg.profiles[language] = prof
    check = validate_language(language, reg)
    if not check["ok"]:
        raise StudioError("; ".join(check["errors"]))
    save_registry(reg)
    return check

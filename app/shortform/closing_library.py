"""The host's fixed closing of a short: the outro sentence and the identity line, spoken by
Fereidoun in ONE clip. Variants (one per approved look: outfit and position) are rendered once,
stored in the shared asset store and picked at random for later shorts.

Settings (stored through AppSetting, effective at once):
  use_stored  ON: pick a stored variant; OFF: render a fresh clip for this video.
  create_new  ON: below ``closing_library.target_variants`` a new variant is rendered and stored
              while a video is made; OFF: no new closing clip is ever rendered.
With use_stored ON, create_new OFF and nothing stored for the language, the video stops with a
visible status (``ClosingUnavailable``): it is never built without its closing.

Selection: among the language's variants that still match the current sentences and voice,
never the one used last (nor the ``recent_history`` before it while others remain), and among
the rest one of the least recently used half, at random."""

from __future__ import annotations

import json
import math
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AppSetting, ClosingClip, HostLook, HostOutfit, SharedReelAsset
from app.shortform.asset_store import ROOT, SharedAssetStore, asset_key
from app.utils import utc_now

KIND_SHORTS = "shorts"
KIND_LONG = "long_intro_identity"
STORE_TYPE = "closing_clip"
SETTINGS_KEY = "closing_library:settings"


class ClosingUnavailable(RuntimeError):
    """The video cannot get its closing clip under the current settings; the text is the status
    shown to the owner."""


@dataclass(frozen=True)
class LookSpec:
    """An approved avatar look a closing can be rendered with: the look's outfit and position."""

    look_id: int | None
    heygen_look_id: str
    outfit_label: str
    position: str


@dataclass(frozen=True)
class CostEstimate:
    characters: int
    voice_eur: float
    seconds: float
    avatar_eur: float

    @property
    def total_eur(self) -> float:
        return round(self.voice_eur + self.avatar_eur, 4)


@dataclass(frozen=True)
class RenderedClip:
    duration_seconds: float
    cost_eur: float


@dataclass(frozen=True)
class ClosingChoice:
    path: Path
    clip: ClosingClip | None       # None: a fresh clip made for this video only
    rendered: bool


class ClosingRenderer(Protocol):
    def estimate(self, language: str, sentences: list[str]) -> CostEstimate: ...

    async def render(self, db: Session, language: str, sentences: list[str], look: LookSpec,
                     dest: Path, case_id: int) -> RenderedClip: ...


# -- what is spoken ---------------------------------------------------------------------------


def closing_sentences(language: str, kind: str) -> list[str]:
    cfg = ai_config.shorts_host_led
    identity = cfg.identity_line[language]
    if kind == KIND_LONG:
        return [identity]
    cta = cfg.outro_sentence[language]
    return [cta, identity] if cfg.closing_order == "cta_then_identity" else [identity, cta]


def missing_setup(language: str) -> list[str]:
    """What a language still lacks before a closing clip can be made for it: its voice and the two spoken sentences."""
    cfg = ai_config.shorts_host_led
    return [name for name, ok in (("voice", language in ai_config.voice.languages),
                                  ("identity line", language in cfg.identity_line),
                                  ("outro sentence", language in cfg.outro_sentence)) if not ok]


def _voice_id(language: str) -> str:
    return str(ai_config.voice.languages[language].voice_id)


def _parameters(kind: str, sentences: list[str], look: LookSpec, voice_id: str) -> dict:
    return {"kind": kind, "sentences": sentences, "outfit": look.outfit_label,
            "position": look.position, "voice_id": voice_id, "heygen_look_id": look.heygen_look_id}


def clip_key(language: str, kind: str, sentences: list[str], look: LookSpec, voice_id: str) -> str:
    return asset_key(STORE_TYPE, language, _parameters(kind, sentences, look, voice_id),
                     ai_config.shorts_host_led.template_version)


def _spec_of(clip: ClosingClip) -> LookSpec:
    return LookSpec(look_id=clip.look_id, heygen_look_id=clip.heygen_look_id,
                    outfit_label=clip.outfit_label, position=clip.position)


def current_clips(db: Session, language: str, kind: str) -> list[ClosingClip]:
    """The stored variants whose key still equals the key of today's sentences and voice (an
    older sentence or voice leaves its clips stored but never selected)."""
    sentences, voice = closing_sentences(language, kind), _voice_id(language)
    return [c for c in db.query(ClosingClip).filter_by(language=language, kind=kind)
            .order_by(ClosingClip.id).all()
            if c.asset_key == clip_key(language, kind, sentences, _spec_of(c), voice)]


# -- settings ---------------------------------------------------------------------------------


class ClosingSettings:
    """The two owner switches; the config holds the defaults, the stored value wins."""

    def __init__(self, db: Session):
        self.db = db

    def _stored(self) -> dict:
        row = self.db.get(AppSetting, SETTINGS_KEY)
        return json.loads(row.value_json) if row is not None else {}

    @property
    def use_stored(self) -> bool:
        return bool(self._stored().get(
            "use_stored", ai_config.shorts_host_led.closing_library.default_use_stored))

    @property
    def create_new(self) -> bool:
        return bool(self._stored().get(
            "create_new", ai_config.shorts_host_led.closing_library.default_create_new))

    def as_dict(self) -> dict:
        return {"use_stored": self.use_stored, "create_new": self.create_new}

    def update(self, *, use_stored: bool | None = None, create_new: bool | None = None) -> dict:
        values = self.as_dict()
        if use_stored is not None:
            values["use_stored"] = use_stored
        if create_new is not None:
            values["create_new"] = create_new
        row = self.db.get(AppSetting, SETTINGS_KEY)
        if row is None:
            row = AppSetting(key=SETTINGS_KEY)
            self.db.add(row)
        row.value_json, row.updated_at = json.dumps(values), utc_now()
        self.db.commit()
        return values


def approved_looks(db: Session, language: str) -> list[LookSpec]:
    """The looks the owner approved and that are uploaded to HeyGen, in this language: the only
    ones a closing may be rendered with (the app never approves a look itself)."""
    rows = (db.query(HostLook, HostOutfit)
            .join(HostOutfit, HostLook.host_outfit_id == HostOutfit.id)
            .filter(HostLook.language == language, HostLook.status == "approved",
                    HostLook.heygen_look_id.isnot(None), HostOutfit.status != "retired")
            .order_by(HostLook.id).all())
    return [LookSpec(look_id=look.id, heygen_look_id=look.heygen_look_id or "",
                     outfit_label=outfit.name, position=f"{look.stance} {look.camera} {look.framing}")
            for look, outfit in rows]


# -- the library ------------------------------------------------------------------------------


def _store(db: Session) -> SharedAssetStore:
    return SharedAssetStore(db, root=ROOT)


async def create_variant(db: Session, language: str, kind: str, look: LookSpec,
                         renderer: ClosingRenderer, *, case_id: int) -> ClosingClip:
    """Render one variant and store it; a variant that is already stored is returned as it is."""
    sentences, voice = closing_sentences(language, kind), _voice_id(language)
    key = clip_key(language, kind, sentences, look, voice)
    existing = db.query(ClosingClip).filter_by(asset_key=key).one_or_none()
    if existing is not None:
        return existing
    tmp = ROOT / "data" / "shared_assets" / STORE_TYPE / f".{key[:24]}.tmp.mp4"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    rendered = await renderer.render(db, language, sentences, look, tmp, case_id)
    path, _ = _store(db).get_or_create(
        STORE_TYPE, language, _parameters(kind, sentences, look, voice),
        ai_config.shorts_host_led.template_version, lambda dest: _move(tmp, dest))
    clip = ClosingClip(
        asset_key=key, language=language, kind=kind, look_id=look.look_id,
        heygen_look_id=look.heygen_look_id, outfit_label=look.outfit_label, position=look.position,
        voice_id=voice, sentences_json=json.dumps(sentences, ensure_ascii=False),
        duration_seconds=rendered.duration_seconds, cost_eur=rendered.cost_eur)
    db.add(clip)
    # making a variant is not using it: the ledger counts only the videos that carry it
    row = db.query(SharedReelAsset).filter_by(asset_key=key).one()
    row.use_count, row.last_used_at = 0, None
    db.commit()
    db.refresh(clip)
    assert path.exists()
    return clip


def _move(source: Path, dest: Path) -> None:
    shutil.move(str(source), str(dest))


def clip_path(db: Session, clip: ClosingClip) -> Path:
    row = db.query(SharedReelAsset).filter_by(asset_key=clip.asset_key).one()
    assert row.file_path is not None
    return ROOT / row.file_path


def record_use(db: Session, clip: ClosingClip) -> None:
    _store(db).record_use(clip.asset_key)


def select_clip(db: Session, language: str, kind: str, *,
                rng: random.Random | None = None) -> ClosingClip | None:
    clips = current_clips(db, language, kind)
    if not clips:
        return None
    cfg = ai_config.shorts_host_led.closing_library
    rng = rng or (random.Random(cfg.seed) if cfg.seed is not None else random.Random())
    ledger = {r.asset_key: r for r in db.query(SharedReelAsset).filter(
        SharedReelAsset.asset_key.in_([c.asset_key for c in clips])).all()}
    last_used = {c.asset_key: ledger[c.asset_key].last_used_at for c in clips}
    used = sorted((c for c in clips if last_used[c.asset_key] is not None),
                  key=lambda c: last_used[c.asset_key] or utc_now(), reverse=True)
    pool = clips
    for window in range(min(cfg.recent_history, len(used)), 0, -1):
        recent = {c.asset_key for c in used[:window]}
        if len(recent) < len(clips):
            pool = [c for c in clips if c.asset_key not in recent]
            break
    pool = sorted(pool, key=lambda c: (ledger[c.asset_key].last_used_at is not None,
                                       ledger[c.asset_key].last_used_at or utc_now(), c.id))
    return rng.choice(pool[:max(1, math.ceil(len(pool) / 2))])


def missing_looks(db: Session, language: str, kind: str, looks: list[LookSpec]) -> list[LookSpec]:
    """Approved looks to render next, up to the target: looks of outfits and positions the
    library has the fewest of come first."""
    target = ai_config.shorts_host_led.closing_library.target_variants
    have = current_clips(db, language, kind)
    keys = {c.heygen_look_id for c in have}
    outfits = [c.outfit_label for c in have]
    positions = [c.position for c in have]
    wanted: list[LookSpec] = []
    left = [look for look in looks if look.heygen_look_id not in keys]
    while left and len(have) + len(wanted) < target:
        best = min(left, key=lambda look: (outfits.count(look.outfit_label),
                                           positions.count(look.position)))
        wanted.append(best)
        left.remove(best)
        outfits.append(best.outfit_label)
        positions.append(best.position)
    return wanted


async def resolve_closing(db: Session, language: str, kind: str, *, case_id: int,
                          renderer: ClosingRenderer, looks: list[LookSpec],
                          rng: random.Random | None = None,
                          fresh_dir: Path | None = None) -> ClosingChoice:
    """The closing clip of one video under the two settings (see the module text)."""
    settings = ClosingSettings(db)
    if not settings.use_stored:
        if not settings.create_new:
            raise ClosingUnavailable(
                f"Rendering a fresh closing clip for {language} is off: creation is off; enable "
                "'Create new closing clips' or 'Use stored closing clips'")
        if not looks:
            raise ClosingUnavailable(f"No approved look for {language}; approve more looks")
        look = rng.choice(looks) if rng else random.Random().choice(looks)
        dest = (fresh_dir or ROOT / "data" / "shared_assets" / "fresh") / f"closing_{language}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        await renderer.render(db, language, closing_sentences(language, kind), look, dest, case_id)
        return ClosingChoice(path=dest, clip=None, rendered=True)
    rendered = False
    if settings.create_new:
        wanted = missing_looks(db, language, kind, looks)
        if wanted:
            await create_variant(db, language, kind, wanted[0], renderer, case_id=case_id)
            rendered = True
    clip = select_clip(db, language, kind, rng=rng)
    if clip is None:
        raise ClosingUnavailable(
            f"No stored closing clip for {language}; enable creation or approve more looks")
    record_use(db, clip)
    return ClosingChoice(path=clip_path(db, clip), clip=clip, rendered=rendered)


def library_table(db: Session, languages: list[str], kind: str = KIND_SHORTS) -> dict[str, dict]:
    """Per language: the target, the number of current variants and each variant's outfit,
    position, seconds, cost and use."""
    target = ai_config.shorts_host_led.closing_library.target_variants
    table: dict[str, dict] = {}
    for language in languages:
        clips = current_clips(db, language, kind)
        ledger = {r.asset_key: r for r in db.query(SharedReelAsset).filter(
            SharedReelAsset.asset_key.in_([c.asset_key for c in clips])).all()}
        table[language] = {"target": target, "variants": len(clips), "clips": [
            {"id": c.id, "outfit": c.outfit_label, "position": c.position,
             "seconds": c.duration_seconds, "cost_eur": c.cost_eur,
             "times_used": ledger[c.asset_key].use_count,
             "last_used_at": ledger[c.asset_key].last_used_at,
             "created_at": c.created_at} for c in clips]}
    return table

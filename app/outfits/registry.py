"""Typed access to ``data/outfits/<language>/<outfit>/<language>_<outfit>_<pose>.png``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2] / "data" / "outfits"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_STANCE_PREFIX = {"sit": "SEATED", "stand": "STANDING"}


@dataclass(frozen=True)
class OutfitPose:
    role: str
    stance: str
    path: Path


@dataclass(frozen=True)
class Outfit:
    """One outfit of the host in one language; ``outfit_id`` is ``<language>/<outfit>``."""

    outfit_id: str
    language: str
    name: str
    label: str
    poses: dict[str, OutfitPose]

    def poses_for(self, stance: str) -> list[OutfitPose]:
        return [pose for pose in self.poses.values() if pose.stance == stance]

    def reference_pose(self, stance: str) -> OutfitPose:
        """A picture of the outfit that fits the stance, else any picture of it."""
        fitting = self.poses_for(stance)
        return (fitting or list(self.poses.values()))[0]


class OutfitRegistry:
    """Read and validate the outfit directory layout."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(_ROOT if root is None else root)

    @staticmethod
    def _label(name: str) -> str:
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)

    @staticmethod
    def _stance(pose: str) -> str:
        """Sit/stand from the pose name; anything new is UNKNOWN, not an error — the
        library classifier (app/outfits/library.py) reads it by keyword instead."""
        for prefix, stance in _STANCE_PREFIX.items():
            if pose.startswith(prefix):
                return stance
        return "UNKNOWN"

    def _read_outfit(self, language: str, directory: Path) -> Outfit:
        stem = f"{language}_{directory.name}_"
        poses: dict[str, OutfitPose] = {}
        for path in sorted(p for p in directory.iterdir() if p.is_file() and p.name != ".DS_Store"):
            if path.suffix.lower() != ".png" or not path.name.startswith(stem):
                log.warning("%s: unexpected outfit file %r — skipped",
                            directory, path.name)
                continue
            if path.read_bytes()[:8] != _PNG_MAGIC:
                log.warning("%s: invalid PNG file %r — skipped", directory, path.name)
                continue
            role = path.stem.removeprefix(stem)
            poses[role] = OutfitPose(role=role, stance=self._stance(role), path=path)
        if not poses:
            raise ValueError(f"{directory}: no pose pictures")
        return Outfit(outfit_id=f"{language}/{directory.name}", language=language,
                      name=directory.name, label=self._label(directory.name), poses=poses)

    def all(self, language: str | None = None) -> tuple[Outfit, ...]:
        if not self.root.is_dir():
            raise ValueError(f"outfit root does not exist: {self.root}")
        languages = sorted(p for p in self.root.iterdir() if p.is_dir()
                           and (language is None or p.name == language))
        outfits = []
        for lang in languages:
            for directory in sorted(p for p in lang.iterdir() if p.is_dir()):
                try:
                    outfits.append(self._read_outfit(lang.name, directory))
                except ValueError as exc:
                    # a broken outfit folder is reported and skipped — it must not
                    # take down every other outfit of the library
                    log.warning("outfit skipped: %s", exc)
        if not outfits:
            raise ValueError(f"no outfit directories found: {self.root}")
        return tuple(outfits)

    def get(self, outfit_id: str) -> Outfit:
        for outfit in self.all():
            if outfit.outfit_id == outfit_id:
                return outfit
        raise KeyError(outfit_id)

    def resolve(self, outfit_id: str) -> Outfit:
        return self.get(outfit_id)

    def variants(self, language: str) -> list[tuple[str, str]]:
        """Every (outfit_id, pose) of a language, ordered so that consecutive entries change
        the outfit first: round-robin over the outfits, one pose each per round."""
        queues = [[(o.outfit_id, pose) for pose in o.poses] for o in self.all(language)]
        ordered: list[tuple[str, str]] = []
        while any(queues):
            for queue in queues:
                if queue:
                    ordered.append(queue.pop(0))
        return ordered

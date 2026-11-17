"""Non-destructive Reel copies. A clip that fails Instagram's or Facebook's media rules gets a copy in the prepared folder; the original is never touched.

What a copy changes, and only that:
  * an edit list or a moov atom at the end of the file: the container is rewritten (video and audio are copied as they are). Without its edit list the audio keeps its encoder delay
    (1024 samples, 21.3 ms at 48 kHz, which the edit list used to skip): the sound then comes 21.3 ms later against the picture than in the original. That is far below the 125 ms
    by which audio may lag before it is noticed (ITU-R BT.1359, quoted from memory, not re-read here);
  * keyframes more than the platforms' limit apart: only the VIDEO is re-encoded (a remux cannot move keyframes), with a keyframe every `keyframe_seconds`, a closed GOP and a quality
    set so high that the copy has to stay within `min_psnr_db` of the original. The AUDIO is always copied bit for bit, and the copy is refused if its audio differs in a single byte.
Anything else that fails (length, codec, shape) cannot be repaired by a copy and is reported as it is.
Run: python -m app.meta_publishing.prepare [--force]"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.core.files import file_sha256
from app.core.media_tools import ffmpeg_bin
from app.meta_publishing.config import MetaPublishingConfig, meta_config
from app.meta_publishing.copies import prepared_path, sidecar_path
from app.meta_publishing.preflight import ClipReport, ReelsPreflight
from app.meta_publishing.probe import MediaFacts
from app.meta_publishing.validator import Verdict

_GOP = "keyframe interval (GOP)"
_CONTAINER = {"no edit list", "moov atom at the front"}
_COLOR_FLAGS = {"color_range": "-color_range", "color_space": "-colorspace", "color_transfer": "-color_trc", "color_primaries": "-color_primaries"}


class PrepareError(RuntimeError):
    pass


@dataclass
class Prepared:
    source: Path
    target: Path | None
    action: str                                  # none | remux | reencode | up_to_date
    reasons: list[str] = field(default_factory=list)
    audio_identical: bool | None = None
    psnr_db: float | None = None
    verdicts: dict[str, str] = field(default_factory=dict)


class ReelsPreparer:
    def __init__(self, config: MetaPublishingConfig | None = None, preflight: ReelsPreflight | None = None):
        self.config = config or meta_config()
        self.preflight = preflight or ReelsPreflight(self.config)

    # -- what is needed ---------------------------------------------------------------------------
    def needed(self, report: ClipReport) -> tuple[str, list[str]]:
        """`none`, `remux` or `reencode`, and the findings that call for it. Findings a copy cannot repair raise PrepareError."""
        if report.error:
            raise PrepareError(report.error)
        fixable_names = {_GOP, *_CONTAINER, "closed GOP"}
        names: dict[str, str] = {}
        for platform in report.platforms.values():
            for check in platform.checks:
                if check.verdict is Verdict.PASS:
                    continue
                if check.verdict is Verdict.FAIL and check.name not in fixable_names:
                    raise PrepareError(f"{check.name} ({check.actual}; needs {check.expected}) cannot be repaired by a copy")
                names[check.name] = check.verdict.value
        if _GOP in names:
            return "reencode", sorted(names)
        if names.keys() & _CONTAINER:
            return "remux", sorted(names.keys() & _CONTAINER)
        return "none", []

    def parameters(self) -> dict:
        return self.config.prepare.model_dump()

    # -- the command ---------------------------------------------------------------------------
    def command(self, facts: MediaFacts, source: Path, out: Path, action: str) -> list[str]:
        ffmpeg = ffmpeg_bin()
        if not ffmpeg:
            raise PrepareError("ffmpeg is not available")
        p = self.config.prepare
        command = [ffmpeg, "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0"]
        if action == "reencode":
            keyframes = round(p.keyframe_seconds * round(facts.fps or 30))
            # passthrough: the source is already constant-rate, so every frame keeps its time and none is repeated or dropped
            command += ["-c:v", "libx264", "-preset", p.video_preset, "-crf", str(p.video_crf), "-profile:v", p.video_profile, "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                        "-g", str(keyframes), "-keyint_min", str(keyframes), "-sc_threshold", "0", "-x264-params", "open-gop=0"]
            for key, flag in _COLOR_FLAGS.items():
                if facts.color.get(key):
                    command += [flag, facts.color[key]]
        else:
            command += ["-c:v", "copy"]
        return command + ["-c:a", "copy", "-movflags", "+faststart+negative_cts_offsets", "-use_editlist", "0", str(out)]

    # -- checks on the result ----------------------------------------------------------------------
    @staticmethod
    def audio_digest(path: Path) -> str:
        """The md5 of the file's AAC packets, exactly as stored: two files with the same digest have the same audio, bit for bit."""
        result = subprocess.run([ffmpeg_bin() or "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True)
        if result.returncode != 0 or "MD5=" not in result.stdout:
            raise PrepareError(f"the audio of {path.name} could not be read: {result.stderr[-200:]}")
        return result.stdout.strip()

    @staticmethod
    def psnr(original: Path, copy: Path) -> float:
        """Average PSNR of the copy's picture against the original's, frame for frame."""
        result = subprocess.run([ffmpeg_bin() or "ffmpeg", "-i", str(copy), "-i", str(original), "-lavfi", "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]psnr", "-f", "null", "-"], capture_output=True, text=True)
        found = re.search(r"PSNR .*?average:(inf|[\d.]+)", result.stderr)
        if result.returncode != 0 or not found:
            raise PrepareError(f"the picture of the copy could not be compared: {result.stderr[-200:]}")
        return float("inf") if found.group(1) == "inf" else float(found.group(1))

    # -- one clip --------------------------------------------------------------------------------
    def prepare(self, source: Path, *, force: bool = False) -> Prepared:
        target = prepared_path(source)
        if target.resolve() == source.resolve():
            raise PrepareError("the copy would replace the original")
        sha = file_sha256(source)
        sidecar = sidecar_path(target)
        if not force and target.is_file() and sidecar.is_file():
            kept = json.loads(sidecar.read_text(encoding="utf-8"))
            if kept.get("source_sha256") == sha and kept.get("parameters") == self.parameters():
                return Prepared(source, target, "up_to_date", kept.get("reasons", []), True, kept.get("psnr_db"), kept.get("verdicts", {}))
        report = self.preflight.check_file(source.stem, source)
        action, reasons = self.needed(report)
        if action == "none":
            return Prepared(source, None, "none", [], None, None, {n: p.verdict.value for n, p in report.platforms.items()})
        assert report.facts is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.stem + ".part.mp4")
        part.unlink(missing_ok=True)
        try:
            result = subprocess.run(self.command(report.facts, source, part, action), capture_output=True, text=True, timeout=self.config.prepare.timeout_s)
            if result.returncode != 0:
                raise PrepareError(f"ffmpeg could not make the copy: {result.stderr[-300:]}")
            same_audio = self.audio_digest(part) == self.audio_digest(source)
            if not same_audio:
                raise PrepareError("the copy's audio differs from the original's: refused")
            quality = self.psnr(source, part) if action == "reencode" else float("inf")
            if quality < self.config.prepare.min_psnr_db:
                raise PrepareError(f"the copy's picture is too far from the original ({quality:.1f} dB, needs {self.config.prepare.min_psnr_db:g})")
            checked = self.preflight.check_file(source.stem, part)
            if checked.error or any(p.verdict is Verdict.FAIL for p in checked.platforms.values()):
                failing = checked.error or "; ".join(f"{n}: {', '.join(c.name for c in p.by(Verdict.FAIL))}" for n, p in checked.platforms.items() if p.by(Verdict.FAIL))
                raise PrepareError(f"the copy still fails the media rules: {failing}")
            os.replace(part, target)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        verdicts = {n: p.verdict.value for n, p in checked.platforms.items()}
        sidecar.write_text(json.dumps({"source_sha256": sha, "action": action, "reasons": reasons, "parameters": self.parameters(), "audio_md5": self.audio_digest(target),
                                       "psnr_db": None if quality == float("inf") else round(quality, 2), "verdicts": verdicts}, indent=2, sort_keys=True), encoding="utf-8")
        return Prepared(source, target, action, reasons, True, None if quality == float("inf") else round(quality, 2), verdicts)


def main(argv: list[str]) -> int:
    from app.db.base import SessionLocal
    from app.documentary import storage
    from app.shortform import clips as C

    preparer = ReelsPreparer()
    settings = preparer.config.preflight
    done = 0
    with SessionLocal() as db:
        for row in C.clips_of(db, settings.case_id, settings.language):
            video = (json.loads(row.media_json or "{}").get("video") or {}).get("path")
            source = storage.resolve(video) if video else None
            if source is None or not source.is_file():
                print(f"{row.clip_key}: no video file", file=sys.stderr)
                continue
            try:
                result = preparer.prepare(source, force="--force" in argv)
            except PrepareError as exc:
                print(f"{row.clip_key}: NOT PREPARED: {exc}", file=sys.stderr)
                continue
            done += 1
            print(f"{row.clip_key}: {result.action}" + (f" ({', '.join(result.reasons)})" if result.reasons else "") + (f" -> {result.target}" if result.target else "")
                  + f" | audio identical: {result.audio_identical} | PSNR: {result.psnr_db} dB | {result.verdicts}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

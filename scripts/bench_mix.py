"""Local benchmark of the music mix (the app's own ffmpeg mix, loudness normalisation and mp3 encode) on synthetic audio: how long one narration takes and how it scales when
several languages are mixed at the same time, with the memory used. No provider is called and nothing in the project is written (a temporary folder is used).

    PYTHONPATH=. .venv/bin/python scripts/bench_mix.py [minutes=30] [parallel=1,3,9]
"""

from __future__ import annotations

import resource
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary import audio as A
from app.documentary.music import _ffmpeg_mix


def tone(path: Path, seconds: float, frequency: int) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=44100:duration={seconds}", "-ac", "2", str(path)], check=True)


def one(workdir: Path, index: int, narration: Path, music: Path, seconds: float) -> float:
    started = time.time()
    placements = [{"path": music, "loop": True, "duration": 60.0, "fade_in": 2.0, "fade_out": 3.0, "start": start, "role": "bed", "level_db": -22.0}
                  for start in range(0, int(seconds) - 60, 240)]
    mixed, final = workdir / f"mix_{index}.wav", workdir / f"final_{index}.wav"
    _ffmpeg_mix(narration, placements, mixed, 44100)
    loud = ai_config.loudness
    A.normalize_loudness(mixed, final, loud.narration_target_lufs, loud.true_peak_db, loud.lra, 44100, 2)
    A.encode_mp3(final, workdir / f"final_{index}.mp3")
    return time.time() - started


def main(minutes: float, parallel: list[int]) -> None:
    seconds = minutes * 60
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        narration, music = work / "narration.wav", work / "music.wav"
        tone(narration, seconds, 220)
        tone(music, 60, 440)
        for n in parallel:
            started = time.time()
            with ThreadPoolExecutor(n) as pool:
                times = list(pool.map(lambda i: one(work, i, narration, music, seconds), range(n)))
            wall = time.time() - started
            print(f"parallel={n}: slowest mix {max(times):.1f}s for {minutes:.0f} min of narration, wall {wall:.1f}s, throughput {seconds * n / wall:.0f} audio-s per s")
        children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576
        print(f"largest ffmpeg process: {children:.0f} MB")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 30.0, [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "1,3,9").split(",")])

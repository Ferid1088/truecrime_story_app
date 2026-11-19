"""Local benchmark of the listening check's speech-to-text model (faster-whisper on the CPU, as configured): how many audio seconds per second it transcribes when several
transcriptions run in threads of one process, for a given number of model workers, and whether the text is the same as a single run's. macOS `say` makes the speech; no
provider is called and nothing in the project is written.

    PYTHONPATH=. .venv/bin/python scripts/bench_asr.py [model=small] [threads:workers ...]     (default: 1:1 4:1 4:4)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from faster_whisper import WhisperModel

from app.core.ai_config import ai_config
from app.documentary.audio import pcm16k_float32

TEXT = ("At midnight the detective waited twenty one seconds in the hallway, and then he knocked on the door of the old farmhouse. Nobody answered, so he walked around the house "
        "and looked through the kitchen window. The table was set for two, and the lamp was still burning. ") * 6


def main(model: str, plans: list[tuple[int, int]]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        spoken = Path(tmp) / "speech.aiff"
        subprocess.run(["say", "-v", "Samantha", "-o", str(spoken), TEXT], check=True)
        wav = Path(tmp) / "speech.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(spoken), "-ar", "16000", "-ac", "1", str(wav)], check=True)
        audio = pcm16k_float32(wav)
        seconds = len(audio) / 16000
        for threads, workers in plans:
            engine = WhisperModel(model, device="cpu", compute_type=ai_config.asr_check.compute_type, num_workers=workers)
            heard: dict[int, str] = {}

            def one(i: int, engine=engine, heard=heard) -> None:
                segments, _ = engine.transcribe(audio, language="en", word_timestamps=True, vad_filter=False, initial_prompt=None, condition_on_previous_text=False)
                heard[i] = " ".join(s.text.strip() for s in segments)

            one(-1)
            started = time.time()
            pool = [threading.Thread(target=one, args=(i,)) for i in range(threads)]
            [t.start() for t in pool]
            [t.join() for t in pool]
            wall = time.time() - started
            print(f"{model} threads={threads} workers={workers}: {seconds * threads / wall:.1f} audio-s per s, same text as a single run: {all(heard[i] == heard[-1] for i in range(threads))}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "small", [tuple(int(x) for x in p.split(":")) for p in (sys.argv[2:] or ["1:1", "4:1", "4:4"])])

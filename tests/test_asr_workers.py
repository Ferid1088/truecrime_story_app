"""The listening check's speech-to-text model: it runs as many transcriptions side by side as the config says (and the `asr` limit allows), it is loaded only once when several
threads ask for it together, and the limit and the workers cannot drift apart."""

from __future__ import annotations

import threading
import time

import pytest

from app.core import concurrency
from app.core.ai_config import ai_config
from app.documentary.asr import FasterWhisperASR


@pytest.fixture
def asr(monkeypatch):
    monkeypatch.delenv("TRUECRIME_TEST_NO_REAL_PROVIDERS", raising=False)           # only to construct the object: the model itself is replaced
    loads = []

    class Model:
        def __init__(self, name, device, compute_type, num_workers):
            time.sleep(0.05)
            loads.append((name, device, compute_type, num_workers))

    monkeypatch.setattr("faster_whisper.WhisperModel", Model)
    monkeypatch.setattr(FasterWhisperASR, "_models", {})
    return FasterWhisperASR(), loads


def test_the_model_is_loaded_once_when_threads_ask_together_and_gets_the_configured_workers(asr):
    checker, loads = asr
    models = []

    def ask():
        models.append(checker._model("en"))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert loads == [("small", "cpu", "int8", ai_config.asr_check.workers)], "one load, with the configured number of workers"
    assert len({id(m) for m in models}) == 1


def test_each_language_model_is_its_own_load(asr):
    checker, loads = asr
    checker._model("en")
    checker._model("fa")
    checker._model("de")
    assert sorted(name for name, *_ in loads) == ["large-v3-turbo", "small"], "German shares the English model, Persian has the large one"


def test_the_asr_limit_and_the_model_workers_agree():
    assert concurrency.limit_size("asr") == ai_config.asr_check.workers == 4, "a limit above the workers would only queue inside the model; below it would leave workers idle"

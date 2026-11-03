"""The phoneme score and the word choice of the reading loop, with a fake recogniser and a fake model: which words are looked at, which are findings
and which are only weak flags. The words are invented."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess

import pytest

from app.core.ai_config import ai_config
from app.documentary.reading import ask as ASK
from app.documentary.reading import chooser as CH
from app.documentary.reading.chooser import WordChooser, variants
from app.documentary.reading.inspector import Inspector, best_form, windows
from app.documentary.reading.words import tokenize


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


class Script:
    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def __call__(self, gen, body):
        self.requests.append(json.loads(body))
        return (self.answer(self.requests[-1]) if callable(self.answer) else self.answer), object()


class Recogniser:
    """Fits a form by a table of losses by its IPA (a form not in the table fits badly)."""

    def __init__(self, losses):
        self.losses = losses

    def log_probs(self, wav):
        return None

    def fit(self, log_probs, start, end, ipa):
        return self.losses.get(ipa, 50.0)


def test_the_wrong_forms_of_a_reading_change_one_vowel_and_keep_the_ezafe():
    assert "'ohdhā" in variants("'ahdhā") and "beham" in variants("baham") and "gurostun" in variants("gurestun")
    assert all(f.endswith("-ye") for f in variants("khāne-ye")) and "khāne-ye" not in variants("khāne-ye")


def test_the_best_form_is_the_one_that_fits_the_audio_best_and_the_margin_is_how_much_better_it_fits_than_the_reading():
    recogniser = Recogniser({"bæhɑːm": 22.0, "behɑːm": 17.0})
    assert best_form(recogniser.fit, None, 1.0, 1.5, "bahām") == ("behām", 5.0)
    assert best_form(Recogniser({"bæhɑːm": 10.0}).fit, None, 1.0, 1.5, "bahām") == ("bahām", 0.0)


def test_a_word_is_looked_at_only_when_a_wrong_form_fits_clearly_better_and_it_is_a_finding_when_the_model_chooses_the_wrong_form(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.reading, "flag_margin", 4.0)
    wav = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(wav)], check=True)
    # word 1 ("عهد") has a wrong form fitting 9 better, word 2 ("حرف") only 1 better, word 0 fits as written
    recogniser = Recogniser({"ʔæhd": 30.0, "ʔohd": 21.0, "hæɾf": 10.0, "hoɾf": 9.0})
    spoken = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.5} for i, w in enumerate(["از", "عهد", "حرف"])]
    runner = Script(lambda req: {"choice": next(k for k, v in req["options"].items() if v == "'ohd")})
    inspector = Inspector(WordChooser(None, 1, runner), recogniser)
    found, weak = asyncio.run(inspector.inspect("a", "از عهد حرف", ["az", "'ahd", "harf"], wav, spoken))
    assert [(f.surface, f.expected, f.said, f.best, f.margin) for f in found] == [("عهد", "'ahd", "'ohd", "'ohd", 9.0)] and weak == []
    assert len(runner.requests) == 1 and runner.requests[0]["word"] == "عهد" and "'ahd" in runner.requests[0]["options"].values()


def test_a_word_the_model_hears_as_the_reading_is_only_a_weak_flag_and_the_same_clip_is_never_asked_twice(tmp_path):
    wav = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(wav)], check=True)
    recogniser = Recogniser({"hæɾf": 30.0, "hoɾf": 21.0})
    spoken = [{"word": "حرف", "start": 0.2, "end": 0.9}]
    runner = Script(lambda req: {"choice": next(k for k, v in req["options"].items() if v == "harf")})
    inspector = Inspector(WordChooser(None, 1, runner), recogniser)
    for _ in range(2):
        found, weak = asyncio.run(inspector.inspect("a", "حرف", ["harf"], wav, spoken))
        assert found == [] and [w.surface for w in weak] == ["حرف"]
    assert len(runner.requests) == 1


def test_a_word_whose_clip_has_no_sound_stays_unchecked(tmp_path):
    wav = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", str(wav)], check=True)
    recogniser = Recogniser({"hæɾf": 30.0, "hoɾf": 21.0})
    runner = Script({"choice": "A"})
    found, weak = asyncio.run(Inspector(WordChooser(None, 1, runner), recogniser).inspect("a", "حرف", ["harf"], wav, [{"word": "حرف", "start": 9.0, "end": 9.5}]))
    assert found == [] and weak == [] and runner.requests == []


def test_the_windows_follow_the_words_by_place_or_by_the_words_that_match():
    words = tokenize("از عهد حرف")
    spoken = [{"word": w, "start": i, "end": i + 1} for i, w in enumerate(["از", "عهد", "حرف"])]
    assert windows(spoken, words) == {0: (0, 1), 1: (1, 2), 2: (2, 3)}
    assert windows(spoken[:2] + [{"word": "پس", "start": 2, "end": 3}, spoken[2]], words) == {0: (0, 1), 1: (1, 2), 2: (2, 3)}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_the_options_of_the_choice_are_the_reading_and_its_wrong_forms_and_the_right_one_is_not_always_first(tmp_path, monkeypatch):
    wav = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(wav)], check=True)
    runner = Script({"choice": "Z"})
    chooser = WordChooser(None, 1, runner)
    answer = asyncio.run(chooser.choose(wav, 0.5, 1.0, "بهم", "baham", "x"))
    assert answer["said"] == "none"
    options = runner.requests[0]["options"]
    assert sorted(options.values()) == sorted(["baham", *variants("baham")])
    assert CH.LETTERS.startswith("".join(options))

"""At most two languages write their spoken text at the same time, English and Persian first, German third."""

import asyncio

from app.core.ai_config import ai_config
from app.documentary import spoken_turns as ST


def test_the_order_is_english_persian_german_then_the_other_languages_as_listed():
    order = sorted(ai_config.documentary.languages, key=ST.rank)
    assert order[:3] == ["en", "fa", "de"]
    assert order[3:] == [lang for lang in ai_config.documentary.languages if lang not in ("en", "fa", "de")]
    assert ai_config.concurrency.spoken_text == 2


def test_languages_that_are_ready_in_the_wrong_order_still_write_in_the_configured_order_and_never_more_than_two_at_once(monkeypatch):
    monkeypatch.setattr(ST, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ST, "_started", set())
    everyone = {"tr", "pt", "ar", "de", "fa", "en"}
    entered, running_now, peak = [], set(), [0]

    async def write(language):
        async with ST.spoken_turn(1, language, running=lambda case: everyone):
            entered.append(language)
            running_now.add(language)
            peak[0] = max(peak[0], len(running_now))
            await asyncio.sleep(0.05)
            running_now.discard(language)

    async def main():
        await asyncio.gather(*[write(lang) for lang in ("tr", "pt", "ar", "de", "fa", "en")])      # the slowest languages are ready first

    asyncio.run(main())
    assert entered[:3] == ["en", "fa", "de"]
    assert entered[3:] == ["ar", "pt", "tr"]
    assert peak[0] == 2


def test_a_language_before_it_in_the_order_that_runs_no_film_does_not_hold_it_back(monkeypatch):
    monkeypatch.setattr(ST, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ST, "_started", set())

    async def main():
        async with ST.spoken_turn(2, "de", running=lambda case: {"de"}):       # English and Persian have no film: German does not wait for them
            return "wrote"

    assert asyncio.run(main()) == "wrote"

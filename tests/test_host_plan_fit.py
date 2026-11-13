"""A host plan is made for the film's story in its few large acts; the spoken version that is voiced is told in dozens of beats. The appearances keep their place
in the story: the middle ones go to the beat nearest their share of the story, the outro stays after the last beat, so no appearance is cut off after the first
beats and an outro knows how the story ended."""

from __future__ import annotations

import pytest

from app.documentary.host_planning import fit_plan
from app.documentary.host_story import HostAct, HostStory


pytestmark = pytest.mark.usefixtures("four_host_appearances")


def story(count: int, words: int, prefix: str, hook_first: bool = True, payoffs: tuple[int, ...] = ()) -> HostStory:
    acts = tuple(HostAct(f"{prefix}{i}", i, f"title {i}", "", f"text {i}", (), (), (), words, hook=hook_first and i == 1, payoff=i in payoffs)
                 for i in range(1, count + 1))
    return HostStory("Case", "Who did it?", "fa", acts)


PLAN = [{"id": "S1", "position": "opening", "role": "opening", "cutoff": 0, "reason": "a"},
        {"id": "S2", "position": "mid", "role": "reflection", "cutoff": 2, "reason": "b"},
        {"id": "S3", "position": "mid", "role": "transition", "cutoff": 4, "reason": "c"},
        {"id": "S4", "position": "final", "role": "outro", "cutoff": 7, "reason": "d"}]


def test_a_plan_made_for_seven_acts_is_placed_by_its_share_of_the_story_in_the_beats():
    placed = {s["id"]: s for s in fit_plan(PLAN, story(7, 1200, "act"), story(56, 150, "B"))}
    assert placed["S1"]["cutoff"] == 0 and placed["S4"]["cutoff"] == 56 and placed["S4"]["beat_id"] == "B56", "the outro knows the whole story"
    assert placed["S2"]["cutoff"] == 16 and placed["S3"]["cutoff"] == 32, "after 2 of 7 acts and after 4 of 7: the same share of the beats"
    assert [placed[i]["beat_id"] for i in ("S2", "S3")] == ["B16", "B32"]


def test_a_plan_of_the_same_acts_is_not_touched():
    acts = story(7, 1200, "act")
    assert fit_plan(PLAN, acts, acts) == PLAN


def test_an_appearance_does_not_stand_right_before_a_payoff_or_on_the_hook(monkeypatch):
    beats = story(56, 150, "B", payoffs=(17,))
    placed = {s["id"]: s for s in fit_plan(PLAN, story(7, 1200, "act"), beats)}
    assert placed["S2"]["cutoff"] == 15, "B17 is a payoff: the appearance moves one beat earlier"


def test_a_plan_that_cannot_be_placed_is_refused():
    with pytest.raises(RuntimeError, match="does not fit"):
        fit_plan(PLAN, story(7, 1200, "act"), story(3, 150, "B"))

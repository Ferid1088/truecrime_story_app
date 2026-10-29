"""A model's almost-JSON answer is repaired locally and for free; one that would need guessing is not."""
from __future__ import annotations

import pytest

from app.providers.generation import json_repair
from app.providers.generation.openai_compat import OpenAICompatibleGenerationProvider

GOOD = {"a": [1, 2, {"b": "x, y]"}], "c": True}


@pytest.mark.parametrize("text", [
    '{"a": [1, 2, {"b": "x, y]"},], "c": true,}',                    # trailing commas
    '{"a": [1, 2, {"b": "x, y]"}], "c": True}',                      # a Python literal
    'Here is the plan:\n```json\n{"a": [1, 2, {"b": "x, y]"}], "c": true}\n```\nDone.',   # prose and fences
    '{"a": [1, 2, {"b": "x, y]"}], "c": true}} trailing',            # extra closer and text
])
def test_an_almost_json_answer_is_repaired(text):
    assert json_repair.repair(text) == GOOD
    assert OpenAICompatibleGenerationProvider._extract_json(text) == GOOD


@pytest.mark.parametrize("text", ['{"a": "cut off in a stri', '{"a": [1, 2, ', '{"a": [1, 2, {"b": "x"}], "c": true',
                                  "no object at all", '{"a" 1}'])
def test_an_answer_that_would_need_guessing_is_not_repaired(text):
    assert json_repair.repair(text) is None


def test_a_string_that_looks_like_a_literal_or_a_comma_is_left_alone():
    text = '{"say": "True, then None,]", "n": [1,]}'
    assert json_repair.repair(text) == {"say": "True, then None,]", "n": [1]}

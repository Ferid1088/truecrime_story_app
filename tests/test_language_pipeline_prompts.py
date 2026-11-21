import re

import pytest

from app.core.prompts import prompt

GUIDE_LANGUAGES = ("fa", "en", "de", "ar")

FORMAT_ARGS = {
    "language_pipeline/draft_writer": dict(
        marker_instruction="M", tone="calm", target_words=9000),
}


@pytest.mark.parametrize("name,args", FORMAT_ARGS.items())
def test_pipeline_prompt_formats_without_leftover_placeholders(name, args):
    text = prompt(name).format(**args)
    assert not re.search(r"\{[a-z_]+\}", text)
    for value in args.values():
        assert str(value) in text


@pytest.mark.parametrize("language", GUIDE_LANGUAGES)
def test_inspector_output_template_is_valid_json_shape(language):
    text = prompt(f"language_pipeline/{language}/inspector").format(style_guide="G")
    template = text[text.index('{"verdict"'):]
    assert template.count("{") == template.count("}")
    assert '"scores"' in template and '"items"' in template


@pytest.mark.parametrize("language", GUIDE_LANGUAGES)
def test_language_guide_exists_and_has_no_format_placeholders(language):
    text = prompt(f"language_pipeline/guides/{language}")
    assert text.startswith("LANGUAGE GUIDE")
    assert text == text.format()


HEDGES = ("apparently", "based on the evidence", "some sources say")


def test_the_english_story_writers_hedge_uncertain_claims_and_keep_the_hedges_they_are_given():
    from app.core.prompts import prompt

    draft = prompt("language_pipeline/draft_writer")
    assert "UNCERTAIN FACTS" in draft and all(h in draft for h in HEDGES)
    assert "An outcome does not turn one party's account of what happened into established fact" in draft
    assert "prosecution" not in draft and "court heard" not in draft          # a legal role in a hedge would spoil the story's reveals
    assert "every uncertain claim carries it" in draft                       # "state each limitation once" does not forbid the short hedge
    for name in ("rewrite_writer", "final_writer"):
        text = prompt(f"language_pipeline/en/{name}")
        assert "Keep every hedge" in text and all(h in text for h in HEDGES)
    # the placeholders of the prompts are still filled by the pipeline
    draft.format(marker_instruction="m", tone="t", target_words=1)


def test_the_spoken_writer_keeps_the_scripts_attribution_adds_none_and_announces_nothing():
    from app.documentary.spoken import writer_system_prompt

    for language in ("en", "fa", "de"):
        text = " ".join(writer_system_prompt(language).split())       # the prompt wraps its lines
        assert "not allowed to write anything from your own knowledge" in text and "only the given text and these instructions" in text

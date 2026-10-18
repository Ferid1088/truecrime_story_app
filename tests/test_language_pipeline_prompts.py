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

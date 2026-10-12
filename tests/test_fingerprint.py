import unicodedata

from app.core.fingerprint import canonical_json, input_fingerprint


def test_key_order_and_float_spelling_do_not_change_canonical_json():
    assert canonical_json({"b": 1.000, "a": [2.5000]}) == canonical_json({"a": [2.5], "b": 1.0})


def test_persian_and_arabic_normalization_is_stable():
    text = "یادداشت عربي"
    decomposed = unicodedata.normalize("NFD", text)
    composed = unicodedata.normalize("NFC", text)
    assert input_fingerprint("story", {"text": decomposed}) == input_fingerprint("story", {"text": composed})


def test_model_and_prompt_version_change_the_hash():
    base = {"model": "model-a", "prompt_version": "p1", "config": {"temperature": 0.2}}
    assert input_fingerprint("story", base) != input_fingerprint("story", {**base, "model": "model-b"})
    assert input_fingerprint("story", base) != input_fingerprint("story", {**base, "prompt_version": "p2"})


def test_volatile_fields_and_absolute_paths_are_excluded():
    one = {"text": "same", "created_at": "2026-01-01T00:00:00Z", "output_path": "/tmp/a.mp4"}
    two = {"text": "same", "created_at": "2027-01-01T00:00:00Z", "output_path": "/Users/other/a.mp4"}
    assert canonical_json(one) == canonical_json(two)

import json
from pathlib import Path

from app.core.member_ids import ensure_pipeline_member_ids, load_legacy_ids
from app.documentary.production.script import compose


GOLDEN = Path(__file__).parent / "golden/production_script_previous.json"


def _small_production_script():
    manifest = {
        "duration_seconds": 2.0,
        "files": {"narration_wav": "n.wav"},
        "timeline": {
            "beats": [{"beat_id": "B01", "start": 0.0, "end": 2.0}],
            "blocks": [{"block_id": "b1", "start": 0.0, "end": 2.0,
                        "pause_after_kind": "end"}],
            "words": [{"word": "Hi", "start": 0.0, "end": 0.5}],
        },
    }
    return compose(manifest, {"beats": [{"beat_id": "B01", "shots": [
        {"command": "BLACK_SCREEN", "share": 1.0},
    ]}]}, {}, {}, "en")


def _without_new_ids(value):
    if isinstance(value, dict):
        return {k: _without_new_ids(v) for k, v in value.items()
                if k not in {"id", "card_id", "piece_id"}}
    if isinstance(value, list):
        return [_without_new_ids(v) for v in value]
    return value


def test_mocked_production_script_matches_previous_golden_without_ids():
    produced = ensure_pipeline_member_ids("production-script:golden:en", _small_production_script())
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _without_new_ids(produced) == expected
    assert all(item.get("id", "").startswith("shot_") for item in produced["shots"])
    assert all(item.get("id", "").startswith("caption_") for item in produced["subtitles"])
    assert all(item.get("id", "").startswith("voice_block_") for item in produced["voice"])


def test_resaving_and_legacy_loading_preserve_member_ids():
    document = ensure_pipeline_member_ids("production:1", {
        "beats": [{"id": "beat_existing", "text": "keep"}],
        "shots": [{"command": "BLACK_SCREEN"}],
    })
    saved = ensure_pipeline_member_ids("production:1", document)
    assert saved["beats"][0]["id"] == "beat_existing"
    assert saved["shots"][0]["id"] == document["shots"][0]["id"]
    first = load_legacy_ids("blueprint:1", "beat", [{"text": "old"}])
    second = load_legacy_ids("blueprint:1", "beat", [{"text": "old"}])
    assert first == second and first[0]["legacy_id"] is True


def test_regenerating_one_beat_does_not_change_other_ids():
    original = ensure_pipeline_member_ids("blueprint:1", {
        "beats": [{"text": "one"}, {"text": "two"}],
    })
    regenerated = ensure_pipeline_member_ids("blueprint:1", {
        "beats": [{"id": original["beats"][0]["id"], "text": "changed"},
                  original["beats"][1]],
    })
    assert regenerated["beats"][1]["id"] == original["beats"][1]["id"]

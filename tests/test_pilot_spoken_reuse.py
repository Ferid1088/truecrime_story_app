"""F1: a pilot that covers every beat narrates the whole film — the stored FULL spoken
version (pilot_beat_ids=None) is the same text and is reused, not rewritten. A real subset
pilot still rewrites."""
from app.documentary.jobs import reusable_spoken


def test_full_spoken_is_reused_when_the_pilot_covers_every_beat():
    saved = {"pilot_seconds": None, "pilot_beat_ids": None}   # the full-film spoken
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}]}
    assert reusable_spoken(saved, pilot_seconds=500, focus_beats=["B01", "B02"],
                           blueprint=blueprint) is True


def test_full_spoken_is_not_reused_for_a_real_subset_pilot():
    saved = {"pilot_seconds": None, "pilot_beat_ids": None}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01"],
                           blueprint=blueprint) is False


def test_an_exact_pilot_match_is_reused():
    saved = {"pilot_seconds": 120.0, "pilot_beat_ids": ["B01", "B02"]}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01", "B02"],
                           blueprint=blueprint) is True


def test_a_different_pilot_cut_is_rewritten():
    saved = {"pilot_seconds": 120.0, "pilot_beat_ids": ["B01", "B02"]}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01", "B03"],
                           blueprint=blueprint) is False


def test_a_spoken_without_stored_cut_info_is_rewritten():
    blueprint = {"beats": [{"id": "B01"}]}
    assert reusable_spoken({}, pilot_seconds=100, focus_beats=["B01"],
                           blueprint=blueprint) is False


def test_each_spoken_text_is_written_once_in_chunks_of_six_beats():
    """A whole-story call ran away to 64,000 output tokens for an 8,000-word story, and one call per beat repeated the 1,300-word system
    prompt 78 times per language: the writer tells six beats per call, each text once, inspected by the one small inspector only; a beat it flags is rewritten in one repair round, and a version with open findings goes on with them on record."""
    from app.core.ai_config import ai_config

    assert ai_config.spoken.write_beats_per_call == 6 and ai_config.spoken.beats_per_call == 6
    assert ai_config.spoken.max_repair_iterations == 1 and ai_config.spoken.max_redos == 0
    assert ai_config.spoken.llm_checks is False and ai_config.spoken.addition_inspector is True
    assert ai_config.production_policies.retry_policy.spoken_versions == "DEGRADE"

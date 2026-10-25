"""Exact picture-track gap detection; not a substitute for semantic verification."""
from app.documentary.visuals.coverage_fill import gate, measure, uncovered_intervals


def test_adjacent_shots_cover_full_narration():
    script = {"duration": 12, "shots": [
        {"start": 0, "end": 4, "kind": "image"},
        {"start": 4, "end": 12, "kind": "document"},
    ]}
    assert uncovered_intervals(script) == []
    assert measure(script)["uncovered_intervals"] == []


def test_coverage_gate_rejects_real_gap():
    script = {"duration": 12, "shots": [
        {"start": 0, "end": 3, "kind": "image"},
        {"start": 5, "end": 9, "kind": "image"},
    ]}
    gaps = uncovered_intervals(script)
    assert gaps == [{"start": 3.0, "end": 5.0}, {"start": 9.0, "end": 12.0}]
    assert any("uncovered" in item for item in gate(measure(script)))


def test_overlap_and_negative_start_do_not_create_fictitious_gap():
    script = {"duration": 10, "shots": [
        {"start": -3, "end": 6, "kind": "image"},
        {"start": 5, "end": 20, "kind": "image"},
    ]}
    assert uncovered_intervals(script) == []


def test_unusable_shot_intervals_never_count_as_coverage():
    script = {"duration": 10, "shots": [
        {"start": 8, "end": 4, "kind": "image"},
        {"start": None, "end": 10, "kind": "image"},
    ]}
    assert uncovered_intervals(script) == [{"start": 0.0, "end": 10.0}]

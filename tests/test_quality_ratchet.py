from scripts.quality_ratchet import compare_findings


def test_ratchet_rejects_an_injected_new_finding():
    baseline = ["app/example.py:E999:existing diagnostic"]
    current = baseline + ["app/example.py:F999:injected diagnostic"]
    assert compare_findings(current, baseline) == [
        "app/example.py:F999:injected diagnostic"
    ]


def test_ratchet_is_line_shift_tolerant_for_existing_identity():
    baseline = ["app/example.py:E999:existing diagnostic"]
    current = ["app/example.py:E999:existing diagnostic"]
    assert compare_findings(current, baseline) == []

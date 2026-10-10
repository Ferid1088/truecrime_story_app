from app.shortform.selection import select_candidates


def _candidate(i, concept_type="MYSTERY_HOOK", hook=None, **extra):
    return {
        "id": f"C{i}",
        "concept_type": concept_type,
        "hook": hook or f"Specific case detail {i}",
        "source_beat_ids": [f"B{i:03d}"],
        "evidence_ids": [f"F{i:03d}"],
        "asset_ids": [f"A{i:03d}"],
        "allowed_reveals": ["R_OPENING"],
        "claims": [{"source_claim_id": f"F{i:03d}", "modality": "ESTABLISHED"}],
        "duration": 35,
        "disclosure_flags": {},
        **extra,
    }


def test_selection_rejects_unsafe_candidates_with_readable_reasons():
    candidates = [
        _candidate(1, "MYSTERY_HOOK"),
        _candidate(
            2,
            "REAL_EVIDENCE",
            text_reveals=["R_CULPRIT"],
        ),
        _candidate(
            3,
            "DOCUMENT_MOMENT",
            claims=[{
                "source_claim_id": "F003",
                "source_modality": "ALLEGED",
                "short_modality": "ESTABLISHED",
            }],
        ),
    ]
    result = select_candidates(candidates, target_count=3)
    assert len(result["selected"]) == 1
    assert result["shortfall"] == 2
    reasons = " ".join(result["shortfall_reasons"])
    assert "forbidden reveal" in reasons
    assert "strengthened" in reasons


def test_mmr_selection_keeps_diverse_survivors():
    candidates = [
        _candidate(1, "MYSTERY_HOOK", "The route still matters"),
        _candidate(2, "REAL_EVIDENCE", "The receipt fixes a time"),
        _candidate(3, "DOCUMENT_MOMENT", "One document changes the timeline"),
        _candidate(4, "LOCATION_OR_MAP", "Look closely at the road"),
    ]
    result = select_candidates(candidates, target_count=3)
    assert len(result["selected"]) == 3
    assert result["diversity"]["passes"]
    assert len({c["concept_type"] for c in result["selected"]}) > 1


def test_one_type_selection_fails_diversity_gate():
    candidates = [
        _candidate(i, "MYSTERY_HOOK", f"Different detail {i}")
        for i in range(1, 5)
    ]
    result = select_candidates(candidates, target_count=3)
    assert len(result["selected"]) == 1
    assert result["shortfall"] == 2
    assert any("diversity gate" in reason for reason in result["shortfall_reasons"])


def test_fewer_than_target_is_valid_and_explained():
    candidates = [
        _candidate(1, "MYSTERY_HOOK"),
        _candidate(2, "REAL_EVIDENCE", duration=75),
    ]
    result = select_candidates(candidates, target_count=4)
    assert len(result["selected"]) == 1
    assert result["shortfall"] == 3
    assert any("duration" in reason for reason in result["shortfall_reasons"])


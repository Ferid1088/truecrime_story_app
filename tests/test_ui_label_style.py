from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_known_title_case_labels_do_not_return_to_user_facing_code():
    roots = [ROOT / "frontend" / "app", ROOT / "frontend" / "components"]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in root.rglob("*.tsx")
        if ".next" not in str(path)
    )
    forbidden = ("Test Report", "Rendered Shorts")
    assert not [label for label in forbidden if label in source]

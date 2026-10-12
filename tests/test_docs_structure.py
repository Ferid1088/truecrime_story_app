import re
from pathlib import Path

ROOT = Path(__file__).parents[1]

INCREMENTAL_HEADINGS = [
    "Purpose and non-goals", "Artifact inventory", "Pipeline stage sequence and resume",
    "Final render today and per-segment analysis", "Dependency table", "Artifact-graph schema",
    "ID scheme", "Invalidation algorithm", "Edit-by-prompt flow", "API endpoints",
    "Implementation steps", "Open risks and questions",
]
SHORTS_HEADINGS = [
    "Purpose, non-goals, and final decisions", "Reality check", "Shorts pipeline design (PROPOSED)",
    "Publishing design (PROPOSED)", "Approval and publishing state machine", "Per-platform requirements",
    "UI design (PROPOSED)", "API endpoints (PROPOSED)", "Implementation steps", "Open risks and questions",
]


def test_required_architecture_sections_exist():
    inc = (ROOT / "docs/architecture/incremental-build.md").read_text()
    shorts = (ROOT / "docs/architecture/shorts-and-publishing.md").read_text()
    for heading in INCREMENTAL_HEADINGS:
        assert heading in inc
    for heading in SHORTS_HEADINGS:
        assert heading in shorts


def test_incremental_dependency_table_has_rows_and_matches_registry_when_present():
    text = (ROOT / "docs/architecture/incremental-build.md").read_text()
    section = text.split("## 5. Dependency table", 1)[1].split("## 6.", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|") and "---" not in line]
    assert len(rows) >= 17
    doc_types = {row.split("|")[1].strip() for row in rows[1:]}
    registry = ROOT / "app/core/artifact_dependencies.py"
    if registry.exists():
        source = registry.read_text()
        registered = set(re.findall(r'"([a-z_]+)":\s*\[', source))
        assert doc_types <= registered

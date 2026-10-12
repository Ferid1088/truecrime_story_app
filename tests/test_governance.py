"""The process files exist and still say what the runbooks promise."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def test_dependabot_covers_every_ecosystem_in_use():
    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    kinds = {(u["package-ecosystem"], u["directory"]) for u in cfg["updates"]}
    assert {("pip", "/"), ("npm", "/frontend"), ("github-actions", "/")} <= kinds


def test_ci_has_every_job_the_docs_name_and_a_weekly_schedule():
    assert {"backend", "backend-postgres", "frontend", "audit"} <= set(CI["jobs"])
    assert "schedule" in CI[True] if True in CI else "schedule" in CI["on"]


def test_audit_job_checks_python_and_production_npm():
    text = "\n".join(str(step.get("run", "")) for step in CI["jobs"]["audit"]["steps"])
    assert "pip-audit" in text and "npm audit --omit=dev" in text


def test_policy_files_exist_and_are_not_empty():
    for f in ("SECURITY.md", "CHANGELOG.md", ".github/CODEOWNERS", ".github/pull_request_template.md",
              "docs/release.md", "docs/operations.md", "docs/security.md"):
        assert (ROOT / f).read_text().strip(), f


def test_codeowners_protects_the_sensitive_paths():
    text = (ROOT / ".github/CODEOWNERS").read_text()
    for path in ("/migrations/", "/app/core/security.py", "/.github/"):
        assert path in text


def test_documented_accepted_risks_have_a_review_date():
    text = (ROOT / "docs/security.md").read_text()
    section = text.split("## Dependency audit and accepted risks", 1)[1]
    rows = [r for r in section.splitlines() if r.startswith("|") and "---" not in r and "Item" not in r]
    assert rows and all(r.split("|")[-2].strip() for r in rows)

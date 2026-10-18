"""T17: the old translation path is gone from app/, config/, prompts/ and tests/.

Not scanned, on purpose: Alembic migrations (history), CHANGELOG.md, docs/ (decisions of the
past), tests/golden/ (recorded outputs). Listed exceptions below carry their reason."""

import json
import re
from pathlib import Path

from app.agents import registry
from app.core import prompts
from app.core.ai_config import REQUIRED_ROLES, ai_config

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("app", "config", "prompts", "tests")
SUFFIXES = {".py", ".json", ".md", ".txt"}
SKIPPED_DIRS = {"__pycache__", "golden"}

# file -> why a forbidden word may stay in it
EXCEPTIONS = {
    "app/db/models.py": "the story_versions column derived_from_master_version exists in the "
                        "database (history stays readable; columns are never dropped)",
    "tests/test_language_pipeline_audit.py": "this file lists the words",
}

FORBIDDEN_WORDS = (
    "localization", "localized_", "derived_from_master", "native_language_critic",
    "LocalizationPipeline", "compare-master", "compare_master", "MASTER_ROLES", "write_master",
    "approve_master", "MasterApprovalError",
)
FORBIDDEN_PATTERNS = {
    "a 120-minute draft": re.compile(r"long_draft_minutes\W+120\b|\b120[ -]minute draft", re.I),
    "a draft written per language": re.compile(r"draft_per_language|per_language_draft", re.I),
}
REMOVED_AGENTS = (
    "story.write", "story.write_act", "story.critique", "story.critique_section",
    "story.improve", "story.fit_section_length", "story.repair_spans",
    "story.apply_revision_ops", "story.section_pass", "story.final_edit",
    "master_story_director", "master_writer", "master_rewriter", "master_engagement_critic",
    "master_final_editor", "localization.",
)
REMOVED_ROLES = (
    "writer", "rewriter", "engagement_critic", "section_critic", "final_editor",
    "master_story_director", "master_writer", "master_rewriter", "master_engagement_critic",
    "master_final_editor", "localization_writer", "native_language_critic",
    "localized_engagement_critic", "localized_grounding_validator", "localized_final_editor",
)
REMOVED_PROMPTS = (
    "agents/story/write", "agents/story/write_act", "agents/story/critique",
    "agents/story/critique_section", "agents/story/improve", "agents/story/fit_section_length",
    "agents/story/fit_section_length_2", "agents/story/repair_section_spans",
    "agents/story/apply_revision_ops", "agents/story/section_pass", "agents/story/final_edit",
)


def _files():
    for top in SCANNED:
        for path in (ROOT / top).rglob("*"):
            if path.is_file() and path.suffix in SUFFIXES and not (SKIPPED_DIRS & set(path.parts)):
                yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_no_forbidden_word_is_left_in_app_config_prompts_or_tests():
    found = []
    for path in _files():
        rel = str(path.relative_to(ROOT))
        if rel in EXCEPTIONS or rel == "config/quality_baseline.json":
            continue
        text = _read(path)
        for word in FORBIDDEN_WORDS:
            if word in text:
                found.append(f"{rel}: {word}")
        for name, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                found.append(f"{rel}: {name}")
    assert not found, found


def test_the_quality_baseline_lists_no_deleted_file():
    baseline = json.loads(_read(ROOT / "config" / "quality_baseline.json"))
    entries = [e for value in baseline.values() if isinstance(value, list) for e in value]
    stale = sorted({e.split(":")[0] for e in entries if isinstance(e, str)
                    and e.startswith(("app/", "tests/")) and not (ROOT / e.split(":")[0]).exists()})
    assert not stale, stale


def test_removed_agents_are_gone_from_config_and_code():
    assert not [n for n in ai_config.agents if n.startswith(REMOVED_AGENTS)]
    hits = []
    for path in _files():
        rel = str(path.relative_to(ROOT))
        if rel in EXCEPTIONS or path.suffix != ".py" and path.name != "agents.json":
            continue
        text = _read(path)
        for name in REMOVED_AGENTS:
            if re.search(rf'["\']{re.escape(name)}', text):
                hits.append(f"{rel}: {name}")
    assert not hits, hits


def test_removed_prompt_files_are_gone():
    available = set(prompts.available())
    assert not [p for p in REMOVED_PROMPTS if p in available]
    assert not [p for p in available if p.startswith("agents/localization/")]
    for path in _files():
        rel = str(path.relative_to(ROOT))
        if rel in EXCEPTIONS or path.suffix != ".py":
            continue
        text = _read(path)
        for name in REMOVED_PROMPTS:
            assert f'"{name}"' not in text, f"{rel}: {name}"


def test_removed_roles_are_gone_from_routing_generation_and_code():
    models = json.loads(_read(ROOT / "config" / "models.json"))
    routing = models["generation_providers"]["apimaster"]["routing"]
    assert not set(REMOVED_ROLES) & (set(routing) | set(models["generation"]) | REQUIRED_ROLES)
    review = json.loads(_read(ROOT / "config" / "parameters" / "story.json"))["review_independence"]
    listed = {*review["author_roles"], *review["reviewer_roles"],
              *(r for g in review["groups"] for r in [*g["authors"], *g["reviewers"]])}
    assert not set(REMOVED_ROLES) & listed
    removed = "|".join(REMOVED_ROLES)
    call = re.compile(r'(?:role\s*=|model_for\(|generation_for\(|fallback_models_for\(|stamp_run\([^)]*,\s*)'
                      rf'\s*["\']({removed})["\']')
    hits = [f"{p.relative_to(ROOT)}: {m.group(1)}" for p in _files() if p.suffix == ".py"
            and str(p.relative_to(ROOT)) not in EXCEPTIONS
            for m in call.finditer(_read(p))]
    assert not hits, hits


def test_every_defined_agent_is_run_by_a_class_or_a_stage():
    """The architecture test's rule, kept here for the four new agents: defined means run."""
    used = set()
    for path in (ROOT / "app").rglob("*.py"):
        used |= set(re.findall(r'run_agent\(\s*"([\w.]+)"', _read(path)))
    for name in ("language.draft", "english.evidence_audit",
                 "language.rewrite.fa", "language.inspect.fa", "language.final.fa",
                 "language.rewrite.de", "language.inspect.de", "language.final.de",
                 "language.rewrite.ar", "language.inspect.ar", "language.final.ar",
                 "language.rewrite.en", "language.inspect.en", "language.final.en"):
        assert name in ai_config.agents and name in used, name
    assert not registry.CLASSES.keys() & set(REMOVED_AGENTS)


def test_the_draft_is_english_and_ninety_minutes_in_config():
    cfg = ai_config.language_pipeline
    assert (cfg.draft_language, cfg.long_draft_minutes) == ("en", 90)
    assert ai_config.multilingual.production_languages == ["fa", "en", "de", "ar"]
    assert ai_config.story_quality.max_evidence_per_story == 100
    assert ai_config.story_quality.max_evidence_for_long_draft is None

"""The Persian/Arabic pronunciation system (reading stage, Tashkeel, irab, pronunciation planner, key and listening loop, pronunciation memory, phonetic
benchmark) was removed on 2026-11-10: a person listens and corrects by hand in the voice review, and numbers are read by the number layers. Nothing of it may
come back by accident: no module, config, agent, table, route, script, prompt or screen."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[1]

REMOVED_MODULES = [
    "app.documentary.reading", "app.documentary.reading.stage", "app.documentary.reading.writer", "app.documentary.reading.tashkeel",
    "app.documentary.reading.voicing", "app.documentary.reading.numbers", "app.documentary.irab", "app.documentary.pronunciation",
    "app.documentary.pronunciation_planner", "app.documentary.pronunciation_risk", "app.documentary.pronunciation_explain",
    "app.documentary.pronunciation_trace", "app.documentary.pronunciation_memory", "app.documentary.pronunciation_auto",
    "app.documentary.pronunciation_import", "app.documentary.pronunciation_review", "app.documentary.pronunciation_agents",
    "app.documentary.phonetic_benchmark", "app.documentary.phoneme_alignment", "app.documentary.voice_experiment", "app.documentary.voice_prepare",
    "app.documentary.lexicon", "app.documentary.narration_preview", "app.documentary.prosody",
]
REMOVED_CONFIG_SECTIONS = ["ppie", "reading", "pronunciation_risk", "pronunciation", "pronunciation_trace", "pronunciation_auto", "irab", "ezafe", "morphology",
                           "homographs", "number_reading", "voice_prepare", "phoneme_alignment", "prosody"]
DROPPED_TABLES = {"reading_resolutions", "reading_approvals", "reading_doubts", "pronunciation_lexicon", "pronunciation_entries", "pronunciation_entry_versions",
                  "pronunciation_interventions", "pronunciation_evidence", "pronunciation_audit_log", "phonetic_samples", "phonetic_labels"}
DROPPED_MODELS = ["ReadingResolution", "ReadingApproval", "ReadingDoubt", "PronunciationLexicon", "PronunciationEntry", "PronunciationEntryVersion",
                  "PronunciationIntervention", "PronunciationEvidence", "PronunciationAuditLog", "PhoneticSample", "PhoneticLabel"]
REMOVED_ROUTE_WORDS = ("reading", "pronunciation", "phonetic", "previews", "doubts")
REMOVED_SCRIPTS = ["approve_reading", "audit_pronunciation_references", "audit_pronunciation_rules", "auto_verify_pronunciations", "benchmark_phoneme_recognizers",
                   "eval_pronunciation_agents", "extract_phonetic_samples", "import_legacy_pronunciations", "persian_spoken_run", "persian_voice_run",
                   "plan_pronunciation", "plan_voice_experiment", "propose_phonetic_labels", "resolve_reading_conflicts", "run_reading_stage",
                   "run_voice_experiment", "render_narration_preview", "run_listening_test", "revert_auto_approvals"]

SYMBOLS = re.compile("|".join([
    r"\bReadingStage\b", r"\bTashkeelStage\b", r"\bTashkeelVoicing\b", r"\bReadingFailed\b", r"\bEzafeAnalyzer\b", r"\bPronunciationEditor\b",
    r"\bPronunciationEntry\b", r"\bPronunciationMemory\b", r"\bPronunciationLexicon\b", r"\bPhoneticSample\b", r"\bReadingDoubt\b", r"\bReadingApproval\b",
    r"\bReadingResolution\b", r"\bPronunciationRiskConfig\b", r"\bPPIEConfig\b", r"\bPronunciationTraceConfig\b", r"\bBlockDictionaries\b",
    r"\bppie\b", r"\bPPIE\b", r"ai_config\.reading\b", r"ai_config\.pronunciation\b", r"ai_config\.irab\b", r"pronunciation_risk", r"pronunciation_planner",
    r"pronunciation_trace", r"pronunciation_memory", r"pronunciation_review", r"pronunciation_explain", r"tashkeel_number_max", r"\brenderer\.planner\b",
    r"\brenderer\.voicing\b", r"\bself\.voicing\b", r"\bimport Voicing\b", r"\bclass Voicing\b", r"app\.documentary\.reading",
    r"documentary/reading/", r"documentary/irab/", r"reading:<lang>", r"\bprepared_languages\b", r"voice_prepare", r"\bPronunciationReviewTab\b",
    r"\bReadingReviewTab\b", r"\bPhoneticBenchmark\b", r"\bNarrationPreviews\b", r"\bpronunciation_check\b", r"\breading_(reader|editor|checker|arbiter|listener)\b",
    r"documentary\.(reading|irab)\.", r"\bpronunciation_editor\b",
]))
SCANNED = ["app", "scripts", "tests", "frontend/app", "frontend/components", "frontend/lib", "frontend/e2e", "config", "prompts"]
SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".json", ".md", ".txt", ".sh"}
THIS_FILE = Path(__file__).resolve()
CHANGED_ON_PURPOSE = {ROOT / "tests" / "test_old_persian_review_is_gone.py"}


def scanned_files():
    for folder in SCANNED:
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in SUFFIXES and path.is_file() and "node_modules" not in path.parts and ".next" not in path.parts and "__pycache__" not in path.parts:
                if path.resolve() != THIS_FILE and path not in CHANGED_ON_PURPOSE:
                    yield path


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_the_removed_module_cannot_be_imported(module):
    try:
        found = importlib.util.find_spec(module)
    except ModuleNotFoundError:          # its parent package is gone too
        found = None
    assert found is None


def test_the_removed_packages_prompts_and_scripts_are_gone_from_disk():
    for folder in ("app/documentary/reading", "app/documentary/irab", "prompts/documentary/reading", "prompts/documentary/irab", "prompts/documentary/pronunciation",
                   "prompts/documentary/voice_prepare", "prompts/pronunciation"):
        assert not (ROOT / folder).exists(), folder
    for name in REMOVED_SCRIPTS:
        assert not (ROOT / "scripts" / f"{name}.py").exists(), name
    assert not (ROOT / "frontend/components/documentary/tabs/pronunciation-review.tsx").exists()
    assert not (ROOT / "frontend/components/documentary/tabs/reading-review.tsx").exists()


@pytest.mark.parametrize("section", REMOVED_CONFIG_SECTIONS)
def test_the_removed_config_section_is_gone(section):
    assert not hasattr(ai_config, section)
    for path in (ROOT / "config" / "parameters").glob("*.json"):
        assert section not in json.loads(path.read_text(encoding="utf-8")), f"{path.name} still has {section}"


def test_no_agent_or_role_of_the_removed_stages_is_left():
    agents = json.loads((ROOT / "config/agents.json").read_text(encoding="utf-8"))
    names = set(agents.get("agents", agents))
    assert not [n for n in names if n.startswith(("documentary.reading", "documentary.irab", "documentary.pronunciation", "documentary.voice_prepare",
                                                   "pronunciation.", "reading_"))]
    roles = {a.get("role") for a in agents.get("agents", agents).values() if isinstance(a, dict)}
    assert not [r for r in roles if r and r.startswith(("reading_", "pronunciation_", "voice_prepare_"))]
    models = (ROOT / "config/models.json").read_text(encoding="utf-8")
    assert not re.search(r'"(reading_\w+|pronunciation_editor|voice_prepare_\w+)"', models)
    assert not [r for r in ai_config.documentary_roles() if r.startswith(("reading_", "pronunciation_", "voice_prepare_"))] if hasattr(ai_config, "documentary_roles") else True


def test_the_database_models_have_none_of_the_dropped_tables():
    import app.db.models as models

    assert not DROPPED_TABLES & set(Base.metadata.tables)
    assert not [name for name in DROPPED_MODELS if hasattr(models, name)]


def test_one_migration_drops_the_tables_and_its_downgrade_recreates_them():
    text = (ROOT / "migrations/versions/0097_remove_pronunciation_stack.py").read_text(encoding="utf-8")
    assert 'down_revision = "0096"' in text
    assert all(f'"{table}"' in text for table in DROPPED_TABLES)
    assert text.count("op.create_table(") == len(DROPPED_TABLES) and "op.drop_table(table)" in text


def test_no_route_serves_a_removed_feature():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert len(paths) > 200
    assert not [p for p in paths if any(word in p for word in REMOVED_ROUTE_WORDS)]
    assert any("block-review" in p for p in paths) and any("voice-review" in p for p in paths), "the owner's voice review stays"


def test_no_file_mentions_a_removed_symbol():
    found = []
    for path in scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        match = SYMBOLS.search(text)
        if match:
            found.append(f"{path.relative_to(ROOT)}: {match.group()}")
    assert not found, found[:20]


def test_the_renderer_has_no_voicing_or_planner_and_no_listening_loop():
    from app.documentary.voice_render import VoiceRenderer

    renderer = VoiceRenderer(provider=object(), use_asr=False)
    assert not hasattr(renderer, "voicing") and not hasattr(renderer, "planner") and not hasattr(renderer, "listener")
    assert not hasattr(VoiceRenderer, "_referee") and not hasattr(VoiceRenderer, "_listen")


def test_arabic_and_persian_numbers_go_through_the_generic_reader_like_german():
    from app.documentary.voice_render import VoiceRenderer

    renderer = VoiceRenderer(provider=object(), use_asr=False)
    for language in ("ar", "de", "tr"):
        assert renderer.says_numbers(language) and renderer.check_numbers(language) is not None
        assert type(renderer.numbers(language)).__module__ == f"app.documentary.spoken_numbers.{language}"

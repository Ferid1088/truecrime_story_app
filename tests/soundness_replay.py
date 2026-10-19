"""Cold, forced replays with one pinned persisted source from a restored job.

The completed template is evidence, not a cache: archive its output files and
reset its disposable DB to case/facts/master inputs. Every downstream producer
then runs cold. An edited derived source (spoken text or asset bytes) is pinned
at its producer boundary, before any consumer runs. This avoids accidentally
overwriting the edit by recomputing its upstream producer. No registry data is
used for observation, or to decide what executes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from soundness_observation import observe
from sqlalchemy import inspect, text

from app.core.artifact_dependencies import (
    ARTIFACT_CONFIG_INPUTS,
    ARTIFACT_SCOPES,
    direct_dependents,
)
from app.db.models import (
    Artifact,
    Case,
    EditorialBlueprint,
    HostPlan,
    ProductionScript,
    ResearchResult,
    StoryVersion,
    VisualAsset,
    VoicePerformance,
)
from app.services.soundness import predicted_change_keys

# Shared with prepare_phase5() below and tests/generate_soundness_status.py's
# CONFIG_SECTIONS: which ARTIFACT_CONFIG_INPUTS section(s) each config-sourced
# perturbation touches.
CONFIG_SECTIONS = {
    "language_profile": {"language_profile"},
    "pronunciation_voice": {"pronunciation_voice"},
    "channel_identity_logo": {"channel_identity"},
    "intro_design": {"intro_design"},
    "agent_model_routing": {"documentary"},
    "music_history_library": {"music_history_library"},
    "logo_bytes": {"logo_bytes"},
    "typed_config_documentary": {"documentary"},
    "typed_config_render": {"render"},
    "typed_config_chapters": {"chapters"},
    "typed_config_visual": {"visual"},
    "typed_config_voice": {"voice"},
    "typed_config_audio": {"audio"},
    "typed_config_host": {"host"},
    "typed_config_music": {"music"},
}


def prepare(db, run: Path, source: str) -> dict:
    assert Path(db.get_bind().url.database).resolve() == (run / "pipeline.db").resolve()
    assert (run.parent / "owned-by-soundness").is_file(), "Refuse to reset an unowned DB"
    before = observe(db, run / "files")
    rows = db.query(Artifact).all()
    source_type = {
        "spoken_de": "spoken_version",
        "naming": "naming",
        "used_visual": "visual_asset",
    }.get(source)
    candidates = [
        r for r in rows if r.type == source_type and (source != "spoken_de" or r.language == "de")
    ]
    selected = candidates[:1]
    predicted = sorted(predicted_change_keys(db, [r.id for r in selected]))
    overlay = {"source": source}
    if source == "spoken_de":
        row = db.query(StoryVersion).filter_by(kind="spoken", language="de").one()
        structure = json.loads(row.narrative_structure)
        old = structure["sections"][0]["text"]
        new = old.replace("walked", "hurried", 1)
        assert new != old
        structure["sections"][0]["text"] = new
        row.story_text = row.story_text.replace(old, new, 1)
        row.narrative_structure = json.dumps(structure, ensure_ascii=False)
        row.text_hash = hashlib.sha256(row.story_text.encode()).hexdigest()
        overlay["version"] = {
            name: getattr(row, name) for name in ("story_text", "narrative_structure", "text_hash")
        }
    elif source == "naming":
        row = db.query(Case).one()
        row.canonical_title += " — renamed"
    elif source == "used_visual":
        assert selected, "No registered used visual source"
        scope = json.loads(selected[0].scope_json)
        asset = db.query(VisualAsset).filter_by(asset_code=scope["asset_id"]).one()
        from PIL import Image

        path = Path(asset.local_path)
        Image.new("RGB", (asset.width, asset.height), (20, 180, 90)).save(path, "JPEG")
        overlay.update({"asset_code": asset.asset_code, "image_hex": path.read_bytes().hex()})
    elif source != "control":
        raise ValueError(source)
    db.commit()
    edited = observe(db, run / "files")
    changed = sorted(k for k in before["hashes"] if before["hashes"][k] != edited["hashes"][k])
    assert changed == (
        [f"{source_type}:{'de' if source == 'spoken_de' else 'shared'}"] if source_type else []
    )
    evidence = {
        "baseline": before,
        "source_changed": changed,
        "predicted": predicted,
        "overlay": overlay,
    }
    # Only this freshly restored disposable DB is reset; the persistent
    # template and the before-edit snapshot remain untouched.
    tables = inspect(db.get_bind()).get_table_names()
    for table in reversed(tables):
        preserved = {"cases", "alembic_version"}
        if os.environ.get("SOUNDNESS_FROM_ZERO") != "1":
            preserved.add("facts")
        if table in preserved:
            continue
        if table == "story_versions":
            db.execute(text("DELETE FROM story_versions WHERE kind != 'master'"))
        else:
            db.execute(text(f'DELETE FROM "{table}"'))
    # Copied SQLite templates retain sqlite_sequence values after derived-row
    # deletion. Reset the disposable run so regenerated member IDs cannot
    # masquerade as a source perturbation.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("DELETE FROM sqlite_sequence"))
    db.commit()
    db.expunge_all()
    (run / "files").rename(run / "baseline-files")
    (run / "files").mkdir()
    (run / "overlay.json").write_text(json.dumps(overlay, ensure_ascii=False))
    (run / "before.json").write_text(json.dumps(evidence, sort_keys=True, ensure_ascii=False))
    return evidence


def install(monkeypatch):
    """Pin the sole edited source before stage registration/consumption."""
    from app.core.ai_config import ai_config
    from app.documentary.pipeline.base import StageRunner

    overlay = json.loads(Path(os.environ["SOUNDNESS_REPLAY"]).read_text())
    source = overlay["source"]
    original = StageRunner._stage
    trace = []
    applied = []

    async def stage(self, name, fn):
        assert ai_config.production_policies.incremental.mode == "off"
        assert self._entry(name)["status"] == "pending", (name, "would reuse a completed stage")

        async def run():
            result = await fn()  # always execute the real stage function
            trace.append(name)
            if source == "spoken_de" and name == "spoken:de":
                row = self.db.get(StoryVersion, result["version_id"])
                for key, value in overlay["version"].items():
                    setattr(row, key, value)
                self.db.commit()
                applied.append(name)
            if source == "used_visual" and name == "visual_research":
                asset = self.db.query(VisualAsset).filter_by(asset_code=overlay["asset_code"]).one()
                Path(asset.local_path).write_bytes(bytes.fromhex(overlay["image_hex"]))
                applied.append(name)
            return result

        return await original(self, name, run)

    monkeypatch.setattr(StageRunner, "_stage", stage)
    return trace, applied


PHASE5_TYPES = {
    "research_result": ("research_result", None),
    "master_story": ("master_story", None),
    "one_beat": ("beats", None),
    "one_voice_take": ("voice_takes", "de"),
    "unused_visual": ("visual_asset", None),
    "host_plan": ("host_plan", None),
    "naming": ("naming", None),
    "language_profile": (None, None),
    "pronunciation_voice": (None, None),
    "channel_identity_logo": (None, None),
    "intro_design": (None, None),
    "agent_model_routing": (None, None),
    "music_history_library": (None, None),
    "logo_bytes": (None, None),
    "typed_config_documentary": (None, None),
    "typed_config_render": (None, None),
    "typed_config_chapters": (None, None),
    "typed_config_visual": (None, None),
    "typed_config_voice": (None, None),
    "typed_config_audio": (None, None),
    "typed_config_host": (None, None),
    "typed_config_music": (None, None),
    "used_visual_metadata": ("visual_asset", None),
    "unused_visual_metadata": ("visual_asset", None),
}


def _change_json_text(value, marker: str):
    """Change one persisted JSON source without changing its shape."""
    data = json.loads(value or "{}")
    if isinstance(data, dict):
        data["_soundness_perturbation"] = marker
    return json.dumps(data, ensure_ascii=False)


def _append_master_story(row, marker: str) -> None:
    """Change the approved text while keeping its stored act structure valid."""
    suffix = f" {marker}"
    row.story_text = (row.story_text or "") + suffix
    try:
        structure = json.loads(row.narrative_structure or "{}")
    except (TypeError, ValueError):
        structure = {}
    sections = structure.get("sections") if isinstance(structure, dict) else None
    if sections:
        sections[-1]["text"] = (sections[-1].get("text") or "") + suffix
        row.narrative_structure = json.dumps(structure, ensure_ascii=False)
    row.text_hash = hashlib.sha256(row.story_text.encode()).hexdigest()


def _asset_codes_from_plan(row) -> set[str]:
    if row is None:
        return set()
    try:
        value = json.loads(row.plan_json or "{}")
    except (TypeError, ValueError):
        return set()

    def walk(item):
        if isinstance(item, dict):
            found = {str(item["asset_id"])} if item.get("asset_id") else set()
            for child in item.values():
                found.update(walk(child))
            return found
        if isinstance(item, list):
            found = set()
            for child in item:
                found.update(walk(child))
            return found
        return set()

    return walk(value)


def _asset_codes_from_scripts(db) -> set[str]:
    """Return assets actually named by the saved production scripts.

    The visual plan is intentionally broader than the renderer's input: it
    contains candidates and map support assets.  The unused-asset
    perturbation must therefore be selected from the scripts consumed by the
    renderer, not from the plan alone.
    """
    found: set[str] = set()

    def walk(item):
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"asset_id", "asset_code"} and value:
                    found.add(str(value))
                walk(value)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    for row in db.query(ProductionScript).all():
        try:
            walk(json.loads(row.script_json or "{}"))
        except (TypeError, ValueError):
            continue
    return found


#: (perturbation name, consumer artifact type) -> code-level reason. Covers
#: both real-root perturbations (keyed by source_type, e.g. "master_story")
#: and config-sourced ones (keyed by the perturbation name itself, e.g.
#: "agent_model_routing"), since the two never collide.
NO_EFFECT_REASONS = {
    ("master_story", "naming"): (
        "app/naming/context.py:54-103 (build_context) reads case.canonical_title, "
        "case.summary and the blueprint, never StoryVersion.story_text/"
        "narrative_structure; this perturbation (tests/soundness_replay.py:193-205) "
        "only changes those two master-story fields."
    ),
    ("master_story", "chapter_plan"): (
        "app/agents/chapters.py:29 reads master.narrative_structure, but "
        "_payload() (chapters.py:45-67) only embeds ctx['acts']/ctx['events'], "
        "built by acts_of()/told_events() (app/documentary/chapters.py:69-89), "
        "which read structure['acts'] (plan metadata), never structure['sections'] "
        "(prose text); this perturbation (tests/soundness_replay.py:193-205) only "
        "appends to sections[-1]['text'] and story_text."
    ),
    ("agent_model_routing", "beats"): (
        "agent_model_routing only reassigns routing['chapter_writer'] "
        "(tests/soundness_replay.py config_edit, 'agent_model_routing' branch); the "
        "mocked request payload embeds ai_config.model_for(role) "
        "(tests/mock_payload.py:126,133), and beats is produced by the "
        "visual_planner role (config/agents.json:298) — a different role, so its "
        "embedded model string is unchanged by this perturbation."
    ),
    ("agent_model_routing", "host_plan"): (
        "agent_model_routing only reassigns routing['chapter_writer'] "
        "(tests/soundness_replay.py config_edit, 'agent_model_routing' branch); the "
        "mocked request payload embeds ai_config.model_for(role) "
        "(tests/mock_payload.py:126,133), and host_plan is produced by the "
        "host_director/host_writer roles (config/agents.json:431,438) — different "
        "roles, so their embedded model string is unchanged by this perturbation."
    ),
    ("agent_model_routing", "master_story"): (
        "agent_model_routing only reassigns routing['chapter_writer'] "
        "(tests/soundness_replay.py config_edit, 'agent_model_routing' branch); the "
        "mocked request payload embeds ai_config.model_for(role) "
        "(tests/mock_payload.py:126,133), and the final story is produced by the "
        "language_* roles — different roles, so their embedded model string is "
        "unchanged by this perturbation."
    ),
    ("agent_model_routing", "naming"): (
        "agent_model_routing only reassigns routing['chapter_writer'] "
        "(tests/soundness_replay.py config_edit, 'agent_model_routing' branch); the "
        "mocked request payload embeds ai_config.model_for(role) "
        "(tests/mock_payload.py:126,133), and naming is produced by the "
        "case_naming_agent role (config/agents.json:361) — a different role, so "
        "its embedded model string is unchanged by this perturbation."
    ),
    ("typed_config_documentary", "beats"): (
        "typed_config_documentary bumps ai_config.documentary.pilot_seconds "
        "(tests/soundness_replay.py config_edit, 'typed_config_' branch). "
        "app/documentary/jobs.py:238 only consults it as `job.pilot_seconds or "
        "ai_config.documentary.pilot_seconds`; this fixture's job is created with an "
        "explicit pilot_seconds=80.0 (tests/test_visual_production.py:1406-1407), "
        "which always wins the `or`, so the config field is never read in this run "
        "at all — not by beats' visual_planner request or anything else."
    ),
    ("typed_config_documentary", "host_plan"): (
        "See the beats edge for this perturbation: ai_config.documentary.pilot_seconds "
        "is shadowed by this fixture's explicit job.pilot_seconds=80.0 "
        "(app/documentary/jobs.py:238; tests/test_visual_production.py:1406-1407) and "
        "is never read in this run."
    ),
    ("typed_config_documentary", "master_story"): (
        "See the beats edge for this perturbation: ai_config.documentary.pilot_seconds "
        "is shadowed by this fixture's explicit job.pilot_seconds=80.0 "
        "(app/documentary/jobs.py:238; tests/test_visual_production.py:1406-1407) and "
        "is never read in this run."
    ),
    ("typed_config_documentary", "naming"): (
        "See the beats edge for this perturbation: ai_config.documentary.pilot_seconds "
        "is shadowed by this fixture's explicit job.pilot_seconds=80.0 "
        "(app/documentary/jobs.py:238; tests/test_visual_production.py:1406-1407) and "
        "is never read in this run."
    ),
    ("typed_config_documentary", "production_script"): (
        "See the beats edge for this perturbation: ai_config.documentary.pilot_seconds "
        "is shadowed by this fixture's explicit job.pilot_seconds=80.0 "
        "(app/documentary/jobs.py:238; tests/test_visual_production.py:1406-1407) and "
        "is never read in this run."
    ),
}


def _config_direct_consumer_types(config_source: str) -> set[str]:
    sections = CONFIG_SECTIONS.get(config_source, set())
    return {
        artifact for artifact, inputs in ARTIFACT_CONFIG_INPUTS.items()
        if inputs & sections
    }


def no_effect_edges(
    source_type: str,
    source_language: str | None,
    actual: list[str],
    *,
    asset_used: bool | None = None,
    config_source: str | None = None,
) -> list[dict]:
    """Record direct registry edges whose saved output stayed identical.

    This is deliberately computed from snapshot keys, never from registry
    hashes.  A missing direct consumer is allowed only with an explicit,
    reviewable reason in the committed result record.

    `config_source` is set for config-sourced perturbations (e.g.
    "agent_model_routing", "typed_config_documentary"): their direct
    consumers come from ARTIFACT_CONFIG_INPUTS/CONFIG_SECTIONS, each at a
    single representative language ("en"), not from the real-root registry
    closure `direct_dependents()` uses for a changed artifact.
    """
    reason_key_source = config_source or source_type
    actual_set = set(actual)
    edges = []
    if config_source:
        targets = sorted(_config_direct_consumer_types(config_source))
        from_label = f"{config_source}:routing-or-config"
    else:
        targets = sorted(direct_dependents(source_type))
        from_label = f"{source_type}:{source_language or 'shared'}"
    for target in targets:
        if config_source:
            languages = ("en",) if ARTIFACT_SCOPES[target] == "per_language" else (None,)
        else:
            languages = ([source_language] if source_language else ("en", "de", "fa", "ar")) \
                if ARTIFACT_SCOPES[target] == "per_language" else (None,)
        for language in languages:
            key = f"{target}:{language or 'shared'}"
            if key not in actual_set:
                reason = NO_EFFECT_REASONS.get((reason_key_source, target))
                if source_type == "visual_asset" and asset_used is True:
                    reason = (
                        "the used asset bytes are consumed by rendering; the visual "
                        "selection/plan metadata remains unchanged"
                    )
                elif source_type == "visual_asset" and asset_used is False:
                    reason = (
                        "the changed asset was not referenced by any saved production "
                        "script shot"
                    )
                if reason is None:
                    reason = (
                        "direct consumer remained snapshot-identical; the changed source "
                        "field is not read by this producer request in this fixture"
                    )
                edges.append({
                    "from": from_label,
                    "to": key,
                    "reason": reason,
                })
    return edges


def _first_file(root: Path, prefix: str | None = None) -> Path | None:
    paths = [p for p in sorted(root.rglob("*")) if p.is_file()]
    if prefix:
        paths = [p for p in paths if p.name.startswith(prefix)]
    return paths[0] if paths else None


def _asset_file(run: Path, asset: VisualAsset) -> Path:
    """Resolve a restored asset within this test's copied output tree."""
    matches = sorted((run / "files").rglob(f"{asset.asset_code}.*"))
    assert matches, f"no copied file for visual asset {asset.asset_code}"
    return matches[0]


def prepare_phase5(db, run: Path, source: str) -> dict:
    """Prepare one source edit, then reset all derived rows as Phase 4 does."""
    before = observe(db, run / "files")
    rows = db.query(Artifact).all()
    source_type, source_language = PHASE5_TYPES.get(source, (None, None))
    selected = (
        [
            r
            for r in rows
            if r.type == source_type and (source_language is None or r.language == source_language)
        ]
        if source_type
        else []
    )
    if source_type and not selected:
        raise AssertionError(f"No registered source for {source}: {source_type}/{source_language}")
    selected = selected[:1]
    predicted = sorted(predicted_change_keys(db, [r.id for r in selected])) if selected else []
    config_sections = CONFIG_SECTIONS.get(source)
    if config_sections:
        predicted = sorted(predicted_change_keys(db, config_sections=config_sections))
    overlay = {"source": source}

    if source == "research_result":
        row = db.query(ResearchResult).first()
        assert row is not None
        row.title = (row.title or "result") + " [soundness]"
        overlay["marker"] = "research-result"
    elif source == "master_story":
        row = (
            db.query(StoryVersion).filter_by(kind="final").order_by(StoryVersion.id.desc()).first()
        )
        assert row is not None
        _append_master_story(row, "Soundness master edit.")
        overlay["marker"] = "master-story"
    elif source == "one_beat":
        row = db.query(EditorialBlueprint).order_by(EditorialBlueprint.id.desc()).first()
        assert row is not None
        data = json.loads(row.blueprint_json or "{}")
        data.setdefault("beats", [])[0]["summary"] = "Soundness beat edit."
        row.blueprint_json = json.dumps(data, ensure_ascii=False)
        overlay["marker"] = "beat"
    elif source == "one_voice_take":
        row = (
            db.query(VoicePerformance)
            .filter_by(language="de")
            .order_by(VoicePerformance.id.desc())
            .first()
        )
        assert row is not None
        row.performance_json = _change_json_text(row.performance_json, "voice-take")
        overlay["marker"] = "voice-take"
    elif source == "host_plan":
        row = db.query(HostPlan).order_by(HostPlan.id.desc()).first()
        assert row is not None
        row.plan_json = _change_json_text(row.plan_json, "host-plan")
        overlay["marker"] = "host-plan"
    elif source in {"unused_visual", "used_visual"}:
        plans = db.query(VisualAsset).filter(VisualAsset.case_id == 1).all()
        used = _asset_codes_from_scripts(db)
        candidates = [a for a in plans if (source == "used_visual") == (a.asset_code in used)]
        asset = candidates[0] if candidates else None
        assert asset is not None
        path = _asset_file(run, asset)
        # A trailing byte changes the saved asset hash without depending on
        # JPEG quantization (solid map tiles can otherwise re-encode byte for
        # byte). JPEG readers accept it, so used-asset renders remain valid
        # while the perturbation is unambiguous.
        changed_bytes = path.read_bytes() + b"\x00"
        path.write_bytes(changed_bytes)
        overlay.update({"asset_code": asset.asset_code, "image_hex": changed_bytes.hex()})
    elif source == "music_history_library":
        path = _first_file(run / "files" / "lib")
        assert path is not None
        raw = path.read_bytes()
        overlay.update(
            {"library_name": path.name, "library_hex": (bytes((raw[0] ^ 1,)) + raw[1:]).hex()}
        )
    elif source == "logo_bytes":
        from PIL import Image

        from app.core.ai_config import ai_config

        original = Path(ai_config.channels["en"].logo)
        altered = run.parent / f"{run.name}-logo.png"
        # Change decoded pixels, not only an ignored trailing PNG byte.  The
        # latter changes a file hash but need not change the rendered intro.
        image = Image.open(original).convert("RGBA")
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, max(1, width // 5), max(1, height // 5)),
                       fill=(255, 0, 0, 255))
        image.save(altered, format="PNG")
        overlay["logo_path"] = str(altered)
    elif source == "naming":
        case = db.query(Case).one()
        case.canonical_title = (case.canonical_title or "case") + " — soundness"
        overlay["approved_titles"] = {
            language: f"{language} soundness title"
            for language in ("en", "de", "fa", "ar")
        }
    elif source in {"used_visual_metadata", "unused_visual_metadata"}:
        assets = db.query(VisualAsset).filter(VisualAsset.case_id == 1).all()
        used = _asset_codes_from_scripts(db)
        want_used = source == "used_visual_metadata"
        candidates = [a for a in assets if (a.asset_code in used) == want_used]
        asset = candidates[0] if candidates else None
        assert asset is not None, f"no {'used' if want_used else 'unused'} asset"
        overlay.update({
            "asset_code": asset.asset_code,
            "metadata": {
                "title": (asset.title or asset.asset_code) + " metadata edit",
                "description": (asset.description or "asset") + " metadata edit",
                "rights_status": "public_domain" if asset.rights_status != "public_domain" else "owned",
                "verification_status": "needs_review" if asset.verification_status != "needs_review" else "verified",
            },
        })
        for field, value in overlay["metadata"].items():
            setattr(asset, field, value)
    elif source not in PHASE5_TYPES:
        raise ValueError(source)
    db.commit()
    edited = observe(db, run / "files")
    changed = sorted(k for k in before["hashes"] if before["hashes"][k] != edited["hashes"][k])
    if source_type:
        expected = f"{source_type}:{source_language or 'shared'}"
        assert changed == [expected], (source, changed, expected)
    else:
        changed = [f"config:{source}"]
    overlay["source_changed"] = changed

    tables = inspect(db.get_bind()).get_table_names()
    for table in reversed(tables):
        preserved = {"cases", "alembic_version"}
        if os.environ.get("SOUNDNESS_FROM_ZERO") != "1":
            preserved.add("facts")
        if table in preserved:
            continue
        if table == "story_versions":
            db.execute(text("DELETE FROM story_versions WHERE kind != 'master'"))
        else:
            db.execute(text(f'DELETE FROM "{table}"'))
    db.commit()
    db.expunge_all()
    (run / "files").rename(run / "baseline-files")
    (run / "files").mkdir()
    (run / "overlay.json").write_text(json.dumps(overlay, ensure_ascii=False))
    return {
        "baseline": before,
        "source_changed": changed,
        "predicted": predicted,
        "overlay": overlay,
    }


def install_phase5(monkeypatch):
    """Apply one Phase 5 source after its producer, or config before the run."""
    import atexit

    from config_read_recorder import install as install_config_read_recorder

    from app.core.ai_config import ai_config
    from app.documentary.pipeline.base import StageRunner

    _, restore_config_reads = install_config_read_recorder(
        ai_config,
        Path(os.environ.get("SOUNDNESS_RUN_ROOT", ".")) / "config_reads.json",
    )
    atexit.register(restore_config_reads)

    overlay = json.loads(Path(os.environ["SOUNDNESS_REPLAY"]).read_text())
    source = overlay["source"]
    original = StageRunner._stage
    trace = []
    applied = []

    def config_edit(name):
        if name == "language_profile":
            ai_config.spoken.max_sentence_words["en"] += 1
        elif name == "pronunciation_voice":
            ai_config.pronunciation.pad_seconds += 0.01
        elif name == "channel_identity_logo":
            ai_config.channels["en"].name += " soundness"
        elif name == "logo_bytes":
            ai_config.channels["en"].logo = overlay["logo_path"]
        elif name == "intro_design":
            import app.documentary.intros as intros

            intros.VERSION += 1
        elif name == "agent_model_routing":
            provider = ai_config.generation_providers["apimaster"]
            current = provider.routing["chapter_writer"]
            provider.routing["chapter_writer"] = next(
                alias for alias in provider.models if alias != current
            )
        elif name.startswith("typed_config_"):
            section = name.removeprefix("typed_config_")
            targets = {
                "documentary": (ai_config.documentary, "pilot_seconds", 1),
                "render": (ai_config.render, "fps", 1),
                "chapters": (ai_config.chapters, "max_title_chars", 1),
                "visual": (ai_config.visual_search, "max_candidates_per_query", 1),
                "voice": (ai_config.voice, "between_blocks_ms", 1),
                "audio": (ai_config.audio_direction, "music_lead_seconds", 0.01),
                "host": (ai_config.host, "word_tolerance", 0.01),
                "music": (ai_config.music_library, "prompt_influence", 0.01),
            }
            obj, field, delta = targets[section]
            setattr(obj, field, getattr(obj, field) + delta)
        else:
            raise ValueError(name)

    if source in {
        "language_profile",
        "pronunciation_voice",
        "channel_identity_logo",
        "logo_bytes",
        "intro_design",
        "agent_model_routing",
        "typed_config_documentary",
        "typed_config_render",
        "typed_config_chapters",
        "typed_config_visual",
        "typed_config_voice",
        "typed_config_audio",
        "typed_config_host",
        "typed_config_music",
    }:
        config_edit(source)

    async def stage(self, name, fn):
        assert ai_config.production_policies.incremental.mode == "off"

        async def run():
            result = await fn()
            trace.append(name)
            db = self.db
            if source == "research_result" and name == "research":
                row = db.query(ResearchResult).order_by(ResearchResult.id.desc()).first()
                row.title = (row.title or "result") + " [soundness]"
                db.commit()
                applied.append(name)
            elif source == "master_story" and name == "master_story":
                row = (
                    db.query(StoryVersion)
                    .filter_by(kind="final")
                    .order_by(StoryVersion.id.desc())
                    .first()
                )
                _append_master_story(row, "Soundness master edit.")
                db.commit()
                applied.append(name)
            elif source == "one_beat" and name == "blueprint":
                row = db.query(EditorialBlueprint).order_by(EditorialBlueprint.id.desc()).first()
                data = json.loads(row.blueprint_json or "{}")
                data["beats"][0]["summary"] = "Soundness beat edit."
                row.blueprint_json = json.dumps(data, ensure_ascii=False)
                db.commit()
                applied.append(name)
            elif source == "host_plan" and name == "host_plan":
                row = db.query(HostPlan).order_by(HostPlan.id.desc()).first()
                row.plan_json = _change_json_text(row.plan_json, "host-plan")
                db.commit()
                applied.append(name)
            elif source == "naming" and name == "naming":
                from app.identity.titles import sync_identity
                case = db.query(Case).one()
                for language, title in overlay["approved_titles"].items():
                    identity = sync_identity(db, case, language, title=title, revise=True)
                    identity.title_approved = True
                db.commit()
                applied.append(name)
            elif source == "one_voice_take" and name == "performance:de":
                row = (
                    db.query(VoicePerformance)
                    .filter_by(language="de")
                    .order_by(VoicePerformance.id.desc())
                    .first()
                )
                row.performance_json = _change_json_text(row.performance_json, "voice-take")
                db.commit()
                applied.append(name)
            elif source in {"unused_visual", "used_visual"} and name == (
                "visual_plan" if source == "unused_visual" else "visual_research"
            ):
                asset = db.query(VisualAsset).filter_by(asset_code=overlay["asset_code"]).one()
                if overlay.get("image_hex"):
                    _asset_file(Path(os.environ["SOUNDNESS_RUN_ROOT"]).parent, asset).write_bytes(
                        bytes.fromhex(overlay["image_hex"])
                    )
                db.commit()
                applied.append(name)
            elif source in {"used_visual_metadata", "unused_visual_metadata"} and name == (
                "visual_research" if source == "used_visual_metadata" else "visual_plan"
            ):
                asset = db.query(VisualAsset).filter_by(asset_code=overlay["asset_code"]).one()
                for field, value in overlay["metadata"].items():
                    setattr(asset, field, value)
                db.commit()
                applied.append(name)
            elif source == "music_history_library" and name == "voice:en":
                path = Path(os.environ["SOUNDNESS_RUN_ROOT"]) / "lib" / overlay["library_name"]
                path.write_bytes(bytes.fromhex(overlay["library_hex"]))
                applied.append(name)
            return result

        return await original(self, name, run)

    monkeypatch.setattr(StageRunner, "_stage", stage)
    return trace, applied

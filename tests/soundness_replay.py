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

from sqlalchemy import inspect, text

from app.db.models import (Artifact, Case, EditorialBlueprint, HostPlan,
                            ResearchResult, StoryVersion, VisualAsset, VoicePerformance, VisualPlan)
from app.services.soundness import downstream_closure
from soundness_observation import observe


def prepare(db, run: Path, source: str) -> dict:
    assert Path(db.get_bind().url.database).resolve() == (run / "pipeline.db").resolve()
    assert (run.parent / "owned-by-soundness").is_file(), "Refuse to reset an unowned DB"
    before = observe(db, run / "files")
    rows = db.query(Artifact).all()
    source_type = {"spoken_de": "spoken_version", "naming": "naming", "used_visual": "visual_asset"}.get(source)
    candidates = [r for r in rows if r.type == source_type and
                  (source != "spoken_de" or r.language == "de")]
    selected = candidates[:1]
    predicted_ids = downstream_closure(db, [r.id for r in selected])
    predicted = sorted({f"{r.type}:{r.language or 'shared'}" for r in rows if r.id in predicted_ids})
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
        overlay["version"] = {name: getattr(row, name) for name in
                              ("story_text", "narrative_structure", "text_hash")}
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
    assert changed == ([f"{source_type}:{'de' if source == 'spoken_de' else 'shared'}"] if source_type else [])
    evidence = {"baseline": before, "source_changed": changed, "predicted": predicted,
                "overlay": overlay}
    # Only this freshly restored disposable DB is reset; the persistent
    # template and the before-edit snapshot remain untouched.
    tables = inspect(db.get_bind()).get_table_names()
    for table in reversed(tables):
        if table in {"cases", "facts", "alembic_version"}:
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
    selected = [r for r in rows if r.type == source_type and
                (source_language is None or r.language == source_language)] if source_type else []
    if source_type and not selected:
        raise AssertionError(f"No registered source for {source}: {source_type}/{source_language}")
    selected = selected[:1]
    predicted_ids = downstream_closure(db, [r.id for r in selected]) if selected else set()
    predicted = sorted({f"{r.type}:{r.language or 'shared'}" for r in rows
                        if r.id in predicted_ids})
    if not selected and source in PHASE5_TYPES:
        # The shadow input deliberately carries the complete typed config for
        # every registered artifact. There is no persisted config root row, so
        # this is the registry's conservative closure: every registered node
        # is reachable from the changed config input.
        predicted = sorted({f"{r.type}:{r.language or 'shared'}" for r in rows})
    overlay = {"source": source}

    if source == "research_result":
        row = db.query(ResearchResult).first()
        assert row is not None
        row.title = (row.title or "result") + " [soundness]"
        overlay["marker"] = "research-result"
    elif source == "master_story":
        row = db.query(StoryVersion).filter_by(kind="master").order_by(StoryVersion.id.desc()).first()
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
        row = db.query(VoicePerformance).filter_by(language="de").order_by(
            VoicePerformance.id.desc()).first()
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
        used = _asset_codes_from_plan(db.query(VisualPlan).order_by(VisualPlan.id.desc()).first())
        candidates = [a for a in plans if (source == "used_visual") == (a.asset_code in used)]
        asset = candidates[0] if candidates else None
        assert asset is not None
        path = _asset_file(run, asset)
        from PIL import Image

        image = Image.open(path).convert("RGB")
        pixel = image.getpixel((0, 0))
        image.putpixel((0, 0), tuple((value + 1) % 256 for value in pixel))
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        changed_bytes = buffer.getvalue()
        path.write_bytes(changed_bytes)
        overlay.update({"asset_code": asset.asset_code,
                        "image_hex": changed_bytes.hex()})
    elif source == "music_history_library":
        path = _first_file(run / "files" / "lib")
        assert path is not None
        raw = path.read_bytes()
        overlay.update({"library_name": path.name,
                        "library_hex": (bytes((raw[0] ^ 1,)) + raw[1:]).hex()})
    elif source == "logo_bytes":
        from app.core.ai_config import ai_config

        original = Path(ai_config.channels["en"].logo)
        altered = run.parent / f"{run.name}-logo.png"
        altered.write_bytes(original.read_bytes() + b"\x00")
        overlay["logo_path"] = str(altered)
    elif source == "naming":
        case = db.query(Case).one()
        case.canonical_title = (case.canonical_title or "case") + " — soundness"
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
        if table in {"cases", "facts", "alembic_version"}:
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
    return {"baseline": before, "source_changed": changed, "predicted": predicted,
            "overlay": overlay}


def install_phase5(monkeypatch):
    """Apply one Phase 5 source after its producer, or config before the run."""
    from app.core.ai_config import ai_config
    from app.documentary.pipeline.base import StageRunner
    from app.db.models import VisualPlan

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
            ai_config.generation_providers["apimaster"].routing["chapter_writer"] = \
                ai_config.generation_providers["apimaster"].routing["chapter_writer"] + "_soundness"
        elif name.startswith("typed_config_"):
            section = name.removeprefix("typed_config_")
            targets = {
                "documentary": (ai_config.documentary, "pilot_seconds", 1),
                "render": (ai_config.render, "fps", 1),
                "chapters": (ai_config.chapters, "max_title_chars", 1),
                "visual": (ai_config.visual_search, "max_candidates_per_query", 1),
                "voice": (ai_config.voice, "between_blocks_ms", 1),
                "audio": (ai_config.audio_direction, "music_lead_seconds", 0.01),
                "host": (ai_config.host, "pace_factor", 0.01),
                "music": (ai_config.music_library, "prompt_influence", 0.01),
            }
            obj, field, delta = targets[section]
            setattr(obj, field, getattr(obj, field) + delta)
        else:
            raise ValueError(name)

    if source in {"language_profile", "pronunciation_voice", "channel_identity_logo",
                  "logo_bytes", "intro_design", "agent_model_routing",
                  "typed_config_documentary", "typed_config_render",
                  "typed_config_chapters", "typed_config_visual", "typed_config_voice",
                  "typed_config_audio", "typed_config_host", "typed_config_music"}:
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
                db.commit(); applied.append(name)
            elif source == "master_story" and name == "master_story":
                row = db.query(StoryVersion).filter_by(kind="master").order_by(StoryVersion.id.desc()).first()
                _append_master_story(row, "Soundness master edit.")
                db.commit(); applied.append(name)
            elif source == "one_beat" and name == "blueprint":
                row = db.query(EditorialBlueprint).order_by(EditorialBlueprint.id.desc()).first()
                data = json.loads(row.blueprint_json or "{}"); data["beats"][0]["summary"] = "Soundness beat edit."
                row.blueprint_json = json.dumps(data, ensure_ascii=False); db.commit(); applied.append(name)
            elif source == "host_plan" and name == "host_plan":
                row = db.query(HostPlan).order_by(HostPlan.id.desc()).first()
                row.plan_json = _change_json_text(row.plan_json, "host-plan")
                db.commit(); applied.append(name)
            elif source == "one_voice_take" and name == "performance:de":
                row = db.query(VoicePerformance).filter_by(language="de").order_by(VoicePerformance.id.desc()).first()
                row.performance_json = _change_json_text(row.performance_json, "voice-take")
                db.commit(); applied.append(name)
            elif source in {"unused_visual", "used_visual"} and name == "visual_research":
                asset = db.query(VisualAsset).filter_by(asset_code=overlay["asset_code"]).one()
                if overlay.get("image_hex"):
                    _asset_file(Path(os.environ["SOUNDNESS_RUN_ROOT"]).parent, asset).write_bytes(
                        bytes.fromhex(overlay["image_hex"]))
                db.commit(); applied.append(name)
            elif source == "music_history_library" and name == "voice:en":
                path = Path(os.environ["SOUNDNESS_RUN_ROOT"]) / "lib" / overlay["library_name"]
                path.write_bytes(bytes.fromhex(overlay["library_hex"])); applied.append(name)
            return result

        return await original(self, name, run)

    monkeypatch.setattr(StageRunner, "_stage", stage)
    return trace, applied

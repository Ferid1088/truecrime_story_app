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

from app.db.models import Artifact, Case, StoryVersion, VisualAsset
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

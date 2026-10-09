"""Every step is saved before the next one starts, and a stopped or failed
step can be tried again: stage bookkeeping (attempts, errors, degraded),
recovery after a restart, resume, atomic files for paid results."""
import asyncio
import json
import shutil
import subprocess
import uuid
from types import SimpleNamespace

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, DocumentaryJob, HostScene, MonitorRun, ResearchJob
from app.documentary import storage
from app.documentary.jobs import DocumentaryPipeline
from app.documentary.recovery import recover_after_restart
from app.utils import utc_now

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def _case(db) -> Case:
    title = f"Trace {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _job(db, case, stages=None, status="running") -> DocumentaryJob:
    job = DocumentaryJob(case_id=case.id, status=status,
                         stages_json=json.dumps(stages or []))
    db.add(job)
    db.commit()
    return job


# ---------------------------------------------------------------------------
# stage bookkeeping
# ---------------------------------------------------------------------------


def test_stage_keeps_attempts_and_the_failures_of_earlier_attempts(db_session):
    job = _job(db_session, _case(db_session))
    p = DocumentaryPipeline(db_session, job)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider timed out")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        asyncio.run(p._stage("voice:en", flaky))
    st = json.loads(job.stages_json)[0]
    assert st["status"] == "failed" and st["attempts"] == 1
    assert st["error_type"] == "RuntimeError" and st["finished_at"]
    assert st["errors"][0]["message"] == "provider timed out"

    p._entry("voice:en")["status"] = "pending"     # what resume does
    asyncio.run(p._stage("voice:en", flaky))
    st = json.loads(job.stages_json)[0]
    assert st["status"] == "done" and st["attempts"] == 2
    assert "error_type" not in st
    assert st["errors"][0]["message"] == "provider timed out"   # history stays


def test_degraded_stage_is_saved_visible_and_not_redone_in_the_same_run(db_session):
    job = _job(db_session, _case(db_session))
    p = DocumentaryPipeline(db_session, job)
    calls = {"n": 0}

    async def weak():
        calls["n"] += 1
        return {"checked": 4, "errors": 2, "degraded": "2 vision checks failed"}

    asyncio.run(p._stage("visual_check", weak))
    asyncio.run(p._stage("visual_check", weak))
    assert calls["n"] == 1
    db_session.refresh(job)
    st = json.loads(job.stages_json)[0]
    assert st["status"] == "degraded"
    assert json.loads(job.result_json)["degraded"] == {"visual_check": "2 vision checks failed"}
    assert job.progress == 1.0


def test_a_stage_from_a_crashed_process_runs_again(db_session):
    stages = [{"name": "blueprint", "status": "done", "detail": {"blueprint_id": 1}},
              {"name": "audio_plan", "status": "running", "detail": None, "attempts": 1}]
    job = _job(db_session, _case(db_session), stages)
    rep = recover_after_restart(db_session)
    assert job.id in rep["documentary_jobs"]
    db_session.refresh(job)
    assert job.status == "interrupted" and job.stage is None
    st = {s["name"]: s for s in json.loads(job.stages_json)}
    assert st["blueprint"]["status"] == "done"
    assert st["audio_plan"]["status"] == "pending"
    assert st["audio_plan"]["errors"][-1]["type"] == "Interrupted"

    p = DocumentaryPipeline(db_session, job)
    ran = []

    async def blueprint():
        ran.append("blueprint")
        return {}

    async def audio_plan():
        ran.append("audio_plan")
        return {"audio_plan_id": 2}

    asyncio.run(p._stage("blueprint", blueprint))
    asyncio.run(p._stage("audio_plan", audio_plan))
    assert ran == ["audio_plan"]          # the saved stage is not paid again
    assert {s["name"]: s for s in p.stages}["audio_plan"]["attempts"] == 2


# ---------------------------------------------------------------------------
# recovery after a restart
# ---------------------------------------------------------------------------


def test_recovery_marks_everything_left_running(db_session):
    case = _case(db_session)
    cancelling = _job(db_session, case, status="cancelling")
    done = _job(db_session, case, status="completed")
    run = MonitorRun(status="running")
    video = ResearchJob(job_type="video_research", status="running", case_id=case.id)
    web = ResearchJob(job_type="research", status="running", case_id=case.id,
                      external_job_id="remote-1")
    scene = HostScene(case_id=case.id, story_version_id=-1, host_segments_id=-1,
                      host_segment_id="S1", language="en", channel="ClueVera",
                      studio_profile_id="STUDIO_EN", studio_asset_id="STUDIO_EN_02_FRONT_MEDIUM",
                      framing_preset="HOST_MEDIUM", status="voice_ready",
                      running_since=utc_now())
    db_session.add_all([run, video, web, scene])
    db_session.commit()

    recover_after_restart(db_session)
    for row in (cancelling, done, run, video, web, scene):
        db_session.refresh(row)
    assert cancelling.status == "cancelled"
    assert done.status == "completed"
    assert run.status == "failed" and run.finished_at is not None
    assert video.status == "failed"
    assert web.status == "running"          # remote job: polled again, not lost
    assert scene.running_since is None and scene.status == "voice_ready"
    assert json.loads(scene.history_json)[-1]["outcome"] == "interrupted"


def test_an_interrupted_job_resumes_and_keeps_its_saved_stages(client, db_session, monkeypatch):
    from app.documentary import jobs as J

    launched = []
    monkeypatch.setattr(J, "launch", lambda job_id: launched.append(job_id))
    stages = [{"name": "blueprint", "status": "done", "detail": {}},
              {"name": "visual_gaps", "status": "degraded", "detail": {"degraded": "x"}},
              {"name": "voice:en", "status": "failed", "detail": "boom"},
              {"name": "render:en", "status": "blocked", "detail": "voice failed"}]
    job = _job(db_session, _case(db_session), stages, status="interrupted")
    r = client.post(f"/api/documentary/jobs/{job.id}/resume")
    assert r.status_code == 200, r.text
    assert launched == [job.id]
    st = {s["name"]: s["status"] for s in r.json()["stages"]}
    assert st == {"blueprint": "done", "visual_gaps": "degraded",
                  "voice:en": "pending", "render:en": "pending"}


# ---------------------------------------------------------------------------
# files: written whole or not at all
# ---------------------------------------------------------------------------


def test_write_atomic_leaves_no_partial_file(tmp_path):
    p = tmp_path / "a" / "take.json"
    storage.write_atomic(p, "{}")
    storage.write_atomic(p, b'{"v": 2}')
    assert json.loads(p.read_text()) == {"v": 2}
    assert [f.name for f in p.parent.iterdir()] == ["take.json"]


def test_research_that_saved_no_facts_stops_before_the_story(db_session, monkeypatch):
    from app.documentary.jobs import research_case
    from app.services import research_jobs

    async def start(db, case):
        return SimpleNamespace(id=99, status="completed", error=None, sources_accepted=0)

    monkeypatch.setattr(research_jobs, "start_research_job", start)
    with pytest.raises(RuntimeError, match="no facts"):
        asyncio.run(research_case(db_session, _case(db_session), lambda: None))


@needs_ffmpeg
def test_a_corrupt_voice_sidecar_is_paid_again_not_trusted(tmp_path, monkeypatch):
    from test_voice_render import TEXT_A, FakeTTS, _block, _render

    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    tts = FakeTTS()
    blocks = [_block("EN_a_01", "act1", TEXT_A)]
    _render(VoiceRenderer(provider=tts, use_asr=False), blocks)
    assert len(tts.requests) == 1
    _render(VoiceRenderer(provider=tts, use_asr=False), blocks)
    assert len(tts.requests) == 1              # saved take reused
    sidecar = next(tmp_path.glob("**/EN_a_01__*.json"))
    sidecar.write_text('{"block_id": "EN_a_01", "alignm')   # cut off mid-write
    m = _render(VoiceRenderer(provider=tts, use_asr=False), blocks)
    assert len(tts.requests) == 2
    assert m["blocks"][0]["cache_hit"] is False
    assert json.loads(sidecar.read_text())["alignment"]["characters"]
    assert not list(tmp_path.glob("**/*.part"))


@needs_ffmpeg
def test_paid_music_survives_a_failed_normalization(tmp_path, monkeypatch):
    from test_spoken_audio import _sine_mp3

    from app.documentary import audio as A
    from app.documentary.music import MusicLibrary

    monkeypatch.setattr(ai_config.music_library, "dir", str(tmp_path / "lib"))

    class Sound:
        name = "fake_sound"

        def __init__(self):
            self.calls = 0

        async def generate(self, prompt, seconds, loop, influence):
            self.calls += 1
            return _sine_mp3(1.0, 330), 40

    sound = Sound()
    lib = MusicLibrary(provider=sound)
    path = lib.dir / "bed_tense_1__abc.wav"
    real = A.normalize_loudness

    def broken(*a, **k):
        raise RuntimeError("ffmpeg killed")

    monkeypatch.setattr(A, "normalize_loudness", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(lib._generate("tense bed", 1.0, True, path))
    assert sound.calls == 1 and path.with_suffix(".mp3").exists() and not path.exists()
    assert not list(lib.dir.glob("*.part*"))

    monkeypatch.setattr(A, "normalize_loudness", real)
    cost = asyncio.run(lib._generate("tense bed", 1.0, True, path))
    assert sound.calls == 1 and cost == 0          # not paid twice
    assert path.exists() and path.stat().st_size > 0


@needs_ffmpeg
def test_an_interrupted_render_leaves_no_film_under_the_final_name(tmp_path):
    from app.documentary.render.engine import VideoRenderer

    wav = tmp_path / "doc.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=2", str(wav)], check=True)
    script = {"language": "en", "duration": 2.0, "audio": {"path": str(wav)},
              "shots": [{"index": 0, "start": 0, "end": 2.0, "kind": "black",
                         "motion": "NONE"}],
              "subtitles": [{"start": 0.1, "end": 1.5, "text": "Night."}]}
    out = tmp_path / "film.mp4"

    def stop(frac, msg):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        VideoRenderer(160, 90).render(script, out, progress=stop)
    assert not out.exists()
    assert not [f for f in tmp_path.iterdir() if ".part" in f.name]

    info = VideoRenderer(160, 90).render(script, out)
    assert out.exists() and out.stat().st_size > 0 and info["frames"] > 0
    assert not [f for f in tmp_path.iterdir() if ".part" in f.name]

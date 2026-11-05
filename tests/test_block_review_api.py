"""The routes of the hand review: open, correct, send, tick, listen, assemble, approve, and the status the Monitoring shortcut reads."""

from __future__ import annotations

from pathlib import Path

from test_block_review_service import narration  # noqa: F401  (the fixture)

from app.core.ai_config import ai_config
from app.documentary.block_review import service as S

V = "/api/documentary/versions/{}/block-review"


def test_the_whole_review_through_the_routes(client, narration, monkeypatch):  # noqa: F811
    db, case, version, voice = narration
    monkeypatch.setattr(S, "default_provider", lambda: voice)
    root = Path(ai_config.voice.work_dir).resolve()
    monkeypatch.setattr("app.core.files.allowed_roots", lambda: [root])          # the files are served only from the data folders: the scratch folder is one here
    base = V.format(version.id)
    status = client.get(f"/api/cases/{case.id}/voice-review-status").json()
    assert status == {"ready": True, "version_id": version.id, "language": "fa", "approved": False, "blink": True, "review_open": False}
    assert client.get(base).json() == {"exists": False, "version_id": version.id, "can_open": True}
    assert client.put(f"{base}/blocks/FA_B02_g01_01", json={"words": []}).status_code == 409, "the review must be opened first"
    opened = client.post(f"{base}/open").json()
    assert opened["status"] == "open" and [b["block_id"] for b in opened["blocks"]][0] == "FA_B01_g01_01" and opened["corrected_blocks"] == 0
    assert client.get(f"/api/cases/{case.id}/voice-review-status").json()["review_open"] is True

    edited = client.put(f"{base}/blocks/FA_B02_g01_01", json={"words": [{"n": 0, "surface": "دُورِ", "finglish": "dore"}]}).json()
    assert edited["edited"] and edited["needs_sending"] and edited["text"] == "دُورِ تخت نبود." and edited["voice_text"] == "دورِ تخت نبود."
    assert [(w["surface"], w["corrected"], w["finglish"]) for w in edited["words"]][:2] == [("دُورِ", True, "dore"), ("تخت", False, "")]
    assert client.put(f"{base}/blocks/FA_B02_g01_01", json={"words": [{"n": 99, "surface": "x"}]}).status_code == 422
    assert client.get(base).json()["to_send"] == 1

    sent = client.post(f"{base}/blocks/FA_B02_g01_01/send").json()
    assert [(t["take_no"], t["active"]) for t in sent["takes"]] == [(1, False), (2, True)] and not sent["needs_sending"] and sent["edited"]
    assert client.get(sent["takes"][1]["audio_url"]).status_code == 200 and client.get(sent["takes"][0]["audio_url"]).status_code == 200
    assert client.post(f"{base}/blocks/FA_B02_g01_01/send").status_code == 409
    assert client.post(f"{base}/blocks/FA_B02_g01_01/takes/1/activate").json()["takes"][0]["active"] is True
    assert client.post(f"{base}/blocks/FA_B02_g01_01/takes/7/activate").status_code == 409
    client.post(f"{base}/blocks/FA_B02_g01_01/takes/2/activate")

    client.put(f"{base}/blocks/FA_B03_g01_01", json={"text": "بعد رفت و آمد."})
    everything = client.post(f"{base}/send-all").json()
    assert everything["sent"] == ["FA_B03_g01_01"] and everything["failed"] == [] and everything["to_send"] == 0

    assert client.post(f"{base}/approve", json={}).status_code == 409, "nothing is assembled yet"
    assembled = client.post(f"{base}/assemble").json()
    assert assembled["assembled_current"] and assembled["blocks"][1]["takes"][1]["active"]
    assert client.get(assembled["assembled_audio_url"]).status_code == 200
    approved = client.post(f"{base}/approve", json={"note": "listened"}).json()
    assert approved["status"] == "approved" and approved["approved"] is True
    final = client.get(f"/api/cases/{case.id}/voice-review-status").json()
    assert final["approved"] is True and final["blink"] is False

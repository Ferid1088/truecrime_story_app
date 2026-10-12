"""Request comparison must not normalize away changed ASR inputs."""

import json

from app.services.soundness import saved_request_snapshot


def test_asr_snapshot_orders_by_block_identity_not_payload(tmp_path):
    path = tmp_path / "requests.jsonl"
    entries = [
        {
            "provider": "asr",
            "stage": "transcribe",
            "language": "de",
            "request_identity": f"block-{i}:seed=1",
            "payload": {"audio": value},
        }
        for i, value in enumerate(("z", "a"))
    ]

    def snapshot(records):
        path.write_text("\n".join(json.dumps(entry) for entry in records))
        return saved_request_snapshot(path)

    before = snapshot(entries)
    assert snapshot(entries[::-1]) == before
    entries[0]["payload"], entries[1]["payload"] = entries[1]["payload"], entries[0]["payload"]
    assert snapshot(entries) != before  # same multiset, wrong block association

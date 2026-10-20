from app.shortform.asset_store import SharedAssetStore, asset_key


def test_same_inputs_give_the_same_key():
    a = asset_key("end_card", "en", {"channel": "ClueVera"}, 1)
    b = asset_key("end_card", "en", {"channel": "ClueVera"}, 1)
    assert a == b


def test_different_language_gives_a_different_key():
    a = asset_key("end_card", "en", {"channel": "ClueVera"}, 1)
    b = asset_key("end_card", "de", {"channel": "ClueVera"}, 1)
    assert a != b


def test_first_call_creates_second_call_reuses(db_session, tmp_path):
    store = SharedAssetStore(db_session, root=tmp_path)
    calls = []

    def create(path):
        calls.append(path)
        path.write_bytes(b"asset-bytes")

    path1, created1 = store.get_or_create("end_card", "en", {"x": 1}, 1, create)
    path2, created2 = store.get_or_create("end_card", "en", {"x": 1}, 1, create)

    assert created1 is True
    assert created2 is False
    assert path1 == path2
    assert len(calls) == 1, "the creator must run exactly once for the same key"


def test_use_count_tracks_every_lookup(db_session, tmp_path):
    store = SharedAssetStore(db_session, root=tmp_path)

    def create(path):
        path.write_bytes(b"x")

    store.get_or_create("cover", "fa", {}, 1, create)
    store.get_or_create("cover", "fa", {}, 1, create)
    store.get_or_create("cover", "fa", {}, 1, create)
    rows = store.usage("cover")
    assert len(rows) == 1
    assert rows[0]["use_count"] == 3


def test_missing_output_file_is_a_hard_error(db_session, tmp_path):
    import pytest

    store = SharedAssetStore(db_session, root=tmp_path)
    with pytest.raises(RuntimeError):
        store.get_or_create("music", "en", {}, 1, lambda path: None)

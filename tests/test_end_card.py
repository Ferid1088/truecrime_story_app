import pytest

from app.shortform.asset_store import SharedAssetStore
from app.shortform.end_card import H, W, create_end_card_asset, render_end_card


@pytest.mark.parametrize("language", ["en", "de", "fa", "ar"])
def test_end_card_renders_full_frame(language):
    img = render_end_card(language)
    assert img.size == (W, H)


def test_missing_config_is_a_hard_error():
    with pytest.raises(ValueError):
        render_end_card("xx")


def test_end_card_created_once_and_reused_via_shared_store(db_session, tmp_path):
    store = SharedAssetStore(db_session, root=tmp_path)
    calls = []

    def create(path):
        calls.append(path)
        create_end_card_asset(path, "en")

    p1, created1 = store.get_or_create("end_card", "en", {}, 1, create)
    p2, created2 = store.get_or_create("end_card", "en", {}, 1, create)
    assert created1 is True and created2 is False
    assert len(calls) == 1
    assert p1.exists()

from lp_manager.db import Store
from lp_manager.live_positions import _live_display_name
from lp_manager.position_identity import authoritative_label, highest_authoritative_position_number


def test_current_open_positions_continue_after_closed_p1_to_p5(tmp_path):
    assert authoritative_label("ROBINHOOD_CHAIN", "1289953") == "P6"
    assert authoritative_label("ROBINHOOD_CHAIN", "1290067") == "P7"
    assert authoritative_label("ROBINHOOD_CHAIN", "1290077") == "P8"
    assert highest_authoritative_position_number("ROBINHOOD_CHAIN") == 8


def test_new_live_position_starts_at_p9_even_with_stale_old_mapping(tmp_path):
    store = Store(tmp_path / "numbering.sqlite3")
    store.set_setting("live:auto-labels:v087", {
        "1289953": "P4",
        "1290067": "P5",
        "1290077": "P6",
    })

    assert _live_display_name(
        store, "1300000", "WETH/TEST", "ROBINHOOD_CHAIN"
    ) == "P9 · WETH/TEST"

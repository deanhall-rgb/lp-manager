import pytest

pytest.importorskip("web3")


def test_live_web3_modules_import():
    import lp_manager.live_positions as positions
    import lp_manager.live_v3_builder as builder
    assert positions.TRANSFER_TOPIC.startswith("0x")
    assert builder.UINT128_MAX > 0

from lp_manager.chain_registry import CHAINS, registry_status


def test_all_supported_chains_have_read_only_fallbacks():
    expected = {"ETHEREUM", "BASE", "ARBITRUM", "OPTIMISM", "POLYGON", "ROBINHOOD_CHAIN"}
    assert set(CHAINS) == expected
    for cfg in CHAINS.values():
        assert cfg.public_rpc_url.startswith("https://")
        assert cfg.chain_id > 0
        assert cfg.position_manager.startswith("0x")
        assert cfg.factory.startswith("0x")


def test_single_alchemy_key_derives_all_supported_rpc_urls(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "example-key")
    for cfg in CHAINS.values():
        monkeypatch.delenv(cfg.rpc_env, raising=False)
    assert CHAINS["ETHEREUM"].rpc_url() == "https://eth-mainnet.g.alchemy.com/v2/example-key"
    assert CHAINS["BASE"].rpc_url() == "https://base-mainnet.g.alchemy.com/v2/example-key"
    assert CHAINS["ARBITRUM"].rpc_url() == "https://arb-mainnet.g.alchemy.com/v2/example-key"
    assert CHAINS["OPTIMISM"].rpc_url() == "https://opt-mainnet.g.alchemy.com/v2/example-key"
    assert CHAINS["POLYGON"].rpc_url() == "https://polygon-mainnet.g.alchemy.com/v2/example-key"
    assert CHAINS["ROBINHOOD_CHAIN"].rpc_url() == "https://robinhood-mainnet.g.alchemy.com/v2/example-key"


def test_registry_status_never_exposes_rpc_url_or_key(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "super-secret-value")
    text = repr(registry_status())
    assert "super-secret-value" not in text
    assert "public_rpc_url" not in text

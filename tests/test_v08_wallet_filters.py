from lp_manager.wallet import _spam_reason


def test_wallet_hides_promotional_claim_tokens_but_not_unknown_legit_symbols():
    assert _spam_reason({"symbol":"www.scam.top ✅ claim","price_usd":0,"value_usd":0}, min_visible_value=1) == "SUSPICIOUS_TOKEN_NAME"
    assert _spam_reason({"symbol":"DELTA","type":"ERC20","price_usd":0,"value_usd":0}, min_visible_value=1) == "NO_LIVE_PRICE"


def test_wallet_hides_priced_dust_below_threshold():
    assert _spam_reason({"symbol":"DUST","price_usd":0.5,"value_usd":0.4}, min_visible_value=1).startswith("VALUE_BELOW_")
    assert _spam_reason({"symbol":"PONS","price_usd":0.5,"value_usd":128}, min_visible_value=1) is None

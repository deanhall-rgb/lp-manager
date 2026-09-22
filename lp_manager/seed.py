from __future__ import annotations

import time

from .db import Store
from .models import Position


def seed_demo(store: Store) -> None:
    if store.count_positions() > 0:
        return
    now = time.time()
    rows = [
        Position(
            id="demo-delta-weth-1", protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair="DELTA/WETH", status="OPEN",
            lower_price=14.75, upper_price=21.50, current_price=15.80, capital_value=1000.0, current_value=1018.5,
            unclaimed_fees=31.42, fees_today=8.42, fees_7d=49.15, fees_30d=63.19, realised_fees=12.0,
            estimated_il=-7.8, gas_costs=2.4, apr_current=74.0, apr_7d=61.0, opened_at=now - 4.5*86400,
            source="demo", notes="Seeded example using the recent DELTA range discussion.",
            strategy_sleeve="TACTICAL_CAMPAIGN", directional_bias="BULLISH", inventory_intent="ACCUMULATE_RISK_ASSET",
            target_hold_days=3.0, monitoring_class="ACTIVE",
        ),
        Position(
            id="demo-weth-usdc-1", protocol="UNISWAP_V3", chain="BASE", pair="WETH/USDC", status="OPEN",
            lower_price=3900.0, upper_price=4700.0, current_price=4380.0, capital_value=1250.0, current_value=1261.0,
            unclaimed_fees=12.10, fees_today=2.86, fees_7d=18.70, fees_30d=44.60, realised_fees=23.0,
            estimated_il=-3.2, gas_costs=1.6, apr_current=28.0, apr_7d=31.0, opened_at=now - 12*86400,
            source="demo", strategy_sleeve="CORE_INCOME", directional_bias="BULLISH",
            inventory_intent="ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE", target_hold_days=30.0, monitoring_class="LOW_TOUCH",
        ),
    ]
    for row in rows:
        store.upsert_position(row)

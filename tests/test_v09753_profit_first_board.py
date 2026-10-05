import time
from pathlib import Path

from lp_manager.deep_analysis_rotation import DeepAnalysisRotationService
from lp_manager.live_scout import preliminary_pool_evaluation
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard, _pair_family
from lp_manager.opportunity_score import SCORE_VERSION, score_opportunity
from lp_manager.portfolio_advisor import rank_opportunities


class _Store:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def list_forecast_snapshots(self, limit=500):
        return []

    def list_opportunities(self, limit=1000):
        return []


class _Universe:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def cached(self, chain):
        return self.snapshots.get(chain, {"ok": False, "summary": {}, "shortlist": []})


def _candidate(pair, address, score=70, tvl=1_000_000, volume=1_000_000):
    return {
        "pair": pair,
        "pool_address": address,
        "protocol": "UNISWAP_V3",
        "version": "V3",
        "research_ready": True,
        "economic_validation": "CROSS_VALIDATED",
        "profit_lab_readiness": {"ready": True, "status": "READY", "label": "PROFIT READY"},
        "tvl_usd": tvl,
        "volume_24h_usd": volume,
        "gross_fee_apr_proxy": 25,
        "turnover_24h": volume / tvl,
        "discovery_score": score,
    }


def _snapshot(chain, rows):
    return {
        "ok": True,
        "chain": chain,
        "generated_at": time.time(),
        "summary": {
            "v3_discovered": len(rows),
            "shortlisted": len(rows),
            "live_validated": len(rows),
            "research_ready": len(rows),
            "profit_lab_ready": len(rows),
        },
        "shortlist": rows,
    }


def test_pair_family_collapses_wrappers_stables_orientation_and_chains():
    assert _pair_family("USDC/WETH") == _pair_family("WETH/USDT")
    assert _pair_family("WBTC/WETH") == _pair_family("ETH/BTC")
    assert _pair_family("WPOL/USDT") == _pair_family("USDC/MATIC")
    assert _pair_family("NVDAon/USDC") != _pair_family("SPCXON/USDC")


def test_leaderboard_gives_one_seat_to_equivalent_trade_family():
    store = _Store()
    universe = _Universe({
        "ETHEREUM": _snapshot("ETHEREUM", [
            _candidate("WETH/USDC", "0x" + "1" * 40, 80, 100_000_000, 20_000_000),
        ]),
        "ARBITRUM": _snapshot("ARBITRUM", [
            _candidate("USDT/WETH", "0x" + "2" * 40, 75, 30_000_000, 15_000_000),
        ]),
        "BASE": _snapshot("BASE", [
            _candidate("NVDAon/USDC", "0x" + "3" * 40, 72, 300_000, 1_500_000),
        ]),
    })
    result = OpportunityLeaderboard(store, universe).rebuild(
        chains=["ETHEREUM", "ARBITRUM", "BASE"], limit=25,
    )

    families = [row["pair_family"] for row in result["rows"]]
    assert families.count("ETH/USD") == 1
    assert "NVDAON/USD" in families
    assert result["funnel"]["equivalent_alternatives_hidden"] == 1
    eth_usd = next(row for row in result["rows"] if row["pair_family"] == "ETH/USD")
    assert eth_usd["equivalent_alternatives_hidden"] == 1


def _score_row(net_7d, *, tvl, volume):
    return {
        "pair": "AAA/USDC",
        "chain": "ETHEREUM",
        "pool_address": "0x" + "a" * 40,
        "tvl_usd": tvl,
        "volume_24h_usd": volume,
        "turnover_24h": volume / tvl,
        "fee_apr_proxy": 20,
        "economic_validation": "CROSS_VALIDATED",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "freshness": {"state": "FRESH"},
        "portfolio_overlap": False,
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 1000,
            "horizon_days": 7,
            "expected_fees_usd": net_7d,
            "expected_net_usd": net_7d,
            "expected_intervention_cost_usd": 0,
            "range_quality_score": 82,
            "economics_evidence_class": "MODELLED_CONCENTRATED_POSITION",
            "economics_confidence": "MODERATE",
        },
    }


def test_profit_first_score_beats_giant_low_return_pool():
    giant = score_opportunity(_score_row(6.0, tvl=100_000_000, volume=20_000_000))
    profitable = score_opportunity(_score_row(80.0, tvl=350_000, volume=1_300_000))

    assert SCORE_VERSION == "OPPORTUNITY_SCORE_V2_PROFIT_FIRST"
    assert profitable["components"]["net_economics"] > giant["components"]["net_economics"]
    assert profitable["score"] > giant["score"]


def test_mature_network_pairs_default_to_core_but_campaign_pair_can_stay_tactical():
    arb = preliminary_pool_evaluation({
        "pair": "ARB/WETH",
        "base_token": {"symbol": "ARB"},
        "quote_token": {"symbol": "WETH"},
        "protocol": "UNISWAP_V3",
        "tvl_usd": 2_000_000,
        "volume_24h_usd": 4_000_000,
    })
    pol = preliminary_pool_evaluation({
        "pair": "WPOL/USDT",
        "base_token": {"symbol": "WPOL"},
        "quote_token": {"symbol": "USDT"},
        "protocol": "UNISWAP_V3",
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 2_000_000,
    })
    campaign = preliminary_pool_evaluation({
        "pair": "DELTA/WETH",
        "base_token": {"symbol": "DELTA"},
        "quote_token": {"symbol": "WETH"},
        "protocol": "UNISWAP_V3",
        "tvl_usd": 400_000,
        "volume_24h_usd": 2_000_000,
    })

    assert arb["preferred_sleeve"] == "CORE_INCOME"
    assert pol["preferred_sleeve"] == "CORE_INCOME"
    assert campaign["preferred_sleeve"] == "TACTICAL_CAMPAIGN"


def _advisor_row(pair, address, monthly_net_pct):
    seven_day_net = 1000.0 * (monthly_net_pct / 100.0) * 7.0 / 30.0
    return {
        "chain": "ETHEREUM",
        "pair": pair,
        "pool_address": address,
        "sleeve": "TACTICAL_CAMPAIGN",
        "advisor_ranking_basis": "LEADERBOARD_OPPORTUNITY_SCORE",
        "opportunity_score": {"score": 82},
        "evaluation": {
            "tactical_pre_score": 80,
            "core_pre_score": 60,
            "risk_tactical": {"eligible": True, "blockers": []},
            "risk_core": {"eligible": True, "blockers": []},
        },
        "economics": {
            "mode": "MODELLED",
            "capital_usd": 1000,
            "estimated_net_month_pct": monthly_net_pct,
            "estimated_operating_net_month_usd": monthly_net_pct * 10,
            "volume_quality": {"factor": 1.0},
        },
        "regime": {"confidence": 50},
        "market_evidence_status": "LIVE_CURRENT",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 1000,
            "horizon_days": 7,
            "expected_fees_usd": seven_day_net,
            "expected_net_usd": seven_day_net,
            "expected_intervention_cost_usd": 0,
        },
    }


def test_advisor_holds_cash_below_target_and_allocates_to_target_clearing_trade():
    rows = [
        _advisor_row("SLOW/USDC", "0x" + "4" * 40, 3.0),
        _advisor_row("FAST/USDC", "0x" + "5" * 40, 18.0),
    ]
    result = rank_opportunities(
        rows,
        available_capital=1000,
        reserve_pct=10,
        monthly_target_pct=10,
        max_positions=4,
        sleeve_filter="ANY",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )

    assert result["monthly_target_pct"] == 10
    assert [x["pair"] for x in result["allocations"]] == ["FAST/USDC"]
    slow = next(x for x in result["ranked"] if x["pair"] == "SLOW/USDC")
    assert any("below 10.0% target" in reason for reason in slow["reject_reasons"])


def test_deep_rotation_uses_two_item_fast_background_cycles():
    assert DeepAnalysisRotationService.BATCH_SIZE == 2
    assert DeepAnalysisRotationService.ACTIVE_CYCLE_SECONDS == 25.0
    assert DeepAnalysisRotationService.IDLE_CYCLE_SECONDS == 60.0


def test_v09753_ui_is_profit_first_and_target_aware():
    root = Path(__file__).resolve().parents[1]
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "Persistent profit-first board." in app
    assert "equivalent alternatives hidden" in app
    assert "Target %/mo" in html
    assert "advisor-target" in app
    assert "/static/app.js?v=0.9.7.5.3" in html

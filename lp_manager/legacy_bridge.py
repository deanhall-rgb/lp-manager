from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .db import Store
from .models import Position


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def discover_legacy_sources(legacy_root: Path) -> dict[str, Any]:
    files = {}
    for name in (
        "campaign_ledger.json",
        "owned_lp_ledger.json",
        "owned_portfolio_manager.json",
        "stage5_last_proposal.json",
        "bot_status.json",
    ):
        p = legacy_root / name
        files[name] = {"exists": p.exists(), "path": str(p)}
    return files


def import_campaign_ledger(store: Store, legacy_root: Path) -> dict[str, Any]:
    path = legacy_root / "campaign_ledger.json"
    payload = _load(path)
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "CAMPAIGN_LEDGER_NOT_FOUND_OR_INVALID", "path": str(path), "imported": 0}

    campaigns = payload.get("campaigns") or {}
    if isinstance(campaigns, list):
        rows = {str(i): x for i, x in enumerate(campaigns)}
    elif isinstance(campaigns, dict):
        rows = campaigns
    else:
        rows = {}

    imported = 0
    skipped = 0
    for campaign_id, campaign in rows.items():
        if not isinstance(campaign, dict):
            skipped += 1
            continue
        snap = campaign.get("position_snapshot") or {}
        economics = campaign.get("economics") or {}
        lower = _f(snap.get("lower_price") or snap.get("price_lower") or snap.get("range_lower"))
        upper = _f(snap.get("upper_price") or snap.get("price_upper") or snap.get("range_upper"))
        current = _f(snap.get("current_price") or snap.get("price") or snap.get("mark_price"))
        if not (lower > 0 and upper > lower and current > 0):
            skipped += 1
            continue
        token0 = str(snap.get("token0_symbol") or "TOKEN0")
        token1 = str(snap.get("token1_symbol") or "TOKEN1")
        ids = snap.get("ids") or []
        token_id = str(ids[0]) if ids else (str(snap.get("token_id")) if snap.get("token_id") is not None else None)
        value = _f(snap.get("position_value_usd") or snap.get("current_value_usd") or campaign.get("capital_usd"))
        cost = _f(campaign.get("entry_value_usd") or campaign.get("capital_usd") or value)
        status_raw = str(campaign.get("status") or snap.get("status") or "OPEN").upper()
        status = "CLOSED" if status_raw in {"CLOSED", "EXITED", "COMPLETE", "COMPLETED"} else "OPEN"
        position = Position(
            id=f"legacy-{campaign_id}",
            protocol=str(snap.get("protocol") or (campaign.get("origin") or {}).get("protocol") or "UNISWAP_V3"),
            chain=str(snap.get("network") or snap.get("chain") or "ROBINHOOD_CHAIN"),
            pair=f"{token0}/{token1}",
            status=status,
            lower_price=lower,
            upper_price=upper,
            current_price=current,
            capital_value=cost,
            current_value=value or cost,
            unclaimed_fees=_f(snap.get("unclaimed_fees_usd") or economics.get("unclaimed_fees_usd")),
            fees_today=_f(economics.get("fees_today_usd")),
            fees_7d=_f(economics.get("fees_7d_usd")),
            fees_30d=_f(economics.get("fees_30d_usd")),
            realised_fees=_f(economics.get("realised_fees_usd") or economics.get("fees_realised_usd")),
            estimated_il=_f(economics.get("impermanent_loss_usd") or economics.get("estimated_il_usd")),
            gas_costs=_f(economics.get("gas_costs_usd") or economics.get("lifecycle_cost_usd")),
            apr_current=_f(economics.get("apr_current") or snap.get("fee_apr_pct")),
            apr_7d=_f(economics.get("apr_7d")),
            opened_at=_f(campaign.get("opened_at_unix") or campaign.get("created_at_unix"), time.time()),
            token_id=token_id,
            campaign_id=str(campaign_id),
            source="legacy_campaign_ledger",
        )
        store.upsert_position(position)
        imported += 1
    return {"ok": True, "path": str(path), "imported": imported, "skipped": skipped}

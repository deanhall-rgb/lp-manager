from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def import_closed_position_finals(store, payload: dict[str, Any]) -> dict[str, Any]:
    """Freeze the operator-reviewed historical evidence pack into the ledger.

    This is intentionally a one-time accounting import. It never performs RPC
    scans and is therefore safe to run on every startup against an existing DB.
    """
    if str(payload.get("schema") or "") != "LP_MANAGER_CLOSED_FINALS_V1":
        return {"ok": False, "reason": "INVALID_CLOSED_FINALS_SCHEMA", "finalised": 0}

    rows = payload.get("positions") or []
    if not isinstance(rows, list):
        return {"ok": False, "reason": "INVALID_CLOSED_FINALS_POSITIONS", "finalised": 0}

    finalised = []
    missing = []

    for item in rows:
        if not isinstance(item, dict):
            continue

        chain = str(item.get("chain") or "")
        token_id = str(item.get("token_id") or "")
        existing = store.find_position_by_token(chain, token_id) if chain and token_id else None
        if not existing:
            missing.append({"chain": chain, "token_id": token_id})
            continue

        pid = str(existing.get("id") or "")
        opening = _f(item.get("opening_capital_usd"))
        fees = _f(item.get("total_fees_usd"))
        gas = _f(item.get("gas_costs_usd"))
        pnl = _f(item.get("realised_pnl_usd"))
        pct = _f(item.get("realised_return_pct"))
        closed_at = _f(item.get("closed_at"))
        quality = str(item.get("quality") or "EVIDENCE_PACK_CLOSED_FINAL")

        store.finalize_closed_position(
            pid,
            opening_capital_usd=opening,
            total_fees_usd=fees,
            gas_costs_usd=gas,
            realised_pnl_usd=pnl,
            realised_return_pct=pct,
            closed_at=closed_at,
            quality=quality,
        )

        label = str(item.get("display_name") or existing.get("display_name") or existing.get("pair") or "")
        campaign = label.split(" ", 1)[0] if label.upper().startswith("P") else str(existing.get("campaign_label") or "")
        try:
            store.update_position_metadata(
                pid,
                display_name=label,
                campaign_label=campaign,
                lifecycle_stage="CLOSED_FINAL",
                cost_basis_quality=quality,
                strategy_version="v0.8.11",
            )
        except Exception:
            pass

        snap = store.get_position_snapshot(pid) or {}
        snap["opening_transaction_hash"] = str(item.get("opening_transaction_hash") or snap.get("opening_transaction_hash") or "")
        snap["opening_block_number"] = int(item.get("opening_block") or snap.get("opening_block_number") or 0)
        snap["opened_at"] = _f(item.get("opened_at"), _f(snap.get("opened_at")))
        snap["closed_final"] = {
            "complete": True,
            "quality": quality,
            "accounting_source": "OPERATOR_REVIEWED_EVIDENCE_PACK",
            "token_id": token_id,
            "opened_at": _f(item.get("opened_at")),
            "closed_at": closed_at,
            "opening_capital_usd": round(opening, 6),
            "closing_principal_usd": round(_f(item.get("closing_principal_usd")), 6),
            "total_distributions_usd": round(_f(item.get("closing_principal_usd")) + fees, 6),
            "total_fees_usd": round(fees, 6),
            "gas_usd": round(gas, 6),
            "realised_pnl_usd": round(pnl, 6),
            "realised_return_pct": round(pct, 6),
            "opening_transaction_hash": str(item.get("opening_transaction_hash") or ""),
            "fee_transaction_hash": str(item.get("fee_transaction_hash") or ""),
            "close_transaction_hash": str(item.get("close_transaction_hash") or ""),
            "opening_block": int(item.get("opening_block") or 0),
            "fee_block": int(item.get("fee_block") or 0),
            "close_block": int(item.get("close_block") or 0),
            "liquidity_settled": True,
            "principal_settled": True,
            "valuation_complete": True,
            "gas_complete": True,
            "reason": None,
        }
        snap["closed_final_checked_at"] = _f(payload.get("generated_at"), 0.0)
        snap["closed_history_mode"] = "FROZEN_EVIDENCE_PACK"
        store.save_position_snapshot(pid, snap)

        finalised.append({
            "id": pid,
            "token_id": token_id,
            "display_name": label,
            "fees_usd": round(fees, 6),
            "pnl_usd": round(pnl, 6),
        })

    store.set_setting("history:closed_finals:v0811", {
        "schema": payload.get("schema"),
        "generated_from": payload.get("generated_from"),
        "finalised": len(finalised),
        "missing": missing,
        "positions": finalised,
    })
    return {"ok": True, "finalised": len(finalised), "missing": missing, "positions": finalised}

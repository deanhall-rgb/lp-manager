from __future__ import annotations

import time
from typing import Any

from web3 import Web3

from .chain_registry import chain_config
from .portfolio_accounting import position_accounting
from .rpc_client import build_read_only_web3


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _hex_topic(value: Any) -> str:
    if hasattr(value, "hex"):
        try:
            value=value.hex()
        except Exception:
            pass
    text=str(value or "")
    return text if text.startswith("0x") else "0x"+text


def decode_collect_tokens(receipt: dict[str, Any], snapshot: dict[str, Any], token_id: int) -> dict[str, float]:
    """Decode exact token amounts returned by NonfungiblePositionManager Collect logs."""
    topic="0x"+Web3.keccak(text="Collect(uint256,address,uint256,uint256)").hex().removeprefix("0x")
    manager=str(snapshot.get("position_manager") or "").lower()
    totals=[0,0]
    for raw_log in receipt.get("logs") or []:
        log=dict(raw_log)
        if manager and str(log.get("address") or "").lower()!=manager:
            continue
        topics=log.get("topics") or []
        if len(topics)<2 or _hex_topic(topics[0]).lower()!=topic.lower():
            continue
        try:
            if int(_hex_topic(topics[1]),16)!=int(token_id):
                continue
            raw=log.get("data") or "0x"
            if isinstance(raw,(bytes,bytearray)):
                data=bytes(raw)
            else:
                data=bytes.fromhex(str(raw).removeprefix("0x"))
            if len(data)<96:
                continue
            totals[0]+=int.from_bytes(data[32:64],"big")
            totals[1]+=int.from_bytes(data[64:96],"big")
        except Exception:
            continue

    t0=dict(snapshot.get("token0") or {})
    t1=dict(snapshot.get("token1") or {})
    a0=totals[0]/(10**int(t0.get("decimals") or 18))
    a1=totals[1]/(10**int(t1.get("decimals") or 18))
    return {
        str(t0.get("symbol") or "token0"):a0,
        str(t1.get("symbol") or "token1"):a1,
    }


def _receipt_for_close(chain: str, tx_hash: str, supplied: dict[str, Any] | None) -> dict[str, Any]:
    receipt=dict(supplied or {})
    if receipt.get("logs"):
        return receipt
    cfg=chain_config(str(chain).upper())
    if not cfg.rpc_url():
        return receipt
    try:
        w3=build_read_only_web3(cfg.rpc_url())
        return dict(w3.eth.get_transaction_receipt(tx_hash))
    except Exception:
        return receipt


def _token_prices(snapshot: dict[str, Any], market: Any | None, chain: str) -> tuple[float,float]:
    t0=dict(snapshot.get("token0") or {})
    t1=dict(snapshot.get("token1") or {})
    p0=max(0.0,_f(t0.get("price_usd")))
    p1=max(0.0,_f(t1.get("price_usd")))
    if market and (p0<=0 or p1<=0):
        try:
            marks=market.token_prices(str(chain).upper(),[
                str(t0.get("address") or ""),str(t1.get("address") or ""),
            ])
            if p0<=0: p0=max(0.0,_f(marks.get(str(t0.get("address") or "").lower())))
            if p1<=0: p1=max(0.0,_f(marks.get(str(t1.get("address") or "").lower())))
        except Exception:
            pass
    return p0,p1


def _value_collected_tokens(tokens: dict[str,float], snapshot: dict[str,Any], market: Any | None, chain: str) -> float:
    t0=dict(snapshot.get("token0") or {})
    t1=dict(snapshot.get("token1") or {})
    p0,p1=_token_prices(snapshot,market,chain)
    s0=str(t0.get("symbol") or "token0")
    s1=str(t1.get("symbol") or "token1")
    a0=max(0.0,_f(tokens.get(s0)))
    a1=max(0.0,_f(tokens.get(s1)))
    if (a0>0 and p0<=0) or (a1>0 and p1<=0):
        return 0.0
    return a0*p0+a1*p1


def finalise_execution_close(
    store,
    *,
    position_id: str,
    chain: str,
    tx_hash: str,
    receipt: dict[str,Any] | None = None,
    market: Any | None = None,
) -> dict[str,Any]:
    """Freeze a known LP Manager close from one confirmed transaction receipt.

    No historical log scan is performed. Exact returned token quantities come from
    the close transaction's Collect event; opening basis and tracked fees are already
    persisted while the NFT is live.
    """
    position=store.get_position(position_id)
    snapshot=store.get_position_snapshot(position_id) or {}
    if not position or not snapshot:
        return {"ok":False,"reason":"POSITION_OR_SNAPSHOT_MISSING"}

    existing_final=dict(snapshot.get("closed_final") or {})
    if existing_final.get("complete"):
        return {"ok":True,"already_final":True,"closed_final":existing_final}

    events=store.list_financial_events(500,position_id)
    close_event=next((
        e for e in events
        if str(e.get("event_type") or "").upper()=="CLOSE_POSITION"
        and str(e.get("status") or "").upper()=="CONFIRMED"
        and (not tx_hash or str(e.get("tx_hash") or "").lower()==str(tx_hash).lower())
    ),None)
    if not close_event:
        return {"ok":False,"reason":"CONFIRMED_CLOSE_EVENT_MISSING"}

    tracker=store.get_setting(f"fees:tracker:{position_id}",{}) or {}
    acct=position_accounting(position,snapshot,tracker)
    opening=max(0.0,_f(acct.get("cost_basis_usd")))
    if not acct.get("basis_ready") or opening<=0:
        return {"ok":False,"reason":"OPENING_BASIS_NOT_READY"}

    close_tx=str(tx_hash or close_event.get("tx_hash") or "")
    close_receipt=_receipt_for_close(chain,close_tx,receipt)
    token_id=int(position.get("token_id") or snapshot.get("token_id") or 0)
    collected=decode_collect_tokens(close_receipt,snapshot,token_id) if close_receipt else {}

    payload=dict(close_event.get("payload") or {})
    recorded_tokens=dict(payload.get("collected_tokens") or {})
    if not any(_f(v)>0 for v in collected.values()) and recorded_tokens:
        collected={str(k):_f(v) for k,v in recorded_tokens.items()}

    close_proceeds=max(0.0,_f(close_event.get("amount_usd")))
    if close_proceeds<=0 and collected:
        close_proceeds=_value_collected_tokens(collected,snapshot,market,chain)
    if close_proceeds<=0:
        return {"ok":False,"reason":"CLOSE_PROCEEDS_NOT_VALUED","collected_tokens":collected}

    confirmed=[e for e in events if str(e.get("status") or "").upper()=="CONFIRMED"]
    gas=sum(max(0.0,_f(e.get("gas_usd"))) for e in confirmed)

    prior_collected=max(
        max(0.0,_f(position.get("realised_fees"))),
        max(0.0,_f(tracker.get("collected_lower_bound_usd"))),
    )
    total_distributions=close_proceeds+prior_collected
    total_fees=max(
        max(0.0,_f(acct.get("fees_earned_usd"))),
        max(0.0,_f(tracker.get("cumulative_earned_usd"))),
        max(0.0,_f(snapshot.get("unclaimed_fees_usd")))+prior_collected,
    )
    realised=total_distributions-opening-gas
    realised_pct=realised/opening*100.0 if opening>0 else 0.0
    closed_at=_f(close_event.get("occurred_at"),time.time())

    quality="ONCHAIN_EXECUTION_RECEIPT_FINAL"
    store.finalize_closed_position(
        position_id,
        opening_capital_usd=opening,
        total_fees_usd=total_fees,
        gas_costs_usd=gas,
        realised_pnl_usd=realised,
        realised_return_pct=realised_pct,
        closed_at=closed_at,
        quality=quality,
    )

    final={
        "complete":True,
        "quality":quality,
        "accounting_source":"DIRECT_CONFIRMED_CLOSE_RECEIPT",
        "close_transaction_hash":close_tx,
        "closed_at":closed_at,
        "opening_capital_usd":round(opening,6),
        "close_proceeds_usd":round(close_proceeds,6),
        "total_distributions_usd":round(total_distributions,6),
        "total_fees_usd":round(total_fees,6),
        "gas_usd":round(gas,6),
        "realised_pnl_usd":round(realised,6),
        "realised_return_pct":round(realised_pct,6),
        "collected_tokens":collected,
        "liquidity_settled":True,
        "principal_settled":True,
        "valuation_complete":True,
        "gas_complete":True,
        "history_scan_required":False,
    }
    snapshot["closed_final"]=final
    snapshot["closed_final_checked_at"]=time.time()
    snapshot["closed_history_mode"]="DIRECT_EXECUTION_RECEIPT"
    snapshot["close_detection"]={
        **dict(snapshot.get("close_detection") or {}),
        "detected_at":closed_at,
        "reason":"CONFIRMED_LP_MANAGER_CLOSE",
        "close_transaction_hash":close_tx,
        "history_mode":"NOT_REQUIRED",
    }
    store.save_position_snapshot(position_id,snapshot)
    return {"ok":True,"finalised":True,"closed_final":final}


def repair_confirmed_execution_closes(store, market: Any | None = None) -> dict[str,Any]:
    """Repair previously confirmed LP Manager closes without historical scans."""
    attempted=finalised=0
    errors=[]
    for position in store.list_positions("CLOSED"):
        if str(position.get("lifecycle_stage") or "").upper()=="CLOSED_FINAL":
            continue
        pid=str(position.get("id") or "")
        events=store.list_financial_events(100,pid)
        event=next((
            e for e in events
            if str(e.get("event_type") or "").upper()=="CLOSE_POSITION"
            and str(e.get("status") or "").upper()=="CONFIRMED"
            and str(e.get("tx_hash") or "")
        ),None)
        if not event:
            continue
        attempted+=1
        try:
            result=finalise_execution_close(
                store,position_id=pid,chain=str(position.get("chain") or event.get("chain") or ""),
                tx_hash=str(event.get("tx_hash") or ""),receipt=None,market=market,
            )
            if result.get("ok") and result.get("closed_final",{}).get("complete"):
                finalised+=1
            elif not result.get("ok"):
                errors.append({"position_id":pid,"reason":result.get("reason")})
        except Exception as exc:
            errors.append({"position_id":pid,"reason":str(exc)[:220]})
    return {"attempted":attempted,"finalised":finalised,"errors":errors}

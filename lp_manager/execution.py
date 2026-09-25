from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Store
from .transaction_plan import TransactionPlan, validate_transaction_plan


class ExecutionService:
    """Safety boundary between dashboard intent and fund-moving code.

    Server-side execution remains build-only. V0.8 can prepare/simulate calls and
    hand an explicitly approved transaction to the browser wallet; private keys
    never enter the server process and autonomous broadcast remains unavailable.
    """

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def capabilities(self) -> dict[str, Any]:
        return {
            "mode": self.settings.execution_mode,
            "signing_enabled": False,
            "broadcast_enabled": False,
            "prepare_close": True,
            "prepare_collect": True,
            "prepare_open": True,
            "manual_wallet_stage_available": True,
            "live_v3_collect_simulation": True,
            "live_v3_close_simulation": True,
            "safety_message": "LP Manager can build/simulate V3 open, collect-fees and close calls. Any prepared call may be sent only after explicit human approval through the connected browser wallet; the server never stores keys or autonomously signs/broadcasts.",
        }

    def _legacy_import(self, module_name: str):
        root = str(self.settings.legacy_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        return importlib.import_module(module_name)

    def prepare_close(self, position: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.store.get_position_snapshot(str(position.get("id")))
        if snapshot and position.get("source") == "live_chain" and str(position.get("protocol") or "").upper() == "UNISWAP_V3":
            try:
                from .live_v3_builder import build_close
                built = build_close({**snapshot, "chain": str(position.get("chain") or snapshot.get("chain") or "")})
                plan = TransactionPlan(action="CLOSE", chain=str(position.get("chain") or ""), protocol="UNISWAP_V3", wallet=snapshot.get("wallet"), position_id=str(position["id"]), pair=position.get("pair"), intent={"token_id":position.get("token_id"),"liquidity_pct":100.0,"collect_all":True}, calls=[built["call"]], simulation=built["simulation"], constraints={"signing":False,"broadcast":False,"slippage_bps":built.get("slippage_bps"),"deadline":built.get("deadline")}, status="PREPARED" if built["simulation"].get("ok") else "SIMULATION_FAILED")
                result={"ok":bool(built["simulation"].get("ok")),"status":plan.status,"builder":"live_v3_builder","transaction_plan":plan.to_dict(),"plan_validation":validate_transaction_plan(plan),"build":built}
                self.store.record_action(position_id=position["id"], action_type="PREPARE_CLOSE", mode="build_only", status=plan.status, payload={"live":True}, result=result)
                return result
            except Exception as exc:
                result={"ok":False,"status":"BLOCKED","reason":"LIVE_V3_BUILDER_ERROR","detail":str(exc)}
                self.store.record_action(position_id=position["id"], action_type="PREPARE_CLOSE", mode="build_only", status="BLOCKED", payload={"live":True}, result=result)
                return result
        if position.get("campaign_id"):
            try:
                module = self._legacy_import("owned_v3_removal_builder")
                built = module.build_v3_remove_collect(str(position["campaign_id"]))
                status = "PREPARED" if built.get("ok") else "BLOCKED"
                self.store.record_action(
                    position_id=position["id"], action_type="PREPARE_CLOSE", mode="build_only", status=status,
                    payload={"campaign_id": position.get("campaign_id")}, result=built,
                )
                return {"ok": bool(built.get("ok")), "status": status, "builder": "owned_v3_removal_builder", "plan": built}
            except Exception as exc:
                result = {"ok": False, "status": "BLOCKED", "reason": f"LEGACY_BUILDER_ERROR:{type(exc).__name__}", "detail": str(exc)}
                self.store.record_action(
                    position_id=position["id"], action_type="PREPARE_CLOSE", mode="build_only", status="BLOCKED",
                    payload={"campaign_id": position.get("campaign_id")}, result=result,
                )
                return result

        plan = TransactionPlan(
            action="CLOSE", chain=str(position.get("chain") or ""), protocol=str(position.get("protocol") or "UNISWAP_V3"),
            position_id=str(position["id"]), pair=position.get("pair"),
            intent={"token_id": position.get("token_id"), "liquidity_pct": 100.0, "collect_all": True},
            constraints={"signing": False, "broadcast": False},
        )
        result = {
            "ok": True,
            "status": "DRAFT",
            "reason": "NO_LEGACY_CAMPAIGN_ID",
            "position_id": position["id"],
            "token_id": position.get("token_id"),
            "transaction_plan": plan.to_dict(),
            "plan_validation": validate_transaction_plan(plan),
            "steps": ["decreaseLiquidity(100%)", "collect(all owed tokens/fees)", "human wallet confirmation required in a later execution stage"],
        }
        self.store.record_action(
            position_id=position["id"], action_type="PREPARE_CLOSE", mode="build_only", status="DRAFT", payload={}, result=result,
        )
        return result

    def prepare_collect(self, position: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.store.get_position_snapshot(str(position.get("id")))
        if snapshot and position.get("source") == "live_chain" and str(position.get("protocol") or "").upper() == "UNISWAP_V3":
            try:
                from .live_v3_builder import build_collect
                built=build_collect({**snapshot, "chain": str(position.get("chain") or snapshot.get("chain") or "")})
                plan=TransactionPlan(action="COLLECT", chain=str(position.get("chain") or ""), protocol="UNISWAP_V3", wallet=snapshot.get("wallet"), position_id=str(position["id"]), pair=position.get("pair"), intent={"token_id":position.get("token_id"),"collect_all":True,"expected_unclaimed_value":position.get("unclaimed_fees")}, calls=[built["call"]], simulation=built["simulation"], constraints={"signing":False,"broadcast":False}, status="PREPARED" if built["simulation"].get("ok") else "SIMULATION_FAILED")
                result={"ok":bool(built["simulation"].get("ok")),"status":plan.status,"builder":"live_v3_builder","transaction_plan":plan.to_dict(),"plan_validation":validate_transaction_plan(plan),"build":built}
                self.store.record_action(position_id=position["id"], action_type="PREPARE_COLLECT", mode="build_only", status=plan.status, payload={"live":True}, result=result)
                return result
            except Exception as exc:
                result={"ok":False,"status":"BLOCKED","reason":"LIVE_V3_BUILDER_ERROR","detail":str(exc)}
                self.store.record_action(position_id=position["id"], action_type="PREPARE_COLLECT", mode="build_only", status="BLOCKED", payload={"live":True}, result=result)
                return result
        plan = TransactionPlan(
            action="COLLECT", chain=str(position.get("chain") or ""), protocol=str(position.get("protocol") or "UNISWAP_V3"),
            position_id=str(position["id"]), pair=position.get("pair"),
            intent={"token_id": position.get("token_id"), "collect_all": True, "expected_unclaimed_value": position.get("unclaimed_fees")},
            constraints={"signing": False, "broadcast": False},
        )
        result = {
            "ok": True,
            "status": "DRAFT",
            "position_id": position["id"],
            "token_id": position.get("token_id"),
            "expected_unclaimed_value": position.get("unclaimed_fees"),
            "transaction_plan": plan.to_dict(),
            "plan_validation": validate_transaction_plan(plan),
            "steps": ["read live tokensOwed / fee state", "build collect calldata", "simulate", "human wallet confirmation required"],
        }
        self.store.record_action(
            position_id=position["id"], action_type="PREPARE_COLLECT", mode="build_only", status="DRAFT", payload={}, result=result,
        )
        return result

    def prepare_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("pair", "lower_price", "upper_price", "capital_value")
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            return {"ok": False, "status": "BLOCKED", "reason": "MISSING_FIELDS", "fields": missing}
        intent = {
            "pair": payload["pair"],
            "chain": payload.get("chain"),
            "lower_price": float(payload["lower_price"]),
            "upper_price": float(payload["upper_price"]),
            "capital_value": float(payload["capital_value"]),
            "fee_tier": payload.get("fee_tier"),
            "protocol": payload.get("protocol", "UNISWAP_V3"),
            "strategy_sleeve": payload.get("strategy_sleeve", "TACTICAL_CAMPAIGN"),
            "directional_bias": payload.get("directional_bias", "NEUTRAL"),
            "inventory_intent": payload.get("inventory_intent", "BALANCED"),
            "target_hold_days": payload.get("target_hold_days"),
        }
        plan = TransactionPlan(
            action="OPEN", chain=str(payload.get("chain") or ""), protocol=str(payload.get("protocol") or "UNISWAP_V3"),
            pair=str(payload["pair"]), intent=intent, constraints={"signing": False, "broadcast": False},
        )
        result = {
            "ok": True,
            "status": "DRAFT",
            "intent": intent,
            "transaction_plan": plan.to_dict(),
            "plan_validation": validate_transaction_plan(plan),
            "steps": ["resolve pool", "validate tick spacing", "quote token split", "build unsigned mint plan", "simulate", "human wallet confirmation required"],
            "note": "The existing transaction_builder will be adapted behind this canonical plan interface after its legacy dependencies are isolated.",
        }
        self.store.record_action(position_id=None, action_type="PREPARE_OPEN", mode="build_only", status="DRAFT", payload=payload, result=result)
        return result

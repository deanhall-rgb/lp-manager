from __future__ import annotations

from typing import Any

from .scheduler import scheduler_manifest

DEFAULT_POLICY = {
    "market_monitoring": True,
    "wallet_refresh": True,
    "scout_discovery": True,
    "strategy_reviews": True,
    "ai_reviews": True,
    "notifications": True,
    "prepare_collect": True,
    "prepare_close": True,
    "prepare_rebalance": True,
    "prepare_open": True,
    "manual_wallet_approval_required": True,
    "autonomous_signing": False,
    "autonomous_broadcast": False,
}

LOCKED_FALSE = {"autonomous_signing","autonomous_broadcast"}


def get_policy(store) -> dict[str, Any]:
    saved=store.get_setting("automation:policy",{}) or {}
    policy={**DEFAULT_POLICY,**{k:v for k,v in saved.items() if k in DEFAULT_POLICY}}
    for key in LOCKED_FALSE: policy[key]=False
    return {"policy":policy,"locked_false":sorted(LOCKED_FALSE),"jobs":scheduler_manifest(),"authority":"BUILD_ONLY_MANUAL_APPROVAL"}


def update_policy(store, changes: dict[str, Any]) -> dict[str, Any]:
    current=get_policy(store)["policy"]
    for key,value in changes.items():
        if key not in DEFAULT_POLICY or key in LOCKED_FALSE: continue
        current[key]=bool(value)
    for key in LOCKED_FALSE: current[key]=False
    store.set_setting("automation:policy",current)
    return get_policy(store)

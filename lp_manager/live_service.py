from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .chain_registry import CHAINS, registry_status
from .market_data import GeckoTerminalClient
from .position_identity import AUTHORITATIVE_LIVE_POSITIONS, authoritative_opening_tx

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Backward-compatible shape retained for tests/older call sites.
_AUTHORITATIVE_OPENINGS = {
    chain: {int(token_id): row["opening_transaction_hash"] for token_id, row in positions.items()}
    for chain, positions in AUTHORITATIVE_LIVE_POSITIONS.items()
}


def _valid_address(value: str) -> bool:
    return bool(_ADDR_RE.fullmatch(str(value or "").strip()))


class LiveDataService:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.market = GeckoTerminalClient() if settings.gecko_terminal_enabled else None
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        wallet = self.settings.wallet_address
        last = self.store.get_setting("live:last_refresh", None)
        return {
            "live_mode_ready": bool(wallet and _valid_address(wallet)),
            "wallet_configured": bool(wallet),
            "wallet_valid": bool(wallet and _valid_address(wallet)),
            "wallet_display": f"{wallet[:8]}…{wallet[-6:]}" if len(wallet) > 16 else wallet,
            "demo_seed": self.settings.demo_seed,
            "market_data": {"geckoterminal": bool(self.market), "thegraph": bool(self.settings.thegraph_api_key)},
            "chains": registry_status(),
            "last_refresh": last,
            "last_scout_refresh": self.store.get_setting("live:last_scout_refresh", None),
            "refresh_seconds": self.settings.live_refresh_seconds,
            "background_refresh": bool(self._thread and self._thread.is_alive()),
            "wallet_snapshot": self.store.get_wallet_snapshot(),
            "signing_enabled": False,
            "broadcast_enabled": False,
        }

    def refresh_positions(self, chains: list[str] | None = None) -> dict[str, Any]:
        from .live_positions import reconcile_scan, scan_chain_positions, finalise_closed_positions_from_store

        if not self._refresh_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "A live refresh is already running", "chains": []}
        try:
            selected = [c for c in (chains or list(CHAINS)) if c in CHAINS and CHAINS[c].enabled()]
            if not self.settings.wallet_address or not _valid_address(self.settings.wallet_address):
                return {"ok": False, "error": "Configure a valid WALLET_ADDRESS in .env", "chains": []}
            # Public RPCs keep every chain readable, but broad NFT log scans are
            # intentionally reserved for operator/Alchemy RPCs unless explicitly allowed.
            allow_public_scan = __import__("os").getenv("LP_MANAGER_SCAN_PUBLIC_RPC", "false").strip().lower() in {"1","true","yes","on"}
            configured = [c for c in selected if CHAINS[c].rpc_url() and (CHAINS[c].rpc_source() != "PUBLIC_FALLBACK" or allow_public_scan)]
            results = []
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(configured)))) as pool:
                futures = {}
                for c in configured:
                    checkpoint = self.store.get_setting(f"live:checkpoint:{c}", None)
                    from_override = max(0, int(checkpoint) - 128) if checkpoint is not None else None
                    known = self.store.live_token_ids(c)
                    finalized={
                        int(p.get("token_id"))
                        for p in self.store.list_positions("CLOSED")
                        if str(p.get("chain") or "").upper()==c
                        and str(p.get("lifecycle_stage") or "").upper()=="CLOSED_FINAL"
                        and str(p.get("token_id") or "").isdigit()
                    }
                    seed_evidence={}
                    for token_id, tx_hash in (_AUTHORITATIVE_OPENINGS.get(c) or {}).items():
                        known.add(int(token_id))
                        seed_evidence[int(token_id)]={
                            "transaction_hash":tx_hash,
                            "discovery_source":"AUTHORITATIVE_OPENING_REFERENCE",
                        }
                    for pos in self.store.list_positions("OPEN"):
                        if str(pos.get("chain") or "").upper()!=c or not pos.get("token_id"):
                            continue
                        snap=self.store.get_position_snapshot(str(pos.get("id"))) or {}
                        tid=int(pos.get("token_id"))
                        persisted_tx=str(snap.get("opening_transaction_hash") or "")
                        authoritative_tx=str(authoritative_opening_tx(c,tid) or "")
                        # For P4/P5/P6 the operator-confirmed opening transaction is
                        # immutable authority. A stale snapshot must never replace it.
                        tx=authoritative_tx or persisted_tx
                        same_tx=(not authoritative_tx) or (persisted_tx.lower()==authoritative_tx.lower())
                        block=int(snap.get("opening_block_number") or 0) if same_tx else 0
                        opened=float(snap.get("opened_at") or 0) if same_tx else 0.0
                        if tx or block or opened:
                            try:
                                seed_evidence[tid]={
                                    **seed_evidence.get(tid,{}),
                                    "transaction_hash":tx,
                                    "block_number":block,
                                    "opened_at":opened,
                                    "discovery_source":"AUTHORITATIVE_OPENING_REFERENCE" if authoritative_tx else (
                                        snap.get("discovery_source") or "PERSISTED_OPENING_EVIDENCE"
                                    ),
                                }
                            except Exception:
                                pass
                    f = pool.submit(
                        scan_chain_positions, CHAINS[c], self.settings.wallet_address,
                        scan_blocks=CHAINS[c].scan_blocks(self.settings.position_scan_blocks), market=self.market,
                        from_block_override=from_override, known_token_ids=known, seed_evidence=seed_evidence,
                        finalized_token_ids=finalized,
                    )
                    futures[f] = c
                for f in as_completed(futures):
                    c = futures[f]
                    try:
                        scan = f.result(); row = reconcile_scan(self.store, scan); results.append(row)
                        if scan.ok and scan.latest_block is not None:
                            self.store.set_setting(f"live:checkpoint:{c}", int(scan.latest_block))
                    except Exception as exc:
                        results.append({"chain": c, "ok": False, "error": str(exc)})
            for c in selected:
                if c not in configured:
                    source=CHAINS[c].rpc_source()
                    reason="Public RPC available for reads/health; broad position scanning requires an explicit/Alchemy RPC" if source=="PUBLIC_FALLBACK" else f"{CHAINS[c].rpc_env} not configured"
                    results.append({"chain": c, "ok": False, "skipped": True, "rpc_source":source, "error": reason})
            closed_finalisation=[]
            for c in configured:
                try:
                    # Closed lifecycle reconstruction can require many historical RPC reads.
                    # Keep normal live refresh responsive by advancing at most one closed NFT
                    # per chain per cycle; failed/partial attempts are throttled by the snapshot.
                    row=finalise_closed_positions_from_store(
                        self.store,CHAINS[c],self.settings.wallet_address,self.market,limit=1
                    )
                    if row.get("attempted"):
                        closed_finalisation.append({"chain":c,**row})
                except Exception as exc:
                    closed_finalisation.append({"chain":c,"attempted":0,"finalised":0,"partial":0,"errors":[str(exc)]})
            payload = {
                "ok": any(r.get("ok") for r in results), "read_at": time.time(),
                "chains": sorted(results, key=lambda r: r.get("chain", "")),
                "closed_finalisation":closed_finalisation,
            }
            self.store.set_setting("live:last_refresh", payload)
            payload["risk_decisions_recorded"] = self._record_position_risk_decisions()
            return payload
        finally:
            self._refresh_lock.release()



    def import_opening_transactions(self, chain: str, transaction_hashes: list[str]) -> dict[str, Any]:
        """Operator recovery/bootstrap path for known LP opening transactions.

        It resolves only V3 NFT transfers into the configured public wallet, then
        verifies ownerOf/positions/pool state through the same read-only scanner.
        No signing or broadcasting capability is involved.
        """
        from .live_positions import discover_token_ids_from_transactions, reconcile_scan, scan_chain_positions
        key=str(chain or "").upper()
        if key not in CHAINS:
            return {"ok":False,"chain":key,"error":"Unsupported chain","transactions":[]}
        if not self.settings.wallet_address or not _valid_address(self.settings.wallet_address):
            return {"ok":False,"chain":key,"error":"Configure a valid WALLET_ADDRESS in .env","transactions":[]}
        cfg=CHAINS[key]
        try:
            token_ids,evidence,tx_results=discover_token_ids_from_transactions(cfg,self.settings.wallet_address,transaction_hashes)
            if not token_ids:
                return {"ok":False,"chain":key,"token_ids":[],"transactions":tx_results,"error":"No Uniswap V3 position NFT transfer to the configured wallet was found in the supplied transaction(s)."}
            scan=scan_chain_positions(
                cfg,self.settings.wallet_address,scan_blocks=1000,market=self.market,
                known_token_ids=self.store.live_token_ids(key),seed_token_ids=token_ids,seed_evidence=evidence,
            )
            reconciled=reconcile_scan(self.store,scan)
            if scan.ok and scan.latest_block is not None:
                self.store.set_setting(f"live:checkpoint:{key}",int(scan.latest_block))
            payload={"ok":bool(scan.ok),"chain":key,"token_ids":sorted(token_ids),"transactions":tx_results,"reconciled":reconciled,"read_at":time.time()}
            self.store.set_setting("live:last_transaction_import",payload)
            return payload
        except Exception as exc:
            return {"ok":False,"chain":key,"transactions":[],"error":str(exc)[:300]}

    def _record_position_risk_decisions(self) -> int:
        from .models import Decision
        from .strategy import edge_risk
        import uuid
        policy=(self.store.get_setting("automation:policy",{}) or {})
        if policy.get("strategy_reviews", True) is False:
            return 0
        count=0; now=time.time()
        for pos in self.store.list_positions("OPEN"):
            risk=edge_risk(pos)
            if float(risk.get("score") or 0) < 48:
                continue
            sleeve=str(pos.get("strategy_sleeve") or "TACTICAL_CAMPAIGN").upper()
            pid=str(pos.get("id") or "")
            severity="ACTION" if float(risk.get("score") or 0)>=80 else "WATCH"
            signature={"state":risk.get("state"),"severity":severity,"action":"REVIEW_POSITION","range_state":risk.get("range_state"),"score_band":int(float(risk.get("score") or 0)//10)}
            previous=self.store.get_setting(f"decision:last-risk:{pid}",{}) or {}
            if previous==signature:
                continue
            did=uuid.uuid5(uuid.NAMESPACE_URL,f"live-risk:{pid}:{signature}:{int(now)}").hex
            try:
                self.store.add_decision(Decision(
                    id=did,position_id=pid,created_at=now,severity=severity,
                    action="REVIEW_POSITION",confidence=min(.95,max(.55,float(risk.get("score") or 0)/100.0)),
                    summary=f"{pos.get('display_name') or pos.get('pair')} moved into {risk.get('state')} risk",
                    rationale=str(risk.get("reason") or "Range proximity crossed a material monitoring threshold."),
                    trigger="LIVE_POSITION_RISK",source="LIVE_MONITOR",
                    evidence={"risk_score":risk.get("score"),"state":risk.get("state"),"pair":pos.get("pair"),"chain":pos.get("chain")},
                )); self.store.set_setting(f"decision:last-risk:{pid}",signature); count+=1
            except Exception:
                pass
        return count

    def background_scout_once(self, chain: str | None = None) -> dict[str, Any]:
        """Rotate one read-only market scan into the persistent opportunity book.

        This is deliberately market-data only: RPC failures must not stop the
        opportunity engine. It never approves, signs or broadcasts anything.
        """
        if not self.market:
            return {"ok":False,"error":"Market data disabled"}
        from .live_scout import preliminary_pool_evaluation
        from .economics_engine import estimate_lp_economics
        from .models import Decision
        import uuid
        policy=(self.store.get_setting("automation:policy",{}) or {})
        if policy.get("scout_discovery", True) is False:
            return {"ok":False,"skipped":True,"reason":"SCOUT_DISABLED_BY_POLICY"}
        order=["ETHEREUM","BASE","ARBITRUM","OPTIMISM","ROBINHOOD_CHAIN","POLYGON"]
        if chain:
            selected=str(chain).upper()
        else:
            idx=int(self.store.get_setting("live:scout_rotation",0) or 0)%len(order)
            selected=order[idx]
            self.store.set_setting("live:scout_rotation",idx+1)
        try:
            rows=self.market.network_pools(selected)
        except Exception as exc:
            payload={"ok":False,"chain":selected,"error":str(exc)[:240],"read_at":time.time()}
            self.store.set_setting("live:last_scout_refresh",payload)
            return payload
        candidates=[]
        for pool in rows:
            if str(pool.get("protocol") or "").upper()!="UNISWAP_V3":
                continue
            ev=preliminary_pool_evaluation(pool)
            sleeve=ev.get("preferred_sleeve") or ("CORE_INCOME" if float(ev.get("core_pre_score") or 0)>=float(ev.get("tactical_pre_score") or 0) else "TACTICAL_CAMPAIGN")
            econ=estimate_lp_economics(pool,capital=1000,active_time_pct=84 if sleeve=="CORE_INCOME" else 60,width_pct=48 if sleeve=="CORE_INCOME" else 22,regime={})
            row={**pool,"evaluation":ev,"sleeve":sleeve,"quick_economics":econ}
            candidates.append(row)
            try:
                self.store.upsert_opportunity(candidate=pool,evaluation={**ev,"quick_economics":econ},status="CANDIDATE" if ev.get("preferred_sleeve") else "WATCH")
            except Exception:
                pass
        candidates.sort(key=lambda r:max(float((r.get("evaluation") or {}).get("core_pre_score") or 0),float((r.get("evaluation") or {}).get("tactical_pre_score") or 0)),reverse=True)
        top=candidates[0] if candidates else None
        now=time.time()
        if top:
            ev=top.get("evaluation") or {}; score=max(float(ev.get("core_pre_score") or 0),float(ev.get("tactical_pre_score") or 0))
            signature={"pool":str(top.get("pool_address") or "").lower(),"score_band":int(score//5)}
            previous=self.store.get_setting(f"decision:last-scout:{selected}",{}) or {}
            if previous==signature:
                top=None
            did=uuid.uuid5(uuid.NAMESPACE_URL,f"background-scout:{selected}:{signature}:{int(now)}").hex
            try:
                if top:
                    self.store.add_decision(Decision(
                    id=did,position_id=None,created_at=now,severity="INFO",action="SCOUT_LEADER",
                    confidence=max(.50,min(.95,score/100.0)),summary=f"{top.get('pair')} leads the current {selected} opportunity scan",
                    rationale="Background read-only scouting compared current pool quality, TVL, activity and preliminary economics.",
                    trigger="BACKGROUND_SCOUT",source="LIVE_SCOUT",
                    evidence={"chain":selected,"pair":top.get("pair"),"score":round(score,1),"est_net_month_per_1000":(top.get("quick_economics") or {}).get("estimated_net_month_usd")},
                    ))
                    self.store.set_setting(f"decision:last-scout:{selected}",signature)
            except Exception:
                pass
        display_top=candidates[0] if candidates else None
        payload={"ok":True,"chain":selected,"count":len(candidates),"top_pair":display_top.get("pair") if display_top else None,"read_at":now}
        self.store.set_setting("live:last_scout_refresh",payload)
        return payload


    def refresh_wallet(self, *, include_health: bool = True) -> dict[str, Any]:
        from .wallet import build_wallet_snapshot
        return build_wallet_snapshot(self.settings, self.store, self.market, include_health=include_health)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.settings.wallet_address or not _valid_address(self.settings.wallet_address):
            return
        if not any(cfg.enabled() and cfg.rpc_url() for cfg in CHAINS.values()):
            return
        self._stop.clear()

        def worker():
            # Small delay lets the web server become responsive before the first chain scan.
            if self._stop.wait(2.0): return
            last_scout=0.0
            while not self._stop.is_set():
                try:
                    policy=(self.store.get_setting("automation:policy",{}) or {})
                    if policy.get("market_monitoring",True) or policy.get("wallet_refresh",True):
                        self.refresh_positions()
                    wallet_snap=self.store.get_wallet_snapshot() or {}
                    if policy.get("wallet_refresh",True) and time.time()-float(wallet_snap.get("stored_at") or 0) >= 300:
                        self.refresh_wallet(include_health=False)
                    if policy.get("scout_discovery",True) and time.time()-last_scout >= 15*60:
                        self.background_scout_once(); last_scout=time.time()
                except Exception:
                    pass
                if self._stop.wait(self.settings.live_refresh_seconds): break

        self._thread = threading.Thread(target=worker, name="lp-manager-live-refresh", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()

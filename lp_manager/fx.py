from __future__ import annotations

import time
from typing import Any

import requests


def money_context(settings, store) -> dict[str, Any]:
    requested=str(settings.currency or "USD").upper()
    if requested == "USD":
        return {"source_currency":"USD","display_currency":"USD","usd_to_display_rate":1.0,"converted":True,"source":"IDENTITY"}
    cached=store.get_setting(f"fx:USD:{requested}",None)
    if isinstance(cached,dict) and time.time()-float(cached.get("read_at") or 0) < 12*3600 and float(cached.get("rate") or 0)>0:
        return {"source_currency":"USD","display_currency":requested,"usd_to_display_rate":float(cached["rate"]),"converted":True,"source":cached.get("source") or "CACHE"}
    try:
        r=requests.get("https://api.frankfurter.app/latest",params={"from":"USD","to":requested},timeout=8)
        r.raise_for_status(); payload=r.json(); rate=float((payload.get("rates") or {}).get(requested) or 0)
        if rate<=0: raise ValueError("missing FX rate")
        saved={"rate":rate,"read_at":time.time(),"source":"FRANKFURTER"}; store.set_setting(f"fx:USD:{requested}",saved)
        return {"source_currency":"USD","display_currency":requested,"usd_to_display_rate":rate,"converted":True,"source":"FRANKFURTER"}
    except Exception as exc:
        return {"source_currency":"USD","display_currency":"USD","requested_currency":requested,"usd_to_display_rate":1.0,"converted":False,"source":"FALLBACK_USD","warning":f"FX unavailable; showing USD to avoid mislabelling. {str(exc)[:120]}"}

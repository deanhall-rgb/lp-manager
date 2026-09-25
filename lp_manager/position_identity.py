from __future__ import annotations

from typing import Any

# Operator-confirmed identity for the three live Robinhood Chain positions.
# These are public NFT IDs / transaction hashes only.
AUTHORITATIVE_LIVE_POSITIONS: dict[str, dict[str, dict[str, str]]] = {
    "ROBINHOOD_CHAIN": {
        "1289953": {
            "label": "P4",
            "opening_transaction_hash": "0x08435860b326074b65d0b3c95634a5531cef061f7017702cb123e37253b3f2a4",
        },
        "1290067": {
            "label": "P5",
            "opening_transaction_hash": "0xe9de48e6b7ef1f2a238febf8d8fd7bb99462a0ebd35b1f93bf4c1f885f93e92c",
        },
        "1290077": {
            "label": "P6",
            "opening_transaction_hash": "0x565f672e404ec8f9d2d1d6fbcd064c495a51200611dd6d96072b3a1162cd0a21",
        },
    }
}


def authoritative_position(chain: str, token_id: str | int) -> dict[str, str] | None:
    return (AUTHORITATIVE_LIVE_POSITIONS.get(str(chain or "").upper()) or {}).get(str(token_id))


def authoritative_label(chain: str, token_id: str | int) -> str | None:
    row=authoritative_position(chain,token_id)
    return str(row.get("label")) if row and row.get("label") else None


def authoritative_opening_tx(chain: str, token_id: str | int) -> str | None:
    row=authoritative_position(chain,token_id)
    return str(row.get("opening_transaction_hash")) if row and row.get("opening_transaction_hash") else None


def canonical_display_name(chain: str, token_id: str | int, pair: str, fallback: str = "") -> str:
    label=authoritative_label(chain,token_id)
    if label:
        return f"{label} · {pair}"
    return fallback or pair

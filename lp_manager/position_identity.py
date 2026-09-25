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


# Operator-confirmed opening transactions for older/historical Robinhood Chain NFTs.
# These public hashes are identity evidence only; economics are still reconstructed
# from immutable on-chain lifecycle events.
AUTHORITATIVE_OPENING_TRANSACTIONS: dict[str, dict[str, str]] = {
    "ROBINHOOD_CHAIN": {
        "1206967": "0xd78b7cff1fe65d1b61ca77bc6b47ecbe8cc60a37dcdcbb1b89740367080ce55a",
        "1209940": "0xe3647aef4c8b2c688493ec459e5d5565cfb230230e4e80ffec4c51fd36308c5f",
        "1252512": "0x8c312e7583e5036bac015e19489820c05b49c65e35253517ab2813e11f84e9c2",
        "1157839": "0x4894f2748ce17a76810e46e4954a627a8e86ad72c2183d4fc978f7aee557a82c",
        "1206612": "0xfd0c071eece65685b528300d9f7080024ddfa116242702d1d9f9ac3cf149266e",
    }
}


def authoritative_position(chain: str, token_id: str | int) -> dict[str, str] | None:
    return (AUTHORITATIVE_LIVE_POSITIONS.get(str(chain or "").upper()) or {}).get(str(token_id))


def authoritative_label(chain: str, token_id: str | int) -> str | None:
    row=authoritative_position(chain,token_id)
    return str(row.get("label")) if row and row.get("label") else None


def authoritative_opening_tx(chain: str, token_id: str | int) -> str | None:
    row=authoritative_position(chain,token_id)
    if row and row.get("opening_transaction_hash"):
        return str(row.get("opening_transaction_hash"))
    return (AUTHORITATIVE_OPENING_TRANSACTIONS.get(str(chain or "").upper()) or {}).get(str(token_id))


def canonical_display_name(chain: str, token_id: str | int, pair: str, fallback: str = "") -> str:
    label=authoritative_label(chain,token_id)
    if label:
        return f"{label} · {pair}"
    return fallback or pair

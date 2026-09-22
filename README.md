# LP Manager v0.5 Live

LP Manager is a local, safety-first control plane for concentrated-liquidity positions.

## V0.5 live-data milestone

V0.5 replaces the demo-only operating surface with a live read-only path:

- loads configuration from the LP Manager `.env` and can fall back to the legacy bot `.env` without copying secrets;
- uses only a **public wallet address** for ownership discovery;
- supports Ethereum, Base, Arbitrum, Optimism, Polygon and Robinhood Chain adapters;
- discovers recent Uniswap V3 NFT positions from PositionManager Transfer logs, verifies ownership with `ownerOf`, and reads position/pool state directly from chain;
- reads tick range, current tick, liquidity, estimated token inventory and claimable fees;
- uses GeckoTerminal as the first cross-chain pool/TVL/volume/OHLC provider;
- exposes a live Uniswap V3 pool browser with pool detail and 30/90-day replay;
- reconciles live positions into SQLite rather than replacing cost-basis/history records;
- builds and `eth_call`-simulates V3 Collect and full Decrease+Collect close calls for live positions;
- never signs or broadcasts transactions.

## Safety boundary

**Never put a seed phrase or private key in this project.** V0.5 does not need one.

`LP_MANAGER_EXECUTION_MODE=build_only` remains the supported mode. Signing and broadcasting are hard-disabled in the control plane.

## First run

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\start_lp_manager.py
```

Open <http://127.0.0.1:8765>.

## Make it live

Run:

```powershell
.\configure_live.ps1
```

That creates `.env` from `.env.example` and opens it in Notepad.

At minimum configure:

```env
LP_MANAGER_DEMO_SEED=false
WALLET_ADDRESS=0xYOUR_PUBLIC_WALLET
BASE_RPC_URL=...
RH_RPC_URL=...
```

Only configure the chains you use. Missing chain RPCs are displayed as unavailable and do not stop other chains from working.

Restart LP Manager after changing `.env`, then use **Refresh wallet**. The System page shows which chains are configured without returning RPC URLs/API secrets to the browser.

### Existing bot reuse

If your existing project remains at:

```text
C:\Users\deano\Documents\crypto-lp-bot
```

set:

```env
LP_MANAGER_LEGACY_ROOT=C:\Users\deano\Documents\crypto-lp-bot
```

The LP Manager `.env` takes precedence; the legacy `.env` is fallback-only.

## Position discovery notes

V0.5 scans a recent PositionManager event window rather than indexing the full history of every chain. Default:

```env
LP_MANAGER_POSITION_SCAN_BLOCKS=500000
```

Per-chain overrides are supported, for example:

```env
LP_MANAGER_BASE_SCAN_BLOCKS=1500000
LP_MANAGER_ROBINHOOD_CHAIN_SCAN_BLOCKS=300000
```

If an old NFT predates the scan window, increase the relevant scan window. Every discovered token ID is still verified with `ownerOf`, so old transferred-away NFTs are not treated as owned.

## Live Scout

The Opportunities page can load live Uniswap V3 pools by chain. Pool cards show current TVL, 24h volume, price movement and transparent Core/Tactical **pre-scores**. These are intentionally preliminary: final approval should include historical replay/range durability rather than treating current APR/volume as authority.

Pool detail can run 30/90-day OHLC replay through the same no-lookahead strategy engine used by Replay Lab.

## Accounting honesty

A live position discovered without a matching historical ledger has unknown historical cost basis. V0.5 uses first-observed value as a temporary baseline and labels the snapshot accordingly rather than fabricating historic P/L. Existing imported cost basis is preserved during chain reconciliation.

Fee-day/week/month and realised APR remain incomplete until snapshot history/claim events provide enough observations. Claimable fee state is live; historical fee performance is not invented.

## Execution status

- Observe live positions: **implemented**
- Live Scout / pool data: **implemented**
- Live historical OHLC replay: **implemented**
- Prepare/simulate V3 Collect: **implemented**
- Prepare/simulate V3 Close (decrease all + collect): **implemented**
- Generic V3 Open/mint: **still build-only intent**; live pool/tick/token split validation is the next execution milestone
- Browser-wallet signing: **disabled / next authority stage**
- Server-side private-key signing: **not planned**

## Tests

```powershell
pytest -q
```

GitHub Actions is also configured to compile and run the test suite in a clean environment.

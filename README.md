# LP Manager v0.8.8 Financial Truth

V0.8.8 is the transition from profitability prototype to auditable live-manager beta. It keeps explicit browser-wallet approval as the signing boundary while hardening financial truth, range realism and execution reconciliation.

## What v0.8.8 adds

- **Financial Truth ledger:** frozen forecast snapshots, transaction/gas events, realised G/L, all-time fees and forecast-vs-actual hooks.
- **Simple position P/L:** `current LP value + tracked fees - verified opening capital`. Transaction costs are shown separately; LP-vs-HODL stays a detail benchmark.
- **Opening-basis recovery:** opening `IncreaseLiquidity` events can reconstruct the mint price/tick when archive RPC history is unavailable; major-asset historical USD fallback improves new-chain WETH reconstruction.
- **More realistic ranges:** Core and Tactical candidates are constrained by realised volatility, holding period and range asymmetry; historical activity remains evidence rather than a hard enter/do-not-enter switch.
- **Advisor sleeve policy:** major/stable and stable/stable inventory is classified as Core before pool-quality gating.
- **Data-poor pool economics:** exact-pool live fee evidence is preferred; same-pair owned evidence may be used as a heavily haircut LOW-confidence fallback instead of silently returning zero fees.
- **Profit Lab resilience:** validated historical series are cached; new-chain WETH history can fall back to global major-symbol history when address-specific data is sparse or wrongly oriented.
- **Execution fixes:** Collect and Close now receive the authoritative chain, can progress to browser-wallet signing after simulation, and confirmed receipts/gas are recorded into the audit ledger.
- **Wallet chooser cleanup:** providers are deduplicated by wallet family and presented in a cleaner single-row-per-wallet layout.
- **Performance:** Today / This Week / This Month remain calendar periods, with **All time fees** added. Profit Scoreboard now includes **Realised gain / loss**.
- **Dependency hygiene:** the unexplained `httpx2` entry is replaced by the actual `httpx` dependency.

## Upgrade from v0.8.7

Extract V0.8.8 into a new folder, then run:

```powershell
cd "C:\Users\deano\Documents\lp_manager_v0_8_8"
Set-ExecutionPolicy -Scope Process Bypass
.\upgrade_from_v087.ps1 -V087Path "C:\Users\deano\Documents\lp_manager_v0_8_7_correction2"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\start_lp_manager.py
```

The upgrade copies the existing `.env` and `data` state forward without modifying v0.8.7. No seed phrase or private key is stored by LP Manager.

## V0.8.8 release gates

The automated suite pins the user-reported failures: major/stable sleeve classification, realistic 7-day Tactical edge geometry, non-zero data-poor fee economics from owned evidence, simple P/L, all-time/realised accounting, opening-event reconstruction, Collect/Close chain propagation, forecast snapshots and wallet-provider deduplication.

---

# LP Manager v0.8.7 Profitability Correction

V0.8.7 is a financial-correctness and profitability release built on v0.8.6. It does **not** widen execution authority: private keys remain outside the server, server-side signing/broadcast remains disabled, and browser-wallet transactions still require explicit human confirmation.

## What v0.8.7 fixes

- **One profit/range engine:** Strategy Lab is now a compatibility view over the same money-first optimiser used by Profit Lab, removing contradictory range recommendations.
- **Pool + fee-tier optimisation:** relevant Uniswap V3 pools for the exact token pair can be compared under the same capital/horizon assumptions; the selected pool is the one with the strongest expected net hold profit among the analysed tiers.
- **USDG/WETH unit correction:** on-chain pair orientation is authoritative and WETH/stable lenses are sanity-checked so a ~$1 stablecoin mark cannot masquerade as `USDG per WETH`.
- **Transparent fee framework:** pool-derived spot APR, owned-position observed APR, forecast fee APR and net horizon return are separate metrics. Observed annualisation is suppressed until at least 24h of fee evidence exists.
- **Conservative calibration:** sub-24h samples are excluded; live/model ratios are shrunk toward 1x and young exact-pool evidence is capped.
- **Cashflow invariants:** expected net fee profit is forecast fees minus explicit cash costs. Historical validation cannot be blended in as extra cash profit.
- **Asymmetric Core inventory outcomes:** falling below a bullish WETH/stable range can be desirable WETH inventory; rising above converts toward stable after selling ETH higher. Range exit is not automatically treated as failure.
- **Real accounting:** fee income, current P/L including fees and LP-vs-HODL are shown separately where opening evidence is reconstructable.
- **P4/P5/P6 authority:** Robinhood NFTs 1289953 / 1290067 / 1290077 are stably labelled P4 / P5 / P6 and carry their supplied opening transaction references for reconstruction.
- **Explicit wallet chooser:** EIP-6963/browser providers are presented for explicit selection; LP Manager no longer silently chooses the first injected provider.
- **GBP-first UI:** money inputs are treated as the configured display currency (GBP by default) and converted back to auditable USD source values internally.

## Primary release invariant

`NET FORECAST RETURN = FORECAST FEE INCOME - EXPLICIT CASH COSTS`

LP-vs-HODL divergence is a separate economic comparison, not a synthetic monthly expense or extra cash return.

## Upgrade from V0.8.6

Extract V0.8.7 into a new folder, then run:

```powershell
cd "C:\Users\deano\Documents\lp_manager_v0_8_7"
Set-ExecutionPolicy -Scope Process Bypass
.\upgrade_from_v086.ps1 -V086Path "C:\Users\deano\Documents\lp_manager_v0_8_6"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\start_lp_manager.py
```

The upgrade copies the existing `.env` and `data` state forward without modifying V0.8.6, and normalises the configured display currency to GBP. No private key or seed phrase is introduced.

## V0.8.7 hard acceptance gates

The release test suite includes explicit gates for:

1. **WETH/USDC, £1,000, 7 days:** compare relevant V3 fee tiers, select the strongest expected-net pool, enforce range guardrails, expose fee maths, APR distinctions and boundary inventory.
2. **WETH/USDG:** execution units must be stable-per-WETH in a plausible ETH-price range; a ~1 USD stable mark cannot pass as the WETH execution price.
3. **P4/P5/P6 accounting:** NFTs 1289953 / 1290067 / 1290077 must retain P4 / P5 / P6 identity, supplied opening references, reconstructed opening capital/time evidence, fee-inclusive P/L and LP-vs-HODL accounting where marks are available.

---
# LP Manager v0.8.6 Profit Engine Preview

This build is deliberately layered on top of the frozen v0.8.5 live-accounting/execution candidate. It does **not** widen execution authority. It adds a money-first decision layer while preserving build-only/manual-wallet safety.

## What v0.8.6 adds

- **Profit Lab**: ask the practical question directly: *for this pool, capital and holding period, what range is expected to maximise net LP fee profit?*
- **Holding-period-specific optimisation**: 1d / 3d / 7d / 14d / 30d changes the range search itself rather than merely relabelling a generic Core/Tactical result.
- **Walk-forward range validation**: older historical windows inform range selection; a more recent holdout is shown separately as validation evidence.
- **Live fee calibration**: observed fee accrual from LPs actually owned by the configured wallet can correct model forecasts. Exact-pool evidence is weighted most strongly; young/noisy samples are deliberately down-weighted.
- **Profit scoreboard**: observed 24h/7d/30d fee production, monthly fee run-rate, capital currently earning and cost-basis evidence coverage.
- **Explicit target gap**: target returns remain benchmarks. The engine reports expected cash profit, downside/upside bands and target attainment instead of inventing yield to hit a chosen target.
- **Evidence stack**: on-chain pool metadata, historical provider/source, historical volume availability, current TVL/volume, regime and live-fee calibration confidence are shown separately.
- **Directional alternatives**: single-sided upper/lower triggered plans can be surfaced when regime evidence is directional; they are not assumed to earn fees before price enters the range.

## Core objective

The range winner is no longer primarily “the range that stays active the longest.” The ranking is driven by expected net fee cashflow over the requested holding period, regularised by historical range behaviour and current regime. Risk gates remain guardrails rather than a reason to leave capital idle by default.

## Important modelling boundary

Historical active tick-liquidity is not yet fully reconstructed. Walk-forward fee tests use observed historical pool volume plus the current active-liquidity-share estimate. This limitation is explicit in the UI and is the next major accuracy frontier after live v0.8.5 fee calibration has accumulated enough real observations.

---
# LP Manager v0.8.5

## V0.8.5 live accounting + execution UX patch

- reconstructs actual V3 mint/opening time and opening token amounts from chain/explorer evidence when available
- upgrades first-observed cost basis to on-chain reconstructed entry value when historical WETH/stable pricing is available
- tracks raw unclaimed-token fee deltas so Today/7d/30d fee performance and observed fee pace are no longer permanently zero
- classifies major/stable live pairs such as WETH/USDG as CORE_INCOME by default while volatile ETH/alt pairs remain TACTICAL_CAMPAIGN
- normalises newly-owned live NFT labels to stable P4+ identifiers without overwriting historical LP1-LP3 evidence
- fixes Execution Desk paired-amount rendering, reduces requote churn, keeps live price polling, and improves MetaMask/EIP-6963 provider detection
- Strategy Lab now carries explicit execution units and explains target shortfall/attainment, fee-share method and persistence haircuts instead of inflating forecasts
- live portfolio backend refresh remains 60s by default; visible dashboard refreshes every 60s while open Execution Desk pool price refreshes every 5s and requotes at most every 15s


## V0.8.5 reliability patch

- Corrects Robinhood Chain Blockscout v2 endpoint to `https://robinhoodchain.blockscout.com/api/v2`.
- Adds Alchemy NFT ownership as a second current-state discovery source for Uniswap V3 positions.
- Keeps transfer-log discovery and `ownerOf`/`positions()` RPC verification as independent checks.
- Uses Alchemy Prices API for wallet/current-token marks when configured, preserving GeckoTerminal quota for pool-specific data.
- Strategy Lab falls back to Alchemy historical token-price points when GeckoTerminal pool OHLC is rate-limited; the UI marks that evidence source explicitly.
- Strategy Lab reuses persisted Scout pool context instead of making a redundant pool request before OHLC.
- Stale successful Strategy Lab results remain available during provider outages.

# LP Manager v0.8.5

V0.8 is the **decision + execution workspace** release. It keeps the read-only/live data and advisory intelligence boundaries from V0.7, fixes the current-vs-historical position authority problems exposed by the DELTA tests, improves wallet/scout/economics reliability, and adds a manual-wallet Uniswap V3 Execution Desk.

## What changed in V0.8

- **Current chain state wins.** Old campaign ledgers and uploaded research files are historical evidence only. A historical `OPEN` flag cannot create a live position; only current NFT ownership/active liquidity can do that.
- **DELTA research import.** The Positions page can import the reconstructed DELTA pool-history JSON and record P1/P2/P3 as historical campaigns with explicit `DELTA_PER_WETH` execution ranges, time-in-range, exits/re-entries and reconstructed fee lower bounds.
- **Explicit price units.** DELTA/WETH is shown as DELTA per WETH (e.g. ~180k–240k), rather than ambiguous `$14 → $16` numbers. USD/token-price or market-cap lenses are only shown when sourced independently.
- **Replay clarity.** Synthetic regression runs are labelled as synthetic tests; historical DELTA campaign replays use observed pool evidence. Detached outcome-audit clutter is removed and summarised inside each replay.
- **Replay economics.** Live-pool replays model fee income from observed volume plus transparent TVL/range assumptions. Imported DELTA history uses reconstructed WETH fee lower bounds rather than invented USD marks.
- **Wallet discovery/pricing.** Alchemy token discovery is used even when a different primary RPC is configured. Robinhood also has a Blockscout discovery fallback. GeckoTerminal token prices are batched, and obvious claim/spam tokens plus priced sub-$1 dust are quarantined by default.
- **Scout resilience.** GeckoTerminal calls are rate-shaped, retried, version-pinned and cached; provider failures fall back to the persisted opportunity book instead of blanking the page with a 502.
- **Profit-oriented economics.** Opportunity/Strategy views show daily/weekly/monthly fee estimates, gross APR, regime/IL allowance, lifecycle cost, a conservative monthly net estimate and a modelled range. Hot 24h activity receives a persistence haircut before it is treated as a monthly forecast.
- **Portfolio Advisor controls.** Compare Core only, Tactical only or best overall, in diversified or best-single-opportunity mode. Unused capital is redistributed within concentration limits instead of being left idle by first-pass weights.
- **Decision Journal grouping.** The latest decision for each open position/opportunity is shown first; older decisions are collapsed into history. Filters separate opportunities, open positions, Core and Tactical.
- **Balanced AI cadence.** Cheap monitoring remains deterministic. Core routine strategy/AI checkpoints are 12h/24h; Tactical 4h/12h, with material range/regime events able to wake them earlier. User-triggered and pre-execution analysis can always wake AI.
- **AI spend visibility.** The System page tracks AI calls, input/output token usage and estimated token cost by day/purpose. Separately billed web-search tool charges are explicitly not included in that estimate.
- **Legacy page clarified.** Missing old bot helper files are marked as optional/not needed; the campaign ledger is migration history, never live authority.
- **Execution Desk.** Read pool metadata directly from chain, enter a range/token amounts/slippage, build exact V3 approval/wrap/mint calls, simulate the mint when prerequisites permit, then explicitly sign each call with the browser wallet. The server never receives a private key.

## Safety boundary

V0.8 does **not** enable autonomous fund movement:

- no private-key or seed-phrase storage
- no server-side signing
- no autonomous broadcasting
- browser-wallet submission requires an explicit user confirmation checkbox
- the connected browser account must match the configured LP Manager wallet
- chain switching is explicit
- mint signing is blocked until the prepared mint simulation passes
- after prerequisite approval/wrap transactions, the plan is rebuilt against fresh allowances/balances

Collect/close preparation remains build/simulation-first. Wider policy-bound automation is deliberately still a later stage.

## Upgrade from V0.7 (Windows PowerShell)

Extract V0.8 to a new folder, then:

```powershell
cd C:\Users\deano\Documents\lp_manager_v0_8
Set-ExecutionPolicy -Scope Process Bypass
.\upgrade_from_v07.ps1 -V07Path "C:\Users\deano\Documents\lp_manager_v0_7"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\start_lp_manager.py
```

Open `http://127.0.0.1:8765`.

The upgrade helper copies the local `.env` and SQLite state forward without modifying V0.7. V0.8 then re-imports legacy history using the stricter historical-vs-live authority rules on startup.

## DELTA historical evidence

On **Positions**, use **Import DELTA research history** and select the supplied `DELTA_LP_POOL_HISTORY(1).json` file. The importer only needs the file's metadata and position summaries; the large swap/event body remains source evidence and is not duplicated into the database.

The imported records are historical unless a current chain scan proves the corresponding NFT is still owned/active.

## Recommended `.env`

```env
LP_MANAGER_DEMO_SEED=false
LP_MANAGER_EXECUTION_MODE=build_only
LP_MANAGER_CURRENCY=GBP
WALLET_ADDRESS=0xYOUR_PUBLIC_WALLET_ADDRESS
ALCHEMY_API_KEY=...
RH_RPC_URL=...                # optional explicit working Robinhood RPC; overrides Alchemy for primary RPC reads
LP_MANAGER_LEGACY_ROOT=C:\Users\deano\Documents\crypto-lp-bot
GECKOTERMINAL_ENABLED=true
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
LP_MANAGER_AI_ENABLED=true
LP_MANAGER_AI_WEB_SEARCH=true
LP_MANAGER_WALLET_MIN_VISIBLE_USD=1
```

No seed phrase or private key belongs in this file.

## Release validation

V0.8 release gate:

- Python compilation passes
- browser JavaScript syntax check passes
- **73 tests pass**
- **1 Web3-specific test is skipped only in dependency-light environments where Web3 is not installed**
- API smoke test confirms V0.8 health/overview and imports the supplied DELTA history as three closed `DELTA_PER_WETH` campaigns

## Still intentionally incomplete

- exact historical LP-vs-HODL P&L for every arbitrary pool still needs historical active-tick/liquidity reconstruction; estimates are labelled as estimates
- exact DELTA fee reconstruction excludes boundary-crossing fee allocation unless reconstructed tick-by-tick; imported fee evidence is labelled a lower bound
- server-side/autonomous transaction signing remains unavailable by design
- browser-wallet Execution Desk currently focuses on Uniswap V3 opening; collect/close signing remains a later explicit-wallet extension after more live testing


## v0.8.5 live-position discovery patch

V0.8.5 makes newly-opened Uniswap V3 positions a first-class live input rather than relying on legacy/manual import.

- Current V3 position NFTs are discovered from the wallet's Blockscout-owned-NFT inventory on Robinhood Chain, then independently verified through `ownerOf`, `positions()` and pool RPC reads.
- Transfer-log scanning remains active and checkpointed, so positions opened while LP Manager is running or offline are picked up automatically.
- New live records preserve the human execution lens (`WETH/USDG`, `WETH/DELTA`, `WETH/HOOKR`) and are assigned operator sequence labels after historical LP1-LP3 (P4, P5, P6, ...).
- Opening block/transaction evidence is retained when the transfer log is available.
- A `Rescan wallet LPs` control forces immediate discovery.
- `Import opening tx` is a deterministic recovery path: paste one or more opening transaction hashes and LP Manager resolves only V3 NFT transfers to the configured wallet, verifies current ownership and hydrates the position.
- A transient RPC/read failure no longer falsely closes an existing live LP. Closure requires explicit ownership evidence.
- The safety boundary is unchanged: discovery and transaction import are read-only; server signing and autonomous broadcast remain blocked.

## v0.8.2 profit/correctness patch

This is deliberately a patch release rather than v0.9. It corrects the evidence and economics issues found during live V0.8 testing:

- fee operating income is separated from LP-vs-HODL/divergence risk instead of subtracting a modelled risk allowance as if it were a monthly cash bill;
- hypothetical V3 fee share uses current active liquidity when an exact candidate range and on-chain liquidity are available;
- extreme 24h turnover/price anomalies are haircut and blocked from auto-allocation until investigated;
- the supplied DELTA pool reconstruction is bundled as a compact authoritative history summary so the real NFT identities/ranges replace misleading legacy LP1/LP2/LP3 labels;
- unpriced ERC-20s stay in the hidden wallet diagnostic drawer;
- Decision Journal defaults to active positions/opportunities and links opportunity decisions directly into Strategy Lab;
- Strategy Lab alternative ranges are individually comparable and display fee-operating economics;
- Execution Desk auto-balances the paired token amount and refreshes the live pool price while the desk is open;
- Portfolio Advisor returns near-miss candidates and rejection reasons instead of a blank result.

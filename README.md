# LP Manager v0.9 — Stable Baseline

V0.9 is the first release we are treating as the stable operating baseline for live testing. It preserves the explicit browser-wallet signing boundary while combining Profit Lab forecasting, automatic capital sizing, live LP discovery/accounting, fee tracking and direct close finalisation in one tested lifecycle.

## V0.9 baseline

- **End-to-end live lifecycle proven:** Profit Lab → Execution Desk → browser-wallet mint → automatic NFT discovery → live tracking → close → FINAL ACCOUNTING.
- **Forecast vs actual:** positions opened from Profit Lab retain their frozen forecast so live fee production can be measured against the original model.
- **Direct close accounting:** LP Manager-managed closes use the recorded lifecycle and confirmed close receipt; automatic historical block reconstruction is disabled for the normal close path.
- **Transparent final accounting:** closed positions show opening capital, returned assets, collected fees, transaction costs, realised P/L and LP-vs-HODL separately.
- **Provider resilience:** partial price-provider responses are merged and temporary price outages retain explicitly marked last-good valuation data rather than zeroing positions.
- **Execution stability:** Profit Lab capital auto-sizes token amounts; WETH wrapping covers only the shortfall; Execution Desk polling is throttled and prerequisite state can be explicitly rechecked.
- **Historical campaigns stay frozen:** P1–P5 remain the reviewed closed evidence set; new positions continue sequential numbering from the live lifecycle.

## Move from v0.8.11

Extract the clean V0.9 ZIP into a new folder, then copy only your existing runtime state:

```powershell
Copy-Item "C:\Users\deano\Documents\lp_manager_v0.8.11_direct_close_accounting_fix_retry\.env" ".\.env" -Force
Copy-Item "C:\Users\deano\Documents\lp_manager_v0.8.11_direct_close_accounting_fix_retry\data" ".\data" -Recurse -Force
```

Then create/activate the virtual environment, install `requirements.txt`, and run `start_lp_manager.py`. No private key or seed phrase is stored by LP Manager.

The intended next phase is evidence gathering rather than feature expansion: run multiple pairs/range styles, compare forecast vs actual fee production and realised after-cost returns, and use that evidence to calibrate later model versions.

---

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

## v0.9.6.3 portfolio-aware Advisor patch

This patch is deliberately narrow. Candidate Universe remains read-only and is **not** yet wired into Portfolio Advisor; that cross-chain unification is reserved for v0.9.6.4.

- Portfolio Advisor now receives the current open LP book and treats every recommendation as an **incremental addition**, not a fresh portfolio starting from zero.
- Existing exact-pool, pair, chain and sleeve exposure is carried into the ranking result. The UI shows the current exact-pool value and the projected value/concentration after the suggested addition.
- Existing exposure applies a small bounded ranking penalty and reduces incremental concentration room using the Advisor's existing Core/Tactical concentration percentages. It can still add to an owned pool when room remains; it simply stops pretending the existing capital is absent.
- The Capital field is explicitly **new money**. Existing open LP exposure informs pool/pair/chain concentration context but does not consume or shrink the newly entered capital budget.
- In Diversified mode, Tactical candidates retain the per-pool concentration cap (30% of deployable new capital), but there is no separate portfolio-wide Tactical cap that arbitrarily strands new capital when all eligible opportunities are Tactical.
- Persisted opportunity rows are explicitly re-labelled `PERSISTED_CACHE` when loaded. They remain useful as research fallbacks but cannot receive an allocation unless the current Advisor run refreshes them as `LIVE_CURRENT`.
- Fresh candidates are selected ahead of stale persisted rows before each chain is capped for comparison, preventing old high-score cache entries from crowding current evidence out of the ranking set.
- Owned/historical fee evidence can only calibrate Advisor economics when mature **exact-pool** samples exist. The Advisor-only adjustment is capped to **0.90x–1.10x** and does not alter the canonical current-market economics persisted for Profit Lab.
- Browser-wallet signing, broadcasting, Profit Lab, Candidate Universe and execution safety boundaries are unchanged.

## v0.9.6.4 cross-chain fairness + soak patch

This patch completes the v0.9.6 discovery/ranking integration without changing Profit Lab or execution signing boundaries.

- Candidate Universe is promoted from an isolated proof surface to the **shared discovery source** used by both Live Scout and Portfolio Advisor. Discovery score still only controls the cheap shortlist; it does not become the Advisor score.
- Portfolio Advisor no longer runs its own one-page `network_pools(page=1)` mini-scan. It consumes the same broad Graph + DEX Screener + limited GeckoTerminal Candidate Universe that Scout sees.
- Advisor default coverage is now **Ethereum, Base, Arbitrum, Optimism, Polygon and Robinhood Chain**. Polygon is no longer omitted from the default cross-chain comparison.
- Cross-chain refreshes are deliberately sequential and a successful Candidate Universe can be reused for 180 seconds. This reduces repeat GeckoTerminal pressure and makes immediate re-runs useful soak/cache tests instead of duplicating provider calls.
- A stale Candidate Universe may still be displayed as degraded research evidence, but its rows are marked `NOT_REFRESHED` and cannot receive capital. Fresh-cache reuse inside the 180-second window remains allocation-eligible.
- Each chain contributes candidates through several independent lenses — TVL, 24-hour volume, Core pre-score, Tactical pre-score and estimated operating return — before the global Portfolio Advisor ranking. This avoids a single Core-score sort silently determining cross-chain representation.
- Portfolio Advisor returns `universe_diagnostics` covering per-chain discovered/shortlisted/live-validated/research-ready counts, provider request counts, cache reuse, rate-limit/timeout events, scan duration and ranking/allocation diversity. The Opportunities UI shows a compact summary so repeated soak runs can be checked without reading logs.
- The v0.9.6.3 new-capital semantics remain unchanged: entered Capital is new money, reserve is deducted from that new money, existing LPs provide concentration context, and stale evidence cannot allocate.
- Profit Lab remains the on-demand range/history authority. Execution Desk remains build/simulate only until the browser wallet explicitly signs.

## v0.9.6.4.1 stability hotfix

This hotfix preserves the v0.9.6.4 cross-chain design while correcting the live-test regressions seen during the first full six-chain soak.

- Portfolio Advisor still consumes the shared Candidate Universe, but its foreground refresh uses a **fast consumer profile**: The Graph + DEX Screener, 200 broad candidates, 24 shortlist rows and at most 6 targeted validations per chain. The manual Candidate Universe control retains the full 500 / 40 / 20 build and GeckoTerminal seed.
- GeckoTerminal is deliberately skipped during the six-chain Advisor refresh so one public-provider cooldown cannot turn a single allocation request into a 60–90 second blocking scan.
- Fresh shared-universe reuse increases from 180 to **300 seconds**. This is a discovery-cache TTL only; stale rows remain ineligible for allocation.
- Browser HTML/static assets now send no-cache headers and carry versioned asset URLs so an older v0.9.6.2/v0.9.6.3 interface cannot sit on top of a newer backend after folder upgrades.
- The first background live-position refresh is delayed from 2 to **8 seconds** so startup RPC work does not compete with the initial page render.
- Manual Wallet refresh no longer performs a redundant full RPC-health pass first; System diagnostics remains the dedicated place for explicit RPC health checks.
- WPOL/POL and other network-major assets remain in executable **token-price** units. Provider market-cap/FDV metadata is no longer allowed to make a WPOL/USDT0 range appear as a misleading small-token market-cap range.
- V4 position discovery and Profit Lab/history request coalescing are intentionally **not** part of this hotfix; they are carried into the v0.9.7 cleanup.

## v0.9.6.4.2 Profit Lab stability hotfix

This hotfix fixes the Profit Lab regression exposed during the v0.9.6.4.1 live soak without changing the range model, Advisor ranking or execution boundary.

- Profit Lab now reuses **shared Candidate Universe / persisted opportunity pool context** before attempting a fresh exact-pool GeckoTerminal lookup. A Profit Plan click no longer spends another public-provider request simply to rediscover a pool Scout/Advisor already knows.
- Shared-universe opportunity persistence now stores the enriched current pool row, including quick economics, so Profit Lab retains the same TVL/volume/current-context evidence used by Scout and Advisor.
- Profit history is now **cache-first**. Fresh validated persisted history is checked before Alchemy or GeckoTerminal rather than after those providers have already been queried.
- The history cache gains a horizon-independent v0.9.6.4.2 key so a valid recent pool history can be reused across 3-day / 7-day Profit Lab runs instead of duplicating provider work solely because the requested holding period changed.
- **USDT0 is treated as a USD stablecoin** in both Profit Lab history selection and on-chain pool reconstruction. WPOL/USDT0 can therefore use the normal stable-quoted history fallback rather than being incorrectly rejected as a volatile/volatile pair.
- The existing stale-result safety remains: a previously validated Profit Lab recommendation may be displayed during a provider outage, but fresh calculations are never fabricated from missing history.
- v0.9.7 remains the planned larger cleanup for protocol-aware V3/V4 position discovery, global provider scheduling/request coalescing, deeper history architecture and the Opportunities UI redesign.
## v0.9.7.1 foundation cleanup

This patch starts the v0.9.7 programme by cleaning the shared data contract and duplicated policy definitions before the background provider coordinator, persistent leaderboard universe and scoring engine are added.

- Added one canonical product metadata source for the application version, display version, release label and HTTP user-agent. API health, overview metadata, support bundles and provider clients now use that shared identity instead of carrying unrelated old release strings.
- Added a canonical **opportunity schema v1.0**. Opportunity records now carry a stable cross-chain identity, explicit protocol/version, normalised token symbols, analysis state and market/analysis freshness metadata while preserving all existing v0.9.6 fields for compatibility.
- The opportunity data model explicitly understands **Uniswap V3 and Uniswap V4** from day one. V4 discovery/execution is not enabled by this patch; the schema is being made V4-safe now so later V4 work does not require another data-model rewrite.
- DEX Screener, The Graph and GeckoTerminal pool normalisation now emit the same canonical opportunity shape before Candidate Universe, Scout or Advisor consume it.
- Centralised stablecoin, ETH/BTC major and network-major symbol policy in one asset registry. USDT0 is therefore consistently recognised across pool-price orientation, Profit Lab history, Strategy Lab, fee-tier assumptions and display rules.
- Core-pair behaviour is deliberately unchanged: ETH/BTC majors and stablecoins retain the existing Core inventory semantics; network tokens such as WPOL/ARB are not silently promoted to Core merely because they are recognised as major display assets.
- Candidate Universe now adds the canonical opportunity identity/freshness contract without changing its V3-only production filter, discovery score, shortlist policy or provider budgets.
- Removed internal patch-history wording from the everyday UI. The sidebar now displays only the current application version at runtime; Opportunities, Provider diagnostics and Candidate Universe use stable product names rather than old patch numbers.
- Internal shared-discovery source names are now versionless (SHARED_CANDIDATE_UNIVERSE) so persisted evidence does not encode a temporary implementation release into its semantic source.
- Advisor ranking, Portfolio Advisor allocation semantics, Profit Lab range/economic maths, wallet/position refresh behaviour and Execution Desk signing boundaries are intentionally unchanged in v0.9.7.1.

Live-sweep follow-up fixes:

- Profit Lab stale fallback is now **request-exact only**. A validated result for the same pool but a different holding period, capital amount, sleeve or target can no longer be displayed underneath new form inputs during a provider failure.
- Changing Profit Lab chain, pool, holding period, style, capital or target now hides the previous result until the operator re-runs optimisation, preventing a stale 7-day / £1,000 result from visually masquerading as a 1-day / £270 result.
- Execution Desk capital auto-sizing can now complete a missing token USD mark from the verified on-chain pool ratio when the other token has a live USD mark. Known USD stables can act as the anchor. This specifically restores QNT/WETH-style Profit Lab → Execution Desk auto-population without fabricating prices for an unanchored volatile/volatile pair.
- Robinhood candidate breadth, owned/watchlist pool seeding, the leaderboard/Advisor presentation, offline Performance Log gap reconstruction, Investor Ledger auto-classification refinement and full provider/API cost observability remain scoped to later v0.9.7 stages rather than being mixed into this stability fix.

### v0.9.7.1.1 error-fix soak

This final foundation hotfix addresses the blockers found in the full v0.9.7.1 software sweep before the leaderboard work begins.

- Profit Lab no longer depends on a previously warmed local history cache for volatile/volatile pools such as QNT/WETH. When address-specific Alchemy history and pool observation history are unavailable, it can reconstruct the **actual execution-pair ratio** from two independently priced USD histories. It first tries global Alchemy symbol histories and then, on demand, both GeckoTerminal pool token OHLC series. A single token-USD series is still rejected for non-stable execution pairs.
- Every reconstructed pair-ratio series is checked against the current on-chain V3 execution price before it can be used. No synthetic flat history or unanchored price path is invented.
- The reconstructed history is persisted through the existing Profit Lab history cache, so repeat 1d / 3d / 7d analysis can reuse validated data instead of repeating provider work.
- Portfolio Advisor presentation has been cleaned without changing ranking logic: the seven-column row now actually has seven grid columns; Tactical/Core pills and action buttons no longer overlap; raw internal guardrail strings are replaced by short human-readable reasons; near misses are explicitly labelled research-only rather than looking like allocations.
- Advisor diagnostics now recognise the canonical `SHARED_CANDIDATE_UNIVERSE` source name, and internal guardrail identifiers are no longer printed as an unexplained footer.
- Product metadata and browser asset cache keys now expose **v0.9.7.1.1** in the sidebar so the operator can confirm the exact hotfix is running.
- Execution Desk's earlier v0.9.7.1 pool-ratio USD-mark fallback remains in place and is covered by regression tests; the intended QNT/WETH path is Portfolio Advisor → Profit Lab → auto-sized Execution Desk.



### v0.9.7.1.1 live sign-off

Operator live testing signed off the v0.9.7.1 foundation/hotfix sequence.

Verified in the live application:

- sidebar reports **v0.9.7.1.1**;
- Candidate Universe refresh completes successfully;
- Portfolio Advisor presentation is materially cleaner and QNT/WETH can flow into Profit Lab;
- Profit Lab successfully recalculates the same live pool across **7-day, 3-day and 1-day** holding periods without the previous zero-history failure;
- the £270 capital amount is preserved correctly through the flow;
- Execution Desk receives the selected pool and range, automatically sizes both QNT and WETH, and the browser-wallet signing flow remains functional.

Deferred observations for later v0.9.7 stages:

- **Opportunity breadth:** the Advisor can still collapse to a single allocation-eligible pool even after broad discovery. Before the leaderboard is considered useful, the cross-chain candidate/validation funnel must expose a materially broader set of genuinely comparable opportunities rather than making the market look artificially empty.
- **Range intelligence:** symmetric ranges such as roughly ±6% may genuinely be optimal sometimes, but the product must prove that rather than appearing to default to a centred guess. Later Profit Lab work should make volatility, regime, skew, replay evidence, intervention cost and downside asymmetry more visible in the selected geometry, and explain why a symmetric range wins when it does.
- **Packaging/testing handoff:** each future test build should be delivered as one downloadable ZIP containing the project directly, not a ZIP containing another ZIP. Each handoff should include the exact PowerShell command(s) needed to launch that build and a short focused test brief.

## v0.9.7.2 provider coordinator + background evidence cache

This patch makes provider traffic a shared application resource instead of allowing Wallet, Candidate Universe, Portfolio Advisor, Profit Lab and direct V3 reads to compete independently for the same upstream services. Ranking, allocation policy and Profit Lab range mathematics are intentionally unchanged.

- Added one process-wide **Provider Coordinator** with provider-specific request gaps/concurrency, request coalescing, short-lived reusable caches, stale fallback during provider failure/cooldown and explicit 429 cooldown handling.
- GeckoTerminal, Alchemy price/history requests, DEX Screener discovery/lookups, The Graph discovery and bounded Uniswap V3 RPC metadata/observation reads now use the shared coordinator when running through the application.
- **Identical in-flight requests are coalesced.** If multiple product surfaces request the same pool/history at the same time, one upstream request fills the shared cache and the other callers reuse its result rather than duplicating traffic.
- Background work uses **interactive-first priority**. Recent or active user-facing provider work can defer evidence warming rather than letting a background scan make the UI wait behind it.
- Provider telemetry records requests, cache hits, stale fallbacks, coalesced waits, errors, rate-limit events, background deferrals, average latency and active cooldown state. Read-only diagnostics are available from `/api/system/providers`.
- Added a persistent **Profit Lab evidence warmer**. It checks the existing durable validated Profit Lab history cache and pre-warms missing execution-price history without running range optimisation or allocating capital.
- Candidate Universe refreshes queue a small number of research-ready pools for evidence warming. Portfolio Advisor queues allocation candidates first and then its strongest likely click-through pools. Repeat 1d / 3d / 7d Profit Lab runs therefore share the same validated pool-history evidence instead of independently building it.
- The evidence warmer rotates slowly through open LP pools, current Candidate Universe pools and persisted opportunities, normally processing only two pools per background pass. It starts after a startup runway and yields provider traffic to interactive requests.
- Candidate Universe and Portfolio Advisor expose a compact coordination status line so live testing can see cache reuse, request coalescing, rate limits and queued evidence without turning the everyday UI into a diagnostics dashboard.
- The existing provider-specific safety logic remains in place underneath the coordinator: current execution prices still require current V3 reads, Profit Lab history still has to match the live on-chain execution ratio, and stale history cannot silently become a fresh allocation signal.
- Strategy-facing discovery deliberately does **not** promote the coordinator's stale payload fallback into a newly fresh Candidate Universe. If DEX Screener/The Graph cannot refresh after their short cache expires, the existing Candidate Universe stale-cache path is used and downstream allocation marks that evidence as not refreshed. GeckoTerminal retains its own explicitly tagged provider-cooldown fallback.
- **Not part of v0.9.7.2:** opportunity breadth, persistent cross-chain universe rotation, Opportunity Score, leaderboard ranking, deeper range-intelligence explanations, V4 discovery and final cost/accounting UI. Those remain staged for the later v0.9.7 patches.

## v0.9.7.2.1 range intelligence correction

This corrective patch closes the issues found during the live v0.9.7.2 stress test before the persistent leaderboard work begins.

- Profit Lab now tests a **denser directional range neighbourhood**. Tactical candidates include sub-1.5% placement steps and the exact regime target (plus softer variants), so short 1d / 3d / 7d holds are no longer forced to choose only between a centred range and coarse directional jumps.
- The volatility guardrail now allows **bounded evidence-led asymmetry**. Neutral markets remain constrained, while a confident trend/breakout can legitimately place more room in the supported direction without permitting extreme geometry.
- Range selection remains **profit-first**. A directional candidate can replace the centred candidate only when it remains inside the existing near-best expected-net band and does not materially weaken range quality.
- Profit Lab now returns an auditable **range placement decision**: centred / higher-ratio skew / lower-ratio skew, target skew, selected skew, whether a same-direction candidate existed, centred-vs-directional expected net where available, and a plain-English reason for the choice.
- The former Profit Lab **profit score** is now presented as **Range Quality**. It compares candidate geometries inside one pool and is explicitly not the cross-pool Opportunity Score planned for the leaderboard.
- Candidate Universe now exposes **Profit Lab readiness** separately from market/research readiness. Stable-quoted pools can show a direct history path; non-stable pools without validated pair-ratio history show HISTORY NEEDED; fresh validated history shows PROFIT READY; and recent background history failures show HISTORY FAILED before click-through.
- The background evidence warmer now persists a **per-pool readiness result**, allowing Candidate Universe and Advisor to reflect successful/failed deep-history preparation rather than hiding that state in an aggregate worker counter.
- Portfolio Advisor carries the same deep-analysis readiness marker into its rows. A pool with an explicit recent HISTORY FAILED state is no longer allocation-eligible, while pools that merely still need warming remain visible for investigation instead of being silently removed.
- Advisor remains a **quick allocation screen**; Profit Lab remains the execution-range authority. This patch makes that boundary visible without prematurely folding the full deep-analysis workload into every Advisor run.
- Execution Desk transaction construction, browser-wallet signing authority, wallet accounting, campaign accounting and provider-coordinator behaviour are unchanged.

## v0.9.7.3 persistent cross-chain leaderboard foundation

This patch turns the shared Candidate Universe into a persistent cross-chain decision surface before any larger provider/API redesign.

- Opportunities now begins with a persistent Top-25 cross-chain leaderboard built from cached Candidate Universe snapshots across all six supported chains.
- Refreshing one Candidate Universe chain updates that chain while the leaderboard retains useful snapshots from the others.
- The board shows the discovery funnel, per-chain freshness, protocol/version, Profit Lab readiness and persisted deep-analysis state.
- Freshness is explicit: FRESH / RECENT / AGING / STALE evidence cannot silently compete as current data.
- Protocol/version is explicit from the foundation layer so later V4 support does not require a leaderboard redesign.
- Deep-analysis state distinguishes SCREEN ONLY, DEEP +VE, DEEP -VE and DEEP STALE from persisted Profit Lab results.
- Portfolio Advisor still performs its fast screening pass, but a candidate must also have fresh positive deep Profit Lab evidence at the strategy planning horizon before it can clear the final allocation gate (3-day Tactical, 30-day Core). A profitable one-day run therefore cannot validate a different holding context.
- The current Screen score is intentionally provisional. It is not the final Opportunity Score; v0.9.7.4 adds the transparent cross-pool scoring model.
- Existing Profit Lab to Execution Desk workflow remains unchanged.
- No new broad provider sweep is introduced here. Leaderboard rebuilds use already-persisted Candidate Universe evidence; larger provider/batch changes remain a later optimisation stage.

Current lifecycle:

**Discovered → Market validated → Profit ready → Deep analysed → Leaderboard eligible → Allocation eligible**

## v0.9.7.3.1 Advisor capital-consistency hotfix

This hotfix closes a decision-boundary bug exposed by live testing of QNT/WETH.

A deep Profit Lab result is only valid for the capital and holding period it analysed. In v0.9.7.3, Advisor correctly required matching-horizon deep evidence, but it could still treat a positive result produced at a larger capital amount as sufficient evidence for a much smaller Tactical allocation. Because Profit Lab contains fixed intervention cash costs, fee income falls with capital while those costs do not. A £1,000 plan can therefore be positive while a £270 allocation is negative.

v0.9.7.3.1 changes that behaviour:

- Advisor still uses the fast cross-chain screen for ranking.
- Deep Profit Lab evidence must match the strategy horizon.
- Before an allocation survives, the persisted deep fee forecast is projected onto the **actual proposed capital**: fee income scales with capital while explicit intervention cash cost remains fixed.
- If the proposed amount turns the deep hold negative, that candidate is removed and allocation is recalculated across the remaining candidates.
- The Advisor economics column now shows the **validated hold estimate at the proposed size** rather than presenting the quick monthly screen as though it were the final profit forecast.
- The quick monthly screen remains visible only as secondary context.
- Profit-plan click-through now carries both the exact proposed capital and the validated holding period into Profit Lab.
- Leaderboard deep evidence now shows the capital basis used by the stored Profit Lab result, making positive/negative deep status less ambiguous.

The final execution decision still comes from a fresh Profit Lab run at the exact capital and horizon; the size-adjusted persisted result is a safety gate, not a substitute for that rerun.



## v0.9.7.4 transparent Opportunity Score

This patch replaces the leaderboard's temporary Screen score ordering with the first transparent cross-pool **Opportunity Score**.

The objective is unchanged: rank pools by their probability of increasing net portfolio wealth after realistic costs and risk, not by headline APR.

The score is a 0-100 weighted model made from six inspectable components:

- **Net economics — 30%:** fresh deep Profit Lab net economics are preferred. Screen economics or gross fee proxies can keep a pool visible, but they receive weaker evidence treatment and cannot masquerade as equivalent to a deep-analysed result.
- **Range durability — 15%:** uses deep Range Quality when available; otherwise the score reflects that deep range evidence is still pending.
- **Liquidity quality — 15%:** rewards genuine TVL depth on a logarithmic scale rather than allowing very large pools to dominate linearly.
- **Sustainable activity — 10%:** combines volume with turnover quality. Very high volume is useful, but implausibly extreme turnover is explicitly discounted rather than rewarded without limit.
- **Risk / friction quality — 15%:** applies visible penalties for validation mismatch, thin liquidity, anomalous turnover, intervention-cost drag and existing portfolio overlap.
- **Evidence quality — 15%:** combines market freshness, provider validation, Profit Lab readiness and deep-analysis maturity.

APR is therefore only one input into expected economics and cannot buy a high rank on its own.

Evidence maturity also imposes explicit score caps. A fresh deep-analysed pool can use the full 0-100 range; stale, screen-only, unverified or deep-negative pools are capped so incomplete evidence cannot silently compete as though it were equally decision-ready.

The Opportunities leaderboard now sorts by Opportunity Score and exposes the score confidence plus the original Screen score as secondary discovery context. Clicking the score opens a breakdown showing every component, its weight, points contributed, evidence cap and plain-English reasons.

The scoring contract is versioned as `OPPORTUNITY_SCORE_V1`. Portfolio Advisor allocation rules and Profit Lab range mathematics are intentionally unchanged in this patch; this is the leaderboard comparison layer.

### v0.9.7.3.1 live sign-off feeding v0.9.7.4

The capital-consistency hotfix is accepted from live testing.

- QNT/WETH was correctly removed when its deep result did not survive the actual proposed allocation economics.
- A later ARB/WETH Advisor run proposed **£270** with a capital-adjusted validated 3-day hold of approximately **£0.66**.
- Profit Lab opened at the same **£270 / 3-day** basis and recalculated approximately **£0.66 expected net**, confirming that the Advisor handoff now matches the execution-range authority.
- A prior research-only ARB investigation prefilled **£681.78** before a fresh allocation existed. This did not affect the later validated allocation path, but the research-only capital prefill is retained as a later UX/state-cleanup item.

## Deferred v0.9.7 live-test backlog

These observations are deliberately retained rather than being mixed into the current feature patch:

- **Background deep-analysis rotation:** the Top 20-25 leaderboard should eventually receive scheduled/rotating Profit Lab evidence automatically. The operator should not need to click every pool manually before it can become deep-confirmed.
- **Advisor breadth:** Portfolio Advisor can still collapse to one or zero allocation candidates. Later work should distinguish a genuinely empty deployable set from evidence that simply has not completed deep analysis yet.
- **Research-only capital prefill:** investigate why a research-only ARB/WETH click-through used £681.78 before the later validated £270 allocation. Research mode should clearly state where its capital assumption came from.
- **Range intelligence/explainability:** symmetric ranges are acceptable when genuinely optimal, but later UI/analysis must make regime, volatility, skew, replay evidence, intervention cost and the reason the selected geometry won easier to inspect.
- **Robinhood / owned-pool breadth:** ensure DELTA, PONS, HOOKR and other relevant owned/watchlist assets seed discovery appropriately rather than relying only on generic provider pages.
- **V4 discovery and position visibility:** wallet/position discovery still needs explicit V4 support. The canonical opportunity model is already V4-safe.
- **Investor Ledger automation:** improve deterministic transfer classification and reduce review items that can be safely auto-resolved.
- **Performance Log continuity:** reconstruct or explicitly mark offline/missing days rather than silently losing a day when the application was not running.
- **System cost observability:** show provider/API costs where available alongside existing AI usage/costs.
- **UI cleanup:** continue removing long internal names, overflow, duplicate controls and awkward click-throughs once all v0.9.7 feature surfaces are present.
- **Provider/API architecture:** a later optimisation pass may replace or batch provider access for broader/smoother scans, but the feature model should be completed first as agreed.
- **Packaging/test handoff:** each test build remains one flat project ZIP, accompanied by exact PowerShell launch commands and a focused test brief.

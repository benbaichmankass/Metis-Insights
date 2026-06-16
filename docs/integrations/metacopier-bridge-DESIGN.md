# MetaCopier → Breakout (DXtrade) bridge — DESIGN (2026-06-16)

> **The design doc is Tier-1** (analysis only). The *build* it describes is
> **Tier-2/3** — it adds a new live execution path (a prop account that routes
> real orders), so every runtime/config step here ships only with explicit
> operator approval, per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers.
>
> **STATUS: PARKED (operator decision 2026-06-16).** This copier/DXTrade-API
> track is **deferred until after the manual browser-Claude POC**
> ([`breakout-poc-manual-bridge-DESIGN.md`](breakout-poc-manual-bridge-DESIGN.md)
> is the ACTIVE track). Two questions to resume with when ready:
> (1) the exact **DXTrade Server URL + Domain** for Breakout (from the dashboard
> "Dashboard Details" / DXTrade terminal, not `app.breakoutprop.com`), and
> (2) whether **Breakout's DXTrade API is enabled for a third-party copier** at
> all (some firms disable it) — which is also the ToS gate-zero in
> [`breakout-compliance-2026-06-16.md`](breakout-compliance-2026-06-16.md) §4.
> Do not build this until both are answered.
>
> Original status: DESIGN — operator decision 2026-06-16 ("go with MetaCopier").
> Companion to
> [`docs/research/prop-firm-testing-tool-DESIGN.md`](../research/prop-firm-testing-tool-DESIGN.md)
> (the offline evaluator) — that doc decides *which strategies* to run on the
> prop account; **this** doc decides *how the orders reach it*.

## 1. Why a bridge at all

Breakout is a crypto prop firm whose funded accounts run on the **DXtrade**
platform. Most prop firms **do not expose a direct trading API** on DXtrade to
funded traders, so our bot cannot POST orders to the Breakout account the way it
does to Bybit. The earlier in-repo attempt (the purged `velotrade` /
`DXtradeClient`, PR #3680) assumed a *direct* DXtrade REST contract that the
firm never provided — which is exactly why it stalled.

**MetaCopier** is the chosen workaround: a cloud trade-copier that mirrors fills
from a **master** account we control into a **slave** (the Breakout DXtrade
account), so we never need DXtrade API credentials or a custom adapter of our
own. It is the "separate server, billed monthly" piece from the original
discussion.

> ⚠️ **Gate-zero (operator, before any build): confirm with Breakout support, in
> writing, that a third-party trade-copier is permitted.** The compliance
> deep-dive ([`breakout-compliance-2026-06-16.md`](breakout-compliance-2026-06-16.md))
> found that Breakout **allows algo trading** but **bans** "third-party/off-the-shelf
> approaches marketed to pass evaluation" (item 6) and "account sharing incl.
> sharing credentials" (item 11) — and a copier like MetaCopier is a third-party
> tool that holds your Breakout login. Our copy is **self→self** (not the banned
> cross-*user* copy), so it's not obviously prohibited, but it's not obviously
> permitted. **Exact question to ask Breakout is in the compliance doc §4.**
> Nothing below ships until that answer is in hand — a ToS breach permanently
> disables the account.

## 2. What MetaCopier is (grounded)

- Cloud service, **no VPS / no install**. Copies trades master→slave across
  MetaTrader, **DXtrade**, TradeLocker, cTrader, MatchTrader, TradingView, and
  crypto venues incl. **Bybit**, Binance, OKX, Bitget, BloFin — so it spans both
  ends we need (Bybit ⇄ DXtrade) natively.
- **Per-copier multiplier** for sizing; MetaCopier's own docs note Bybit "works
  differently" from a forex broker, so the multiplier must be tuned so the
  slave's per-trade profit/risk aligns with the master's.
- **Webhook** events (account- or project-level HTTP POST on trade events) — our
  observability hook into what the copier did.
- Account-level **risk-management controls** (e.g. loss caps) — usable as
  belt-and-braces on top of our own gating.
- DXtrade specifics: **TP/SL are set via multiple requests** (not atomic at
  entry) — a latency/partial-state consideration for the slave side.

(Capabilities per MetaCopier's site + docs, 2026-06-16; re-verify exact plan
limits + pricing at signup — they drift. Sources in § 11.)

## 3. Integration modes — the key architectural fork

| | **Mode A — account-copy (RECOMMENDED)** | **Mode B — webhook source** |
|---|---|---|
| How | Bot trades a **master** account on Bybit (a venue we already have wired). MetaCopier watches the master and mirrors fills into the Breakout DXtrade **slave** with a multiplier. | Bot emits a per-order **webhook** to MetaCopier (TradingView/webhook source); MetaCopier places on DXtrade. |
| Bot code change | **Near-zero** — the bot already trades Bybit. The "prop account" is a master we drive with the existing order path. | New per-order webhook emitter + payload schema + retry/idempotency. New live-path surface. |
| Sizing | One multiplier knob in MetaCopier. | We compute slave size and trust MetaCopier to honour it. |
| Failure surface | Copier lag + master/slave divergence only. | All of A's, plus our emitter + webhook auth + delivery. |
| Prop-rule control | Enforced on the **master** by the existing `PropRiskManager` (see § 5) — the master never makes a trade that, when mirrored, breaches Breakout. | Same, but applied before emit. |

**Recommendation: Mode A.** It reuses everything that survived the velotrade
purge — the `type: prop` account loader path and `PropRiskManager`
(`src/units/accounts/prop_risk.py`) — and keeps MetaCopier a "dumb mirror." No
DXtrade client, no webhook emitter, no new order-path code in the bot. The whole
job becomes "run the right strategy combo on a prop-gated master account and
point MetaCopier at it."

## 4. Recommended architecture (Mode A)

```
                 our infra                          MetaCopier cloud        Breakout
  ┌─────────────────────────────────────┐        ┌────────────────┐     ┌──────────┐
  │ ict-trader-live                      │        │  copier:        │     │ DXtrade  │
  │   coordinator.multi_account_execute  │        │  master→slave   │     │ funded   │
  │     └─ prop-master account (Bybit)   │──fills─▶│  × multiplier   │──▶ │ account  │
  │         type: prop                   │  (API) │  risk caps      │ API │ (slave)  │
  │         PropRiskManager(breakout.yaml)│        │  webhook events │     └──────────┘
  └─────────────────────────────────────┘        └───────┬────────┘
                   ▲                                       │ webhook (HTTP POST)
                   │ which strategies/combo                ▼
        prop-firm-testing-tool (PR #3813)        runtime_logs/metacopier_events.jsonl
                                                  (observe what the slave actually did)
```

- **The prop-master account is `bybit_1`** (operator decision 2026-06-16) — the
  existing Bybit **demo** account (`account_class: paper`), chosen because it
  carries a high demo balance so its trades don't get floored on margin. Using
  it as master means **(a) no new subaccount and no new GitHub secrets** — its
  creds (`BYBIT_API_KEY_1`/`BYBIT_API_SECRET_1`) are already wired — and **(b)
  zero real capital on our side** (demo money), so the only money at risk is the
  $45 eval + the MetaCopier subscription. `PropRiskManager` (Breakout 1-Step
  Classic ruleset: 10% target / 3% daily / 6% static DD) gates what bybit_1
  trades, so the mirrored slave stays inside the rules **by construction**.
  - **Open question this raises:** can MetaCopier read a Bybit **demo** account
    as its copy source? (Bybit demo has API access — the bot already trades it —
    but MetaCopier may expect a live Bybit account.) Confirm in MetaCopier
    before relying on it; if demo isn't supported, fall back to a small dedicated
    live subaccount (`BYBIT_API_KEY_3`/`_SECRET_3`).
  - **Roster scoping:** bybit_1 currently runs the FULL demo roster on BTCUSDT +
    ETHUSDT. MetaCopier must be configured to mirror **only Breakout-available
    symbols** (filter to BTC; drop ETH if Breakout doesn't offer it), and we may
    narrow bybit_1's copied set to the evaluator's survivor combo.
- **MetaCopier** holds the DXtrade slave credentials (entered in *its* UI, never
  in our repo) and the multiplier. We never touch DXtrade directly.
- **The webhook** posts trade events back to a small read-only sink
  (`runtime_logs/metacopier_events.jsonl` via a new Tier-1 `/api/bot/metacopier`
  ingest or a static log) so the dashboard can show what the slave executed vs
  what the master intended — the only thing we *add*, and it's observe-only.

## 4a. Tech stack — end-to-end trade path

What actually carries an order from a strategy decision to a fill on the
Breakout account, component by component and hop by hop (Mode A):

| # | Stage | Component (where it runs) | Tech / protocol |
|---|---|---|---|
| 1 | Signal → order package | `strategy_signal_builders` → `order_package()` (our VM, `ict-trader-live`) | Python 3, pandas/numpy on OHLCV |
| 2 | Netting + intent aggregation | `src/runtime/intents.py::aggregate_intents` (our VM) | in-process Python — same as live |
| 3 | Per-account routing + **prop gate** | `coordinator.multi_account_execute` → `PropRiskManager.evaluate()` (our VM) | in-process; reads `breakout.yaml` ruleset |
| 4 | Order placement on the **master** | `execute.py::execute_pkg` → Bybit client (our VM) | **Bybit V5 REST over HTTPS** via CCXT/pybit |
| 5 | Master fill ✦ leaves our infra | the prop-master **Bybit account** | Bybit matching engine; fill visible on the account's API |
| 6 | Detect fill on master | **MetaCopier cloud** (SaaS) | reads the master via **Bybit API key** (operator-entered in MetaCopier) |
| 7 | Map + scale + place on slave | **MetaCopier cloud** | symbol map + **multiplier**; POSTs to **Breakout's DXtrade REST API** (TP/SL = multiple requests) |
| 8 | Fill on Breakout | **DXtrade** funded account (Devexperts) | the slave position — the firm's capital |
| 9 | Event back to us (observe-only) | MetaCopier **webhook** → `/api/bot/metacopier` (our VM FastAPI) → `runtime_logs/metacopier_events.jsonl` → dashboard | HTTP POST in; JSONL on disk; Streamlit render |

**The trust/ownership boundary sits between hops 5 and 6.** Everything up to the
master fill is *our* stack (the existing trader — unchanged). Everything from
the master fill to the Breakout slave is *MetaCopier's* cloud (Bybit-read +
DXtrade-write), which is exactly why we never hold DXtrade credentials or run a
DXtrade client of our own. Hop 9 is the only new code on our side, and it's
read-only.

So the literal answer to "what sends trades to Breakout": **our bot places on
Bybit as it already does; MetaCopier (cloud, paid monthly) reads that Bybit fill
and re-places it on the Breakout DXtrade account.** No direct network path from
our VM to Breakout exists or is needed — which is the whole point of using a
copier instead of reviving the direct `DXtradeClient`.

### What we run vs what we rent

| Layer | Who runs it | Cost |
|---|---|---|
| Strategy engine, netting, prop-risk gate, Bybit order placement | **us** — existing `ict-trader-live` on the OCI Ampere VM | $0 (already running) |
| Master fill → DXtrade slave copy | **MetaCopier** (their cloud) | monthly subscription |
| The Breakout funded account on DXtrade | **Breakout** (the prop firm) | $45 one-time eval (1-Step Classic) |
| Observe-only webhook sink + dashboard panel | **us** — FastAPI + Streamlit | $0 |

## 5. Prop-rule enforcement (defence in depth)

Three layers, in order of authority:

1. **Pre-trade (ours, authoritative):** `PropRiskManager` on the master,
   seeded from `config/prop_rulesets/breakout.yaml` (the same ruleset the
   evaluator uses). It already models daily-loss cap, max-drawdown, position
   size, min-days, overnight/weekend gates — extended only as needed for the
   **static 6% DD** and (if confirmed) a **consistency rule**. The master simply
   doesn't open the trade that would blow the slave.
2. **Strategy selection (ours, offline):** only run the strategy combo that the
   **prop-firm-testing-tool (PR #3813)** showed survives the 1-Step Classic
   ruleset on history. Garbage combos never get deployed to the master.
3. **MetaCopier risk caps (vendor, belt-and-braces):** set MetaCopier's own
   account-level loss cap at/under Breakout's daily loss as a backstop in case
   of copier divergence.

This is why Mode A is clean: the prop rules live where they already live (our
risk manager), not smeared across a vendor we don't control.

## 6. Sizing & symbol mapping

- **Sizing:** the master is sized so a chosen MetaCopier **multiplier** (start
  near 1:1) lands the slave inside Breakout's per-trade and daily risk. Because
  Bybit P&L mechanics differ from a forex-style broker, the multiplier is
  calibrated by **observing realized slave vs master profit on the first paper
  copies** and adjusting — exactly as MetaCopier's docs advise. Document the
  calibrated value in the account row.
- **Symbol mapping:** our master trades Bybit `BTCUSDT`; the Breakout/DXtrade
  symbol is likely `BTCUSD` (or a firm-specific symbol). Confirm Breakout's
  **available instrument list** and map master↔slave symbols in MetaCopier's
  per-copier symbol mapping. Any master symbol with no slave instrument must be
  **excluded from the prop-master's roster** (the evaluator already enumerates
  the roster, so this is a roster filter, not new code).
- **TP/SL:** DXtrade sets TP/SL via multiple requests (non-atomic) — accept a
  brief window where the slave may hold a position before its stop is live;
  MetaCopier manages this, but the webhook log should be watched for
  stop-attach failures on the slave.

## 7. Credentials & secrets

Per the autonomy contract (`credentials-and-vm-mutations` skill): **the operator
originates secret values; everything else is automated.**

- **MetaCopier account + DXtrade slave creds:** entered by the operator in
  MetaCopier's web UI (the slave login + the MetaCopier subscription). These
  never enter our repo or VM.
- **Bybit master API keys:** with the master = **bybit_1** (current decision),
  the bot already has its creds (`BYBIT_API_KEY_1`/`BYBIT_API_SECRET_1`) — **no
  new GitHub secret is needed bot-side.** Separately, the operator pastes a
  bybit_1 API key into **MetaCopier's** dashboard (read access, so MetaCopier
  can see the master's fills) — that copy lives in MetaCopier, not our repo.
  (Only if we fall back to a dedicated live subaccount would we add
  `BYBIT_API_KEY_3`/`BYBIT_API_SECRET_3` via `init-actions-secrets` +
  `sync-vm-secrets`.)
- **MetaCopier webhook ingest:** if we add the observe-only sink, its shared
  secret is an Actions secret too.

## 8. Failure modes & safety

- **Copier lag / divergence:** the slave can drift from the master (latency,
  rejects, multiplier rounding). Mitigation: MetaCopier risk caps (§5.3) + the
  webhook log surfaces divergence; the master's `PropRiskManager` keeps the
  *intended* path inside the rules.
- **Slave-only breach:** if the slave breaches despite the master being clean
  (e.g. a DXtrade-side gap), Breakout closes the slave — our master keeps
  running harmlessly. We lose the $45 eval + the month's MetaCopier fee, not
  real capital.
- **Kill switch:** pause the copier in MetaCopier (vendor) **and/or** flip the
  prop-master account `mode: dry_run` via the existing `set-account-mode`
  operator action (ours). Two independent stops.
- **No new always-on daemon on our VMs** — MetaCopier is the cloud component;
  our side is the existing trader + an optional log sink.

## 9. Build plan (after design approval + ToS confirmation)

1. **Operator (gate-zero):** confirm Breakout permits copiers/automation; sign
   up for MetaCopier; open the Breakout 1-Step Classic account; connect the
   DXtrade slave in MetaCopier.
2. **Tier-3 config PR:** add the `prop-master` account to `config/accounts.yaml`
   (`type: prop`, `account_class: paper`, the survivor strategy combo from
   PR #3813's matrix, `mode: dry_run` initially). Reuses the existing prop loader
   path — no order-path code.
3. **Tier-1:** finish `config/prop_rulesets/breakout.yaml` (confirmed numbers)
   and wire `PropRiskManager` to read it (small extension for static-DD +
   consistency if confirmed). + tests.
4. **Tier-1 (optional, observe-only):** the MetaCopier webhook sink
   (`/api/bot/metacopier` ingest → `runtime_logs/metacopier_events.jsonl`) +
   a dashboard panel showing slave vs master fills.
5. **Soak:** master `mode: dry_run` → verify the *intended* order stream is
   rule-clean against the evaluator's prediction. Then operator-gated flip to
   `mode: live` on the master with a small multiplier; calibrate the multiplier
   from observed slave P&L; widen only after a clean soak.
6. **Live promotion stays Tier-3**, operator-approved, exactly like MGC/MHG.

## 10. Open questions for the operator

1. **ToS:** does Breakout allow trade-copiers / automated execution at all?
   (gate-zero — blocks everything.)
2. **Master account:** dedicated Bybit subaccount, or reuse an existing one?
   (Reusing a real account double-counts exposure — a dedicated master is
   cleaner.) Can MetaCopier read a Bybit **demo/paper** account as source, or
   must the master be a live Bybit account? (Determines whether our side risks
   any real capital at all.)
3. **Symbols:** what crypto instruments does the Breakout account actually
   offer, and what are their DXtrade symbols (for the master↔slave map)?
4. **Mode confirmation:** OK to proceed with **Mode A (account-copy)** as
   designed, or do you want **Mode B (webhook source)** instead?
5. **MetaCopier plan:** which subscription tier (account count / features) — and
   confirm the monthly cost for the record.

## 11. Honesty / limits

- MetaCopier's exact capabilities, plan limits, and pricing are **external facts
  re-verify at signup** — this doc reflects their public site/docs on
  2026-06-16, not a contract.
- A copier adds **latency and a divergence surface** the direct-API path
  wouldn't have; the slave is never a perfect mirror. The defence-in-depth in §5
  bounds the *risk*, not the *tracking error*.
- This doc does **not** change any runtime behaviour. It is the plan; the build
  is gated as in § 9.

## Sources

- [MetaCopier — supported platforms (Bybit ⇄ DXtrade)](https://metacopier.io/)
- [MetaCopier docs — features / specifications](https://docs.metacopier.io/features/specifications)
- [MetaCopier docs — basic features (webhook, risk, multiplier)](https://docs.metacopier.io/features/basic-features)
- [Best DXtrade copiers for prop firms (QuantVPS)](https://www.quantvps.com/blog/dx-trade-copiers)
- [DXtrade prop-trading technology](https://dx.trade/prop-trading-technology/)

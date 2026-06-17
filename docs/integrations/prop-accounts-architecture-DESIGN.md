# Prop accounts — scalable architecture (DESIGN, 2026-06-17)

> **STATUS: DESIGN.** The blueprint for making the prop-trading work first-class
> and **scalable to N prop accounts with different rules**, integrated into the
> *standard* strategy flow (not a silo). Tier gates are marked per section; the
> live-wiring pieces (Tier-3/2) ship as a DRAFT PR pending validation + operator
> approval. Supersedes the ad-hoc single-account assumptions in
> `breakout-poc-manual-bridge-DESIGN.md` (which stays as the executor/Comet ref).

## Why this exists (operator directives, 2026-06-16/17)

1. The promising research finds (e.g. `trend_donchian` +EV on high-vol Bybit
   alts) must become **real strategies in the system** — their own variant/setup
   — not siloed research.
2. **Per-account backtest compatibility must be a standard, mandatory part of
   every strategy flow**: each account (especially each prop account) has its own
   rules, so every new/changed strategy is evaluated against *every* account's
   ruleset → a strategy×account matrix that says, top-down, **which strategies
   belong on which account**.
3. The prop account's "integration" is **not a broker API** — it **emits a
   Telegram ping** that an assistant (Comet/Claude) picks up and executes.
4. The ping must be **per-account-aware**: with >1 prop account on different
   rules, the message must render each account's variation AND **explicitly flag
   any discrepancy** (place on A, skip on B, different size, …) so the executing
   assistant uses the right variation for the account it is on. Build multi-account
   now even though only one prop account exists today — no single-account bugs later.

## The one core abstraction: account → ruleset binding

Everything below hangs off a single map: **each account declares the ruleset it
is evaluated and sized against.**

- **Prop accounts** → a prop ruleset file `config/prop_rulesets/<firm-plan>.yaml`
  (breach rules + `economics` + sizing; e.g. the existing `breakout.yaml`).
- **Real / paper broker accounts** → a **`standard` ruleset** (the account's own
  `risk_pct`, no breach/economics) — for which the "compatibility test" is just
  the ordinary net-of-fee performance backtest.

Binding lives in `config/accounts.yaml` as a per-account field, e.g.:

```yaml
accounts:
  bybit_2:        { backtest_ruleset: standard }          # real money, normal risk
  breakout_1:     { exchange: breakout, backtest_ruleset: prop_rulesets/breakout.yaml }
```

Resolved by one helper (`src/prop/account_rulesets.py::ruleset_for_account`)
returning a `PropRuleset` (the `standard` case = a no-breach ruleset with the
account's risk). **No code assumes a fixed account or a single ruleset.** Adding a
prop account = an `accounts.yaml` entry + a ruleset file. (Tier-1 to build the
resolver + the `standard` default; the per-account YAML field is additive.)

## 1. Mandatory per-account compatibility matrix (standard strategy flow)

A new step, **required** by the `backtesting` and `new-strategy` skills:

> Before any strategy is proposed for live routing, run it through **every
> account's ruleset** and produce a **strategy×account compatibility matrix**.

- Runner: `scripts/prop/account_compat_matrix.py --strategy <name> --data <feed>`
  → for each account, resolve its ruleset and evaluate:
  - prop ruleset → the cost-aware EV + survival gate (`montecarlo_prop`/`evaluate_prop`).
  - `standard` ruleset → net-of-fee performance backtest (the per-strategy harness).
  - Emits one row per account: pass?/EV/survival (prop) or net-PnL/expectancy/maxDD
    (standard), plus a **recommended verdict** (route / don't route).
- Output: `runtime_logs/prop_eval/<date>/compat_matrix.{md,json}` — the top-down
  "which strategies on which account" answer.
- This makes the prop-eval engine a *standard* gate, not a one-off. (Tier-1
  tooling + skill-doc updates.)

## 2. Strategy promotion (the alt variant)

`trend_donchian` already exists (live on BTC for bybit_1/2). The alt edge is a
**distinct strategy variant with its own setup**, not a reroute of the BTC one:

- New `config/strategies.yaml` block(s) — e.g. `trend_donchian_sol` /
  `trend_donchian_eth` (or one `trend_donchian_alt` parametrised per routed
  symbol) — reusing `src/units/strategies/trend_donchian.py` with alt-tuned
  params, its own `signal_prefixes`, risk, changelog, descriptions, tests
  (the `new-strategy` skill, treating the existing unit as the engine).
- **Gate:** promotion requires the compat matrix (§1) to PASS on the target
  account **on real Bybit perp data + a walk-forward** — the research used a
  Binance-spot proxy with realised-only-optimistic EV. (Tier-3 — DRAFT PR,
  operator-approved before merge/deploy.)

## 3. Per-account trade ticket + discrepancy flagging

One signal → a `PropTicket` that is a **list of per-account instructions**:

```
PropTicket(signal, accounts=[
  AccountLeg(account_id, ruleset, side, size, entry_band, sl, tp, valid_until,
             decision="place" | "skip", reason),
  ...
])
```

- Each `AccountLeg` is computed from **that account's** ruleset: size =
  `risk_pct × account_size / stop_distance`; `decision="skip"` (with reason) when
  the trade can't fit the account's live headroom (daily-loss / static-DD
  cushion) or its rules forbid it.
- `render_ticket` (extends `src/prop/breakout_ticket.py`):
  - **1 account** → one instruction block (today's behaviour; no banner).
  - **≥2 accounts** → a **discrepancy banner** when legs differ
    (`⚠ ACCOUNTS DIFFER — use the block for the account you are trading`) followed
    by one labelled block per account. Identical legs collapse to one block with
    "applies to: A, B".
- The executing assistant is told (in the ticket preamble) to **execute only the
  block matching its account**. (Tier-1 to build the multi-account ticket model +
  renderer; it is observe-only formatting.)

## 4. Telegram-ping executor (the prop "integration")

- Flesh out the existing `EXCHANGE_MAP["breakout"]` (`BreakoutAPI` stub) in
  `src/units/accounts/integrator.py` into a **Telegram-ping executor**: `place()`
  builds the account's `AccountLeg`, adds it to the in-flight `PropTicket`, and
  emits `prop_signal` (FCM + the prop Telegram bot) instead of hitting a broker
  API. Observe-only; journals order packages like any account (`db-wiring`).
- When several prop accounts route the same signal on one tick, their legs are
  **aggregated into a single `PropTicket`** so the operator/assistant gets one
  message with the per-account breakdown + discrepancy banner (not N messages).
  (Tier-2/3 — new execution integration; DRAFT.)

## 5. Bot routing

`prop_signal` Telegram sends go to the **dedicated prop-account bot** (the
repurposed comms bot — see the bot-restructure work); each leg/message is tagged
with its `account_id`. FCM `prop_signal` pushes likewise carry `account_id`.
(Tier-2.)

## Scalability invariants (the "no future bugs" contract)

- **Accounts are always a list.** No module hardcodes "the prop account" or one
  `account_size`/`risk_pct`. The ticket, the executor aggregation, and the compat
  matrix all iterate accounts.
- **Ruleset is data.** A new prop firm/account = a YAML ruleset + an
  `accounts.yaml` entry; zero code change.
- **Discrepancy is explicit, never implicit.** Differing per-account legs always
  surface a banner; the assistant never has to infer which variation to use.
- **The compat matrix is mandatory** in the strategy flow — a strategy is never
  routed to an account it wasn't evaluated against under that account's rules.

## Tier summary

| Piece | Tier | Ship |
|---|---|---|
| account→ruleset resolver + `standard` ruleset | 1 | merge |
| compat-matrix runner + skill mandates | 1 | merge |
| multi-account `PropTicket` model + renderer (discrepancy banner) | 1 | merge |
| `trend_donchian` alt variant config | 3 | DRAFT (real-perp validation + operator OK) |
| Telegram-ping executor + prop account in `accounts.yaml` | 2/3 | DRAFT |
| bot restructure routing (prop bot) | 2 | DRAFT (operator token) |

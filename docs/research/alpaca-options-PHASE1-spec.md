# Alpaca L3 Options — Phase-1 Implementation Spec (paper options MVP)

**Date:** 2026-06-27 · **Branch:** `claude/alpaca-level3-options-research-djr017` · Parent: [`alpaca-options-l3-research-memo.md`](./alpaca-options-l3-research-memo.md) (Hybrid path, §9).

**Goal of Phase 1:** prove the full options loop end-to-end **in paper** — chain discovery → premium/max-loss sizing → multi-leg order submit → snapshot greeks/IV read → poll-based expiry/assignment monitor → position/P&L surfacing — on **one underlying (XLF)**, starting with the smallest slice. No real money until the Phase-3 gate (memo §7).

**Operator-confirmed facts (2026-06-27):** `alpaca_live` total capital ≈ **$150**; **L3 approval active**; **free (15-min indicative) data tier** — real-time OPRA ($99/mo) deferred to the Phase-3 gate. The decoupled v1 selects structure from IV-rank/term-structure, not the regime ML (which is itself an unvalidated, designed-not-run A/B — see the memo's reconciled §4 note).

---

## Architecture decisions (grounded in the current code)

1. **Hand-roll the options REST, do NOT add `alpaca-py`.** The repo has no `alpaca-py` dependency; `AlpacaClient` is deliberately raw `requests` with retCode-style envelopes (`src/units/accounts/alpaca_client.py`). Options chain/snapshot/mleg are a handful of documented endpoints — adding a heavy SDK to the live trader isn't warranted. New code mirrors the existing client's style.
2. **Options sizing is a separate pure function, not a hack into `RiskManager.position_size`.** That sizer is hard-wired to `|entry − SL|` price-distance (`src/units/accounts/risk.py::_size_unbounded`); a spread has no such stop. Options size off **max-loss per contract × 100** → `contracts = floor(budget / max_loss_per_contract)`, refuse-sub-1 (mirrors the futures whole-contract rule). Lives in `src/units/accounts/options_sizing.py`.
3. **Multi-leg = atomic `order_class="mleg"` (2–4 legs), no equity leg.** Per the verified Alpaca contract. Debit structures only for the $150 pilot (credit needs a ≥$2k margin account — memo §0).
4. **Assignment/expiry is POLL-based.** Alpaca does **not** push assignment over the websocket; the monitor must poll `GET /v2/account/activities` (non-trade activities). Auto-exercise of ITM-by-$0.01 longs at expiry is Alpaca-side.
5. **Greeks/IV come from the snapshot endpoint server-side** — no Black-Scholes engine for live. (Open question: does the *free indicative* feed populate them? Answered by the Phase-0 probe, not assumed.)
6. **Prop-style isolation.** Options trades must never blend into the existing real-money/paper equity KPIs in a way that misrepresents them; surface options positions/P&L as their own class (follow-up in the surfacing step).

---

## Build slices (ordered; tier in brackets)

### ✅ Slice 0 — Foundation (THIS PR; Tier-1, all dormant / un-wired)
- `src/units/accounts/options_sizing.py` — pure premium/max-loss sizer (debit + a credit helper for Phase-4). **Tested** (`tests/test_options_sizing.py`, 12 cases).
- `src/units/accounts/alpaca_options_data.py` — **read-only** chain discovery (`/v2/options/contracts`) + snapshot greeks/IV (`/v1beta1/options/snapshots/{u}`), raw-requests, retCode envelopes, free-`indicative`-feed default. Pure helpers tested (`tests/test_alpaca_options_data.py`).
- `scripts/options/probe_alpaca_options.py` — read-only Phase-0 probe (run on the live VM): confirms L3 active, measures free-feed greeks/IV coverage, prices a sample near-ATM XLF contract vs the $150 budget.

> Nothing in Slice 0 is imported by the order path or the live runtime — these are dormant modules + a diagnostic. Safe to merge inert.

### ⏭ Slice 1 — Phase-0 verification (no code; run the probe)
Run `probe_alpaca_options.py` on the live VM via the ops relay. **Gate:** L3 confirmed active; record whether the free feed returns greeks/IV (if not: compute IV locally from quotes, or budget the $99/mo OPRA plan for Phase-3). Capture a real XLF chain snapshot as the fixture for Slice 2 tests.

### ✅ Slice 2 — Options ORDER execution (built dormant; WIRING is Tier-2/operator-gated)
- `src/units/accounts/alpaca_options_exec.py` — **its own module** (the live equity bracket path in `AlpacaClient` is untouched): `place_spread()` (atomic 2-4 leg `order_class="mleg"`), `place_single_option()` (the degenerate long-option smoke case — mleg needs ≥2 legs), `option_positions()`, `close_position()`.
- Pure builders `build_mleg_body` / `build_single_option_body` with full validation; tested (`tests/test_alpaca_options_exec.py` — request shapes + guards). No live calls in CI.
- **Dormant**: nothing imports it. Merging the dormant module is inert; *wiring it into a strategy/account order path* is the Tier-2/3 operator-gated step (Slice 3).

### ✅ Slice 3a — Strike/expiry selector (built dormant; Tier-1)
- `src/units/accounts/options_selector.py` — pure `select_debit_vertical()`: given the normalised chain + direction + underlying price + DTE band, picks the ~ATM long strike and the next strike in the profit direction (width auto-derived, so $1/$5 spacing is handled), computes net debit / max-loss / max-gain / breakeven, with an **opt-in IV-rank gate** (honest: true IV-rank needs a trailing-IV store this repo lacks — a later slice). Composes with the Slice-0 sizer and Slice-2 executor (`to_option_legs`). Tested (`tests/test_options_selector.py`, 11 cases incl. the full selector→sizer→legs compose). Places nothing.

### ✅ Slice 3b — Account-scoped options expression (built; ships INERT, activation operator-gated)
- **Overlay baseline (operator decision 2026-06-27):** reuse the existing equity signals — when a GDX/SLV equity strategy fires, the `alpaca_options_paper` account expresses it as a debit vertical. The strategies stay pure signal generators; options is an **account-scoped execution capability**, not a strategy hack (respects the strategy-agnostic-execution invariant the `new-strategy` skill flags).
- `src/units/accounts/options_overlay.py` — the seam composition: `account_expresses_options()` (the gate) + `build_chain_from_responses()` (contracts+snapshot join) + `place_options_expression()` (chain → `select_debit_vertical` → `size_debit_structure` → `place_spread`; refusal places nothing). Tested (`tests/test_options_overlay.py` — gate, join, happy/dry/refusal with injected fake clients).
- `src/units/accounts/execute.py` — **one branch** at the `_submit_order` seam: an account with an `options:` block routes opens through the overlay; equity path byte-for-byte unchanged otherwise; reduce-only (close) falls through to equity (options close is Slice 4); a refusal journals a rejection row.
- `config/accounts.yaml` — new **`alpaca_options_paper`** account (paper host), `options: {express_as: debit_vertical, max_loss_per_trade_usd: 60, …}`, routes `slv_trend_1h`/`slv_pullback_1d`/`gdx_pullback_1d`, `symbols: [SLV, GDX]`. **Ships INERT** behind a creds-unset gate (`ALPACA_API_KEY_ID_OPTIONS`/`*_SECRET_KEY_OPTIONS` — a dedicated paper key pair that doesn't exist yet → `configured: False`).
- **Operator hand-off to activate:** create a second Alpaca paper account, add its key pair to Actions secrets, run `sync-vm-secrets`. Then it paper-soaks debit verticals on GDX/SLV. (Tier-3: `accounts.yaml` merge needs operator approval.)

### ⏭ Slice 4 — Monitor: expiry / assignment / multi-leg (Tier-2)
- `order_monitor` poll of `/v2/account/activities` for assignment/exercise/expiry; multi-leg position tracking; expiry-window handling; the ~1-day paper NTA lag noted.

### ⏭ Slice 5 — Config + surfacing (Tier-2/3)
- `accounts.yaml` options block (`options: {enabled, level, data_feed}`); options contract modeling in config; positions/P&L surfaced as their own class on `/positions` + dashboard/Android.

---

## Honest gates carried forward
- **Free-feed limit:** 15-min indicative data is fine for the paper MVP; **Phase-3 real-money needs OPRA ($99/mo)** — do not place real-money options off delayed data.
- **Backtest fidelity:** Alpaca history only to Feb 2024; primary validation is **forward paper-soak**, not deep historical backtest (memo §5).
- **Real-money pilot (Phase-3)** stays Tier-3 / operator-gated regardless of how clean the paper soak is.

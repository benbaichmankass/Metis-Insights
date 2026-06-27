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

### ⏭ Slice 2 — Multi-leg execution in `AlpacaClient` (Tier-2, order-path — needs operator OK before merge)
- `place_option_spread(legs, *, limit_price, qty, tif="day")` → `POST /v2/orders` with `order_class="mleg"`, per-leg `position_intent`.
- `option_positions()` (read), `close_option_position(...)` (submit the closing mleg).
- A single long option is the degenerate 1-leg case used for the first smoke test (smallest plumbing slice).
- Tests against the Slice-1 fixture (request-shape assertions; no live calls in CI).

### ⏭ Slice 3 — Strike/expiry selection + a paper options strategy (Tier-2/3)
- A small selector: given underlying + direction + target DTE window + width, pick the debit-vertical legs from the chain (IV-rank gate on entry).
- Wire as a **paper-only** strategy on `alpaca_paper` (the existing paper account), `execution: shadow`→`live` per the standard gate. Greenfield vs overlay decided here per the memo's per-strategy verdict.
- `account_compat_matrix` extension to score the paper-soak ledger.

### ⏭ Slice 4 — Monitor: expiry / assignment / multi-leg (Tier-2)
- `order_monitor` poll of `/v2/account/activities` for assignment/exercise/expiry; multi-leg position tracking; expiry-window handling; the ~1-day paper NTA lag noted.

### ⏭ Slice 5 — Config + surfacing (Tier-2/3)
- `accounts.yaml` options block (`options: {enabled, level, data_feed}`); options contract modeling in config; positions/P&L surfaced as their own class on `/positions` + dashboard/Android.

---

## Honest gates carried forward
- **Free-feed limit:** 15-min indicative data is fine for the paper MVP; **Phase-3 real-money needs OPRA ($99/mo)** — do not place real-money options off delayed data.
- **Backtest fidelity:** Alpaca history only to Feb 2024; primary validation is **forward paper-soak**, not deep historical backtest (memo §5).
- **Real-money pilot (Phase-3)** stays Tier-3 / operator-gated regardless of how clean the paper soak is.

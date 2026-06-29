# Design Proposal — Faster profit-banking / dynamic exits for the PROP account (`breakout_1`)

> **Status:** Read-only research/design. **Every live-affecting change (exit logic, stops,
> take-profit, strategy params, sizing, ticket path) is Tier-3 — PROPOSE ONLY, operator-approved +
> backtest-gated.** No code/config/VM state touched. Origin: the 2026-06-29 optimization
> investigation (delegated research). Scope: the Breakout 1-Step Classic $5k prop account, a
> Telegram-ping manual bridge.

## 0. Diagnosis (one paragraph)

The 5-day, $20–30-PnL trade is a direct consequence of **exit geometry** on the routed prop
strategies, compounded by the manual bridge having no live exit management. Three of the four
strategies routed to `breakout_1` (`trend_donchian_sol`, `trend_donchian_eth`, `eth_pullback_2h`)
use `tp_r: 50.0` (a far ~9.9%-clamped sentinel — "TP effectively disabled",
`config/strategies.yaml:734,768,1536`; `trend_donchian.py:98`) and `trail_mult: 5.0` (a wide
Chandelier trail) — a "let one winner run for weeks" profile. On a daily-swap prop venue with a
static 6%/$300 max-DD killer and a BANK-ASAP withdrawal mandate, **time is the cost** (swap drag +
breach exposure for zero upside), so that profile is wrong. The repo already has the building
blocks to fix it (an `ExitPlan` partial-TP ladder + materializer + observe-only soak; a Verdict
`close_qty_pct`/`next_tp` schema; one already-tightened variant `eth_pullback_prop_2h`).

## 1. Current exit machinery (cited)

- **Single SL/TP at entry** — `trend_donchian.order_package()` (`trend_donchian.py:206-218`):
  `tp = entry ± tp_r·risk` clamped to ±9.9% (`:98`); with `tp_r=50` the TP is the clamp (a
  sentinel that "practically never fires", `:328`). The **trail is the sole profit-exit** (`:74-81`).
- **Monitor** — `trend_donchian.monitor()` (`:318-419`): SL-cross close, TP-cross close (rare),
  Chandelier trail ratchet using the frozen entry-time ATR. **None of this runs for prop** (§2).
- **Verdict schema is richer than what these strategies use** — `strategy_verdict.py:30-52`
  supports `action:"close"` + **`close_qty_pct`** + **`next_tp`** (partial banking + runner roll).
  `turtle_soup.monitor()` already drives a TP1 partial → TP2 runner (`turtle_soup.py:450-538`). So
  **partial banking is solved on the server-monitor (API) side** — the trend/pullback strategies
  just don't emit it, and prop can't consume it.
- **ExitPlan exit-ladder soak (the half-built fix):** `exit_plan.py` (schema +
  `build_exit_plan_from_legacy`), `exit_plan_materializer.py` (`materialize_exit_plan` → concrete
  lot-rounded reduce-only rungs), `exit_ladder_soak.py` (logs the laddered exit that *would* be used
  vs the single target placed; `/api/bot/exit-ladder/soak`). For prop it's already wired:
  `breakout_executor.emit_prop_ticket()` calls `record_exit_ladder_soak(venue="prop", …)`
  (`breakout_executor.py:143-157`). **Observe-only — nothing reads `exit_plan_state` back to drive
  an order** (`coordinator.py:2906-2913`, grep-confirmed). Graduating P4 = emit the ladder in the
  ticket + a backtest gate.

## 2. Prop execution + economics (cited)

- **Manual bridge:** `breakout_1` (`config/accounts.yaml:667-705`) emits a `prop_signal` ticket
  (`breakout_executor.py:69-227`), returns a `prop-manual-<uuid>` marker (`:132`), opens no socket;
  the per-tick `order_monitor` never sees prop positions (`prop_monitor_pulse.py` exists for this).
  **No amend/modify-ticket path exists in `src/prop/`** — so a "dynamic stop" must be **baked into
  the opening ticket** (multi-rung bracket / close-by), not a server loop or per-tick amend.
- **DD killers** (`config/prop_rulesets/breakout.yaml`): 3% daily = $150 (intraday equity); 6%
  STATIC max-DD = $300 off starting balance (not trailing). BANK-ASAP withdrawal economics.
- **EV/survival** — `account_rulesets.unit_for_account()` → `montecarlo.run_ev_montecarlo()`
  block-bootstraps the strategy's R-ledger into renewable-account paths; **a slow trade is uniquely
  bad** because the synthetic clock advances by inter-trade gaps (fewer trades fit a horizon → fewer
  banking windows → lower net-$), the static floor's breach window is longer, and BANK-ASAP wants
  frequent realizations. The daily-loss check is realised-only (conservative on the killer).

## 3. Why the 5-day trade happened

Primarily **exit geometry**: 3 of 4 routed strategies run `tp_r:50` + `trail_mult:5.0` (run-for-weeks)
on 1h/2h bars → a position drifting near entry never hits the sentinel TP, never trips the wide
trail, just sits. The tightened sibling **`eth_pullback_prop_2h`** (`tp_r:6.0`, `trail_mult:3.5`,
`strategies.yaml:1543-1582`) proves tighter exits raise prop EV (post-swap +$421/5y, 12-mo EV +$603
@75.9%, 4/4 folds). Secondary: the manual bridge means even the wide trail never reaches the prop
position (it's frozen at the entry SL unless a human trails it).

## 4. Options (feasibility on the manual bridge · backtest plan)

- **(a) Graduate the ExitPlan partial-TP ladder to prop tickets [RECOMMENDED]** — bake a multi-rung
  bracket into the opening `prop_signal` (e.g. 50% at +1.5R, 25% at +3R, runner trails). **High
  feasibility, no mid-trade amend** (whole exit in one ticket). Touches `emit_prop_ticket` +
  ticket renderer + `prop_report.py` (partial-fill report-backs). One code gap: `backtest_system.py`
  models `monitor()` `close_qty_pct` partials (so a partial-emitting prop `monitor()` is testable
  today) but doesn't fill an ExitPlan *ladder* for non-partial strategies.
- **(b1) Tighter initial trail/`tp_r` [Phase 0, cheapest]** — apply the `eth_pullback_prop_2h` recipe
  (`trail_mult` 5.0→~3.5, `tp_r` 50→~6) to the three un-tightened prop strategies, as **new
  prop-only variant blocks** (don't touch demo/real-money cells). Config-only, directly backtestable.
- **(b2) Live amend-trailing — AVOID** (one human ping per ratchet; infeasible on the bridge).
- **(c) Faster/shorter-TF strategies — DEFER** (ping-volume strain; higher bar).
- **(d) Time-based "close-by" rung** — bake "close at market if not ≥ +1R by N bars" into the ticket;
  pairs with (a).

## 5. RECOMMENDATION

- **Phase 0 (cheapest, do first):** apply the proven tightened-exit params (`trail_mult` ~3.5,
  `tp_r` ~6) to `trend_donchian_sol`/`trend_donchian_eth`/`eth_pullback_2h` as prop-only variants.
  Config-only; backtest via `scripts/backtest_system.py` (trail/TP run through `monitor()`), gated
  by `scripts/prop/account_compat_matrix.py` → `run_ev_montecarlo` (bar = the `eth_pullback_prop_2h`
  evidence).
- **Phase 1 (the real turnover lever):** graduate the **ExitPlan partial-TP ladder + a time
  close-by rung** into the opening prop ticket (the P4 the soak was built for; no mid-trade amend).
  Needs a partial-emitting prop `monitor()` (or a harness exit-ladder fill mode — the one code gap).
- **Avoid (b2); defer (c).** All Tier-3, prop-EV-gated; ship each as a new prop-variant block with a
  one-line ROLLBACK (`execution: shadow` / restore the param).

## 6. Backtest validation

1. Soak (`/api/bot/exit-ladder/soak?venue=prop`) — confirm `differing_pct` high (ladder ≠ single
   target). 2. Strategy backtest (`backtest_system.py`) on each prop variant with tightened
   exits/ladder. 3. **Prop EV/survival gate** (`account_compat_matrix.py` → `run_ev_montecarlo` under
   `breakout.yaml`): faster banking → more horizon-fitting trades → higher `mean_net_usd`/`p_profitable`
   at equal-or-better survival. 4. Compare vs today's single-far-target baseline; require strict
   improvement without worse `p_breach`. 5. Operator approval + reversibility per change.

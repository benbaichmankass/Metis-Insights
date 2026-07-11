# trend_donchian live anomaly — root cause (PERF-20260601-001)

**Date:** 2026-07-11
**Session:** S-TREND-DONCHIAN-ROOTCAUSE (operator-directed #1 next-strategy triage)
**Backlog:** `PERF-20260601-001` (was `in_progress` since 2026-06-01)
**Data:** live→trainer synced `trade_journal.db` (mtime 2026-07-11T00:02 UTC),
pulled via `trainer-vm-diag` relay issues #6136 / #6137 / #6138. All 85
`trend_donchian` BTCUSDT non-backtest rows + the `signals` (audit dual-write) +
`order_packages` reasons.

## TL;DR — verdict

**The "0% win / −198R over 19 live trades" was a snapshot MEASUREMENT ARTIFACT,
not a dead edge and not a broken exit path.** Decomposed against the actual
journal:

- The **−198 figure is 4 genuine fills**, of which **−$196.87 was DEMO (paper)
  money** and **only −$1.56 was real money** (two 0.001-BTC bybit_2 fills). This
  matches the backlog's own `−196.87 / −1.56/4` split exactly.
- The **"19 trades / 0% win"** count was **padded with ~15 NULL-pnl phantom
  rows** — `intent_reduce` / `reconciler_incomplete` re-entry-storm bookkeeping
  legs — so a handful of real losses read as "0 of 19."
- The 4 real fills were **LONG breakouts that stopped out in the late-May BTC
  $75–78k RANGE** — the textbook "trend-breakout loses in chop" failure the
  regime study already identified — NOT an execution/monitor bug.

Two real execution defects were present in that window but **both amplified the
*appearance*, not the real-money loss, and both are already fixed**: the
TP-sentinel exchange-reject storm (no fills produced) and the same-bar re-entry
storm (count padding). Over the **full window (2026-05-25 → 07-10)** the strategy
is **net-positive on both accounts** (demo +$1191, real +$4.77). **Recommendation:
KEEP LIVE — do not demote.** The one genuine forward constraint is that the
real-money account (`bybit_2`) is undercapitalized for BTC and refuses most
signals at `sized_qty=0`, so a real-money track record cannot accrue.

## What the −198R actually was (per-trade evidence)

The 2026-06-01 demotion pull (ad-hoc query, issues #2537/#2539 — **not** the
hardened `/strategy/attribution` endpoint) counted every `strategy_name=
'trend_donchian'` closed row without filtering bookkeeping legs. The pnl-bearing
rows in that window:

| id | acct | class | dir | entry→exit | pnl | exit_reason | class |
|----|------|-------|-----|-----------|-----|-------------|-------|
| 1721 | bybit_2 | **real** | long | 77603.9→76624.6 | **−$1.07** | reconciler_filled | (a) genuine SL stop-out |
| 1722 | bybit_1 | demo | long | 77709.9→76469.1 | **−$105.42** | reconciler_filled | (a) genuine SL stop-out |
| 1731 | bybit_1 | demo | long | 78016.6→77625.9 | **−$91.45** | reconciler_filled | (a) genuine SL stop-out |
| 1732 | bybit_2 | **real** | long | 78016.6→77614.5 | **−$0.49** | reconciler_filled | (a) genuine SL stop-out |

- **Demo sum = −$196.87**, **real sum = −$1.56** — exact match to the backlog.
- All 4 are `reconciler_filled` — the *normal* way a linear-perp exchange-side
  SL is journaled (fade + ict_scalp win through the same path). NOT a phantom.
- All 4 are **LONG breakouts** entered at ~$77–78k that reversed into the range
  and stopped ~$1–2k lower. BTC ranged $75–78k for the whole late-May live
  window (confirmed by the Donchian channels logged: `[7505, 7611]`, `[7469,
  7570]`, etc. — every "breakout" was a range edge that reverted).

The remaining ~15 rows counted in the "19" were **NULL-pnl padding**:
`setup_type='intent_reduce'` / `exit_reason='reconciler_incomplete'` legs
(1743, 1745, 1764, 1766, 1770, 1772, and the 2026-06-01 06:45–12:09 storm
2041–2063). These are same-bar re-entry duplicates the intent layer no-op'd —
bookkeeping, not fills. They contribute 0 to PnL but pad the denominator → the
"0% win" is `0 wins / (4 real losses + ~15 non-trades)`.

## Full classification of all 85 trend_donchian BTCUSDT rows

| Bucket | Rows | Real-money $ | Verdict |
|---|---|---|---|
| **(a) genuine fills** (real position, SL/exchange-filled exit) | 1721/1722/1731/1732 (May chop) + 2534/2535/2745/2761/2769 (later stop-outs) + the demo winners | see below | Real strategy outcomes — chop losses early, big winners once BTC trended |
| **(c) TP-sentinel exchange-reject storm** | 1747–1781 (May 27), ~13 demo + 7 real `exchange_rejected` | $0 (no fill) | `BL-20260525-007` — the 50R sentinel / `entry*0.01` clamp produced a **negative / >10% TP** (rows show `tp=−764, −751, 2553, 752`) → Bybit **ErrCode 10001** → order rejected. **No position, no PnL.** Fixed by `_TP_SENTINEL_CAP_PCT=0.099` + `long_only` (all shorts now suppressed). |
| **(c) re-entry storm padding** | `intent_reduce`/`reconciler_incomplete` NULL-pnl legs | $0 | Fixed by the bar-close debounce `#2548` (2026-06-01). Pads count only. |
| **(c) sized_qty=0 refusals** | 24 bybit_2 `rejected` rows (`sz=0.0`) | $0 (no fill) | 1.5% risk of the small **real** balance < BTC min-lot at $60–77k → real-money `trend_donchian` can barely enter. The genuine forward constraint (`PB-20260630-001` / `BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR`). |
| **(b) reconciler / orphan artifact** | 2746/2762/2770 (`stuck_strategy_watchdog`, reconciled) + 3088 (unreconciled) | ≈ −$0.3 net (+$6.70 on 3088) | **Minimal.** NO `superseded` rows at all — the MGC-18-phantom mass-duplication class (`PB-20260618-001`) did **NOT** hit trend_donchian BTC. A 4-row tail, not a driver. |

**Real-money PnL contamination is essentially nil here** — no phantom orphan
duplication, no superseded rows. The `(b)` class that this session was primed to
suspect is not present for this strategy/symbol.

## Does the edge survive live now? (forward read)

**Full window 2026-05-25 → 2026-07-10, excluding `superseded`:**

- **bybit_1 (demo):** 28 closed, **net +$1,191.04**, 7 wins / 6 losses. The edge
  prints its characteristic fat-tailed profile: 2039 short **+$3,870** (caught
  the 06-01→06-03 drop $73k→$65k), 3087 long **+$2,132** (07-01), 2594 +$659,
  2610 +$898; losers 2769 −$3,350, 2761 −$3,254, 2745 −$785.
- **bybit_2 (real):** **net +$4.77** (7 closed +$5.06, 3 orphaned-reconciled
  −$6.99, 1 orphaned-unreconciled +$6.70), on tiny sizes.

**Caveat on the demo magnitude — it is NOT decision-grade.** It is inflated by
(1) huge 0.7–1.0 BTC demo position sizes and (2) `intent_reduce` **phantom-pnl**
rows booked `reconciler_filled` with `entry==exit` yet a large positive pnl
(2604 +$561, 2607 +$620, 2610 +$898 = +$2,079 of fabricated demo gain). Those
are a reconciler accounting bug (logged to health-review below). Directionally,
though, the strategy is clearly **not** a 0%-win loser once BTC trends.

**Config note:** the −198 window ran the *old* config (2h / both-sides /
`min_confidence 0.30` / trail 3.5 + the sentinel bug). The strategy was retuned
2026-06-01/06-10 to the OOS-best **1h / trail 5.0 / long-only / min_confidence
0.60** (the config that backtested +43R OOS). So the −198 window does **not even
test the current research geometry** — it predates every fix.

## The task's explicit sub-questions, answered from the audit

- **Is the regime vol-gate (BTC 15m advisory head, `REGIME_ML_VERDICT_MODE=use`)
  silently dropping signals?** **No.** All 6 `regime_hard_gate` rows for
  trend_donchian carry `gated:false, cell:"on", reason:regime_allow_explicit`.
  The `regime_ml_vol_shadow` rows show the ML vol verdict but never gate. Long is
  ON in every regime cell (per the policy table), so the gate *allows*
  trend_donchian longs — it is not the fill-rate constraint.
- **Is `long_only`/`FLIP_POLICY=hold` interacting badly?** `long_only` fires as
  designed — `short_suppressed_long_only` ×351 (since 2026-06-01). The −198
  losses **predate** `FLIP_POLICY=hold` (05-31) and were plain SL stop-outs, not
  flips — hold is not implicated.
- **Is it even filling?** Demo fills fine. **Real (bybit_2) rarely fills** — 24
  `sized_qty=0` refusals; only ~9 real fills in 7 weeks. Over the whole window
  trend_donchian emitted **52 `multi_account_dispatched`** decisions of 35,742
  evaluations (33,901 no-breakout, 1,126 below-min-conf) → ~41 reached a
  position. It is a genuinely rare-setup strategy; the low real-money fill count
  is **capital**, not gating.

## Actions

1. **KEEP `trend_donchian` LIVE (no demote).** The −198 was a measurement
   artifact on a superseded config; the edge is intact.
2. **Analytics guard (this session, Tier-1 draft PR):**
   `exclude_reduce_leg_predicate` in `src/web/api/_clean_trades.py`, applied in
   `/strategy/attribution`, drops `intent_reduce` bookkeeping legs (setup_type
   **and** the `notes.intent_reduce` flag) so a reduce leg can never again pad a
   win-rate denominator or inject a phantom win/loss. This is the guard that
   makes a future demotion pull immune to the exact false-alarm class.
3. **Deeper reconciler accounting bug → health-review backlog:** a reduce leg
   booked `reconciler_filled` with `entry==exit` yet a non-NULL positive pnl
   (2604/2607/2610) contradicts the `apply_intent_reduce_partial_close` design
   (pnl left NULL). The analytics guard masks it; the write path should not book
   the phantom pnl at all.
4. **Real-money capital constraint** stays tracked at `PB-20260630-001` /
   `BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR` — trend_donchian cannot build a
   real-money record until `bybit_2` can size ≥ 1 BTC min-lot.

## Trust in the research→live pipeline

The headline concern — "a pipeline-execution bug makes the research-best strategy
lose live" — is **not borne out**. The live-vs-research gap was: (i) a
transient exchange-reject bug on an absurd TP sentinel (fixed), (ii) a re-entry
storm inflating the count (fixed), (iii) a wrong-regime snapshot on the *old*
config (retuned), and (iv) a measurement pull that counted bookkeeping legs and
demo money as real trades (guarded now). None of these is the research harness
lying about the edge. The research→live path is trustworthy; the failure was in
**reading** the live record, not in the edge or the execution of it today.

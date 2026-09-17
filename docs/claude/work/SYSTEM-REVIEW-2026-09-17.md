# System review — 2026-09-17, weighted to performance (7d) and soak monitoring

> **Doc status:** `live` · category `record` · produced by the OPS lane
> (`session_0178pRo8ZxrnrREzDzDzb9Zk`, MI-294) · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

## ⚠️ COVERAGE — READ THIS BEFORE QUOTING ANYTHING BELOW

**This is NOT a complete `/system-review` and must not be counted as one.** The
skill mandates ~15 `review_coverage` keys and a rendered HTML report under
`comms/reports/`; this review covers the **two areas the operator weighted** —
performance for the past week, and soak monitoring — plus account/ML/service
state, and it says plainly what it did not cover.

| `/system-review` coverage key | this review |
|---|---|
| performance (operator-weighted) | ✅ **covered, deeply** |
| `soak_status` (operator-weighted) | ✅ **covered, 14 of 14 soaks read live** |
| `account_reachability` | ✅ covered |
| `ml_training_health` | ✅ covered |
| `backlog_classes` | ✅ covered — [`BACKLOG-TRIAGE-2026-09-17.md`](BACKLOG-TRIAGE-2026-09-17.md) |
| `flags_raised[]` | ✅ § 5 |
| `strategy_promotion` | ⚠️ **stance only, no push** — the skill requires a PUSH per candidate; not done |
| per-order-package A–F grading → `comms/claude_strategy_scores.jsonl` | ❌ **not done** |
| `authored_cells` · `execution_capture` · `since_last_build_verification` · `structural_health` · `ml_output_actionability` · `unexercised_fixes` · `research_results_disposition` · `test_execution_verification` | ❌ **not done** |
| rendered report under `comms/reports/` + `index.json` | ❌ **not produced** |

**Why this is stated rather than papered over:** a `review_coverage` block filled
in thinly would pass the coverage guard while measuring nothing, which is the
exact "green while measuring nothing" failure the guard exists to prevent. A full
`/system-review` is its own session; **that is the recommendation to the manager.**

---

## 1. PERFORMANCE — THE PAST WEEK

**Window:** `since = 2026-09-10T19:04:45Z` (7d back from the read).
**Population:** `trade_journal.db::trades WHERE status='closed' AND pnl IS NOT NULL
AND closed_at >= since` (`src/web/api/routers/performance.py:442-444`).
**Locator:** `/api/bot/performance?window=7d`, read 2026-09-17.

| class | n | W/L | win% | total PnL | expectancyR | **pnlCoverage** | M/E/F/U |
|---|---|---|---|---|---|---|---|
| **real money** (`bybit_2`) | **4** | 0/4 | **0.0%** | **−$10.96** | **−0.8472** | 1.00 | 4/0/0/0 |
| paper (5 accounts) | 142 | 55/87 | 38.7% | −$48,206.52 | −0.0653 | **0.2958** | 42/100/0/0 |
| paper-portfolio (2) | 11 | 3/8 | 27.3% | −$5,348.73 | −0.5996 | 0.3636 | 4/7/0/0 |
| prop (`breakout_1`) | **0 executed** | — | — | n/a | n/a | **no rows** | — |

⚠️ **`paperPortfolio` ⊂ `paper` — do not sum them** (verified at
`performance.py:1225-1227`: the same `demo=True` query restricted to portfolio
account ids). The 11 are inside the 142.

⚠️ **Prop PnL is not "flat" — there is NO POPULATION.** One ticket in the window
(`trend_donchian_eth_prop`, `expired`), two fills both `skipped`. Its account
snapshot is **412.4 hours (17.0 days) stale** against a documented 24h limit.

### 1.1 The `days=` trap — CONFIRMED, two independent ways

`/api/bot/performance` **silently ignores `days=`**. Byte comparison of four
fetches: `days=7`, `window=all` and the bare call are **byte-identical**
(147,368 B, same md5); `window=7d` is 58,081 B. Confirmed at source —
`performance.py:1183` declares `window` as the only query parameter, `days`
appears nowhere, and FastAPI silently drops undeclared params.

**Every figure above uses `window=7d`.** A reviewer using `days=7` measures
lifetime and does not know it.

### 1.2 THE TAKE-PROFIT QUESTION — 0 of 4, and worse than the buckets say

**Answer: ZERO take-profits on real money. 0 of 4 gradeable (0.0%).**
Five rows closed; one is ungradeable (`noExitPrice`).

Two independent bases agree — the route's own `bracketOutcome`
(`reachedTp 0 · reachedSl 2 · midBracket 2 · gradeable 4`) and a direct
price-vs-declared-bracket computation from `/api/diag/journal`:

| id | strategy | dir | →SL | →TP | `exit_reason` |
|---|---|---|---|---|---|
| 5644 | trend_donchian_eth_4h | short | **1.0023** | −0.278 | `sl` |
| 5675 | trend_donchian | long | **0.9990** | −0.016 | `reconciler_filled` |
| 5682 | trend_donchian_eth_4h | long | **1.0120** | −0.358 | `sl` |
| 5697 | xrp_pullback_2h | short | **0.9946** | −0.506 | `reconciler_filled` |
| 5702 | eth_pullback_2h | short | — | — | `intent_reduce_executed` |

🔴 **All 4 gradeable trades ended between 99.46% and 101.20% of the way to their
STOP.** The two `midBracket` rows sit at 99.90% and 99.46% — stop-outs the
reconciler closed a hair before the level was tagged. **On an outcome basis the
week is 4 stop-outs and 0 take-profits, not 2 and 2.**

🔴 **Every `→TP` figure is NEGATIVE** — all four exited on the opposite side of
entry from their target. Not one closed in profitable territory.

Fleet take-profit rate for the week: **5 TPs in 146 gradeable trades = 3.4%**,
against **9.6% lifetime** on real money (41 of 427).

⚠️ **Bound on this whole subsection:** it compares the **exit price** to the
bracket. No surface carries the intra-trade path, so *"reached TP"* means
*exited at/beyond TP*, never *ever touched TP*. A trade that ran to target and
gave it back grades `midBracket`. This is a real limit and was not worked around.

### 1.3 Per-strategy, real money (n=4, all `bybit_2`, pnlCoverage 1.0)

| strategy | n | W | win% | PnL | expR |
|---|---|---|---|---|---|
| trend_donchian_eth_4h | 2 | 0 | 0.0% | −$6.26 | −1.0544 |
| xrp_pullback_2h | 1 | 0 | 0.0% | −$3.86 | −1.0358 |
| trend_donchian | 1 | 0 | 0.0% | −$0.83 | −0.2441 |

**Legs with n≥5 AND a 0% win rate — swept all three classes, both windows:**
- **`window=7d`: ZERO qualify.** Nearest: `ict_scalp_sol_15m` (n=9, 11.1%, −$7,630), `squeeze_breakout_4h` (n=5, 20.0%), `uso_trend_1h` (n=4, 0% — below the n bar).
- **`window=all`: 3 qualify** — real money `trend_donchian_xrp_4h` (n=5, 0%, −$7.09); paper `gld_pullback_1h` (n=13, 0%, −$5,030.58) and `mhg_pullback_1d` (n=5, 0%, **−$54,570.82**).

⚠️ Per **"Tune before demote"** (`CLAUDE-RULES-CANONICAL.md`, operator-directed
2026-08-23), none of these is a demotion proposal. Where no M8 sweep is on
record, **the proposal is to run the sweep** — said in those words.

### 1.4 Real-money week vs lifetime

Win rate **27.1% lifetime → 0% this week**; expectancyR **−0.3231 → −0.8472**
(2.6× worse). Directionally consistent with the open
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30` bleed row — **and it does
NOT attribute it.** n=4 cannot distinguish a regime change from noise, and the
paper arm's −$48.2k is ~70% unmeasured. **No attribution is offered.**

---

## 2. SOAK MONITORING

**All 14 targets returned `present: true` with rows. ZERO `could_not_look`.**
**ZERO soaks graded `not_writing`.** Live VM at read time: `git_sha c068b7448`,
`git_sha_on_disk 19e596622`, **`restart_pending: true`**.

Formal verdict, `src_soaks(root, 2026-09-17)`: **4 declared soaks — ready=1 ·
accruing=0 · not_writing=0 · unknown=3.** Population: 88 items in
`OPEN-ITEMS.json`, 4 declare a `soak` block.

### 2.1 ⚠️ THE FINDING IS THAT NOBODY READS THEM

**`scripts/ci/check_soak_registered.py`: 19 soak logs · 3 registered · 16 carried
as pre-2026-09-02 BASELINE debt. The baseline has not shrunk by a single entry
since it was set.** Writers with neither alarm nor baseline: 0. Stale baseline
entries: 0.

**3 of the 4 declared soaks grade `unknown` — and the stated blocker is
discharged.** All three carry a `probe_absent_reason` saying *"the route 404s
until <PR> merges"*. **All three routes now serve with data.** So `unknown` here
means *nobody wrote the reader*, not *nobody can read it* — and one of the three
has a threshold that is in fact **MET**.

### 2.2 DECISION-READY — thresholds met

**(a) `invariant_violations` — the closed-flat falsifier HAS SPOKEN, 35 times.**
COMPLETE census (35 rows = the whole 6,473 B file), `2026-09-10T14:04:12Z →
2026-09-17T09:03:18Z`, **28 distinct `trade_id`s**. Every row `db_status: closed`
with a **non-zero `exchange_qty`**. Accounts `bybit_1` 29 / `ib_paper` 5 /
`alpaca_portfolio` 1 — **no real-money account**. Magnitudes include −14,952.9,
+3,589.9, +1,438.6. `phase: alert_only` ×35.

🔴 **`OI-20260909-CLOSED-FLAT-INVARIANT-CAN-FINALLY-SPEAK-AND-HAS-NOT-YET-SPOKEN`
is STALE ON ITS OWN FIRST CLAUSE.** It reads `loud: true`, `verified_at:
2026-09-12`, and its title says the falsifier has not spoken. **The first
violation landed 2026-09-10 — one day after the row's own `declared_at`, and
before two subsequent re-verifications.** Its clause (1) is met 35×.
⚠️ **Clause (2) — the matching `Level.ERROR` reaching the operator — is
`could_not_look` from here** (`/api/bot/logs` is not on the diag surface), and
**no such banner appears on `/api/bot/notifications`.** Say which clause you
cleared. ⚠️ And per the row's own text, **0 → non-zero is the mechanism starting
to work, not the book getting worse** — do not file this as a regression.

**(b) `ict_scalp_exit_head_soak` — the only formal `ready`, and it CONTRADICTS
its register row.** Over a 1000-row tail: `decision_state` census `not_scored`
939 / `scored_no_fire` 61. Scored rows carry real `model_id`s —
**`exit-head-ict_scalp-15m-v1` (32)** and **`exit-head-ict_scalp-5m-v1` (29)**,
all `stage: shadow`, all `acted: false`, all `mode: annotate`.
🔴 `OI-20260906-ICT-SCALP-EXIT-HEAD-CONSUMER-SHIPPED-AND-CANNOT-SCORE-UNTIL-A-SCALP-HEAD-IS-PUBLISHED`
says *"IT CANNOT SCORE A SINGLE LEG TODAY … the live mirror publishes exactly TWO
exit_head artifacts … BOTH are 1h"*. **Measured: 5m/15m scalp heads exist and are
admitting legs.** Both of that row's clauses look met; it needs re-reading.

**(c) `arbitration_fanout_soak` — clause (1) of
`OI-20260912-FANOUT-APPLY-PATH-REPAIRED-AND-STILL-HAS-NEVER-DISPATCHED` is MET.**
COMPLETE census (728 rows). `rounds_applied` entry key-sets split **exactly two
ways**: `('accounts','strategy')` ×407 (the old two-key projection) and
`('accounts','confidence','entry','side','sl','strategy','tp')` ×147 (**full
geometry**). The repaired writer is observed feeding the dispatcher.
⚠️ **Clause (2) — a journal row carrying `meta.arbitration_fanout_round` — was
NOT read, and that row states in terms that (1) does not imply (2).**
Also: `starved_accounts` non-empty on 90 of 728 rows, **`breakout_1` 76**.

**(d) `closed_flat_coverage` — `examined>=1` MET.** 55 of 1000 tail rows have
`examined>=1`; `checked: true` ×1000; `controls_ok: true` ×1000. `state_counts`:
`flat` 62, `residual` 7, **`could_not_look` 0**.

**(e) `cash_settlement_soak` — 21 of 51 rows carry `would_have_reduced_usd > 0`.**
COMPLETE census. `global_mode` `apply` 46 / `annotate` 5; `applied` True on 1.

### 2.3 Thresholds measured NOT met — and one that can never be met

- **`bybit_coverage_soak`**: `verdicts_differ` census over 1000 rows = **False
  1000, True 0**, key present on 1000/1000, all `bybit_1`, all `global_mode:
  annotate` / `apply_scope: not_apply`. **NOT met after 15 days.** By the
  operator's own criterion (`OI-20260902-BYBIT-COVERAGE-BASIS-MERGED-WITH-ARMING-DELIBERATELY-HELD-FOR-A-SOAK`),
  *"20 rows in which every grade agrees means the gate is inert on this book, and
  arming it would be a change with no measured effect"* — **1000 agreeing rows is
  a FINDING, not a pass**, and it is decision-ready now.
- 🔴 **`position_read_state_soak`: `dropped_symbol_dedupe_count` = 0 on 1000 of
  1000**, across `bybit_1` 669 / `bybit_2` 166 / `bybit_portfolio` 165, with
  `could_not_look_count: 0` — a real, non-vacuous denominator. **`CLAUDE.md`
  records `bybit_1` dropping a non-zero book on 376 of 376 reads on 2026-09-12.
  That is now 0 of 1000.** This is the soak's declared **outcome (b)**, the
  OPPOSITE finding from (a) — and **its `ready_when` (`>=1`) can never be
  satisfied if the repair holds.** A soak whose only ready condition is the
  defect recurring will accrue forever. **This needs re-specifying, and it is
  decision-ready.**

### 2.4 🔴 ALARMING, outside the declared set — the operator decision channel

Population: **47 complete records recovered from the tail of
`runtime_logs/work_decision_sweep_receipt`, 2026-09-17T15:24:29Z → 19:06:44Z
(3.70h).**

- `checked: true` ×47 · `paused: false` ×47 · `destination: claude` ×47 ·
  `poll_state: polled_with_handler` ×47 · `held_write_gate 0` · `held_route 0` ·
  `held_not_polled 0` — **all ×47**
- **`candidates: 4` ×47 · `failed: 4` ×47 · `prompted_choice: 0` ×47**

**The channel reports itself checked, unpaused, unheld and routed to the
preferred bot with a live poller — and every one of 4 pending operator decisions
fails to send, on every run, across the whole recoverable window.** The three
`held_*` counters are the designed way to say why nothing went out, and all three
read zero while nothing goes out.

Filed `BL-20260917-THE-WORK-DECISION-SWEEP-REPORTS-ITSELF-HEALTHY-AND-ROUTED-WHILE-FAILING-TO-SEND-EVERY-CANDIDATE-ON-47-OF-47-RUNS`
(critical). **This is why `DEC-20260917-SESSION-RECEIPT-CADENCE` is also being
put to the operator through the manager in conversation.**

### 2.5 Denominators worth keeping

- **`exit_interval_soak`** (1000-row tail, 21 distinct processes): `over_requirement` False 980 / None 20; **max 46,440.6 ms** against a 60 s requirement. **`ib_breaker` = `{state: armed, tripped: false, skipped: 0}` on 1000 of 1000** — exactly the positive denominator `OI-20260910-IB-PER-PASS-BREAKER-ARMED-AND-HAS-SKIPPED-NOTHING` asks for before reading any zero: the breaker is live and the queue healthy, **not** the field unwritten. Still **never exercised**.
- **`prop_ticket_risk_soak`: 4 rows LIFETIME**, 4.5 d stale, `would_have_capped: False` ×4 — armed, never capped, denominator 4 rows in 18 days.
- **`netting_attribution_soak`** 1.5 d stale; **`stray_oca_soak`** 127 rows COMPLETE, 5 `stray_unkeyed` all acted; **`protection_reassert_soak`** 19 rows, 4 acted.

⚠️ **`/api/diag/log_file` hard-caps at 1000 lines regardless of `lines=`**
(verified: `lines=6000` still returned 1000). Every "tail" above is a tail, not a
census; the ones marked COMPLETE were verified against the file's own byte size.

---

## 3. PROVENANCE AND TRUST — three defects in the instruments

🔴 **D1 — The two operator-facing surfaces disagree on the real-money trade
count: 4 vs 5.** `/api/bot/performance?window=7d` says `totalTrades: 4`;
`/api/bot/trades/closed?since=<same>` returns **5**. Cause at
`performance.py:444`: `AND t.pnl IS NOT NULL`. The dropped row is trade **5702**
(`eth_pullback_2h`, `bybit_2`), `exitPrice: null`, `realizedPnl: null`,
`pnlProvenance: null`, `openedAt == closedAt` to the microsecond,
`exit_reason: intent_reduce_executed`.
**Consequence: real-money `pnlCoverage: 1.00` is 4/4 of the rows the route chose
to count. Against the `/trades/closed` population it is 4 of 5 = 80%.**
**A perfect coverage score is being produced by excluding the only uncovered row.**

🔴 **D2 — `journalTrust` is NOT clean on real money.**
`accountsKnownDivergent: ["bybit_2"]` — the **only** real-money account — and all
5 `/trades/closed` rows carry `journalTrust: known_divergent` individually.
Paper and paper-portfolio are `accountsUnrecorded` (5 and 2 accounts), which is
**`no_record` — nobody looked — NOT "trusted"**. `readState: "read"` throughout,
so the trust read itself succeeded. **No block anywhere reports a trusted state.**

🔴 **D3 — the paper coverage cliff, and the loss sits in the unmeasured half.**
7d paper pnlCoverage **0.2958** (100 of 142 ESTIMATED). **12 legs at exactly 0.00
coverage carry 79 trades and −$19,031.60**; 14 legs with coverage >0 carry 63
trades and −$29,174.92. **56% of the week's paper trades have zero measured PnL**,
and the entire `pairs_*` sleeve (65 trades, 4 legs) is 0.00 coverage — not one
measured row. Lifetime paper reports **+$64,563.33 at 0.2254 coverage with 128
FABRICATED rows**; a positive lifetime headline against a week at −$48,206 is the
population-dependent sign flip this repo already documents. **Do not quote the
+$64.5k without its coverage.**

---

## 4. SECURITY — a new instance measured

🟠 **`/api/bot/*` served HTTP 200 with NO bearer token.**
`/api/bot/performance?window=7d` returned 200 unauthenticated over the public
Caddy host — real-money PnL, account ids, strategy names and per-trade rows.

⚠️ **This is a DIFFERENT surface from `/api/diag/*`**, whose public-token exposure
is a **closed, accepted operator decision** (2026-08-30). That decision rests
explicitly on the **read-only diag premise** and its own stated reopening
condition is a change to that surface — **it does not cover `/api/bot/*`.**
Reported for triage into the SECURITY class; **not acted on.**

---

## 5. FLAGS RAISED

| # | flag | severity |
|---|---|---|
| 1 | The operator decision channel fails 4 of 4 candidates on 47 of 47 runs while reporting itself healthy | **critical** |
| 2 | Real money took 4 trades, 0 take-profits, all 4 exited 99.46–101.20% of the way to their stop | **high** |
| 3 | `journalTrust` = `known_divergent` on the only real-money account; no block anywhere reports trusted | **high** |
| 4 | Real-money `pnlCoverage 1.00` is produced by excluding the only uncovered row (4 of 5) | **high** |
| 5 | Tier-1 crit/high rows >14 days: canonical doc says 0, measured 85 | **high** |
| 6 | `invariant_violations` has spoken 35× and its register row still says it has not | **high** |
| 7 | `bybit_coverage_soak` 1000/1000 agreeing — the gate is INERT on this book, decision-ready | medium |
| 8 | `position_read_state_soak`'s `ready_when` can never be met if the repair holds | medium |
| 9 | `/api/bot/*` answers 200 unauthenticated; the closed diag decision does not cover it | medium |
| 10 | `check_soak_registered` baseline has not shrunk by one entry since 2026-09-02 (16 of 19) | medium |
| 11 | 6 of 6 active backlog snoozes have silently expired | low |
| 12 | Prop account snapshot 412.4h stale against a 24h limit; zero trades executed | medium |

---

## 6. WHAT I COULD NOT READ

1. **Prop is structurally absent from `/api/bot/performance`** — `grep -c prop` on the router returns **0**. Prop has no expectancyR, no rCoverage, no bracketOutcome, by design.
2. **The `Level.ERROR` half of the `invariant_violations` clears_when** — `/api/bot/logs` is not on the diag surface.
3. **No MFE / intra-trade path on any surface** — so §1.2's take-profit figure is *exited at/beyond TP*, never *ever touched TP*.
4. **`/api/diag/journal` is clamped to 1000 rows id DESC** (ids 4881–5880). The week's rows are inside it; **no lifetime claim may be made from that file.**
5. **Clause (2) of the fan-out row** (a journal row carrying `meta.arbitration_fanout_round`) — not read.
6. **Why trade 5702 has `openedAt == closedAt`** to the microsecond with a null exit — needs the order-package trail. Flagged as D1, **not diagnosed**.
7. **`pnlProvenance` was not independently re-derived** through `src/runtime/provenance.py`; the route's own bucket counts are reported, with UNVERIFIED confirmed as its own field and excluded from MEASURED.

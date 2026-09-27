# breakout_1 — true state, 2026-09-24 (lane E59)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Question (checklist row E59):** what is breakout_1's true state today: are
tickets flowing, what is the honest baseline, and how much risk can the account
take before the rule-distance kills it?

**Machine-readable record:** [`prop-state-2026-09-24.json`](prop-state-2026-09-24.json).
**Lane:** E59, dispatched by manager `session_01Ljhs6sFAdWHdMDhJpL5aBP`.
**Tier:** this document is Tier-1. Every change it *proposes* is Tier-3 and is
filed with `next_action: ask_operator`. **Nothing here was applied.**

## Sources (every number below comes from one of these)

| id | read | at (UTC) | population |
|---|---|---|---|
| S1 | `GET https://ict-bot.duckdns.org/api/bot/prop/status?account_id=breakout_1` | 2026-09-24T12:38:33Z | latest account_status row, **id 20**, `reported_at` 2026-09-24T09:38:09Z (operator screenshot of terminal 823528 at 12:22 local), `status_freshness: ok`, age 3.0 h |
| S2 | `GET …/prop/fills?account_id=breakout_1&limit=500` | same | **44 rows** (ids 1–44): 18 closed, 10 filled, 9 open, 7 skipped |
| S3 | `GET …/prop/tickets?account_id=breakout_1&limit=500` | same | **164 rows** (order_package projection). `/prop/reconcile` counts 94 tickets in the sidecar, 44 fills, **0 unacted** |
| S4 | `scripts/ops/diag_fetch.sh 'log_file?name=arbitration_fanout_soak&lines=1000'` (direct) | ~12:39Z | **1000 rows**, 2026-09-02T18:53:59Z → 2026-09-24T11:00:15Z; 109 name breakout_1 |
| S5 | `scripts/ops/diag_fetch.sh 'log_file?name=prop_ticket_risk_soak&lines=20'` | ~12:40Z | 6 rows returned; newest 2026-09-23T14:12:22Z |
| S6 | code at `origin/main` b157598 | — | `src/prop/{breakout_executor,breakout_ticket,multi_account_ticket,prop_risk_gate,prop_reconcile}.py`, `config/accounts.yaml`, `config/prop_rulesets/breakout*.yaml`, `comms/strategy_evidence/*.json` |

Labels: **MEASURED** means read this session from the named source. **INFERRED** means derived
by arithmetic or code-reading, with the assumption stated. **UNVERIFIED** means
a claim I could not check this session.

---

## 1. FLOW: applied rounds → tickets → fills

**MEASURED (S4 ⋈ S3 ⋈ S2).** Since 2026-09-23 there were **2** fan-out rounds
that applied a `trend_donchian_eth_prop → [breakout_1]` round. Rows naming
breakout_1 on 2026-09-23 or later: exactly these two. No SOL round for
breakout_1 exists anywhere in the window after 2026-09-22T08:25Z. (A positive
control for the search: the same query finds the 2026-09-22 `not_allowlisted`
rows.)

| soak row (S4) | apply_scope | ticket (S3), Δt | ticket status | fill (S2) |
|---|---|---|---|---|
| 2026-09-23T14:12:15Z ETHUSDT short, entry 2681.92 / SL 2736.109 | `allowlisted`, in `rounds_applied` | `prop-manual-5ef9bb2e58c2` at 14:12:21.7Z (**+6.6 s**), risk_usd 75.00, qty 1.38403744, valid to 15:12:22Z | `filled` | **#44**: short 1.3 @ 2666.11, terminal-opened 23/09 17:13 local (INFERRED 14:13Z at UTC+3, inside validity) |
| 2026-09-23T15:02:00Z ETHUSDT short, entry 2676.45 | `allowlisted`, in `rounds_applied` | `prop-manual-bc832c0bbf02` at 15:02:06.8Z (+6.0 s) | `suppressed`: "outstanding_ticket:awaiting_report: prop-manual-5ef9bb2e58c2" | none (correct: one ticket per trade) |

**Counts:** 2 rounds applied → **2/2 reached `/prop/tickets`** → 1 emitted, 1
suppressed by the one-ticket-per-trade guard → **1/1 emitted tickets placed**.
Before that, the newest ticket was 2026-09-13 (`expired`, unanswered). Before
2026-09-22T08:24:49Z, 107 soak rows read `not_allowlisted`. **So flow is
restored as of 2026-09-23, on n=2 rounds.** That is too few to call it steady.

**Gaps found in the join:**

1. **Report-back latency 19.4 h.** The ticket was emitted 09-23 14:12Z, but fill #44
   was only journaled 09-24 09:37:56Z, from an operator screenshot. For those
   19 h the system did not know it held a position. The suppressor still
   worked, because it keyed on the ticket's `awaiting_report` state, not on
   the fill.
2. **The live stop is not in the journal.** Fill #44 has `sl: null`. Its
   `reason` text says SL **2736.00**. The manager's brief says the operator
   later moved it to **2711.10**, and nothing in S1–S3 records that
   (**UNVERIFIED** this session). Every at-stop figure in §2 uses 2711.10 on
   the manager's word. The system itself cannot compute the open risk.
3. **Placed vs ticketed:** qty 1.3 vs 1.384 suggested; entry 2666.11 vs
   ticketed 2681.92, which is 15.81 lower and so worse for a short. It is
   outside the 2668.37–2695.47 band **if** the ticket's band was the binding
   instruction (INFERRED; the fill time is minute-resolution only).
4. **The E17 closure criterion is now met in the field.** E17 requires a soak
   row with breakout_1 `allowlisted` AND its round in `rounds_applied`. Both
   09-23 rows show exactly that. E17's own row says `blocked` pending a stale
   soak. The soak is **not** stale today: its newest row is 2026-09-24T11:00:15Z.
   That is for the manager to act on, not this lane.

## 2. SIZING vs the floor (the urgent part)

### Measured state (S1, snapshot 2026-09-24T09:38:09Z, read 12:38:33Z)

| field | value | label |
|---|---|---|
| balance | **4,784.78** | MEASURED (venue, via operator screenshot) |
| equity | **4,794.76** | MEASURED (balance 4,784.78 + uPnL 9.98 = 4,794.76 ✓) |
| static DD floor | 4,700.00 | MEASURED (6% off the $5,000 start, `drawdown_type: static`) |
| **distance to DD floor** | **$94.76** | MEASURED (`distance_to_dd_floor_usd`) |
| daily-loss limit | $143.54 (3% × 4,784.78; day-start balance unreported, so the code uses balance) | INFERRED basis |
| distance to daily loss | **null**: `day_pnl_state: realized_unreported` | **NOT COMPUTED.** The daily half of the account-killer guard is blind, as it was on 09-21 |
| binding limit | the DD floor, unless today's losses exceed $48.78 (143.54 − 94.76) | INFERRED |

### The open position (fill #44)

Short 1.3 ETH @ 2666.11. The snapshot mark is 2658.43 (INFERRED from uPnL
9.98 / 1.3).

| if the stop is hit at | loss from snapshot mark | cushion left (incl. ~$1.35 close commission, INFERRED from #39's $1.49 on 1.5 ETH) |
|---|---|---|
| **2711.10** (moved, UNVERIFIED) | $68.47 (from entry: $58.49 ✓ manager) | **≈ $24.94** ($26.29 gross; the manager's $26.51 does not reproduce, 22 ¢ apart) |
| 2736.00 (as placed, per #44) | $100.84 | **−$7.43: BREACH** |
| TP 2416.00 | +$315.16 | ≈ $408.57 |

So moving the stop turned a breach-on-stop into a survivable loss. Financing
is also accruing: −1.17 in ≈ 0.8 day on this position, which erodes the cushion.

### What the config sizes the next trade at

**MEASURED from code (S6):** `breakout_ticket.build_ticket` computes
`risk_usd = risk_pct/100 × account_size_usd` = 1.5% × **nominal $5,000** =
**$75.00** on every ticket, whatever the cushion. The ticket text tells the
placer to recompute at 1.5% of live balance, which gives **$71.77**.

| cushion state | cushion | full-size ($75 + ~$1.5 cost) stop-outs until breach |
|---|---|---|
| now, ignoring the open ETH risk (what the gate sees) | $94.76 | **1**; the 2nd breaches |
| after #44 stops at 2711.10 | ≈ $24.94 | **0**: the next full-size stop-out breaches by ≈ $51.6 |
| after #44 hits TP | ≈ $408.57 | 5 |
| **concurrent**: a SOL (or ETH-long) ticket while #44 is open | $75 + $68.47 = **$143.47 at risk vs $94.76** | **breach if both stop** |

### Three defects that make the table worse than it looks

**D1: `enforce` is live and does nothing beyond `annotate`.** MEASURED:
- Every S5 soak row reads `global_mode: "enforce"`, and the pipeline item
  `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` records it as
  operator-approved Tier-3 on 2026-08-31.
- In code at b157598, no path reads `mode() == "enforce"`. `breakout_ticket.py:173`
  checks only `!= "off"` and then **annotates**. The size is fixed earlier at
  `breakout_ticket.py:96`, with nothing between that line and emission that
  could cap it.
- A repo-wide search of `src/prop/` and `src/units/accounts/` for
  `capped|cap_risk|_cap(|enforce` finds no cap. As a positive control, the same
  search does hit `dump_capped` and `montecarlo.py`'s "capped".
- The soak's own field is named `would_have_capped`.

So the Tier-3 decision the operator approved bought a caveat, not a cap.
OI-20260831's `clears_when` needs a row "showing the suggested size was
actually reduced", and **it cannot be satisfied by construction**.

**D2: the cushion ignores open-position risk.** `compute_rule_distance`
returns *equity − floor*. It does not subtract the loss-to-stop of open prop
positions. `grade_ticket_risk` compares one ticket's risk against that figure.
So a $75 SOL ticket today would grade `within_cushion` ($75 < $94.76) while
#44 still has $68.47 to lose. D2 cannot be fixed from the journal alone,
because fills carry no stop (gap 2 above).

**D3: the one-ticket-per-trade guard is keyed on (account, symbol, direction).**
An open ETH short suppresses another ETH short. It does **not** suppress a SOL
ticket or an ETH long. Concurrent exposure is permitted by design, and neither
the guard nor the gate adds it up.

### Proposed sizing rule (Tier-3, NOT APPLIED; filed `ask_operator`)

Risk a **fixed fraction k of the remaining DD budget**, capped at today's 1.5%:

```
budget_usd   = min(distance_to_dd_floor, distance_to_daily_loss if known)
             − Σ open_positions loss_to_stop            # D2
             − cost_buffer_usd                           # commissions, ~$2
risk_usd     = min(risk_pct × live_balance,  k × budget_usd)
if risk_usd < min_risk_usd  → leg decision "skip", reason "dd_budget_exhausted",
                               journaled AND pinged (never silent: E16's lesson)
if cushion is stale         → size off last_known_cushion (already carried by
                               grade_ticket_risk), and the ticket carries the
                               status ask (operator decision 2026-09-21: ask on new exposure)
```

**Arithmetic**, with cost $1.50 per stop-out, `cost_buffer` $2, and floor `min_risk_usd` $10:

| cushion | k = 0.25 | **k = 0.33 (recommended)** | k = 0.5 |
|---|---|---|---|
| $24.94 (after #44 stops) | skip | **skip** | $11.47, then 1 stop-out to the floor |
| $94.76 (now, flat) | $23.19; 3 consecutive stop-outs, $37.66 left | **$30.61; 3 stop-outs, $26.72 left** | $46.38; 3, $10.97 left |
| $408.57 (after TP) | $71.77 (1.5% cap binds); 9 | **$71.77; 8** | $71.77; 7 |

At k = 0.33 no sequence of stop-outs can breach the floor. Each loss uses a
third of what is left, and the account stops trading (loudly) at the $10 floor
instead of dying. The cost is speed. The eval target is $5,500 equity, $705.24
away. At the recorded (non-reproducible, see §4) expectancy of 0.0422 R, that
is about 540 trades at a $31 risk, against about 223 at $75, where the 1–2
stop-out breach margin makes P(breach) the dominant term. **Neither horizon is
practical. That is a roster/edge question for another lane, stated here so the
sizing rule is not mistaken for a path to the target.**

**Exact change (proposal, not applied):**

1. `config/prop_rulesets/breakout_routing.yaml`, add:
   ```yaml
   dd_budget_sizing:
     fraction: 0.33          # k — share of the remaining DD budget risked per ticket
     min_risk_usd: 10.0      # below this the leg SKIPS (journaled + pinged)
     cost_buffer_usd: 2.0    # round-trip commission reserve
   ```
2. `src/prop/prop_risk_gate.py`: add a pure function next to `grade_ticket_risk`:
   ```python
   def dd_budget_risk_usd(*, nominal_risk_usd: float, verdict: dict,
                          open_risk_usd: float, fraction: float,
                          min_risk_usd: float, cost_buffer_usd: float) -> tuple[float | None, str]:
       cushion = verdict.get("cushion_usd")
       if cushion is None:
           cushion = verdict.get("last_known_cushion_usd")   # stale: labelled, only shrinks on loss
       if cushion is None:
           return None, "cushion_unknown"
       budget = float(cushion) - float(open_risk_usd) - float(cost_buffer_usd)
       risk = min(float(nominal_risk_usd), fraction * budget)
       if risk < min_risk_usd:
           return None, f"dd_budget_exhausted: budget ${budget:,.2f}, k×budget ${fraction*budget:,.2f} < ${min_risk_usd:,.2f}"
       return round(risk, 2), "scaled" if risk < nominal_risk_usd else "nominal"
   ```
3. `src/prop/breakout_executor.py::emit_prop_ticket`, after `unit = unit_for_account(...)`:
   when `prop_risk_gate.mode() == "enforce"`, grade the nominal risk. Compute
   `open_risk_usd` over `find_open_prop_positions(account_id)` as
   `|entry − sl| × qty` when the fill carries `sl`, else the matching ticket's
   `risk_usd` (conservative: $75 for #44 against the true $58.49). Call
   `dd_budget_risk_usd`. On `None`, return a `skip` leg with the reason and page
   it through the existing `prop_sizing_refusal` channel. Otherwise
   `unit.risk_pct = risk / unit.account_size_usd * 100` before `build_account_leg`.
   This makes `enforce` mean what the 2026-08-31 approval assumed.
4. `prop_ticket_risk_soak` rows gain `sized_risk_usd` and `open_risk_usd`, so
   OI-20260831's `clears_when` becomes satisfiable.
5. Tests: the §2 table as fixtures. $24.94 → skip; $94.76 at k 0.33 → $30.61;
   concurrent #44 plus a SOL ticket → budget $94.76 − 75 − 2 = $17.76 → k×
   $5.86 → skip.

Rollback: `PROP_TICKET_RISK_GATE_MODE=annotate` (byte-identical sizing to today).

## 3. BASELINE: realized P&L since inception, never blended

| figure | value | label | population |
|---|---|---|---|
| sum of reported realized `pnl` | **−$146.52** | **MEASURED** (reported fills) | 18 `closed` fills, 2026-06-23 → 2026-08-30, all with pnl, no duplicate (ticket, exit, pnl) triples |
| venue balance vs the $5,000 start | **−$215.22** (4,784.78) | **MEASURED** (venue balance, operator screenshot 09-24) | whole account life |
| unexplained gap | **−$68.70** | INFERRED residual | commissions/financing (some `pnl` values are stated **gross** of commission, e.g. #41, and some net, e.g. #17), plus any unreported trade. **It cannot be split with the journal as recorded** |
| open #44 unrealized | +$9.98 at 09:22Z local-screenshot time | venue-reported **unrealized**, not realized | 1 position |
| mark-price uPnL (`prop_estimate`, ESTIMATED) | **not computed this session** | — | no ETH mark reachable: Bybit public API geo-blocked from the sandbox, and `/api/bot/positions` carries no ETH row |

Win/loss over the 18 closed: 4 wins (+18.81, +299.66, +242.02, +78.25, plus
+35.28 from an operator-moved stop), so 5 positive, 13 negative. **Five of the
eighteen are operator-discretionary closes** (manual, moved stop), so this is
not a strategy-quality sample. It is the account's cash history.

## 4. Non-reproducible leg evidence

**MEASURED (S6):** 12 committed evidence records cite a `source_run` under
`/tmp/…`. That path does not survive the session that wrote it, so none of the
12 can be re-derived from the repo. As a positive control, the same scan finds
the committed `comms/strategy_evidence/runs/2026-09-22-r5/…` paths on other
records.

| record | rosters it sits on |
|---|---|
| **trend_donchian_eth_prop** | **breakout_1** |
| **trend_donchian_sol_prop** | **breakout_1** |
| trend_donchian_eth_4h, trend_donchian_xrp_4h, xrp_pullback_2h | bybit_1, **bybit_2** (real money), bybit_portfolio |
| iaum_pullback_1d, ief_pullback_1d, slv_pullback_1d | alpaca_paper, **alpaca_live** (real money), … |
| eth_pullback_2h, eth_pullback_prop_2h, trend_donchian, fvg_range_15m | bybit_1 |

**So breakout_1's entire roster (2 of 2 legs) rests on evidence nobody can
rerun.** Two further facts about those two records:
- Both carry `fidelity: approximate, omitted_levers: ["tp_r"]`. tp_r
  (6.0 vs the base twin's 50.0) is one of the two levers that make a `_prop`
  twin a `_prop` twin. The backtest behind the prop variant does not model
  the lever that defines it.
- sol_prop: net 3.45 R over 63 OOS trades, **2 of 4 folds positive**, and fold
  4 alone is +10.73 R, so without fold 4 the total is −7.28 R. eth_prop: net
  7.13 R / 169 / 3 of 4 folds (fee-only 13.05 R, as the manager stated; reproduced from
  the record).

Filed; see below. Re-running them is a backtest lane, not this one.

## Filed (docs/claude/work/PIPELINE.jsonl)

| id | what | next_action |
|---|---|---|
| PI-20260924-MQ3CDMU6-0001 | DD-budget sizing rule (§2), Tier-3 | ask_operator |
| PI-20260924-MQ3CDMU6-0002 | `enforce` approved 2026-08-31 is not implemented (D1); OI-20260831 unclearable | ask_operator |
| PI-20260924-MQ3CDMU6-0003 | journal lacks the live stop (fill #44 `sl` null, the 2711.10 move unrecorded), so open risk is uncomputable (D2 prerequisite) | dispatch_lane |
| PI-20260924-MQ3CDMU6-0004 | 12 evidence records with `/tmp` source_run, incl. both breakout_1 legs; `tp_r` omitted | dispatch_lane |
| PI-20260924-MQ3CDMU6-0005 | −$68.70 unexplained between reported pnl and venue balance; gross/net pnl mixing | dispatch_lane |

**What I would queue next (not this lane):** (a) re-run both `_prop` legs
with tp_r modelled and the source committed (0004), before any breakout_1
roster decision; (b) daily-loss distance: get `day_start_balance` into the
status report-back template so the daily half of the guard is computed at all;
(c) E17 closure: its field criterion is met on 2 rows (§1 gap 4).

## What this lane could NOT establish

- The 2711.10 stop move (manager's word; not in any journal row).
- A current mark / `prop_estimate` uPnL.
- Today's realized P&L / day-start balance, so the daily-loss distance.
- Whether the $68.70 residual is all costs or includes an unreported trade.

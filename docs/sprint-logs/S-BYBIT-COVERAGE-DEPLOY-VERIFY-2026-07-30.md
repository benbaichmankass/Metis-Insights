# Sprint Log: S-BYBIT-COVERAGE-DEPLOY-VERIFY-2026-07-30

## Date Range
- Start: 2026-07-30 ~09:30 UTC
- End: 2026-07-30 13:05 UTC

## Objective
- Primary goal: verify the day's two live deploys — (a) that `BYBIT_TPSL_MODE=partial` is active and scalp exits are firing per-trade, (b) that `side_filter: short` on `trend_donchian_xrp_4h` (real-money `bybit_2`) + `sol_pullback_2h` is deployed and suppressing longs. Report in dollars, not R.
- Secondary goals (operator escalation mid-session): stop guessing at bracket state — establish *definitively* what the system is doing at the broker level, and ship a structural fix so that when brackets fail the monitor intervenes in real time, including for partial closes.

## Tier
- Tier 1 (verification, read paths, tooling, docs) + **Tier 2** (`src/runtime/order_monitor.py`, `pull-and-deploy`, `cancel-stale-tpsl-legs --apply`)
- Justification: the verification half is Tier-1 read/observability. PR #8000 touches the order-monitor and its partial-gap top-up *places a real protective leg*, so it was prepared, validated, surfaced to the operator with the one behavioural change stated explicitly, and merged only on their in-chat approval. `cancel-stale-tpsl-legs --apply` was likewise operator-directed in chat, and was dry-run and reviewed leg-by-leg first.

## Starting Context
- Active roadmap items: BL-20260720-ICTSCALP-PASTSTOP-EXITS (Fix 2 = the partial-mode bracket), BL-20260721-BYBIT2-XRP-TPSL-LEGCAP (leg-id tracking), BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT (real-money `bybit_2` XRPUSDT observed with no bracket).
- Prior sprint reference: `docs/sprint-logs/S-XRP-TPSL-LEGCAP-2026-07-21.md`.
- Believed-true at start, and **wrong**: that `BYBIT_TPSL_MODE` defaulted to `full`, that partial had just been activated at 05:37Z, and that this would explain the scalp exit leak.

## Repo State Checked
- `main` at session start `ca7be7e9`; ended `f8ef69f` (five PRs merged in between).
- Live VM `ict-bot-arm` (`141.145.193.91`) pre-deploy HEAD `ca7be7e9`, post-deploy `dd07b45f`, `ict-trader-live.service` restarted 11:42:52Z, PID 1996332.
- `git log -p src/runtime/order_monitor.py` read before editing (Tier-2 file).

## Files and Systems Inspected
- `src/runtime/order_monitor.py` (`_bybit_position_protection`, `_check_broker_naked_bybit_positions`, `run_monitor_tick` :8022), `src/units/accounts/execute.py` (`_bybit_tpsl_mode` :1749, `modify_open_order` :1935), `src/runtime/strategy_signal_builders.py` (side_filter helpers + 3 wire sites), `config/strategies.yaml` (:1258, :2216), `config/accounts.yaml` (:298), `scripts/install_systemd_units.sh` (:407), `scripts/ops/cancel_stale_tpsl_legs.py`.
- Live VM via the diag relay: `/api/diag/journalctl`, `/api/diag/services`; via system-actions: `pull-and-deploy`, `bybit-bracket-audit`, `cancel-stale-tpsl-legs`.
- Trainer VM via `trainer-vm-diag` (`m20_exit_analysis.py`).
- `/proc/<MainPID>/environ` on the trader — the authoritative env read.

## Work Completed
- **#7993** — new `scripts/ops/bybit_bracket_audit.py` + `bybit_bracket_audit_action.sh` + the `bybit-bracket-audit` Tier-1 system-action (read-only broker-truth coverage audit: `get_positions` + `get_open_orders` only, places no order). 10 tests.
- **#8000 (Tier-2)** — `_bybit_position_protection` returns a **quantity, not a boolean**. A netted Bybit position is ONE exchange position holding N journal trades and N qty-scoped legs, and a Partial leg's `slSize` covers only its own qty, so a surviving leg satisfied `any()` and the sweep skipped a *partially unprotected* position — invisible to the DB-driven check too, since the rows keep their journal SL/TP. Now: `covered == 0` → Full re-arm (unchanged); `0 < covered < size` → top up a qty-scoped Partial SL leg for exactly the uncovered qty via `execute.modify_open_order` (the one sanctioned order path); unparseable leg qty → **skip rather than guess**. Plus two detect-only counters, `over_covered` and `journal_qty_divergent`. 17 tests in `test_bybit_naked_rearm.py`, 237/237 across every monitor/protection suite.
- **#8006 / #8018** — 7 backlog entries.
- **#8022** — corrected two `CLAUDE.md` claims this session disproved (below).
- **Deployed** #8000 via `pull-and-deploy` (#8008) and re-ran `bybit-bracket-audit` (#8009).
- **`cancel-stale-tpsl-legs`** dry-run (#8019) then `--apply` (#8020) on `bybit_1` XRPUSDT.

## Validation Performed
- **`BYBIT_TPSL_MODE` is `partial`, and was already** — `.env:81`, the unit's `EnvironmentFiles`, and authoritatively `/proc/1957540/environ`; `_bybit_tpsl_mode()` resolves `'partial'`. **75 of 211 pre-flip opens already carried a non-NULL `sl_order_id`**, a column written ONLY on the partial branch ⇒ live since ~2026-07-21. **The 05:37Z flip (#7946) was a no-op re-assertion.**
- **#8000 verified end-to-end on live HEAD `dd07b45f`** (diag #8016) — all three counters fired, no `broker-naked Bybit sweep raised`:
  - `12:29:52Z JOURNAL/BROKER QTY DIVERGENCE bybit_1/BNBUSDT — 13.43 vs 9.72 (excess 3.71)`
  - `12:29:53Z JOURNAL/BROKER QTY DIVERGENCE bybit_1/BTCUSDT — 1.553 vs 0.01 (excess 1.543)`
  - `12:29:55Z LEG OVER-ACCUMULATION bybit_1/XRPUSDT — position 17438.9, resting SL legs 144789.3 (830%)`
- **The exit leak, in dollars** — 7d bybit scalps: **37 closed / −$6,358.37**, only **2 of 37** on a clean `tp`/`sl`; `reconciler_filled` n=28 / −$6,360.46. Trade #4218 opened 7 min *after* the flip with legs captured, held **1h56m on a 5m scalp**, exited `reconciler_filled`, `exit_price`/`pnl` both NULL. Real money: 7d −$17.33 / 30d −$26.40. Paper: 7d −$22,754.15 / 30d −$57,361.31.
- **`side_filter` deployed but UNEXERCISED** — `97aae2f` is an ancestor of live; trader restarted 08:58:31Z, 26 min after the 08:32:22Z merge; both legs loaded, `execution: live`, XRP in `bybit_2`'s list (real money). Zero `long_suppressed_short_only` rows across **1,267,009** audit lines and zero `buy` signals since the merge — both legs are non-actionable *upstream* of the gate, so the gate is correct-but-untested live.
- **`bybit_2` real money audited clean** — ETHUSDT 0.06 and XRPUSDT 149.5, 100% covered, legs alive, one journal row each.
- **`cancel-stale-tpsl-legs --apply` post-state** — SL `2922b63d` cancelled `retCode 0`; its paired TP returned `110001 order not exists` but `post_state` confirms it gone, so the intended end state was reached in full. Survivor is the **journal-tracked** leg on #4165 (`c7454b41`, 58686.8 @ 1.0942) + its TP. Coverage **830% → 336%**. Position protected throughout — the script never cancels the leg it keeps.

## Documentation Updated
- `CLAUDE.md` (#8022): the naked-autoprotect bullet asserted the re-arm is IB-only *"(Bybit/OANDA/Alpaca attach SL/TP atomically at entry, so a naked orphan can't occur there)"* — **false**, disproven by the real-money `bybit_2` incident; kept as a quoted, explicitly refuted claim so the record of the wrong assumption survives. Broker-sweep bullet "both venues"/"Two sweeps" → **three**, documenting the Bybit sweep. The `BYBIT_TPSL_MODE` row now records the **live** value, how it was verified, that it predates today, and that the 05:37Z flip was a no-op.
- `docs/claude/health-review-backlog.json`: 7 entries (#8006, #8018). Backlog 310 items / 36 open at close (310 includes one entry from a concurrent session).
- Coordination board #6927: `START`, `DONE`, and an amendment carrying the verified-counter receipt and the 830% escalation.

## Contradictions or Drift Found
- The two `CLAUDE.md` claims above — fixed in #8022.
- `docs/research/exit-capture-deepdive-2026-07-30.md` rests on the disproven "default `full` / not deployed" premise. **Deliberately NOT patched** — rewriting it needs the real root cause, and making it *look* correct while the cause is unknown would recreate the problem. Filed at high severity instead.
- `scripts/ops/bybit_bracket_audit.py` prints *"every audited symbol is fully SL-covered at the broker"* directly beneath a 444.7% over-coverage, because the roll-up tests only `uncovered` qty. From #7993, so #8000's sweep counters do not fix the script an operator reads.
- Adjacent, found by a concurrent session and already fixed in their PR: `BL-20260730-SIDE-FILTER-NOT-FORWARDED` — `regime_debt_matrix.py`'s lever maps didn't know the `side_filter` key, so the harness measured BOTH legs for the two short-only strategies. Does **not** contradict the live finding above (that was live audit rows, not harness measurement).

## Risks and Follow-Ups
- `BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING` (high, tier 2) — over-coverage went **444.7% → 830% in 41 min** because the position halved while the legs stayed byte-identical. Add-only Partial legs never shrink, so every partial close widens the gap. De-dup took it to 336%; it cannot resize the survivor. `bybit_1` is paper and `bybit_2` audited clean — the only reason this is not P1.
- `BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE` (high) — **the scalp exit leak has no established cause.**
- `BL-20260730-BRACKET-AUDIT-ROLLUP-MISLEADING` (medium), `BL-20260730-DIAG-RELAY-BODY-PARSE-FOOTGUN` (medium), `BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED` (medium-high), `BL-20260730-CLOSED-TRADE-NULL-EXITPRICE-PNL` (medium), `BL-20260730-DEVNULL-DEPLOY-REDIRECT-FRAGILITY` (low).
- Still open by design: the **Full-mode re-arm overrides per-trade geometry** — live on `bybit_1` BNBUSDT, position stop 1149.8 = #3755's `journal_sl` across 5 rows spanning 1137.2→1168.6, 3 with no tracked leg. Its "100%" coverage *is* that override; the Partial legs alone cover 6.77 of 9.72 (69.7%), so the override simultaneously masks a real partial gap.

## Deferred Items
- Phantom-row cleanup and leg de-accumulation (remediations, not detections) — need their own reviewed Tier-2 change.
- Per-leg live greeks/PnL for options rows — unrelated, pre-existing.
- A wider doc sweep for the disproven premise — blocked on the root cause.

## Next Recommended Sprint
Root-cause the scalp exit leak. `reconciler_filled` is the *normal* exit path for bybit scalps (28 of 37) rather than the exception, and that is where the money goes. Separate what is a **specific fix** from what needs **understanding first** before touching the order path. Candidate leads: (1) why the monitor doesn't close on the bracket; (2) leg/position desync as a *cause* rather than a cosmetic problem; (3) tick starvation — a monitor pass now exceeds the 60s `TICK_INTERVAL_SECONDS`, with repeated 5s IB liveness-probe timeouts to `10.0.0.251:4002`; (4) how often `exit_price_source='entry_order_avg_price_unreliable'` → NULL/NULL occurs.

## Wrap-Up Check
- [x] Every number in this log is a receipt from #8008 / #8009 / #8013 / #8015 / #8016 / #8019 / #8020 or a read of the code at the merged SHA — no inferences reported as findings.
- [x] Tier-2 changes operator-approved in chat before merge; `--apply` dry-run and reviewed first.
- [x] Backlog entries filed with honest severity, including **one graded DOWN after I disproved my own hypothesis** (`DEVNULL-DEPLOY-REDIRECT-FRAGILITY`: it looked like six silently-skipped timer enables; `/api/diag/services` showed all five active — the disproof is recorded in the entry so nobody re-chases it as an outage).
- [x] Coordination board updated, including a public correction of a wrong prediction I had posted there (`bybit_1` BNBUSDT read PROTECTED, not `PARTIALLY_NAKED ≈50%`).
- [x] Errors made and disclosed in-session: overwrote board #6927's issue body via `issue_write update` instead of a comment (original unrecoverable, faithful reconstruction posted and labelled); a SQL timestamp bug that falsely reported "0 opens since the flip"; a false "zero post-flip closes" claim; three malformed diag-relay requests.

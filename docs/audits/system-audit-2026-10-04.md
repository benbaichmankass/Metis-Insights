# System audit — 2026-10-04 (lane OPS-AUDIT)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

- **Dispatch:** manager `session_01MM8o5js6TcDFeNAPBY4Ntv`, operator: *"a thorough audit"*.
- **Lane:** `session_01NQGspf9jnJZ81SGcfA5doR`.
- **Scope:** everything merged to `src/ scripts/ config/ deploy/` since the CA code audit (2026-09-27) and the E75 system audit (2026-09-29). That is **310 commits** (`git log --since=2026-09-27 origin/main -- src/ scripts/ config/ deploy/`), read money-path first.
- **How it was read:**
  - Three read-only sub-agents covered the prop order path, the trader path and governance.
  - The lane re-checked every claim it relies on below.
  - Each claim is marked **MEASURED** (where it lives), **VERIFIED-CODE** (file:line read by the lane), or **SUB-AGENT** (read by a sub-agent, not re-verified line by line).
- **Live state:** read through the diag relays, issues #16213, #16214 and #16216, captured 2026-10-04 08:02–08:04Z.

## Findings, ranked by money at risk

| id | sev | what | disposition |
|---|---|---|---|
| OA-03 | **HIGH (operator)** | breakout_1 is **$2 below its $4,700 static DD floor**, and its live executor has been **latched since 03:49Z** | PI-20261004-GCFA5DOR-0001 (ask_operator) |
| OA-01 | **HIGH** | Neither live prop executor has a unit-failure alert, so a latched or crashed executor is silent | **PR #16222** (HELD Tier-2) |
| OA-02 | **HIGH if confirmed** | tradeify_1 `ETH/USD` terminal rows never match `ETHUSD` | **PR #16223** (HELD Tier-2) |
| OA-10 | HIGH (governance) | The hold-merge alarm has never delivered a ping: 174 alarms, 171 open | PI-…-0005 (ask_operator, exact diff inside) |
| OA-04 | MED | tradeify's ticket-opener fallback can double-click a price cell when one-click reads `unknown` | PI-…-0002 |
| OA-05/06 | MED | An SL lost after `open` is never alerted; the SL repair always refuses, so the flatten waits a tick | PI-…-0003 |
| OA-12 | MED (Tier-3 Q) | #15830 added catch-up entries on every forming leg, an entry-timing change with no backtest | PI-…-0007 |
| OA-13 | MED | The parity check reads `ok` when its own controls `could_not_check` | PI-…-0008 |
| OA-15 | MED | order_monitor now skips dry_run accounts (ib_live, oanda_practice); unknown whether either still holds a position | PI-…-0009 |
| OA-11 | MED (governance) | Held money-path PRs merged with no recorded approval or notification | PI-…-0006 |
| OA-14 | LOW | diag `restart_pending` reads `true` on research-only diffs (drifted from the deploy's filter) | **PR (HELD, diag.py is Tier-2 by path)** |
| OA-07 | LOW | The submit-label price is not compared; 2% bracket tolerance; shared browser lock; shared symbol map | PI-…-0004 |
| OA-16 | LOW | Trainer units have no OnFailure; `unit_failures.jsonl` is not readable via diag; three trader-path nits | PI-…-0010 |

### OA-03 — breakout_1: below the static floor, live executor latched (MEASURED, diag #16216 / #16213)

- **03:39Z onward:** every tick's `rule_distance` reads balance **$4,698**, `static_dd_floor_usd` **$4,700**, distance **−$2.00**.
- **03:40Z:** the executor placed a SOLUSD long anyway.
  - Size: 65.78 lots, risk $74.99, about $7,953 notional.
  - Prices: entry 120.91, SL 119.77, TP 127.75.
  - It logged `breach_guards=report, placing anyway`. That is the operator's 2026-09-28 policy (`config/accounts.yaml` breakout_1 `risk.breach_guards`), and this audit does not dispute it.
- **The submit:** the button was clicked, but `dialog_confirmed: false`, and three re-reads found no order and no position. Balance is unchanged and positions/orders read 0. **There is no exposure.**
- **03:49:36Z:** the AUTO-REVERT latch was set: `unconfirmed placement (not found after 3 re-reads)`. Two `{"alert"}` lines were emitted on that tick only.
- **Since then:** every 5-minute tick exits 3 and `ict-prop-executor.service` reads `failed`, with no further alert (see OA-01).
- **The documented revert has not run.** The PROP-EXEC env/timer revert is `set-env PROP_EXECUTOR_MODE=read_only` + `executor-disable-timer`. The executor still reports `env_mode: live` and the timer is `active`.
- **Unknown, and for the operator:**
  - Does Breakout count $4,698 under a $4,700 static floor as a failed account?
  - Clear the halt, or revert?
  - Why did a clicked submit register nothing on the venue? This is distinct from tradeify's disabled-submit item PI-20261003-ILZMCTFQ-0002.

### OA-01 — live prop executors have no failure alert (VERIFIED-CODE + MEASURED)

- **Cause:** `scripts/install_systemd_units.sh:413-427` installs FIX-SA-08's `onfailure.conf` only for `deploy/*.timer`, and skips `*@*`. Both prop executor timers live in `deploy/opt-in/`.
- **Effect:** `prop_executor_tick.sh:116-129` pings only when the Python prints an `{"alert"}` line. A latched tick (exit 3), a crash, a timeout (`timeout` rc 124/137) or a missing venv (exit 5) all fail silently.
- **Observed:** OA-03's 4h+ silent `failed` streak.
- **Fix, PR #16222:** add `OnFailure=ict-notify-failure@%n.service` to both unit files, with a test. `systemd-analyze verify` accepts the instance name with two `@`.

### OA-02 — tradeify slash symbols (VERIFIED-CODE; venue display NOT observed)

- **The bug:**
  - `positions_from_tables` / `orders_from_tables` / `trade_history_from_tables` (`src/prop/platform/dxtrade.py:359-497`) store the Symbol cell verbatim.
  - `match_terminal` (`src/prop/prop_executor.py:569-572`) and every other executor match compare `p.symbol.upper()` with `ETHUSD`.
  - tradeify displays `ETH/USD` on the watchlist, the ticket and the submit label. PI-20261003-ILZMCTFQ-0002 records the label `Buy 1 SOL/USD at …`.
- **If Positions/Orders also show the slash:**
  - A real fill reads `unconfirmed_submit` and halts.
  - A bracket-less fill is never classed `partial_no_sl_tp`.
  - Stale LIMIT orders are not cancelled.
  - The journal posts `closed_on_terminal` on an open position.
  - The trail skips the position.
  - Close-row and edit-dialog matches fail.
- **Not observed:** tradeify_1 has held 0 positions on every read.
- **Fix, PR #16223:** canonicalise at the parsers and in the JS matches. It is a no-op for Breakout symbols. The new tests pass 3/3 with the fix and fail 3/3 without it, and the prop suite passes (1256).

### OA-10 — the hold-merge alarm never pages (MEASURED)

- **Cause:** `.github/workflows/pr-landing-hold-merge-alarm.yml` opens its `send-ping` issue with `GH_TOKEN: ${{ github.token }}`. GitHub does not trigger workflows from `GITHUB_TOKEN` events, so `system-actions.yml` (`issues: [opened]`) never runs.
- **Evidence:** #16201 (the alarm for #16183) was opened by `github-actions[bot]` and is still open with 0 comments.
- **Scale (SUB-AGENT count):** 174 alarm issues between 09-28 and 10-04, 171 of them open.
- **Unaffected:** `r4-demotion-gate` and `exit-cell-mandate` use `secrets.BRANCH_PROTECTION_TOKEN` for their pings.
- **Fix, not applied:** set the step's `GH_TOKEN` to `secrets.BRANCH_PROTECTION_TOKEN`.
- **Why it is not applied:** the alarm counts only GitHub APPROVED reviews, and none of the 128 held PRs has one. Delivery would therefore page on every manager merge. This is a policy choice for the operator.

### OA-11 — governance facts (SUB-AGENT; report only)

- **Examined:** 133 HELD/HOLD/Tier-2/3 commits since 09-27; 15 money-path PRs were read individually.
- **No operator-approval quote found:**
  - #16183
  - #14899 / #14643 / #14585, which rest on a manager decision alone
- **Merged as data-backed decisions, with no notification artifact found:** #15593, #15340, #14590, #14577 and #15595.
- **Automated scan:** 48 of 79 money-path held PRs had no operator-quote hit. A miss is not proof that no approval exists.
- **Why merges can't be attributed:** `merged_by` reads `benbaichmankass` for both operator and manager merges, so it cannot tell them apart.

### OA-14 — fix in its own HELD PR (read path; diag.py is Tier-2 by name)

- **Problem:** diag `_NON_RUNTIME_PATHS_RE` is documented as "lock-step" with `deploy_pull_restart.sh`'s filter. #15830 widened the deploy's filter and the diag copy was not updated.
- **Observed effect:** web-api running `63bd0ce01` vs on-disk `cd9dc2ed8` read `restart_pending: true` on a diff of only `.github/ comms/research/ research/ scripts/research/ tests/ docs/` files.
- **Changes:**
  - The diag regex now equals the deploy filter.
  - `--no-renames` was added, matching the deploy.
  - New `tests/test_diag_restart_pending_lockstep.py` extracts both regexes and pins them equal. Both of its tests fail on the old code.

## Checked and found safe (VERIFIED-CODE or SUB-AGENT, with location)

**Prop order path** (SUB-AGENT; the halt latch, `_trip` and `_contain` were read by the lane at `prop_executor.py:785-808, 1935-1990`):
- `dry_walk_max_lots` (#16183) applies only when `run_round_trip(arm=False)` (`prop_executor.py:1296-1300`).
- Lots are rounded down, and the $-cap only shrinks a position (`304`, `345-383`). The 03:40Z ticket shows it working: 65.83 → 65.78 lots.
- Quantity, side and symbol are checked on the form read-back and the submit label (`dxtrade.py:4697-4703, 4803-4870`).
- The form read-back is load-bearing. At 03:40Z it caught a mis-filled SL (pass 1 showed 120.84) and refilled it to 119.77 before submit.
- Both SL and TP are required (`prop_executor.py:338-339`, `dxtrade.py:5167-5192`).
- Duplicates are prevented: the `intended` row is fsynced before the click, retries happen only for never-clicked submits (≤3), and `_contain` never resubmits.
- **Account isolation (breakout_1 ↔ tradeify_1):** each account has its own state dir, ledger, halt latch, lock, session and kill-switch env (`prop_executor_tick.sh:47-66`). The two units ran independently on the VM: tradeify_1 ticked `halted: null` while breakout_1 was latched.
- PROP-TRAIL only tightens, on both sides (`prop_trail.py:203-262`, `dxtrade.py:5085-5117`).
- `EDIT_DIALOG_MEASURED=True` arms only the rollout-guarded, SL-tighten-only modify.

**Trader path** (SUB-AGENT; 138 tests passed):
- The #15830 ledger write is atomic and gitignored.
- The restart classifier fails toward restart. Its one gap is `scripts/research/queue_throughput` (OA-16).
- PREVBAR introduces no lookahead.
- #15886 only raises the pytest timeout.
- #16175 weakens no guard.
- `ief_pullback_1d` trail_decay keys match the reader (`src/runtime/trail_decay.py:136-149`). It is tighten-only and identical on the mirror, and `test_mirror_trade_shaping_fields_equal_live` passes.
- The open-package gate fails closed.
- The ORDER-AUDIT-2 halt and IB/Bybit changes are correct.

## Re-verification of the earlier audits' fixes

| item | merged | deployed | observed |
|---|---|---|---|
| FIX-SA-01…13 | **all 13 merged**, 09-29/09-30 (shas via `git log --grep`) | Trader is running `7563f8c1c` (diag `status.git_sha_running`), which contains all 13 | SA-11: the `restart_pending` field is live, but it read a false positive (OA-14; fix HELD). SA-12: trainer disk at **43%** (trainer diag #16214), fixed. SA-08: **partial**. No prop or trainer coverage (OA-01, OA-16), and live delivery is not observable via diag. |
| FIX-CA-01…32 | per `docs/audits/code-audit-2026-09-27.md` (all MERGED by its own record) | contained in `7563f8c1c` | **not re-verified individually this session.** That doc's merge claims were not re-checked commit by commit. |

- **Web-api** is running `63bd0ce01` against on-disk `cd9dc2ed8`. The difference is non-runtime only (OA-14), so the web-api is effectively current.
- **All 12 accounts** load with the expected rosters. tradeify_1 and breakout_1 are `dry_run: false`. ib_live and oanda_practice are `dry_run: true`.

## Not covered

- The six real-money LIVE-PARITY divergences (PR #16198). Lane LIVE-SILENCE-2 owns them.
- Strategy-level performance.
- The 48 held PRs with no operator quote, beyond the automated scan.

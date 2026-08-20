# Full-System Audit — 2026-08-20

> Program doc (the shared brain) per `.claude/skills/full-system-audit/SKILL.md`.
> Session `comprehensive-system-audit-p2dlkd`, branch
> `claude/comprehensive-system-audit-p2dlkd`. Board `START` posted to #6927.
>
> **Operator mandate, verbatim in substance:** audit the audit skill FIRST — it
> is insufficient — then audit *everything*, critically. *"Do not assume. Do not
> trust. Verify."* And explicitly wider than conformance: *"not just audit, but
> research and criticism of everything that we built… do we need to rethink
> things."*

## Phase 0a — instruments

- Clone arrived **SHALLOW (50 commits, from 2026-08-17)**. `git fetch
  --unshallow` → **3,459 commits from 2026-03-22**. Every historical claim below
  is made against the deep clone. (`BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE`.)
- **Direct diag egress is FIREWALLED in this session.** `curl` to both
  `141.145.193.91:8001` and `158.178.210.252:8001` timed out (exit 124) — the
  Trusted network level. All VM data below came through the **issue relay**
  (#9993 batch of 15 paths, #9996 batch of 2).
- ⚠️ **Because the live IP also timed out, the dead-IP probe has no positive
  control and proves nothing about that host.** See F-7, which stands on the
  configuration alone.

---

## Part 1 — The audit skill was structurally unable to find these defects

### The case that proves it (F-0)

`IBClient.protection_coverage` graded a resting stop and a resting take-profit
with **one** membership test, so a stop-only position reported *fully covered*.

| | |
|---|---|
| introduced | **2026-07-26**, PR #7641 (`git log -S`, deep clone) |
| audits that ran over it | **2026-07-31** and **2026-08-04** — both clean on the order path |
| caught | **2026-08-16**, only when `/api/diag/ib_open_orders` made the reduced verdict contradictable |
| consequence | MGC 4487 sat **122.74 points past its declared target for 11 days** |

Three structural reasons, each now an axis in the rewritten skill:

1. **The defect was in the seam, not in a line.** The repo's own root-cause doc
   already says it: *"Every contributing component was individually correct,
   which is why line-by-line audits kept returning clean: the defect lives at
   the seams."* The old skill's headline pass — Phase 3C, "read every single
   line" — aimed at the one place these defects are not.
2. **Every probe asked the system to describe itself.** `/api/diag/services`
   says `active`; `protection_coverage` says `covered`; `filter_state` said
   nothing while dropping the filter; `_WARN_LEVELS` never matched `warn`.
   When the auditor's instrument is the audited system's own summariser, a
   broken summariser produces a clean audit.
3. **Nothing checked that a fixed bug cannot return.** A finding ended when the
   code changed. No pass asked *what permanent detector fails if this recurs.*

### Supporting measurements

| # | Finding | Measurement |
|---|---|---|
| F-1 | **The backlog is not mechanically queryable**, so the program cannot use its own memory to detect recurrence | `health-review-backlog.json`: **708 items, 130 distinct keys, 34 distinct `status` values**; 205 rows lack `severity`, 203 lack `resolution_criteria`, 102 lack `tier` |
| F-2 | Open-item load | **261 open** (`open`/`kept_open`/`measured_open`) — **9 `critical`, 59 `high`**; median age 7d, 21 over 30d, 5 over 60d |
| F-3 | **Guards are largely unproven instruments** | **10 of 41** guard scripts have a failure-path self-test. By this repo's own *"green is not evidence"* rule the other 31 cannot be shown capable of failing |
| F-4 | **"Read every line" has never once been achieved** | every audit's coverage map ends *"NOT reached: per-line `src/` sweep"* — 5 for 5 |
| F-5 | **The merge-slot guard does not enforce** | `BL-20260819-MERGE-SLOT-GUARD-DOES-NOT-FIRE`, now **four** independent sightings. The skill text calls the claim *"hard-enforced"*, so a merge going through reads as evidence the protocol ran. It is not |

---

## Part 2 — What the new BEHAVIOR axis found in one pass

Tooling shipped this session: **`scripts/ops/system_invariants.py`** (7
invariants, three-state verdicts, 28/28 planted-control self-tests) run against
live broker ground truth pulled through the relay at **2026-08-20 06:30–06:34Z**.

### F-6 · MONEY-AT-RISK · Two live IB positions are target-naked

`/api/diag/ib_open_orders` (`ib_paper`, `read_state: orders_read`, 4 orders) vs
`/api/bot/positions`:

| position | resting STOP | resting TARGET | declared `takeProfit` | verdict |
|---|---|---|---|---|
| MGC 95 | STP 95 `oca-protect-389` | **none** | 4393.02 | **target-naked** |
| MES 15 | STP 15 `oca-protect-336` | **none** | 8390.59 | **target-naked** |
| MHG 29 | STP 29 `308977633` | LMT 29 `308977633` | 7.1415 | correctly bracketed |

The bot's own `ib_target_naked` detector (shipped 2026-08-16) **is firing** —
both appear on `/api/bot/notifications` as `alert`. So detection works and the
condition is **un-remediated by design** (the target side alerts without
re-arming). MES 4350 has been open since **2026-08-03**. Open backlog rows
`BL-20260818-ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH` (critical) and
`BL-20260818-DID-TARGET-NAKED-EVER-FIRE-FOR-MGC` cover the cause; this is an
independent confirmation from a second path, plus the observation that the
alert has now been standing all session with no repair path.

### F-7 · CONFIG · `DIAG_BASE_URL` points at a terminated VM

This session's environment carries `DIAG_BASE_URL=http://158.178.210.252:8001`.
`CLAUDE.md` records that host as the x86 micro **terminated 2026-06-16**; the
live trader is `141.145.193.91`. The `canonical-doc-coherence` guard has a
*dead VM IP single-source* check and it **passes** — because it checks the
**docs**, not the **environment**. That is the thesis in miniature: the
instrument checks the description, not the world.

Risk to state precisely: `scripts/ops/diag_fetch.sh` sends the
`DIAG_READ_TOKEN` bearer to whatever answers on that address, and OCI reclaims
and reassigns public IPs. I could not test reachability (no egress, no positive
control), so I assert the misconfiguration, **not** that a leak has occurred.

### F-8 · ACCOUNTING · Live double-counted unrealised PnL on a netted symbol

Two open journal rows, one broker position:

```
bybit_1 SOLUSDT   trade 4816  qty 1409.4  entry 84.54  unrealizedPnl 255.16
                  trade 4810  qty  367.8  entry 85.14  unrealizedPnl 255.16
broker position:  size 367.8            uPnL 255.16
consumer sum:     510.32  =  2.00x the broker figure
```

Two positions of different size and different basis cannot have the same
unrealised PnL. Both rows are labelled `unrealizedPnlSource: "broker"` /
`unrealizedPnlProvenance: "measured"`.

**Root cause — `src/web/api/routers/dashboard.py:292`:**

```python
share = min(q / size, 1.0)
if share < 0.999:
    value *= share
```

`size` is the **exchange** position (367.8). Row 4816's qty is 1409.4, so
`q/size = 3.83`, the cap clamps `share` to `1.0`, the `< 0.999` test fails, and
the row keeps the **full** position figure. Row 4810 is exactly 1.0 and keeps it
too. Verified arithmetically: both → 255.16, sum 510.32, exactly 2.00x.

**The proration fix is defeated by the very condition it was written for.** Its
docstring cites the *"operator-reported XRPUSDT double count"*; the cap exists
so *"a stale over-sized row can never inflate"*. It bounds each row at 1.0x and
does nothing about the **sum** — and rows exceeding the exchange size is exactly
the netting-divergence state (`BL-20260801`, measured at 451× on this same
account/symbol in August). The sibling symbol proves the correct path exists:
`bybit_1 AVAXUSDT` rows 4817/4795 carry an **identical per-unit delta**
(10.90/822.9 = 0.0132458; 72.97/5508.1 = 0.0132479) and sum to one position
figure, because there journal total (6331) == exchange size (6331).

**This exact class is already named in this repo.**
`src/runtime/provenance.py::FABRICATED_SOURCES` carries
`netted_duplicate_unattributed` — *"one netted broker record's FULL magnitude
written onto N sibling journal rows, so the same figure lands on rows whose
quantities differ by orders of magnitude"* — found on the **realised** side
2026-08-06 (`BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS`), writer fixed
(`order_monitor._prorate_netted_broker_pnl`), history marked
(`scripts/ops/mark_netted_duplicate_pnl.py`). **The unrealised sibling surface
was never swept.** The class was found, named, given a vocabulary and a
retroactive marker — on one of the two surfaces.

Second, separable defect: the prorated value is returned with source `"broker"`,
so `classify()` grades it **`measured`**, while `provenance.py` declares any
`*_prorated` source **FABRICATED** *"regardless of how measured the underlying
broker record was, because the SPLIT is an assumption."* The docstring makes
this explicit — *"Source stays `broker` — the number is broker-measured, scaled
by the row's declared share"* — a 2026-07-04 decision that the 2026-07-30
provenance rule overtook and nobody reconciled.

Tier: **2** (the sizer does not read it; consumers and reports do).

### F-9 · ACCOUNTING · Journal/exchange quantity divergence, live

`bybit_1 SOLUSDT`: journal open **1777.2** vs exchange **367.8** — **4.83x**,
the whole of trade 4816 unbacked, open since 03:22:04Z. Every other
both-sides-observed pair reconciles exactly (10 pairs graded; `alpaca_paper` /
`alpaca_portfolio` excluded — batch-1 truncation, not read).

### F-10 · MONEY-AT-RISK · The exit-loop 60s requirement is breached, and worse than recorded

`/api/diag/log_file?name=exit_loop_health`, process started 06:00:14Z:

```
requirement_s 60.0 · intervals_measured 40 · max_interval_ms 89878.0
interval_breaches 11 · last_breach 06:28:05Z · requirement_state "breached"
state "fresh"
```

**11 of 40 intervals (27.5%) exceeded the requirement; the worst was 89.9 s.**
CLAUDE.md's most recent record is *"max 61.04 s, 2 breaches"*.

Read with the n-discipline the repo itself insists on: a maximum grows with
sample size, so comparing maxima across different n is invalid — **but here the
larger max came from the far SMALLER sample** (89.9 s at n=40 vs 58.9 s at
n=694), which makes the degradation *stronger* evidence, not weaker. The
**breach rate** is the n-independent comparator and it is 27.5%.

`state: fresh` beside `requirement_state: breached` is the coexistence the
design predicted — liveness and the requirement are different questions.

### F-11 · The trader tick has roughly doubled since the M20 decouple

`/api/diag/tick_cost` (n=7 ticks, one process, 06:25:18Z):
`run_one_tick` **mean 138.1 s / max 179.7 s**, against the documented post-M20
**69.3 s mean / 96.8 s max**. `pipeline.signal_build` max **73.6 s**;
`fetch.1d` **mean 17.4 s** over n=22.

Stated honestly: **n=7 on one process.** But it is corroborated by F-10 (the
exit pass is fetch-bound and shares the root) and by the derived tick interval
(25 min / 7 ticks ≈ 214 s). Both point the same way.

### F-12 · Two strategies cannot get market data right now

- `monitor_blindness` alert: `ict_scalp_mgc_15m` / MGC — `candles_unavailable`
  for 3 consecutive ticks, *"open position has no live dynamic exit"*. This is
  the **same MGC 95 position that is target-naked** in F-6: no target, and the
  dynamic exit is blind. Only the stop stands.
- `mhg_pullback_1d`: *"no candle data returned for symbol=MHG timeframe=1d"*.

### F-13 · Latent trapdoor · 20 test fixtures declare a column production lacks

`order_packages.id` — the exact fictional column behind `BL-20260810` (pairs
`max_hold_bars` never once evaluated; legs ran 300–595 bars against a limit of
20). CLAUDE.md records that fix; it covered the **pairs** tests. 20 other files
still declare it. No production code queries it today, so this is a trapdoor,
not a live bug: the next author who writes `WHERE id = ?` gets a green CI and a
production `OperationalError`.

**Detector shipped:** `scripts/ci/check_test_schema_fidelity.py` (6/6 planted
controls, parses `ALTER TABLE` migrations as well as `CREATE TABLE` — a
migration-blind first draft reported 19 phantom violations on
`trades.reconcile_status`).

---

## Verified NON-issues (recorded so they are not re-raised)

- **`BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS` (open `critical`)
  appears RESOLVED.** It records MES holding 30 contracts of stop against a 15
  long in two disjoint OCA groups. Measured now: **one** MES stop of 15 in one
  group; `INV-PROTECT-OVERCOVER` passes on all three IB positions.
- **The pairs `skip_state_unreadable` fix holds.** The 959 unreadable rows in
  the `by_event` summary are historical: across the newest **40** records
  (00:07→06:05Z today) there is **not one**, and `close` events are present with
  `bars_held` 1–2, so `max_hold_bars` is being evaluated.
- **`ict-ib-gateway-watchdog.timer` reading `inactive` on the live VM is
  correct** — it is auto-enabled only where `/etc/ict-vm-role == gateway`.
- **Exposure three-state behaviour is correct.** `ib_live` / `oanda_practice`
  report `measured: false` + `unmeasured_reason: equity_unavailable` rather
  than a fabricated `0.0`.

## Coverage — stated honestly

**Behavioral:** 7 invariants asserted; 3 IB positions against broker order
truth; 10 (account, symbol) pairs reconciled against exchange truth; 2 netted
groups tested for duplicated uPnL; 1 exit-loop requirement; 1 read-state
collapse check. **4 FAIL, 3 pass, 0 not-measured.**

**Reading:** the audit skill; `guard_selftests.py` + `check_selftest_wiring.py`;
`dashboard.py::_broker_unrealised_for_trade`; `provenance.py` FABRICATED
vocabulary; `ib_client.py::_protective_leg_side`; `vm-diag-snapshot.yml`
allowlists; the 5 prior audit docs; the three backlogs (schema-level).

**NOT reached — do not read this audit as covering them:** the trainer VM
entirely · the gateway VM · `ict-trader-dashboard` and `ict-trader-android`
(both cloned, neither audited) · the backtesting harnesses · the 106 workflows
beyond the diag relay · `config/strategies.yaml` strategy-by-strategy · the ML
registry · closed-trade PnL populations · the remaining ~30 skills.

---

## Part 3 — The backtesting infrastructure (operator-named scope)

**Credit first, so this is not read as a novel discovery.** The *general*
backtest↔live fidelity gap is **already known, already measured, and already
being worked**: `docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md`
states the harnesses *"**re-implement** strategy logic separately from
`src/units/strategies/`"* and concludes *"our current backtests do NOT reproduce
live, and now we can prove it with a number"*, with a calibration gate
(`scripts/research/backtest_fidelity_calibrate.py`) scoring per-leg agreement.
`scripts/backtest_system.py` also genuinely imports the LIVE intent layer
(`aggregate_intents`, `_evaluate_confidence_override`), the ONE shared cost
model (`src/runtime/execution_costs.py` — fees + slippage + perp funding) and
the live monitor verdict. That is real parity on real axes.

What follows is a **specific defect inside that known gap** that the design doc
does not name.

### F-14 · The harness's risk unit is not the live risk unit

| | live | backtest |
|---|---|---|
| code | `risk.py:201` `risk_usdt = balance_usdt * risk_pct` | `backtest_system.py:866` `(bal * (rpct / 100.0)) / stop_dist` |
| unit | **fraction** — `risk.py:31` *"fraction of balance risked per trade (operator default 0.01)"* | **percent number** |
| live value | `accounts.yaml` → `risk_pct: 0.015` = **1.5 %/trade** (read from `/api/bot/config`, 06:31Z) | `--risk-pct` default **0.3** = **0.3 %/trade** |

Three separable problems:

1. **The docstring claims a parity it does not have.** `backtest_system.py:857`
   says *"Sizing mirrors the live RiskManager.position_size math
   (src/units/accounts/risk.py:141): risk_usd = balance * risk_pct"* — and then
   implements `balance * risk_pct / 100`. Field beats comment; the comment is
   the bug.
2. **The help text invites a 100× error.** `--risk-pct` is described as *"the
   shared account's risk_pct"*. The account's declared value is `0.015`.
   Passing it gives `0.015/100` = **0.015 %/trade — 100× under-live**.
3. **The defaults are not the live system.** At default flags the harness models
   **0.3 %/trade against live's 1.5 %** (5× smaller) and halts on a **3 % daily
   loss against live's 5 %** (stricter). Because the harness compounds the
   balance *and* models the daily-loss halt, the sizing scale is **not** a
   neutral constant: the halt binds at a different frequency and compounding
   differs, so two arms of an A/B can rank differently at live size.

**A confirmed instance, not a hypothetical:**
`docs/audits/system-portfolio-backtest-2026-05-30.md` records its invocation as
`--initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0` — a *system
portfolio* backtest run at one fifth of live per-trade risk under a stricter
halt.

Tier **1** to fix (research tooling, no live path). The honest repair is to make
the flag take the live unit (a fraction), default it to the account's declared
value, and correct the docstring — or, if the percent convention is kept
deliberately, say so and stop claiming it mirrors `risk_pct`.

### F-15 · A backtest's invocation is usually not recoverable from its record

Of 9 research docs sampled that reference `backtest_system.py`, **8 record zero
full command lines**. So for most Tier-3 evidence runs the parameters — risk
size, daily-halt, flip policy, regime-router arm, date range — cannot be
recovered from the record, and F-14 therefore cannot be ruled in or out for
them retrospectively.

This is the research-side analogue of *"always state the population"*: a result
whose invocation was never recorded cannot be re-derived, contradicted, or
re-run against a corrected harness. Suggested repair: have the harness emit its
own resolved argv into the run artifact, so recording it is automatic rather
than a discipline the author must remember.

### Not assessed

Whether the ~13 per-strategy harnesses (`backtest_pullback.py`,
`backtest_trend.py`, …) share this unit convention; look-ahead bias; the
min-lot / whole-contract floor (CLAUDE.md already concedes *no backtest models
the exchange min-lot floor*); the trainer-side sweep pipeline.

---

## Part 4 — The VMs (previously unreached)

Access note, corrected: direct HTTP egress is firewalled from this session, but
that is **not** the same as having no VM access. Three channels work and were
used: the **live diag relay** (`vm-diag-request`, read-only, batched), the
**trainer relay** (`trainer-vm-diag-request`, **arbitrary SSH bash**), and
**`system-actions`** (tiered; used `gateway-logs`, Tier-1 read).

I deliberately did **not** fire `vm-ib-gateway-selftest` — its documented
mechanism is to `docker stop` the gateway to prove the watchdog recovers it.
With three open IB positions, one already target-naked and one monitor-blind, a
controlled outage is the wrong instrument for an audit pass.

### F-16 · GATEWAY · The IB gateway container is flapping

`gateway-logs` (2026-08-20 06:56Z). Container reports `Up 22 minutes` — i.e.
started ~06:34. `IBC: Login has completed` events in the retained log:

| when | scheduled? |
|---|---|
| 2026-08-19 23:59:27 | — (near IBC's own `AutoRestartTime=11:59 PM`) |
| 2026-08-20 06:01:55 | **no** |
| 2026-08-20 06:05:24 | yes — `ict-ib-gateway-reset.timer` fires 06:05 UTC |
| 2026-08-20 06:34:48 | **no** |

**Three container starts inside 33 minutes, only one of them scheduled.** Each
logs `autorestart file not found: full authentication will be required`, so
every one is a full re-auth, not a soft restart. `socat … 127.0.0.1:4002:
Connection refused` appears repeatedly during each startup window.

`vm-ib-gateway-selftest`'s own stated purpose #1 is *"restart cadence — is the
container flapping?"* — and the answer is yes, obtained from a log read without
stopping anything.

**Tight temporal correlation with the live-VM symptoms** (all timestamps from
the same 06:30–06:34Z pull):

- all three IB positions report `unrealizedPnlSource: "unavailable"`,
  `unrealizedPnlProvenance: "unverified"`, `unrealizedPnl: null`
- `monitor_blindness` on `ict_scalp_mgc_15m` / MGC — `candles_unavailable` for
  3 consecutive ticks, *since 06:02:19Z*
- `mhg_pullback_1d`: *"no candle data returned for symbol=MHG timeframe=1d"*,
  *since 06:01:32Z*
- both `ib_target_naked` alerts *since 06:02:13Z*

**Stated honestly: this is correlation, not established causation.** The trader
process also restarted at 06:00:14Z, which is an alternative explanation for
some of it. What is established is that the gateway restarted three times in 33
minutes, that only one restart was scheduled, and that IB market data was
unavailable across that window.

### F-17 · TRAINER · Disk at 93 %, and the growth mechanism is unpruned dataset versions

`df`: **42 G of 45 G used, 3.2 G free (93 %)**. Trend across the audit record:
**86 % (07-31) → 79 % (08-04) → 93 % (08-20)**.

Breakdown: `datasets-out` **12 G**, of which `market_features` is **9.9 G**;
`ml` 4.2 G; `runtime_logs` 3.3 G; `data` 1.9 G.

⚠️ **MY FIRST HYPOTHESIS WAS WRONG AND IS RETRACTED HERE.** Seeing
`BTCUSDT/15m/v520` and `ETHUSDT/15m/v901` I inferred a per-build version
counter accumulating unpruned. **Counted (#10002): 32 version dirs in total**
across the whole family — BTCUSDT/15m has 8, ETHUSDT/15m has 4, several have 1.
`v520`/`v901` are **labels, not sequence numbers**. There is no version
accumulation.

The real mechanism is simply **a few very large datasets**: BTCUSDT/15m is
2.9 G across 8 dirs, BTCUSDT/5m 1.5 G, ETHUSDT/15m 1.7 G, and three single
`data.jsonl` files are 1.2 G each. The disk is consumed by dataset *size*, not
dataset *count* — which points the remedy at retention/compaction of the big
frames, not at pruning stale versions.

**A GC tool exists and I could not establish that it runs:**
`scripts/ops/trainer_dataset_gc.py`. No `gc`/`prune`/`clean` timer appears in
`systemctl list-timers`; it is referenced from `scripts/ops/run_training_cycle.sh`,
so it may run inside the cycle. Pending in #10003 — recorded as *unestablished*,
not as "never runs".

Two side observations from the same read:

- **~1 G of CUDA runtime on a GPU-less box** —
  `.venv/…/nvidia/cu13/lib/libcublasLt.so.13` (0.6 G) and `triton/_C/libtriton.so`
  (0.4 G) on a 1-OCPU/6-GB Ampere trainer with no GPU. M19 does spot-GPU bursts
  *elsewhere*; these libraries sit on the local venv regardless.
- **`data/signal_audit.jsonl` is 0.5 G** and `runtime_logs` is 3.3 G.

At 3.2 G free with a 12 G dataset tree still growing, this is a
weeks-not-months runway, and a full disk on the trainer stops dataset builds
and training — the failure would present as *"cycles stopped succeeding"*, not
as a disk alarm, because nothing watches trainer disk.

### F-18 · TRAINER · The stray journal is still there

`/home/ubuntu/ict-trading-bot/trade_journal.db` = **8.2 MB**, beside the real
`/home/ubuntu/ict-trading-bot/data/trade_journal.db` = **872 MB**. This is the
same stray that session `system-review-trade-mechanics-falsp8` hit **this
morning** — its exit census returned `LEGS 0` off it and was nearly reported.
The `canonical-db-resolver` guard exists to stop CWD-relative fallbacks creating
exactly this, and one is sitting on the trainer right now.

**Confirmed (#10002):** the stray is **8,495,104 bytes with `trades = 0`**; the
real journal is **913,940,480 bytes with `trades = 4820`**. A zero-row 8.5 MB
decoy is precisely the shape that reads as *"no data"* rather than as *"wrong
file"*.

### F-19 · TRAINER · Worktree "dirty" — RETRACTED, plus two stray backup files

⚠️ **DOWNGRADED — I called these "modified" and they are not.** All 15 are
`??` (**untracked**), not `M`: `datasets-out/`, `ml/registry-store/`,
`ml/experiments-runs/`, `results/`, `data/signal_audit.jsonl`,
`data/ibkr_datasets/`, `artifacts/calibration/`, `datasets-live/`,
`datasets-union/`, `datasets-out-bt/`. These are generated outputs correctly
kept out of git, so a `git reset --hard` destroys nothing. HEAD == `origin/main`
(`113741bc`, 0 behind). **Not a finding.**

Two genuine leftovers in the list, both LOW: `scripts/backtest_pullback.py.m20`
and `scripts/research/backtest_trend.py.m20` — stray `.m20` backup copies of
harness files sitting in the worktree — and a file literally named `-`.

### Verified healthy on the trainer

`failed_count=0`; every `ict-*` timer firing on schedule (`ict-trainer.timer`
last 00:06 today / next 00:01 tomorrow, publish every ~2 min, forecast, git-sync,
drift-retrain, promotion-readiness, catchup); uptime 35 days; memory fine
(343 MB used of 5.9 G).

---

## Part 5 — The consumer repos (previously unreached)

Both were **shallow (52 commits)** and were unshallowed first —
`ict-trader-dashboard` → 238 commits from 2026-05-04, `ict-trader-android` →
118 from 2026-05-26.

### F-20 · The F-8 double-count reaches the operator's screen

`streamlit_app.py::_upnl_sum` totals per-row `unrealizedPnl` across open
positions (correctly excluding `unavailable` legs and counting them separately),
and that total feeds the **"Unrealized P&L"** metric and the exec summary's
**"uPnL · real"** tile. So the bot's duplicated per-row value (F-8) becomes a
**~2× overstated unrealized total** on any netted symbol carrying more than one
open journal row.

Today the duplication is on `bybit_1`, a **paper** account, so the *real-money*
tile is unaffected **right now**. The mechanism is account-agnostic, and
`bybit_2` is real money on the same netting venue — it currently holds one row
per symbol, which is the only reason it is clean.

### F-21 · Two of three frontends never read the provenance grade the bot ships

`/api/bot/positions` returns `unrealizedPnlProvenance` on **every** row
(`measured` / `estimated` / `fabricated` / `unverified`).

| consumer | references |
|---|---|
| Streamlit `streamlit_app.py` | **0** |
| Svelte SPA `webapp/src/` | **0** |
| Android (Kotlin) | 6 — **consumes it** |

So the app renders a number without saying whether it is broker truth or a mark
estimate, on two of the three production surfaces. This is the
written-and-never-read class that `provenance-consumer-guard` exists to catch —
and that guard is a **bot-repo** CI check, so it structurally cannot see a
consumer repo. A provenance key can satisfy the guard with one bot-side reader
and still be invisible in the apps.

### F-22 · Dead code

`streamlit_app.py::_position_upnl` (line 1413) has **zero call sites** —
superseded by `_open_upnl` (1443), which is the one that reports whether the
value is known. LOW.

### F-23 · TRAINER · The dataset-audit alarm is noisy at ~15 %

`runtime_logs/training_cycle.jsonl`, **7,442 rows over 149 cycles**, status tally:

| status | count |
|---|---|
| `manifest_ok` | 4,754 |
| `manifest_skipped` | 408 |
| **`manifest_audit_flagged`** | **406** |
| **`manifest_untrained_stale`** | **290** |
| `manifest_audit_skipped_enforced` | 150 |
| `cycle_start` / `pulled` / `sync_ok` | 149 each |
| `datasets_ok` | 147 |

Non-`ok` manifest events are **846 against 4,754 ok ≈ 15 %**. `CLAUDE.md`'s
"If you see something, say something" rule cites `MB-20260719-DATASET-AUDIT-NOISE`
— the trainer dataset audit degenerating to 62/86 manifests alarming, inside
which the ETH-xa dead-feature bug soaked for weeks. This is the same instrument
still firing at a rate where individual flags are unlikely to be read.

`outcome` across all 7,442 rows is only `{trained: 20, already_complete: 20}`.

### F-24 · RETRACTED — the forecast producer is healthy; my glob named a family that does not exist

I globbed `datasets-out/forecast_live/**` and got nothing. The family is
**`forecasts`**, not `forecast_live`. Measured (#10004): the producer runs every
15 min and succeeds — `06:30:37`, `06:45:27`, `07:00:27` all "Finished", each
emitting `{"written": 3}` for BTC/ETH/SOL into
`runtime_logs/trainer_mirror/forecasts/*.json`, with
`datasets-out/forecasts/{BTCUSDT,SOLUSDT}/15m/v002/data.jsonl` refreshed
**2026-08-20**. **Not a finding.**

### F-25 · TRAINER · The dataset GC tool exists and is never invoked — on a 93 %-full disk

`scripts/ops/trainer_dataset_gc.py` is present. Measured (#10004):

- `grep -n "trainer_dataset_gc" scripts/ops/run_training_cycle.sh` → **no match**
- `systemctl list-timers | grep -iE "gc|prune|clean"` → only
  `systemd-tmpfiles-clean.timer` (an OS timer, not this)
- `grep -c "gc" runtime_logs/training_cycle.jsonl` → **0** over 7,442 rows

So the retention tool for a `datasets-out` tree that is **12 G of a 45 G disk
now at 93 %** has no caller, no schedule, and has never appeared in the cycle
log. This is the *written-and-never-read* family the repo already guards for
signals (`provenance-consumer-guard`) applied to an **operational tool**: the
remedy for the disk problem was built and never wired.

Tier 1 to schedule; the retention *policy* (what may be deleted) is the part
that needs a decision, not the wiring.

### F-26 · TRAINER · `registry.status` can only ever say `candidate` — and it cost me two wrong reads

Measured over all **95** models in `ml/registry-store/registry.jsonl`:

| field | distribution |
|---|---|
| `status` | **`candidate`: 95** — 100 %, no other value exists |
| `target_deployment_stage` | `candidate` 63 · **`shadow` 28** · **`advisory` 3** · `research_only` 1 |

**The deployment ladder lives in `target_deployment_stage`; `status` tracks the
training lifecycle and never advances** — `history[]` entries read
`to_status: candidate` with reasons like *"initial registration"* and
*"re-trained (run_id=…)"*, i.e. it is a registration/training state that is
`candidate` forever.

✅ **The fleet is CONSISTENT with `CLAUDE.md`.** The three at
`target_deployment_stage: advisory` are `btc-regime-15m-lgbm-fc-pcv-v2`,
`sol-regime-15m-lgbm-fc-pcv-v2` and `mes-regime-5m-lgbm-v2` — the first two are
exactly the BTC and SOL advisory heads the docs describe, and 28 at `shadow`
matches the shadow-auto-wire design. **No promotion is missing.**

**The finding is the legibility defect, and I am the evidence for it.** I probed
this registry twice and got a confidently wrong answer both times: first
`r.get("stage")` → `None ×95` (no such key), then `r.get("status")` →
`candidate ×95`, which reads as *"nothing has ever been promoted; the ML vol
gate has no advisory head"* — a false alarm about a live order-path capability.
Only dumping the row's actual keys resolved it.

A field named `status` that is invariant, sitting beside a field named
`target_deployment_stage` that is the real stage, is a trap the repo's own
`diagnostic-provenance` rule already describes (sub-class **A**, semantic
substitution: the label names a quantity the accessor does not return). Two
candidate repairs, both Tier-1: publish a resolver
(`ml.manifest.canonical_stage` over the right field) that every probe must
import — the same *"one module owns this"* pattern as
`scripts/ml/_regime_score_semantics.py` — and/or rename/retire the inert
`status`. MED, because the failure mode is a *false alarm about live ML
routing*, which is expensive to chase.

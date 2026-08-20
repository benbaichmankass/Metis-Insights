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

---

## Part 6 — Build-and-abandon, measured (operator directive 2026-08-20)

> *"we don't keep building things out half way and then leaving them to rust
> while the system chugs along with bad structure."*

The repo already guards this for **declared provenance keys**
(`provenance-consumer-guard`) and for **registered guard self-tests**
(`check_selftest_wiring.py`). Neither asks it of **executable tools**. New
detector: **`scripts/ci/check_unwired_artifacts.py`** (6/6 planted controls,
one corpus pass, 6.7 s over the whole tree).

### F-27 · 161 of 384 tools under `scripts/` have nothing that runs them

| class | n | meaning |
|---|---|---|
| **no runner references it at all** | **12** | nothing — no workflow, unit, script, `src/`, or doc |
| **referenced ONLY by docs** | **149** | built, written up in a sprint log or research doc, and then never run again |
| wired | 223 | |

⚠️ **A first pass reported 103 (11 / 92) and was UNDER-counting**, because it
treated a mention inside a comment or docstring as wiring. It does not: prose
about a tool is what a heavily-documented repo produces, and it does not run
anything. With comments and docstrings stripped, the count is 161.

The checker was defeated by exactly that, twice, on itself — first its own
docstring cited `trainer_dataset_gc.py` as the motivating example (making the
tool read as wired), then, after a path-exclusion band-aid, a new comment in
`render_system_report.py` cited the same tool and it read as wired again. The
band-aid fixed an instance; stripping non-executable text fixed the class. Both
cases now have planted controls (8/8).

**The number is not the finding, and 103 deletions is not the remedy.** Many of
these are legitimately manual research one-offs. The finding is that **not one
of them says so**, so *"deliberately manual"* and *"abandoned in July"* are
today indistinguishable — which is exactly why `trainer_dataset_gc.py` could
sit unrun while the disk it was written for climbed to 93 % (F-25).

The remedy is a declaration, not a purge: a tool either has a runner, or
carries `# wiring: manual-only — <who runs it, when>`. The marker is
**verified, not presence-only** — it must carry a reason on its own line, the
lesson this repo already paid for with `new-table-wiring-guard`'s
presence-only `# data-wiring:` marker.

### F-28 · A CI-shaped guard that has never run

Of 43 `scripts/check_*.py` + `scripts/ci/check_*.py`, **36 are named in
`run_guards.py`**. Of the 7 that are not:

- 4 are **runtime watchdogs** driven by systemd timers, not CI guards
  (`check_db_integrity`, `check_heartbeat`, `check_ib_gateway`,
  `check_web_api`) — correctly absent. **Not findings.**
- 2 are **mine, added today** (`check_test_schema_fidelity`,
  `check_unwired_artifacts`), deliberately left unwired pending review — and
  the detector correctly flags its own author. **Wiring them is a follow-up in
  this PR's scope.**
- **`scripts/check_roadmap_status_glyphs.py` is a genuine orphan** — CI-guard
  shaped, in the same family as the 36 wired ones, and referenced by no
  workflow, no unit, no script and no runner at all. A guard that has never
  run is the "green is not evidence" case one level earlier than
  `check_selftest_wiring.py` catches: not *registered-but-never-invoked* but
  *written-but-never-registered*.

### Why this belongs in the SKILLS, not just the backlog

Every instance of this class in the record was found **by accident, months
later** — the GC tool during a disk investigation, the IB pnl reader during an
unrelated verification, the exposure block by a session that went looking for
its reader. A backlog row per instance is a treadmill. The structural fix is
that **shipping a capability without a runner is a CI failure**, and that the
periodic review asks *"what was built since the last review, and is each piece
actually running?"* — both of which are skill changes, delivered separately in
this session.

---

## Part 7 — Per-strategy config (previously unreached)

Cross-checked `config/strategies.yaml` (55 declared) against
`config/accounts.yaml` (11 accounts) and the **live** runtime's loaded set
(`runtime_status.strategies`, 52, pulled 06:31Z).

| check | result |
|---|---|
| enabled but NOT loaded by the live runtime | **none** ✅ |
| routed by an account but NOT declared | **none** ✅ |
| loaded live but not enabled in YAML | **none** ✅ |
| routed strategy × account pairs whose **symbols do not intersect** | **0 of all routed pairs** ✅ |
| enabled but routed to **no account** | **1 — `turtle_soup`** ⚠️ |

Declared 55 → enabled 52 → loaded live 52 → named by an account 52. The three
disabled ones are correctly absent from the runtime. Execution gates:
**48 `live`, 7 `shadow`**.

### F-30 · `turtle_soup` — ⚠️ SUBSTANTIALLY CORRECTED after reading the history

**My first framing was wrong, and `git log` is what refuted it.** I reported an
enabled-but-unrouted strategy as an oversight. It is a **deliberate,
operator-approved downgrade**:

- **2026-07-01** (`8106255806`, Tier-3): de-routed from `bybit_1` — *"net-negative
  at every stop"* on the BTCUSDT `atr_stop_mult` sweep.
- **2026-07-07** (`8984eb5f`, Tier-3): `execution: live → shadow` *"to make the
  downgrade explicit + robust"*, and dropped from `ib_paper`'s list where it was
  a BTCUSDT no-op parked on an IBKR **futures** account — *"the illegible cruft"*
  the same commit removed.

That is a correct disposition, correctly recorded. **This would have shipped as a
false finding without the Phase-0a unshallow** — the clone arrives at 50 commits
and `git log -S` over it returns nothing. That is the concrete value of the
un-shallow step, not a formality.

**What survives is a LOW legibility nit, not a defect.** It remains
`enabled: true`, so it still loads and evaluates each tick while `shadow` + no
route means it can neither trade nor log a per-account order package. If the
intent is "kept, off", `enabled: false` expresses that with no evaluation cost.
Tier-3 file; proposal only.

### Verified sound

- **Routing coherence is exact**: every routed strategy shares at least one
  symbol with its account. The "declared live on an account that cannot trade
  its instrument" class is **absent**.
- `ib_live` and `oanda_practice` carry `mode: dry_run` **and** zero strategies —
  coherently shelved, not half-shelved.

---

## Part 8 — Modularity / scalability (operator directive 2026-08-20)

> *"Building things so that system changes require as few code edits as
> possible (or at least concentrate the edits to one place so we don't have to
> chase down random hard-coded items across the repo)."*

The measurable form is **change amplification**: how many files must be edited
to make one system change? Measured from the deep clone.

### F-31 · Adding one strategy leg costs 15–17 files

Two real wirings, from `git show --name-only`:

| commit | leg | files |
|---|---|---|
| `2026-07-28` | `ict_scalp_mgc_15m` | **17** |
| `2026-07-21` | M27 altcoin legs (SOL/XRP/AVAX) | **15** |

Both touch the **same three `src/` files** and the **same satellite registries**:

```
src/runtime/strategy_signal_builders.py   a named wrapper fn
src/runtime/intent_multiplexer.py         {name: builder} registry entry
src/runtime/intents.py                    DEFAULT_PRIORITIES entry
config/strategies.yaml                    the declaration      (source of truth)
config/accounts.yaml                      the routing          (source of truth)
config/strategy_descriptions.json         satellite registry
config/strategy_changelog.json            satellite registry
config/regime_coverage_exemptions.yaml    satellite registry
docs/research/exit-refinement-coverage.json   satellite registry
docs/strategy-coverage-matrix.md          satellite registry
+ 4–5 test files
```

**Be fair about what is already good.** The strategy *logic* is properly
factored: the new builder is a thin wrapper delegating to a shared
`_ict_scalp_variant_builder` that *reads the timeframe and symbol from config*
and routes the candle fetch by venue. Nothing about the mechanism is copied per
leg. The account roster is modular too — see "Verified sound" below.

**What is not factored is the REGISTRATION.** Two hand-maintained maps plus
5–6 satellite registries must be kept in sync by hand with two YAML files that
already contain the facts. Every one is an opportunity to half-wire, which is
the operator's stated failure mode. A config-declared strategy could register
itself (declare its builder family + priority in `strategies.yaml`; build the
maps at import), collapsing three `src/` edits to zero.

### F-32 · The "safe" default priority now outranks 90 % of the declared roster

`intents.py::_UNKNOWN_STRATEGY_PRIORITY = 10`, documented as *"picked
deliberately below the in-scope strategies so a misconfigured new strategy never
silently overrides Turtle Soup / VWAP."* That was true when the roster was
`{turtle_soup: 50, vwap: 40}`.

The convention has since become **"a new leg gets 0"** — the *untested-roster
floor*, so that (in the `ict_scalp_mgc_15m` comment's own words) *"a wiring slip
stays safe"* and a new leg *"can never OVERRIDE the established sleeves in a
conflict."*

Measured now: **41 of 50 listed legs are pinned at 0, and 45 of 50 sit BELOW the
unlisted default of 10.**

**So omission is now less safe than declaration — the exact inverse of the
constant's stated purpose.** Five enabled strategies carry no entry
(`gdx_pullback_1d`, `iaum_pullback_1d`, `scha_trend_long_1d`,
`slv_pullback_1d`, `splg_trend_long_1d`) and therefore resolve to 10, above
every floored peer. A live collision exists:

```
slv_pullback_1d  (unlisted -> 10)   vs   slv_trend_1h  (pinned 0)
shared symbol SLV, on alpaca_paper / alpaca_portfolio / alpaca_live / alpaca_options_paper
=> on an SLV conflict the FORGOTTEN leg outranks the DELIBERATELY FLOORED one
```

**Bound the blast radius honestly.** The structural inversion is certain. A
realized incident is **not** demonstrated: both legs would need actionable,
opposing intents on SLV on the same tick, and `FLIP_POLICY=hold` means an
opposing signal does not reverse a held position anyway. What is established is
that the fail-safe no longer fails safe, and that this is a direct consequence
of F-31 — a hand-maintained map drifting from the convention it encodes.

`src/runtime/intents.py` is order-routing: **Tier-3, proposed not changed.** The
minimal fix is to move the default to the floor the roster actually uses (0) —
or better, derive priority from config so the map cannot drift at all.

### Verified sound — the account axis IS modular

Account ids appear as string literals in executable code in only **7 files**
(comments and docstrings stripped — an un-stripped count says 12+ and is
measuring prose, the same error the unwired detector made):

```
2 ids  src/web/api/routers/pnl_exchange.py     1 id  src/units/strategies/pairs_executor.py
2 ids  src/core/account_profile.py             1 id  src/units/strategies/macro_thesis/thesis_tick.py
1 id   src/web/api/routers/prop.py             1 id  src/units/strategies/macro_thesis/thesis_engine.py
1 id   src/prop/prop_expiry_prompt.py
```

Mostly single-account defaults on a router or a prop path, not rosters. **Adding
an account does not require chasing literals through `src/`.** The builder
registry is also complete — **52 of 52** enabled strategies have an entry, so
the documented *"declared live, no builder"* class is **absent today**.

---

## Part 9 — Backlog classes, harness sizing, and the trainer's green light

Added after the modularity pass, covering the operator's message-3 asks: review
the WHOLE backlog for larger structural issues rather than knocking items off
one by one, and verify that training sessions actually produce actionable
results daily rather than trusting a green trainer VM.

### F-33 — The backlog's `status` field has no controlled vocabulary (severity: HIGH)

`status` is free text. Across the three review backlogs (910 rows) there are
**18 distinct spellings that mean "open"**, dominated by two that differ only by
convention:

| spelling | rows |
|---|---|
| `kept_open` | 243 |
| `open` | 67 |
| `measured_open` | 7 |
| `fix_landed_pending_live_verification` | 3 |
| 14 further one-off spellings | 14 |

plus free-text values such as `code fix shipped; DATA REPAIR still open (52 rows
written degraded)` and `resolved (guard); follow-up OPEN, see
resolution_criteria (3)` — a sentence in a status field.

**Measured consequence, on my own first attempt:** filtering for
`status in {open, in_progress, snoozed, ""}` returned **67 open items**. The
vocabulary-aware count is **334**. An 80% undercount, produced by the obvious
query, reported with no indication anything was missed. This is sub-class C
(unasserted denominator) sitting inside the governance system that is supposed
to catch it — and `backlog_drive` in the review-coverage guard is judged against
exactly this denominator.

Open-item age (n=303 datable): median 7d, p90 44d, max 87d; 14 items older than
60 days.

**Fix shape:** an enum with a migration, plus a `backlog-status-vocabulary`
guard. Tier-1.

### F-34 — Observability defects accumulate; concrete runtime faults do not (severity: HIGH, structural)

The operator asked whether the backlog hides *larger issues*. It does, and the
split is measurable rather than thematic.

Cohort: every item opened on/after 2026-07-01 (**n=574**), which controls for
resolved items being systematically older. Cohort-wide open rate **49.0%** — the
denominator each figure below is judged against.

| class | n | open % | vs cohort |
|---|---|---|---|
| **cannot-see-itself** (collapsed state · no read surface · written-never-read) | 110 | 62.7% | **+13.8pp** |
| **concrete runtime fault** (reconciler · orphan · netting · wedge · timeout · crash) | 144 | 42.4% | **−6.6pp** |

The two populations overlap by only 26 items (11% of their union), so this is a
**20.4pp spread between largely distinct classes**, not one signal counted twice.

Reading: the system closes concrete, reproducible, money-visible faults *better*
than its own average, and closes defects in its ability to observe itself
*worse*. That is the structural issue behind the operator's complaint about
things "built halfway and left to rust" — the rusting is concentrated in
observability, which is precisely the category whose decay is invisible by
construction.

**A retraction, recorded because the method matters.** An intermediate run of
this analysis reported provenance-class items at **+26.0pp**. Controlling for
the time confound collapsed that to **+3.1pp** — the lift was almost entirely
an artifact of resolved items being older than the provenance work itself
(started 2026-07-30). The uncontrolled figure was wrong and is withdrawn. A
looser first-pass classifier also matched 94% of all items and is likewise
withdrawn; it failed the control (resolved items matched at nearly the same
rate), which is why the control was run before anything was reported.

### F-35 — The trainer reports GREEN while 9 of 76 manifests have not trained (severity: HIGH)

Measured live, 2026-08-20 (relay #10013). This is the operator's exact
complaint, reproduced.

`/api/bot/ml/status` — the first surface a review session reads — returns:

```
last_cycle: {failed: 0, trained: 0, overall_rc: 0, outcome: "already_complete"}
dataset_builds_24h: {ok: 120, failed: 0}
manifests_24h: {ok: 68, failed: 0}
```

All green. The trainer's own cycle log, same run, says:

```
training_staleness_summary: scanned 76, stale 7, never_trained 2, awaiting_source 1
```

- `exit-policy-v1` — **0 registered runs across 95 registry files**, manifest
  24.8d old; the trainer's own words: *"it has been skipped/failed every cycle
  since it landed"*.
- `setup-candidates-metalabel-paper-v1` — 0 runs, 23.9d old, same.
- Five further manifests last trained **2026-07-26**, all at exactly **25.0d**
  against a 7d threshold.

Seven manifests sharing one last-trained date is not seven independent
failures — it is **one event on 2026-07-26 after which a class stopped
training**, which nothing has surfaced in the 25 days since.

**Two distinct defects, both of classes F-34 names:**

1. **Written and never read.** The trainer DETECTS this correctly and emits
   `manifest_untrained_stale` + `training_staleness_summary` rows into
   `training_cycle.jsonl`. `trainer_status.json` does not carry them, so
   `/api/bot/ml/status` cannot show them. The detector works; nothing consumes it.
2. **Semantic substitution (sub-class A).** `training_center.py` does discuss
   "staleness" — but only `mirror_age_seconds`, i.e. *is the trainer reachable*.
   That is a different question from *are manifests training*, sharing a word.
   A reviewer who checks "staleness" on this surface gets a confident answer to
   the question they did not ask. (`hourly_report.py`'s `tick_stale` is a third
   distinct meaning.)

The collapse that makes it green: the cycle counts a SKIPPED manifest as
`already_done`, so `already_done: 76` of 76 scanned yields
`trained: 0, failed: 0, overall_rc: 0, outcome: already_complete`. "Trained
successfully" and "skipped every cycle for 25 days" are indistinguishable in the
headline — a collapsed state in the ML lifecycle's own summary.

**Fix shape:** publish the staleness block into `trainer_status.json`, surface it
on `/api/bot/ml/status`, and make `ml_output_actionability` (the review-coverage
key added this session) assert `stale == 0 and never_trained == 0` rather than
reading `overall_rc`. Tier-1. The 2026-07-26 root cause is separate work.

### F-36 — A daily timer fired 7 times in 9 hours (severity: LOW; cause NOT established)

`ict-research-results-gate.timer` declares `OnCalendar=*-*-* 07:12:00`,
`Persistent=true`, `RandomizedDelaySec=300`, and no `OnBootSec`/`OnUnitActiveSec`.
Journal for 2026-08-19T23:04 → 2026-08-20T07:53 shows **seven** completed runs
(23:04, 05:59, 06:31, 06:59, 07:13, 07:32, 07:53).

The timer unit alone does not explain this, and **I did not establish the cause**
— stating that rather than naming a mechanism no probe tested. The plausible
direction is the deploy path re-enabling timers on every merge to `main`, which
would match today's merge frequency, but I have not confirmed it.

Impact is low (1.1–1.2s CPU per run) but not nil: the report file is rewritten
per-deploy rather than daily, so anything reasoning about "yesterday's daily
report" is reading a deploy-triggered snapshot instead.

### Resolved by measurement, not findings

- **The `--risk-pct` unit divergence is confined to `backtest_system.py`.** The
  other 12 harnesses do not reference `risk_pct` at all because they are
  **R-normalized and capital-free** (`r_multiple`, `net_total_r`,
  `max_drawdown_r`) — they never size in currency, so the unit bug cannot reach
  them. This **narrows** the Part-3 framing, which left open whether the family
  shared the defect. It does not.
- **No R-vs-dollars comparison exists.** The R4 gate reads live measured dollar
  PnL only (`totalPnlMeasured` + a coverage floor, abstaining below it); it never
  compares harness R output to live currency. The apples-to-oranges risk I went
  looking for is not present.
- **`scripts/research/backtest_trend.py` is a deliberate hard-fail shim**, not
  rot. It occupies the name so a third engine cannot quietly reappear, and its
  docstring accurately records the retirement. A positive modularity pattern,
  and the model the change-amplification findings should be fixed toward.
- **The R4 gate is genuinely running** (F-36 is about its cadence, not its
  liveness) and writing `report-7d.json` + `report-30d.json`. Not build-and-abandon.

---

## Part 10 — `risk_pct`: one name, two unit conventions, and why R-normalization hid it

Operator-directed (2026-08-20): the backtest risk and the live config don't
match; the harness should either be independent of the risk setting or kept
current, and in any case should sweep risk levels. *"That's a gap probably of
more than one type in the workflow and in the infra."* That reading is correct,
and the gap is wider than the single file Part 3 named.

### F-37 — Two incompatible unit conventions share the name `risk_pct` (severity: HIGH)

**FRACTION** (production). `config/accounts.yaml::risk_pct: 0.015`, consumed at
`src/units/accounts/risk.py:201` as `risk_usdt = balance_usdt * risk_pct`. 0.015
means 1.5%. Every live account declares 0.015.

**PERCENT** (research + prop). `rpct / 100.0`, so 0.3 means 0.3%. Sites:

| file | sites |
|---|---|
| `scripts/backtest_system.py` | 1 (`:866`) |
| `scripts/research/allocator_multisymbol_backtest.py` | 5 |
| `src/prop/montecarlo.py` | 5 |
| `src/prop/breakout_ticket.py` | 1 |
| `scripts/prop/account_compat_matrix.py` | 1 (docstring) |

`--risk-pct` defaults to **0.3** in `backtest_system.py`,
`build_backtest_panel.py`, `allocator_multisymbol_backtest.py`, and
`walkforward_flip_policy.py` — i.e. **0.3%, against a live basis of 1.5%. A 5×
divergence**, and the walk-forward that decides Tier-3 questions runs on the
wrong one.

**The sharp edge is a flag that means two things.** `scripts/research/` contains
both conventions under one flag name:

```
pairs_dollar_lots.py    --risk-pct 0.015  ->  1.5%      (fraction; :138 balance * risk_pct)
walkforward_flip_policy.py --risk-pct 0.015  ->  0.015%  (percent;  rpct / 100.0)
```

Same flag, same value, **100× apart**. A researcher who passes the live figure
because it is the live figure gets a run 100× under-sized in one script and
correct in the other, with nothing in either output naming the convention.

**`risk_pct: 0.3` in `config/accounts.yaml` comments is stale prose**, not a
field — the per-strategy multiplier was removed 2026-06-29
(`risk.py:366`). The one grep hit in `strategies.yaml` is `risk_pct: 0.3.`
*with a trailing period*: a sentence, not YAML. Worth fixing, but it is not the
cause; it is a plausible-looking decoy that would mislead the next reader.

### F-38 — R-normalization is an assumption, not neutrality (severity: HIGH, conceptual)

Part 9 recorded that 12 of 13 harnesses are R-normalized and capital-free, and
framed that as why the unit bug cannot reach them. That is true and it is
**not** the reassurance it reads as, which the operator's "it needs to check
various different risk percentages" gets at exactly.

Working in R silently asserts two things:

1. **PnL is linear in `risk_pct`** — so the risk level is a scalar you can apply
   afterwards; and
2. **the set of trades is invariant to `risk_pct`** — the same trades happen at
   any risk level.

**(2) is false in production, discontinuously.** `RiskManager.position_size`
floors futures to **whole contracts and refuses sub-1-contract outright**, floors
Alpaca to whole shares, applies `min_qty`, and caps against available margin.
So below a threshold the trade does not shrink — **it does not happen**:

```
balance_min_for_1_contract = stop_distance × contract_value_usd / risk_pct
```

| symbol | contract_value_usd | stop (illustrative) | live 1.5% | harness 0.3% |
|---|---|---|---|---|
| MES | 5.0 | 20.00 | $6,667 | $33,333 |
| MGC | 10.0 | 8.00 | $5,333 | $26,667 |
| MHG | 2500.0 | 0.05 | $8,333 | $41,667 |

(Contract values are from `config/instruments.yaml`; the stop distances are
illustrative, so read the **formula and the 5× ratio** as the result, not the
dollar figures.)

The consequence is directional and unflattering: the harness's risk level is the
one that makes futures legs *stop trading*, and an R-normalized harness reports
their per-trade edge cheerfully because in R-space the refused trades are simply
absent from a population it never claimed to enumerate. **Small risk looks safe
when what it actually means is "this strategy does not trade."** Ruin and
drawdown are nonlinear in risk too, and margin refusal is already live and
measured (`bybit_2`, 110007, 30% of that account's orders in-window).

So neither branch of the operator's either/or is currently satisfied:
independence is claimed but is really an untested assumption, and the one
capital-simulating harness is 5× out of date.

### F-39 — Nothing sweeps `risk_pct`, though the sweep machinery exists (severity: MEDIUM)

`scripts/backtest_trend.py` ships `_parse_grid()` + `--confidence-sweep` +
`_confidence_sweep()` — a working, general grid sweeper. It is pointed at
**confidence**. Across every harness there is **no `--risk-sweep`, no risk grid,
no risk axis in any panel builder**.

The parameter that determines whether a futures trade happens *at all* is the
one parameter never swept, while a parameter that only scales an existing trade
is swept routinely.

### F-40 — The right guard exists and its scope stops at the boundary (severity: HIGH — this is the root cause)

This is the answer to *how it should have worked and what went wrong*, and it is
not "someone got the units wrong."

The operator already settled the principle on **2026-06-29**: position sizing is
the `RiskManager`'s sole responsibility, risk lives at the account level and
nowhere else. It was enforced in code (`strategy_risk_pct` removed end-to-end)
**and** given a CI guard, `scripts/check_strategy_risk_field_in_diff.py`, which
fails a PR that re-introduces a risk level anywhere.

That guard's declared scope is `config/strategies.yaml` and `src/`. **`scripts/`
is outside it.** So the repo made the correct decision, built the correct
enforcement, and drew its boundary around *production* — while the concept
"there is one definition of per-trade risk" has to hold across *research* too,
because research is what authorises Tier-3 changes to production.

The divergence is therefore not an oversight in a file. It is a **guard whose
boundary and concept's boundary don't match** — and that mismatch is invisible
precisely because the guard is green.

### The generalizable rule

**Class: a value that crosses the research↔production boundary without a shared
resolver.** The repo already killed this class twice for other concepts —
`src.utils.paths.trade_journal_db_path()` for the DB path (after stray journals),
`src/runtime/provenance.py` for measured-vs-manufactured (after a phantom
−$6,358 leak). Both are the same move: one module owns the definition, and a CI
guard fails a second one. It was never applied to `risk_pct`.

For any parameter appearing in **both** a config file and a research/backtest
CLI, three questions — each mapping to one of the failure types the operator
predicted:

1. **INFRA** — is there exactly one function that converts it, imported by both
   sides? (`risk_pct`: no. Two conventions, five files, zero resolvers.)
2. **WORKFLOW** — does a guard assert the harness default against the live
   declaration? (`risk_pct`: no. `strategy-risk-guard` exists and excludes
   `scripts/`.)
3. **METHOD** — does the harness sweep it, or fix it? If fixed, is the fixed
   value the live one, asserted rather than assumed? (`risk_pct`: fixed, at 5×
   off, unasserted.)

A "yes, because it's R-normalized" to (3) is only valid if the harness also
models the **quantization and refusal** paths that make the trade set a function
of the parameter — otherwise independence is an assumption the harness is
structurally unable to test.

### Proposed fixes (not applied — review first, per the operator)

1. **One resolver.** `src/runtime/risk_units.py` owning the fraction↔percent
   conversion, imported by every harness and prop module; `--risk-pct` accepts a
   declared unit and echoes the resolved fraction in every run's params block
   (diagnostic provenance: the output states what it computed). Tier-1.
2. **Extend `strategy-risk-guard` to `scripts/`** — fail a new `/ 100.0` on a
   `risk_pct` outside the resolver, and assert every `--risk-pct` default equals
   the live `accounts.yaml` basis unless it carries an inline justification.
   Tier-1. This is the fix that generalizes; 1 and 3 are instances of it.
3. **A risk sweep.** Reuse `_parse_grid`; add `--risk-sweep` and report
   per-risk-level expectancy, max drawdown, ruin, **and refusal count** — the
   last is the column that makes F-38 visible instead of theoretical. Tier-1
   for the harness; any resulting change to a live `risk_pct` is Tier-3.
4. **Quantization in the R-normalized harnesses**, or an explicit declared
   caveat that their results hold only above the per-symbol balance threshold.
   Tier-1.
5. Fix the stale `risk_pct 0.3` prose in `accounts.yaml`. Tier-1, cosmetic, but
   it is the decoy the next reader hits first.

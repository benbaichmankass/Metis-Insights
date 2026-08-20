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

---

## Part 11 — GitHub Actions (106 workflows, operator-named scope)

F-40 predicted the yield here: defects concentrate at guard and automation
boundaries. They do, and the largest one is an ML verification substrate that
has produced nothing for a month while every surface reports green.

### F-41 — `replay-pregate-nightly` has been red every night for a month (severity: HIGH)

Measured 2026-08-20 over the last **30 scheduled runs** (2026-07-22 → 2026-08-20):

| conclusion | runs |
|---|---|
| failure | 27 |
| cancelled | 3 |
| **success** | **0** |

Not a liveness problem — the cron fires reliably at 04:0x every night. It runs,
and it fails, and it has done so **without a single success in the sampled
month**. (Total scheduled runs = 55; I read the most recent 30 and state that
bound rather than claiming the whole history.)

**What it is.** The ML **replay pre-gate (RG3)**: it scores every shadow-stage
regime head through the LIVE predict path against the dataset's own
`regime_label`, and commits the report back to the repo. Its own header states
the purpose — *"so the result survives a dead Claude session… the overnight 'run
through the trainings' substrate."*

So this is precisely the infrastructure for the operator's standing requirement
that *"just checking that the trainer VM is green isn't enough — we need to
verify the training sessions are producing reliable and actionable results,
every day."* It has produced zero for a month. **Read this next to F-35**
(trainer reports `overall_rc: 0` while 9 of 76 manifests are stale): two
independent mechanisms, one blind spot — the ML lifecycle's verification layer
is dark, and every surface that would show it reports green.

### F-42 — The root cause discards work that already succeeded (severity: HIGH)

From the 2026-08-20 run log, the failure is deterministic, not a flake. The
fleet scores **9 of 21 heads successfully** — with good numbers
(`TRUSTWORTHY_SIGNAL`, AUC 0.71–0.93, n≈25k–30k) — then at head 10/21:

```
[fleet] (10/21) btc-regime-5m-lgbm-yz-v1 ...
client_loop: send disconnect: Broken pipe
##[error]no JSON object in driver output
```

Three compounding defects, all verified in the workflow file:

1. **No SSH keepalive.** The invocation sets `ConnectTimeout=20` and **no
   `ServerAliveInterval` / `ServerAliveCountMax`**, on a remote command that runs
   ~25 minutes (04:37 → 05:02) emitting output sparsely. An idle NAT/firewall
   timeout kills the channel — the textbook `Broken pipe`.
2. **An all-or-nothing output contract.** The consumer is
   `raw.find('{')` / `raw.rfind('}')` — ONE JSON object spanning the entire
   output. A truncated run has no closing brace, so **nine heads of completed,
   valid scoring are thrown away every night.** The work is real; only the
   transport is broken.
3. **The failure carries no partial result.** The error text is `no JSON object
   in driver output`, which describes the PARSE, not the disconnect immediately
   above it — a reader is pointed at the driver rather than at the SSH channel.

Note `timeout-minutes: 45` was NOT reached; this is not a budget problem.

### F-43 — The failure alerter cannot tell delivered from undelivered (severity: HIGH)

`claude-run-failure-alert.yml` **exists, is wired, and explicitly names
`replay-pregate-nightly` in its watch list.** Its `if:` condition is correct —
`failure || cancelled || timed_out`, and these runs conclude `failure`. So the
alerting was built for exactly this and 27 nights still passed.

Two silent-no-op paths, both verified in the step body:

1. **Missing secrets → `exit 0`.** If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
   are unset it prints a `::warning::` and exits **0** — the alert job goes
   **green having alerted nobody**.
2. **The send's result is discarded.** `curl -sS … >/dev/null`, with no `-f` and
   no response check. Telegram returns **HTTP 200 with `{"ok":false}`** for
   `chat not found` / `bot blocked`, so an API-level rejection is
   indistinguishable from a delivery. The step then prints
   `Alerted: ${WF_NAME} concluded ${WF_CONCLUSION}` — a confident claim of
   delivery over code that only established *a request was sent* (diagnostic
   provenance sub-class A).

**What I could NOT establish, stated rather than assumed.** I could not confirm
from the run list whether the alert fired on the nightly failures: the sampled
page of `claude-run-failure-alert` runs (30 of 19,358) was entirely
`skipped` — but all 30 were from one 12-second CI burst at 08:31 today, i.e.
successes being correctly skipped, not evidence about 05:02. So two modes remain
open and they need different fixes:

- **(a) it fires and the ping is walked past** → the desensitized-alarm P1 that
  `CLAUDE.md` names in its own words (*"an alarm that fires constantly and is
  routinely walked past… the desensitized alarm is ITSELF a P1 bug"*);
- **(b) it fires and silently no-ops** via either path above → the alert has
  never worked and its green runs say otherwise.

**The distinguishing test** (cheap, decisive): read the job log of one
`claude-run-failure-alert` run triggered by a 04:37 `replay-pregate-nightly`
failure. `Alerted: …` present ⇒ mode (a); `::warning::Telegram secrets not
configured` ⇒ mode (b). **Either way F-43 stands**, because the alerter reports
success in both — its own claim is not falsifiable by anything it controls,
which is the INDEPENDENCE axis failing on the component whose entire job is to
be the independent observer.

### F-44 — What the workflow corpus gets RIGHT (recorded, because it bounds the finding)

Two classes I probed for and did not find, each stated with its denominator:

- **Untrusted-input injection: ZERO sites across all 106 workflows.** Every
  issue-driven workflow routes `github.event.issue.body` through an env var
  rather than inline `${{ }}` interpolation, exactly as `CLAUDE.md` documents.
  With 82 of 106 workflows issue-triggered, this is the highest-exposure class
  in the repo and it is handled consistently.
- **The `curl … || echo` defaulted-parse class: 2 sites, 1 file.**
  `alpaca-options-probe.yml:70,78` curl into a file, swallow failure with
  `|| true`, then `jq … || echo 0` — so "the API call failed" and "this
  underlying has zero contracts" both render as `contracts=0`. **Step [3] of the
  same file does it correctly** (`-w "%{http_code}"`, parse gated on `= "200"`),
  which makes this inconsistency rather than ignorance, and means the fix is
  already written one step below the defect.

⚠️ **A methodology note, because my first probe was wrong.** My initial detector
for that class returned **0 sites** — over a corpus containing a known positive I
had already read by eye. The bug was granularity: it scanned whole `run:` blocks,
so step [3]'s `%{http_code}` masked its absence in steps [1] and [2]. Re-scoped
per-`curl`, it finds both. **A zero from a probe that was never shown to find a
positive is not a measurement** — the control is what turned a false all-clear
into the honest count above.

### Smaller items

- **109 workflows are registered against 106 files on disk.** Registered
  workflows persist after file deletion, so 3 are candidates for stale
  registrations. Not chased.
- **`continue-on-error: true` on 14 workflows** and `|| true` / `|| echo` in 80
  of 106 — the latter is mostly correct usage in diagnostic printers where the
  failure is honestly labelled (`(failed)`, `unreachable`, `none`, `000`).
  Not swept individually; the two that mattered are F-44.
- **Only 4 of 106 workflows run on `pull_request`.** 82 are issue-triggered
  relay/system-action paths — the automation surface is far larger than the CI
  surface, which is itself the argument for auditing it.

### Proposed fixes (not applied — review first)

1. `ServerAliveInterval=30` + `ServerAliveCountMax=6` on the pregate SSH, and on
   every other long-running SSH relay (sweep needed — this is a class, not a
   file). Tier-1.
2. Make the pregate emit **one JSON object per head, streamed**, so a
   disconnect keeps the heads already scored; the consumer takes the last
   complete record set and reports `heads_scored / heads_total` as an explicit
   coverage figure. Tier-1. This converts a nightly total loss into a partial
   result with a stated denominator.
3. Make the alerter falsifiable: check Telegram's `ok` field, **fail the job**
   when the send is rejected, and make missing secrets a **failure, not
   `exit 0`** — an alerter that cannot alert must go red, not green. Tier-1.
4. Run the distinguishing test in F-43 before choosing between the mode-(a) and
   mode-(b) remedies; if (a), the fix is de-duplication/escalation, not another
   ping.

---

## Part 12 — Skills as instructions, and what actually enforces Tier-3

The operator's original scope named *"all of the instructions, all of the skills"*.
This pass treats a skill as what it is: a **binding instruction to every future
session**. A defect there does not cause one bad outcome, it causes a class.

### F-45 — Skill path references are healthy: 7 dead of 366 (severity: LOW)

Probe: every `` `path` `` a skill instructs a session to use, checked for
existence. **366 distinct references across 30 skills, 7 nonexistent (1.9 %).**
Stated with the denominator because the result is a *pass* and a bare "7 dead"
would read as a failure.

Discriminated rather than reported raw — three of the seven are **not** defects:

| ref | verdict |
|---|---|
| `runtime_logs/account_reachability_alert_state.json` (×2: health-review, system-review) | **correct** — a runtime file that exists only on the VM |
| `config/risk_caps.yaml` (×2: health-review, performance-review) | see F-46 |
| `.github/workflows/canonical-db-resolver.yml` (db-wiring) | **stale** — retired into `run_guards.py` in the CI fan-out consolidation |
| `scripts/ops/fetch_dukascopy_index.py`, `scripts/research_decider.py` (backtesting) | **stale** — instructs a session to run tools that do not exist |

So the real dead-instruction count is **3**, in 2 skills. This corpus is in far
better shape than `scripts/` (161 of 384 unrun) or the backlog vocabulary.

### F-46 — `config/risk_caps.yaml` has NEVER existed, and is named in the Tier-3 hard limit (severity: MEDIUM)

`CLAUDE.md` § "VM authority split" lists it among the files that must never be
merged *"without explicit operator approval"*, and 10+ sprint logs, 2 review
skills and several design docs reference it. `git log --all -- config/risk_caps.yaml`
returns **nothing**: it is not deleted, it was never created.

**The protection nonetheless holds — by a different file.** The real caps are
`config/accounts.yaml::risk.daily_loss_pct: 0.05` (per account), and
`accounts.yaml` is itself on the Tier-3 list. So this is not an exposure; it is a
**phantom entry that manufactures false confidence** that risk caps are
separately guarded. A reader auditing the list would tick "risk caps: covered"
against a file that does not exist, and never look at where they actually live.

### F-47 — Tier-3 approval is enforced by nothing mechanical (severity: HIGH)

The sharpest finding of this pass, and it was reached by asking the F-40
question — *does the guard's boundary match the concept's boundary?* — of the
governance layer itself.

**Live branch protection on `main`** (read from the GitHub API via the
`[bp-report]` relay, issue #10025, not inferred from the workflow's intent —
`branch-protection-sync.yml` *preserves* this field rather than setting it, so
its own text cannot answer the question):

```json
{
  "required_status_checks": ["pytest-collect", "pytest-run", "guards"],
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

**`required_pull_request_reviews` is `null`.** Four mechanisms that each *look*
like they would catch an unapproved Tier-3 merge, every one absent or inert:

| mechanism | state |
|---|---|
| `required_pull_request_reviews` | **null** — no approval required, measured live |
| `CODEOWNERS` | **does not exist** — no path-based review gate |
| the Tier-3 file list in `CLAUDE.md` | **prose only** — no guard, workflow or script consumes it |
| merge-slot `PreToolUse` guard | **never invoked** on this runtime (`BL-20260820-PROJECT-HOOKS-INERT-ON-WEB`, 1,379 consecutive `Found 0 total hooks in registry`) |

So a session can merge a change to `config/strategies.yaml`, `config/accounts.yaml`,
`src/runtime/orders.py` or the order path with **green CI and nothing else**.

⚠️ **This is an exposure finding, NOT an incident report, and the distinction is
load-bearing.** The one Tier-3 PR I checked shows the process working *well*:
**#9930** (`order_monitor` package-wide effectuation) was genuinely
operator-approved in conversation on 2026-08-18, the PR body **records the
approval explicitly**, and it **preserved the superseded ⛔ DO-NOT-MERGE banner
rather than deleting it** — *"recording that rather than silently deleting it,
since a stale gate notice is exactly the thing a reviewer would act on."* That is
better discipline than most enforced systems get.

The finding is that the discipline is **all there is**. The approval's only
artifact is prose written by the same session that wanted to merge, and nothing
can distinguish that PR from one whose banner was quietly deleted.

**The remedy converges with an independent finding.** The session that
root-caused the merge-slot guard reached the same conclusion from the other
direction: *"the only shape that would bind on the web is a required CI status
check."* Two unrelated governance mechanisms, one remedy — because on this
runtime a required check is the only thing that binds. That convergence is the
argument for building it once, generally.

### Proposed fix (not applied — review first, and it is the operator's call)

A `tier3-approval-guard` required check: when a PR's diff touches a path on the
Tier-3 list, fail unless the PR carries an explicit operator-approval marker
(a label only the owner can apply, or an approving review). Two properties it
must have, both learned this session:

- **The path list lives in ONE place**, imported by the guard rather than
  restated — otherwise it becomes the second copy that drifts (F-37), and it
  should be the same list `CLAUDE.md` renders, so the phantom `risk_caps.yaml`
  entry surfaces as a guard error instead of sitting unread (F-46).
- **Adding it changes the required-check set on every open PR**, which is
  precisely why the #10015 session declined to slip it into a fix. Tier-2/3 by
  nature — the operator approves it, and `workflow_dispatch` on
  `branch-protection-sync.yml` is the sanctioned wire.

---

## Part 13 — The two frontends (delegated, then independently verified)

Produced by a parallel read-only auditor over `streamlit_app.py` (9,587 lines,
56 API paths) and `webapp/src/` (38 files, 32 API paths) at dashboard HEAD
`47bb971`. **Android excluded** per the standing 🧊 ON ICE directive.

⚠️ **Provenance of these findings, stated because it is exactly the discipline
this audit is about.** F-48, F-49 and F-50 I **re-derived myself** against the
source before recording — an agent's report is a claim, and accepting it
unverified is the same error as accepting a system's self-description. F-51 and
below are **relayed at the auditor's stated confidence** and marked as such.

### F-48 — The SPA's prop killer-limit alert is structurally unsatisfiable (severity: HIGH) — VERIFIED

`webapp/src/routes/Prop.svelte:35-37` reads its cushions from the live API:

```js
const rd        = $derived(status?.rule_distance ?? null);   // ← api.propStatus()
const dailyLeft = $derived(rd?.daily_loss_remaining ?? null);
const ddLeft    = $derived(rd?.static_dd_remaining  ?? null);
```

The producer, `src/prop/prop_reconcile.py:387,390`, emits
**`distance_to_daily_loss_usd`** and **`distance_to_dd_floor_usd`**. The names
the SPA reads do not exist on that payload.

Both are therefore permanently `null`, both metrics render `—`, `cushionClass(null)`
returns `"flat"` so no colour escalation ever occurs, and — the part that matters —
`Prop.svelte:70`:

```js
{#if (dailyLeft != null && dailyLeft <= 25) || (ddLeft != null && ddLeft <= 25)}
  <div class="alert neg">⚠ A killer limit is very close — the cushion is thin.</div>
```

**cannot evaluate true under any account state.** This is a safety alert on a
prop account whose two hard limits are a $150 daily loss and a $4,700 static-DD
floor, and it is inert. Streamlit reads the correct keys
(`streamlit_app.py:8183,8187`), so the SPA is alone in this.

### F-49 — The same key drift reaches the executive system report (severity: HIGH) — VERIFIED, and wider than the auditor could establish

The delegated auditor flagged this as *unconfirmed — "I did not trace the skill's
assembly step"*. Traced:

| site | vocabulary |
|---|---|
| **producer** `src/prop/prop_reconcile.py:387,390` | `distance_to_daily_loss_usd` / `distance_to_dd_floor_usd` |
| **schema template** `comms/schema/system_report_response.template.json:96-97` | `daily_loss_remaining` / `static_dd_remaining` |
| **report renderer** `scripts/reports/render_system_report.py:222-223` | `daily_loss_remaining` / `static_dd_remaining` |
| **SPA** `Prop.svelte:36-37` | `daily_loss_remaining` / `static_dd_remaining` |
| **Streamlit** `streamlit_app.py:8183,8187` | `distance_to_*` ✅ |
| `RiskManager.report` `src/units/accounts/risk.py:1328` | `daily_loss_remaining` — a **different object**, correctly named for itself |

So **one field name, `rule_distance`, carries two different key vocabularies
depending on which side produced it.** The renderer reads
`data.get("rule_distance")` out of the assembled report, so it is only correct
while a session hand-fills it per the template — and the *natural* action, dropping
`/api/bot/prop/status`'s own `rule_distance` block in verbatim (identical field
name!), silently produces two em-dashed KPIs on the prop cushion.

That is the cohesion class in its purest form: **one concept, two implementations
that can disagree, and the seam is owned by nobody.** `RiskManager`'s use of the
same names for an unrelated object is what makes a grep look reassuring.

### F-50 — The provenance caveat is suppressed exactly when the data is 100% estimated (severity: HIGH, BOTH frontends) — VERIFIED

`streamlit_app.py:1958-1961`, and byte-for-byte the same logic at
`webapp/src/routes/Performance.svelte:38-40`:

```python
if covf >= 1.0:      return None      # full coverage: no caveat needed  ✅
fab = int(block.get("pnlFabricatedCount") or 0)
unv = int(block.get("pnlUnverifiedCount") or 0)
if fab + unv <= 0:   return None      # ← suppresses a WARRANTED caveat
```

The docstring asserts `fabricated + unverified > 0 ⇔ coverage < 1.0`. **That
biconditional is false.** `src/runtime/provenance.py:468-478` computes
`coverage() = MEASURED / total` over a four-bucket partition, so an **ESTIMATED**
row lowers coverage while incrementing neither count.

A window that is 100% ESTIMATED therefore yields `pnlCoverage: 0.0,
pnlFabricatedCount: 0, pnlUnverifiedCount: 0` → `fab + unv <= 0` → **no caveat at
all**, on a P&L figure with zero broker-measured content.

**This is not hypothetical — `CLAUDE.md` records the live case verbatim:**
`trend_donchian_avax_4h` returning `pnlCoverage: 0.0` beside
`totalPnlMeasured: -5415.17`, both rows ESTIMATED. The bot added
`pnlEstimatedCount` per-strategy in August *specifically* so this pair
reconciles — and **neither frontend reads it** (0 and 0).

The `fab + unv` test sits beneath the `covf >= 1.0` test that already returns on
the only case where no caveat is warranted. It is a redundant `AND` that can
**only** suppress, never add. Pure downside.

### F-51 and below — relayed at the auditor's stated confidence, not re-derived by me

| # | finding | sev |
|---|---|---|
| F-51 | `totalPnlMeasured` / `pnlMeasuredCount` / `pnlEstimatedCount` are **write-only** (0 reads, both frontends); both render `totalPnl`, the fabricated-inclusive sum. Compounds F-50: the fabricated-inclusive number is shown *and* its warning is suppressible. | MED-HIGH |
| F-52 | `status_freshness`, `status_age_hours`, `balance_basis`, `equity_provenance` — **all 0/0**. Streamlit branches on `present` (the field the bot documented as insufficient) and fires `⛔ BREACHED` / `⚠ Thin cushion` off values that may be weeks old; the SPA renders no `as_of` at all. | HIGH |
| F-53 | Streamlit's stale-cache path returns `(payload, None)` — **indistinguishable from a fresh fetch** — for up to `STALE_OK_S`=120 s. The SPA never clears last-good state on error, so it renders stale data **unbounded**. Neither frontend renders a "last updated" anywhere (grep: 0). | MED |
| F-54 | The SPA requests exactly the 200-row cap on `/trades/closed` and recomputes totals client-side; a full 200 is indistinguishable from truncation and it never tests for it. Streamlit does detect and caption the cap. | MED |
| F-55 | SPA `Models.svelte` renders per-model stage dots with **no `mirror_age_seconds`** — "advisory" reads identically whether the trainer is live or down since Tuesday. Streamlit handles this correctly. | MED |
| F-56 | SPA sums uPnL over measured legs only but never renders `segOpen.length - known`, so "Open trades: N" sits beside a sum over fewer than N. Streamlit discloses the excluded count. | MED |
| F-57 | Streamlit **overrides** the bot's authoritative `assetClass` when it is `"unknown"` (`streamlit_app.py:3083-3091`) with a local heuristic — treating a deliberate config-driven "unknown" as a miss. The fallback should key on **absence** of the field, not its value. | LOW-MED |

### Recorded as sound, because it bounds the negatives

**No funding-class blending in either frontend.** The three-way real/paper/prop
separation is enforced structurally — `_segment_filter_rows`, `funding.ts`, prop
sourced from its own journal, and `paper_role: portfolio` scoping driven off
`/config` rather than a hardcoded id list. The auditor looked and found nothing;
this is the strongest area of both codebases.

**One finding was retracted mid-analysis** and I am recording that it was: the SPA's
`typeof p.unrealizedPnl === "number"` exclusion was initially flagged as unsafe
versus Streamlit's explicit `!= "unavailable"` test, then withdrawn after checking
that **every** `"unavailable"` return in `dashboard.py` is paired with `None`. The
residual is that the SPA's correctness is *incidental* — it depends on a producer
invariant it neither states nor checks.

### The one detector that covers this class

`provenance-consumer-guard` already enforces writer/reader pairing **inside** the
bot repo. **Eight** fields above are N-writers / **0**-readers *across the repo
boundary* — invisible to it by construction (F-40's guard-boundary shape again).
A declared honesty-key manifest the frontends' CI must satisfy would have caught
F-50, F-51, F-52 and F-55 **as a class**, rather than one at a time in an audit.

---

## Part 14 — The skills as binding instructions (delegated, top findings verified)

Produced by a parallel read-only auditor over all 30 `SKILL.md` files, with depth
concentrated on the 22 outside the ones I had already rewritten. **F-58 through
F-61 I re-derived myself**; the rest are relayed at the auditor's stated
confidence and marked.

### F-58 — `vm-migration` instructs autonomous termination of a production VM, with no tier gate anywhere (severity: HIGH) — VERIFIED

`.claude/skills/vm-migration/SKILL.md` contains **zero** occurrences of the word
"tier" — measured, `grep -ci tier` → `0` over the whole file. It then instructs:

> **Decommission an old box (no human needed):** dispatch `terminate-instance`
> with `mode: list` … then dispatch `terminate-instance` with
> `instance_id: <ocid>` + `confirm: yes`.

**The asymmetry is the finding.** `docs/claude/system-actions.md:537` requires
an operator ack for **`reboot-vm`** (Tier-2). So under the governing documents,
*restarting* a production box needs a human and *permanently destroying the same
box* is instructed as needing none.

**The machinery does not compensate.** `terminate-instance.yml:35-38` gates on
`github.event.issue.user.login == github.repository_owner` — and a Claude
session **posts as the owner**, so that is an anti-abuse check, not a human
gate. The only other guard is `confirm: yes`, a string the session writes
itself. Same file, same gap: `cutover-live` — the money-path cutover — is listed
with "dry-run first" and no tier.

This composes directly with **F-47**: with `required_pull_request_reviews: null`
and no guard consuming the Tier-3 list, the tier system is *entirely* carried by
what the skills tell a session. A skill that omits the tier removes the only
remaining control.

### F-59 — `stop-micro-zombie` is a phantom workflow, and its failure mode is silence (severity: HIGH) — VERIFIED

`vm-migration/SKILL.md:88` lists `stop-micro-zombie` in a table headed *"Tooling
(all issue-label-driven)"*, and a decommission-hygiene checklist step depends on
it. Measured:

- `.github/workflows/stop-micro-zombie.yml` — **does not exist**
- `bootstrap-labels.yml` — **0** occurrences of the label
- 7 workflows named in that table, **6 exist, 1 does not (14%)**

**Opening an issue with a label no workflow listens for produces no run, no
error, and no comment** — indistinguishable from success to a session that does
not poll. This is the *"we did not look" vs "we looked and found nothing"*
collapse, in the tooling itself.

The step exists solely because of `BL-20260615-MICRO-ZOMBIE` — a retired box
kept calling Bybit from a de-allowlisted IP, spamming `10010` and masking a
diagnosis for a day. **There is no backlog row for the missing workflow**, so
this is undetected rather than known-and-deferred.

### F-60 — `diag-data` tells every session the relay body is ignored; the body OVERRIDES the title (severity: HIGH) — VERIFIED, including from this session's own behaviour

`.claude/skills/diag-data/SKILL.md` states it **twice**:

- `:33` — *"**title** = the path: `[diag-request] <path>` (body ignored)"*
- `:93` — *"Live VM: the **title is the path**; the body is ignored."*

The field says the opposite. `vm-diag-snapshot.yml:28` — *"When the body parses
to a non-empty list, it **wins over the title**"*, restated in the workflow's own
rejection comment at `:783`. `docs/CLAUDE-RULES-CANONICAL.md` (rank 1, above
skills) carries an 18-line ⚠️ block: *"**The BODY IS NOT A PLACE FOR PROSE — a
non-empty body OVERRIDES the title**"*, recording a 2026-07-30 session that lost
a round trip diagnosing a "failed relay" that was its own prose.

**I can confirm this first-hand rather than only by reading:** my own relay
request this session (#10013) passed a JSON array in the body and it was
processed as a 5-path batch. The body is emphatically not ignored.

Three-way wrong — internally contradictory (`:33`/`:93` vs the body-batching
section at `:62-81`), contradicting the highest-precedence canonical doc, and
contradicting the field. And `diag-data` is the **most-referenced skill in the
catalog (15 inbound refs of 30)**, so the error has maximum reach: a session that
has read it and believes "body ignored" is *more* likely to put prose there.

### F-61 — Two skills are unreachable by skill-first lookup, one of them owning money-affecting Tier-3 rules (severity: HIGH) — VERIFIED

Measured across all 30 skills plus `CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md` and
`.claude/settings.json`:

| skill | inbound references outside itself |
|---|---|
| `regime-selectivity` | **0** |
| `macro-research` | **0** |

`regime-selectivity` owns the binding rules for authoring a **regime OFF-cell** —
which drops a strategy's intents before routing, i.e. removes real-money trades.
Skill-first lookup is declared binding in `CLAUDE.md`, and this skill cannot be
found by it unless the session already knows the word "regime".

**The auditor's paired finding, which I have NOT re-derived and relay as its
claim:** `new-strategy` tells an author *"Author a real regime cell… **This is
the right answer**"* under a merge-blocking `strategy-coverage-guard`, while
`regime-selectivity` Rule 1 says *"**Do NOT author an OFF-cell for a book that is
healthy**"* and Rule 2 requires a walk-forward that a brand-new leg with zero
trades cannot possibly have. If both hold, CI pressure manufactures exactly the
cosmetic cell that `BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` names as the
anti-pattern. **The auditor flagged its own dependency here honestly** — it did
not verify that `strategy-coverage-guard` has real merge-blocking force, and
notes that my F-47 finding makes that worth checking independently. Left open.

### F-62 and below — relayed at the auditor's stated confidence

| # | finding | sev |
|---|---|---|
| F-62 | The **M22 pairs sleeve** — an isolated 2-leg live order path with its own `execution:` gate — is named in **0 of 30 skills** (`config/pairs.yaml`: 0 mentions; `new-strategy`: 0). Every step of the 716-line `new-strategy` checklist is *wrong* for a pairs leg and nothing says so. Same class, second instance: the **Alpaca options overlay** (6 modules) appears in 0 of 30 skills. | HIGH |
| F-63 | `health-review`'s "HARD COMPLETION GATE" (`count_untriaged MUST be 0`) is arithmetically unachievable — **290 open health rows** — and its four buckets **cannot express `snoozed`**, the disposition `RULES-CANONICAL` requires for accrual-blocked rows. Measured: **243 of 350 open rows across all three backlogs are `kept_open`**; `snoozed_until` is set on **10 (1.1%)**. Carrying a row forward unchanged is exactly what the canonical rule says must not satisfy `backlog_drive`. | HIGH |
| F-64 | Three skills + the SessionStart hook assert **`run_workflow` 403s / no run-log read**; `CLAUDE.md` records it re-verified working 2026-08-06 (HTTP 204), and this session's own tool list carries `actions_run_trigger` / `get_job_logs`. The skills are the stale side, and one was edited *after* the re-verification. Drives a standing design tax: `git-actions` tells sessions to *add* issue-label triggers rather than dispatch. | MED |
| F-65 | All three review skills teach a backlog-row schema **omitting `severity`**, which `scripts/ops/check_backlog_criteria.py` now rejects (`_SEVERITIES = {critical,high,medium,low}`). 4 of 4 skills that write backlog rows omit it. The guard is diff-scoped, so it fires on the new row — at exactly the moment a review is closing out. | MED |
| F-66 | `delegate-work`'s spawn-prompt template teaches a **two-tier** model (Tier-3 propose / Tier-1 ship) — **Tier-2 is absent**. That template is the entire tier education of a spawned sub-session, and its binary framing puts deploy/timer/service/DB-write changes in the self-merge bucket. One-clause fix. | MED |
| F-67 | `vm-ops`'s Tier-2 illustrative list reads as plumbing (deploys, toggles, backfills) while `system-actions.md`'s Tier-2 now includes `flatten-ib-position`, `flatten-bybit-position`, `cancel-ib-order` — actions that **place real closing orders on real-money accounts**. The tier assignment is correct; the legibility is not. | MED |
| F-68 | `exit-refinement` documents **5 of 8** lever columns and **5 of 7** statuses of the artifact it declares as its contract — missing `shipped_gate_failed`, which `RULES-CANONICAL` cites as one of its five canonical collapsed-state fixes. A session filling a cell reaches for `shipped` and re-collapses the state the fix created. | MED |
| F-69 | `doc-freshness` asserts *"no other operating doc, skill, or script hardcodes a VM IP"* — measured, `141.145.193.91` appears in **98 files**, including live defaults in `diag_fetch.sh`, `publish_trainer_mirror.sh`, `sync_trainer_data.sh`, `oci_vm_ssh.sh`. The invariant is false and is not one of its own enforced scans; `vm-migration`'s environment-contract step depends on it being true. | MED |
| F-70 | 4 of 16 doc-targeting relative links in skills resolve one level short (`../../docs/…` → the nonexistent `.claude/docs/`). **My own path-reference probe (F-45) was structurally blind to this** — it checked string existence, not depth resolution from the containing directory. `drift-remediation` is 55 lines whose substance is *"the runbook carries the detail"*; a session that cannot resolve it gets the summary and believes it has the skill. | MED |

### F-71 — Only 7 of 30 skills carry the detector obligation, and none of them are the fixing skills (severity: HIGH, structural)

The *"**A DETECTOR exists.** Something fails if this silently stops working…
'We'll notice' is not a detector"* clause appears in exactly **7 files** — the
seven build skills, all added by me this session (`9fdca362`).

**The 22 without it include every skill whose job is to find and fix defects:**
`health-review`, `performance-review`, `ml-review`, `system-review`,
`doc-freshness`, `drift-remediation`, `db-wiring`, `workplan-vs-architecture`.
`health-review`'s `fixed-now` bucket authorises Tier-1 fixes with no detector
requirement; `doc-freshness` repairs doc contradictions in place with no
requirement that a scan be added so they cannot return.

That is the recurrence class this audit keeps rediscovering — **sitting precisely
where the fixing happens**. It is a three-line copy into eight files, and it
converts the repo's best-evidenced lesson from a *build-time* rule into a
*fix-time* rule.

### Recorded as models, because the report is not only a list of defects

The auditor named eight skills as templates. Three worth citing here:
**`exit-refinement`** is the only skill with a machine-readable done-condition —
a committed 52×8 matrix and *"a sweep whose verdict isn't in the matrix didn't
happen"*, evaluable in one command. **`regime-selectivity`** is factually the
cleanest checked (its `FOLD_PANEL = (3,4,5)` verified against
`regime_cell_walkforward.py:72`) and contains the best sentence in the corpus for
this audit's own method: *"**A verdict over zero trades is not a negative
finding; it is no finding.**"* **`backtesting:19`** carries an explicit
retraction of an earlier over-claim — the correct response to a stale coverage
claim, and precedent for how the others should be fixed.

**The auditor also retracted one of its own findings mid-analysis** — it flagged
`vm-ops` as mis-tiering `set-account-mode`, then withdrew it on reading that the
Tier-3 boundary is *new code paths that write `mode:`*, not *dispatching the
sanctioned wire*. Recorded because the retraction is the evidence the method ran.

---

## Part 15 — LIVENESS: the zombie hunt (delegated; top finding verified)

### F-72 — A second, UNREADABLE env var can silently disable the live regime hard gate (severity: HIGH) — VERIFIED

`src/runtime/intents.py::_regime_router_active` has **two** independent disable
paths:

```python
if _truthy(_os.environ.get("REGIME_ROUTER_DISABLED")):   # :1066
    return False
legacy = _os.environ.get("REGIME_ROUTER_ENABLED")        # :1069
if legacy is not None and legacy.strip() != "" and not _truthy(legacy):
    return False
```

Measured against `scripts/ops/get_env.py::ALLOWED_KEYS`:

| var | disables the gate | readable via `get-env` |
|---|---|---|
| `REGIME_ROUTER_DISABLED` | yes | **yes** |
| `REGIME_ROUTER_ENABLED` (explicit falsy) | yes | **NO** |

`CLAUDE.md` states the live VM *currently carries* `REGIME_ROUTER_ENABLED=true` —
so the variable is **present** in the live environment, one character from
falsy, and a stale `.env` carried through a VM migration is exactly how the
netting-guard regression happened before.

**The failure mode is a confident wrong answer.** A session checking whether the
gate is active queries `REGIME_ROUTER_DISABLED`, gets `unset`, and concludes
"enforcing" — while the other key may have turned it off. That is diagnostic
provenance sub-class C (an unasserted denominator reading as a clean negative)
applied to a live order-routing gate whose documented failure is *"the
money-losing `trend_vol` OFF-cells would trade again."*

**Answered independently rather than by reading the config** (relay #10032): the
gate emits `regime_hard_gate` (`enforced:true`) when enforcing and
`regime_shadow_gate` when not, and never both on a tick — so the audit log
partitions by event name regardless of what the env says. Result recorded below
when it returns.

**Population:** of **43** order-path env vars read in `src/runtime/`,
`src/units/accounts/`, `src/core/`, `src/prop/` but absent from the `CLAUDE.md`
table, **43 of 43** are also absent from `ALLOWED_KEYS`.

### F-73 — `ict-promotion-readiness.timer` is dual-defined with DIFFERENT schedules, and the un-installed copy carries the known-bad value (severity: MED-HIGH)

| copy | `OnCalendar` |
|---|---|
| `deploy/trainer/ict-promotion-readiness.timer:27` | `*-*-* 04:00:00` |
| `deploy/training-vm-cloud-init.yaml` (inline heredoc) | `daily` (= 00:00) |

The `deploy/trainer/` header documents why `daily` was a bug (`BL-20260717`): at
00:00 the ~99-min / ~3.2 GB sweep ran **concurrently with `ict-trainer.timer`**
on the 6 GB box — a documented OOM collision. The sibling service file states the
invariant explicitly: *"Mirrors the unit embedded in
`deploy/training-vm-cloud-init.yaml` … **Edits must stay in lock-step.**"*

**They are not, and nothing checks.** Aggravating: **`deploy/trainer/*` has no
installer** — a repo-wide grep for `deploy/trainer` across `*.sh`/`*.py`/`*.yml`
returns **zero** hits, so which value is live depends on whether a human ran a
manual `cp`. A freshly-provisioned trainer re-acquires the OOM collision.

Population: 8 dual-defined units compared — 6 agree, 1 differs, 1 retracted.

### F-74 — 14 of 58 systemd units are outside ANY inventory guard, and this was already filed 16 days ago (severity: MEDIUM, RECURRENCE)

`scripts/check_diag_unit_allowlist.py` globs `deploy/*.service` + `deploy/*.timer`
only. It passes cleanly — *"44 deploy units scanned, 37 allowlisted, 8 exempted,
0 failures"* — over a population that **excludes 14 units by construction**
(cloud-init heredocs, `deploy/trainer/`, installer-script `cat > … <<'UNIT'`).

The load-bearing one is **`ict-trainer-publish.timer`**: `CLAUDE.md` keys the
entire `trainer_down` banner and `TRAINER_DOWN_STALE_SECONDS` alert on its 2-min
mirror heartbeat, and it exists only inside a YAML heredoc and a shell script.

This is the same structural invisibility the code already flags for
`caddy.service` (*"THIS ENTRY IS HAND-MAINTAINED AND NO GUARD PROTECTS IT"*). I
asked whether other such exceptions exist undocumented: **yes — 14, and unlike
caddy none carries the warning.**

⚠️ **Already logged as B-1 in `docs/audits/full-system-audit-2026-08-04.md:163`
and in the committed 2026-08-04 report — still open 16 days later.** Recorded as
a recurrence with a number, not a new find. That is F-71's detector gap in
action: a finding with no detector came back.

### F-75 — Three soak logs have a wired writer and ZERO readers (severity: MEDIUM)

Full matrix over **15** soak/observability logs. Twelve are reachable via
`/api/diag/log_file` and/or a REST route. Three are not:

| log | writer called from | diag allowlist | REST |
|---|---|---|---|
| `conflict_taxonomy_soak` | `src/core/coordinator.py` | ❌ | ❌ |
| `macro_thesis_soak` | `src/main.py:880` | ❌ | ❌ |
| `invariant_violations` | `order_monitor.py:9498` | ❌ | ❌ |

Writer wired to a live tick path, zero hits across `src/web/`, `scripts/` and
diag's 24-entry `_LOG_FILES`. They accrue on the live VM and **no relay-bound
session can read them** — the precise gap `/api/diag/log_file` exists to close.
Mitigating: `invariant_violations` additionally Telegrams, so a *violation*
alerts even though the *log* is unreadable; the other two have no second channel.

### F-76 — One served route undeclared in `CLAUDE.md` — and the instructive contrast (severity: LOW)

`GET /api/bot/exit-interval/soak` is served (`exit_interval.py:41`), mounted, and
rowed in `docs/api-tier-policy.md` — but absent from `CLAUDE.md`'s API table.
**1 of 97 served routes undeclared; 0 of 94 declared-but-not-served.**

The contrast is the point: `api-tier-policy.md` **is** guarded
(`check_api_tier_policy.py`) and is genuinely complete — its "96 of 96" claim
independently confirmed. `CLAUDE.md`'s table is **not** guarded, and is the one
that drifted. The guard works; it is pointed at one of two inventories.

### The meta-finding, which generalises F-40

The delegated auditor's closing observation, and I think it is the most valuable
sentence any of the six produced:

> **Every guard in this repo that exists is passing, and each one is passing over
> a population narrower than the thing it names.**

`check_diag_unit_allowlist` reports "44 units scanned, 0 failures" over 44 of
**58**. `check_api_tier_policy` reports "96 of 96" over **one of two**
inventories. The failure mode is no longer *missing detectors* — it is
**detectors whose denominators are silently smaller than their titles**.

**A guard that printed its own coverage fraction beside its verdict would have
surfaced F-74 and F-76 with no audit at all.** That is a one-line change per
guard and it is the single highest-leverage fix in this document.

### Clean results, each with a planted control

Recorded because a negative without a control is not a measurement:
**strategy dispatchability — 0 orphans in both directions** (55 declared, 55 in
the intent roster; a planted `zzz_planted_fake_strategy` was caught, 1 of 56, so
the zero is measured) · **instrument coverage — 0 gaps** (24 symbols, all
present) · **0 services without an explained driving timer** · **0 dead
`ALLOWED_KEYS` entries** · **0 documented-but-unread env vars** (33 candidates,
30 retracted as the constant-indirection idiom).

The auditor retracted **nine** of its own initial findings and listed them —
including one, R6, that was *the same failure mode as my own morning probe*: a
one-line regex at the wrong granularity, missing the `_ENV_CONST = "VAR"` then
`getenv(_ENV_CONST)` idiom.

---

## Part 16 — CONSISTENCY: canonical docs vs the field (delegated; top findings verified)

**First, the guard's boundary**, since F-40 says that is where defects live.
`check_canonical_doc_coherence.py` passes 5/5 over: 9 `ACTIVE_DOCS` + skills +
commands, checking 2 dead IPs, 6 removed gate names, the 7-stage ladder string,
hierarchy ordering, and 3 hand-written value contracts.

⚠️ **`ROADMAP.md`, `ROADMAP_MACRO.md` and `docs/sprint-logs/**` are scanned by
NOTHING** — verified: `ROADMAP` is absent from `ACTIVE_DOCS` and `_active_files()`
never globs `docs/`. Rank 3 of the instruction hierarchy has no coherence guard,
and that single gap is the structural precondition behind three findings below.

### F-77 — `authored_cells` declared mandatory, enforced by nothing, for 20 days (severity: HIGH)

`system-review/SKILL.md:119` declares `review_coverage.authored_cells`
**mandatory on the weekly window**. `render_system_report.py:699-716`'s
`_REQUIRED_COVERAGE_KEYS` holds 9 keys and **`authored_cells` is not among them**.

| mandatory key | implementation files |
|---|---|
| `account_reachability` | 13 |
| `execution_capture` | 2 |
| `since_last_build_verification` | 1 |
| `backlog_classes` | 1 |
| `ml_output_actionability` | 1 |
| **`authored_cells`** | **0** |

`git log -S` dates the declaration to 2026-07-31 → **20 days unenforced**. This
is *exactly* the `account_reachability` gap I found and fixed this morning — and
**the comment block immediately above `_REQUIRED_COVERAGE_KEYS` narrates that
very bug** while omitting this key from the same fix. I fixed one instance of a
class and left its sibling in place, in the file I was editing.

**Honest limit:** the consequence is currently clean — all 7 regime-cell
strategies and all 3 `trend_vol` strategies have register rows, 0 of 12 past due.
That is luck, not enforcement.

### F-78 — `BROKER_PNL_READER_EXCHANGES` documented as one member; the field has three (severity: HIGH) — VERIFIED

`src/units/accounts/clients.py:643-647` → `frozenset({"bybit",
"interactive_brokers", "alpaca"})`. `CLAUDE.md:970` → *"today `{bybit}`"*.
`alpaca` entered the set 2026-07-31 → 20 days stale.

**Why it matters beyond tidiness:** `CLAUDE.md:973` tells a session that
IBKR/Alpaca realised PnL is filled by `_sweep_local_pnl_for_unpriced` as
`local_compute`. Under `provenance.classify_pnl` that is a **different
provenance bucket** than broker truth — so a session reasoning from this doc
mis-grades Alpaca rows in exactly the measured-vs-manufactured analysis
`provenance.py` exists to protect.

### F-79 through F-84 — relayed at the auditor's stated confidence

| # | finding | sev |
|---|---|---|
| F-79 | `CLAUDE.md:880` places `PUBLIC_ROUTES` in `main.py`; it lives in `auth.py:44`, and the lower-ranked `api-tier-policy.md` has it right. The companion claim "enforced by the test suite" **verifies true**. Detector built and validated: **39 symbol-home claims checked, 1 mismatch, 0 false positives.** | MED |
| F-80 | **53 dead `.github/workflows/*.yml` references of 187 (28.3%)** after the CI fan-out consolidation — including **present-tense enforcement claims in rank-1 and rank-2 docs**. The guards themselves are real and registered (all 57 scripts `run_guards.py` names exist), so only the *path* is stale — but a session verifying "is this enforced?" opens the named file, finds nothing, and may conclude the guard was deleted. | MED |
| F-81 | **105 of 256 sprint logs (41%) appear in neither roadmap**; 16 of 44 dated 2026-08. **Lag ruled out by a same-day control** — `S-LLM-BURST-WORKER-2026-08-18` is referenced while `S-SYSREV-TRADE-MECHANICS-2026-08-18` is not. `ROADMAP.md` is defined as *"the centralized record: **every** milestone/sprint"*. Note the sting: `S-M20-WRITTEN-AND-NEVER-READ-2026-08-17` was itself never written into the record. | MED |
| F-82 | The hierarchy-mirror guard **silently drops items it cannot classify** (`_normalize_item` returns `None`, `if key:` skips). **Demonstrated with both controls on a scratch copy:** inserting an unrecognised 9th item into one list only → guard **PASSES**; deleting a recognised item → guard correctly **FAILS**. So it is blind to precisely the drift that is most likely (someone adds a new canonical doc to one list). One-line fix: append a sentinel instead of skipping. | MED |
| F-83 | `CLAUDE.md`'s `EXIT_LOOP_*` row contradicts itself three ways in one entry — "three processes in ~8.5 h" vs "five OBSERVED processes in ~10 h"; "the maxima are three points" followed by a fourth (47.4 s); and an `n` series that omits the two daytime samples it reasons from. Neither side is the field (both are prose about VM measurements), but the row is the authority on a **live safety requirement** whose margin it puts at **1.1 s**. | MED |
| F-84 | `CLAUDE.md` describes the review-coverage guard as **5 keys including `flags_raised`** — which is *not* in the required tuple — while the field has **9** and the skill declares **10**. A session trusting `CLAUDE.md` believes a report passes coverage with 5. | MED |

### The auditor's own retractions

Six, listed in its report, including four claimed arithmetic mismatches that were
all regex coupling artifacts — **real arithmetic mismatches: 0 of 26.** And one
left explicitly **unresolved rather than forced**: whether `_MAX_HOOK_NAMES`'s
"one name of margin" refers to the state before or after a third instrumentation
cut. Recording that an auditor declined to resolve an ambiguity is worth as much
as recording what it found.

### Highest-yield detectors from this pass

1. **Add `ROADMAP.md` + `ROADMAP_MACRO.md` to the coherence guard's file set** —
   2 lines, and the structural precondition behind F-80, F-81 and F-5.
2. `check_referenced_paths_exist()` — workflow files + relative links (435
   links, 2 broken; 187 workflow refs, 53 dead).
3. `check_symbol_home()` — already written and validated, 0 false positives.
4. `check_mandatory_keys_enforced()` — AST-parse `_REQUIRED_COVERAGE_KEYS`
   against every `**mandatory**` declaration in a SKILL.md, failing in **both**
   directions. This is the one that matters for live safety; it closes F-77 and
   F-84 together and would have prevented the `account_reachability` gap.

---

## Part 17 — RECURRENCE + the harness family (delegated; the live findings verified by me)

### F-85 — LIVE: exit evaluation breaches its 60 s requirement on 28.2% of intervals, and the row tracking it is CLOSED (severity: HIGH — money at risk)

**Measured by me directly, 2026-08-20T09:13Z**, against the live trader.

Single process (`process_started_utc` 08:15:14Z):

```
requirement_s        : 60.0
intervals_measured   : 77
interval_breaches    : 23          -> 29.9%
max_interval_ms      : 78707.4     -> 78.7 s
requirement_state    : breached
last_breach_utc      : 09:11:08Z   (~2 min before I read it)
state: fresh · stale: False
```

**And it is not a single-process artifact.** The durable `exit_interval_soak`
over **7 processes / 394 measured intervals**:

| statistic | value |
|---|---|
| breaches > 60 s | **111 = 28.2%** |
| max | **91.6 s** |
| **p90** | **73.8 s** |
| median | 35.9 s |

**The p90 is itself 23% above the requirement.** Nearly three in ten exit
evaluations exceed the stated maximum, persistently, across every process.

⚠️ **`BL-20260814-EXIT-PASS-SLOWER-THAN-M20` is marked `resolved`.** `CLAUDE.md`
states the mechanism itself: *"The MAX is the quantity the requirement is written
against… it is why `BL-20260814-EXIT-PASS-SLOWER-THAN-M20` was closed while the
max kept growing."* The doc's most recent figure is 58.9 s — *"1.1 s of margin"*.
It is now **91.6 s**.

**Why nothing escalated.** The detector is *correct* — `requirement_state` is a
proper four-state field and it says `breached`. Two things defeat it:

1. `state: fresh` and `requirement_state: breached` coexist by design (liveness
   and requirement are deliberately different questions), so every liveness-shaped
   check reads green.
2. **The breach alarm fires once per PROCESS**, and the trader restarts on every
   merge to `main`. A per-process latch on a process that restarts hourly is a
   latch that never accumulates — which is exactly why the durable soak exists
   and exactly what nothing reads.

**Fix shape:** move the alarm off the per-process latch onto
`exit_interval_soak.jsonl` — which already computes `summary.max_interval_ms`
over the whole file — and alert on **breach rate over the last N intervals across
processes**, rate-limited. Tier-2. Reopening the backlog row is Tier-1 and should
happen regardless.

### F-86 — LIVE: the sanctioned direct-diag path is dead, "resolved" twice, and the working base is in this repo's own docs (severity: HIGH) — VERIFIED

Filed **five times**; two of those marked `resolved` on 2026-08-16; **two more
filed on 2026-08-18**, which is the proof the resolution never held.

Measured by me this session:

```
$ echo $DIAG_BASE_URL
http://158.178.210.252:8001                    # the micro, terminated 2026-06-16

$ bash scripts/ops/diag_fetch.sh 'version'
diag_fetch: ... rewriting to the live Ampere host 141.145.193.91 ...
curl: (28) Connection timed out after 10003 milliseconds   # THE FIX DOES NOT WORK

$ DIAG_BASE_URL=https://ict-bot.duckdns.org bash scripts/ops/diag_fetch.sh 'version'
{"git_sha":"e4c274af","captured_at":"2026-08-20T09:12:43Z"}   # works instantly
```

**The 2026-08-16 fix rewrote a DEAD host to an UNREACHABLE one.** The working
base — `https://ict-bot.duckdns.org` → Caddy → `:8001` — is documented in
`CLAUDE.md` § "Dashboard consumer". Both `resolved` rows carry criteria
explicitly demanding *"a subsequent session must OBSERVE the changed state, not
merely the doc edit"*; the resolution was reached by **reading** the script, in a
repo whose Rule One is *"read the field, not the prose about it."*

⚠️ **This corrects a claim I made earlier in this very session.** I reported
*"direct egress is blocked as documented — using the issue relay."* That was
wrong, and it was wrong in the same way the two `resolved` rows were: I accepted
the script's own failure message (*"egress blocked / web-api down"*) instead of
testing the alternative my own repo documents. The message collapses two states —
config-wrong vs network-blocked — which is the unprovenanced-diagnostic class
`diagnostic-provenance-guard` exists to catch, in the script that closed this row.

**Every relay round-trip I made this session was avoidable.**

### F-87 — `unwired-artifact-guard` is registered SELF-TEST ONLY; its real scan has never run (severity: HIGH — and it is mine, from this morning)

`run_guards.py:375` invokes `check_unwired_artifacts.py --self-test` and nothing
else. Measured: `--self-test` → `8/8 passed`, exit 0. The real scan → **exit 1,
159 of 384 tools with no runner.**

**The guard I shipped this morning to detect build-and-abandon is itself
registered in a way that can never fail.** Its name appears in the registry and
in the guard log, reading to any reviewer as "unwired artifacts are policed."

I chose self-test-only deliberately because the script has no diff-scoped mode
and registering the full scan would redden every PR — but I did not record that
choice anywhere, which is what makes it indistinguishable from an oversight.
**Fix:** add `--changed <paths>` (fail only when a PR *adds* an unwired tool),
register that as the blocking step, keep `--all` as a ratcheting census. And
extend `check_selftest_wiring.py` to forbid a guard whose only step is a
self-test.

### F-88 — `pytest-run` short-circuit: a five-times-recurring class, and instance 5 is live (severity: HIGH)

`pytest-run` is a **required** branch-protection check that short-circuits to
green without running a test when the diff matches no path in its filter. Its own
header enumerates **four** prior instances, two of which produced a 9- and
10-second green and left `main` red.

The current fix added a derivation test with its own negative control — good work
— but it scans only `docs/` and only one path idiom. **Instance 5:**

```
data/ict_validate_manifest.csv   read by tests/test_backtest_ict_cli.py:234
                                 assert manifest.exists()
                                 assert by_symbol["BTCUSDT"] == "5m"
```

`data/` is not in the filter. A PR touching only that file gets a green
`pytest-run` having executed nothing.

**Hand-enumeration has now failed five times.** The fix is to generalise the
derivation test to *any git-tracked non-`.py` path any test resolves against a
module-level root*.

### F-89 — Detector coverage over resolved findings: 17 of 25 = 68%, and that is an UPPER bound (severity: HIGH, structural)

Sample of 50 seeded-random rows from the 563 resolved. Of those, **25 were
actual code/config/workflow defects** (24 were research programmes, negative
results or doc corrections — not defects at all). **17 of the 25 carry a
detector = 68%.**

⚠️ **Stated as an upper bound, because the auditor stated it as one:** "detector
exists" meant *a registered guard names the class, or a topically-matching test
file exists*. **It was not verified for any of the 17 that the test actually
reddens when the fix is reverted.** Extrapolated, ~90 of the 563 resolved rows
carry no permanent detector.

**16 verified fix-then-refile pairs**, and the calibration is the finding: at the
auditor's first similarity threshold its own **known-duplicate control was
missed** — the scan was blind to a duplicate already read by eye. Recalibrated
against both controls, it found 16. Title similarity cannot see *semantic*
recurrence; three further chains were found by mechanism and appear in none of
the 16 — including the merge-protocol chain running **four generations over 30
days**, in which a row *whose own id contains the word RECURRENCE* was closed.

### F-90 through F-94 — the harness family, relayed with one live verification

| # | finding | sev |
|---|---|---|
| F-90 | **A degenerate or stalled price feed manufactures winning trades.** `backtest_fade.py` on a constant-price fixture: `trades=88, win_rate=100.0%, max_mfe_r=0.0, rc=0` — arithmetically impossible, unflagged. Demonstrated on **real** data too: replacing the last 20% of `backtest_candles.csv` with a repeated bar flips gross_r from **−7.35 to +85.35**, both `rc=0`. Zero variance checks across all 23 files. | HIGH |
| F-91 | **`_fee_r` has five different semantics under one name**; 7 of 13 harnesses cannot express slippage or funding at all, and `src/backtest/backtester.py` charges **11 bps vs the canonical 7.5**. Measured omitted term: **0.19–0.79 R/trade** over n=1042 — larger than typical per-trade expectancy. All emit the same field name `net_total_r` into the same fleet report. | HIGH |
| F-92 | **The `risk_pct` comment states the live fraction formula eight lines above code that divides by 100** (`backtest_system.py:857` vs `:866`), and cites a line number that is the wrong function. Defaults across 8 scripts span `0.015`→`1.0`, a **66× range for one flag name**. This extends my F-37 with the mechanism that made it invisible: *the comment is itself the finding*. | HIGH |
| F-93 | `src/backtest/run_backtest.py::summarize` writes **four permanently-zero columns** into `trade_journal.db::backtest_results` — `max_drawdown`, `max_drawdown_pct`, `sharpe_ratio`, `total_pnl_pct` are hardcoded literals — and a zero-trade run returns a **full row of zeros**, so a run that measured nothing reads as a perfectly safe strategy. `CLAUDE.md` states the correct rule for the live API (*"null (not 0) when uncomputable"*) and the backtest writer inverts it. | HIGH |
| F-94 | **12 of 13 harnesses report a broken input as `rc=0, trades=0`.** `backtest_ict_scalp.py` alone refuses (exit 1, *"window selected 0 bars — no overlap"*). Also: `--symbol` defaults to `BTCUSDT`, is never cross-checked against the data, and **selects the venue cost policy** — an equity file run under the default gets perp funding charged. | MED-HIGH |

**And the guards do not scan any of it:** `silent-empty-guard` and
`diagnostic-provenance-guard` both scope to prefix lists that **exclude
`scripts/backtest_*` and `src/backtest/`** — ~540 KB of code. F-94's symbol bug
is a textbook instance of the very class `diagnostic-provenance-guard` names, in
the blind spot of the guard that names it. **F-40's shape, a fourth time.**

The harness auditor also **retracted its own unit detector** — its AST probe
reported `backtest_system.py: div100=no`, the opposite of the truth, because the
code renames the parameter to a local before dividing. It flagged that *any file
in that column may be a false negative* rather than standing behind the result.

---

## Part 18 — 3.2 FULL PIPELINE VERIFICATION (the pass that had never been run)

Run by me against the live system 2026-08-20T09:15–09:25Z over **direct diag**
(F-86 unblocked this). **Population: the 400 most recent `order_packages`,
2026-08-03T18:52Z → 2026-08-20T07:02Z (17 days)** — stated once here and assumed
below.

### The death-hop histogram

| hop | survivors | died here | note |
|---|---|---|---|
| package created | 400 | — | the decision population |
| **intent/risk resolution** | 251 | **149 (37.2%)** | the dominant hop |
| reached a terminal state | 251 | — | 213 closed · 15 open · 14 emitted · 9 shadow |
| closed with a `pnl` | 185 of 200¹ | 15 null (7.5%) | |
| **PnL is broker-MEASURED** | **30 of 200 = 15.0%** | 170 | 68 estimated · 87 unverified |

¹ the closed-trade hop is measured over the 200 most recent closed trades
(`include_paper=true`), a different and narrower population than the 400 packages.

**Rejection causes — all 149:**

| cause | n | verdict |
|---|---|---|
| `all_accounts_noop` | 88 | investigated below |
| `no_fill_all_accounts` | 34 | placed, never filled |
| `same_direction_reinforcement` | 27 | **correct** — the netting guard suppressing a pyramid |

### F-95 — Three strategy legs are 100% dead, and nothing can see them (severity: HIGH)

| leg | packages | placed | window |
|---|---|---|---|
| `mgc_trend_1h` | 57 | **0** | 17 days |
| `tlt_pullback_1h` | 23 | **0** | 17 days |
| `fade_breakout_4h` | 9 | **0** | 17 days |

**89 of the 149 rejections (60%) are concentrated in three legs that placed
nothing at all.** (`avax_pullback_2h` is near-dead: 14 packages, 1 placed.)

**Traced `mgc_trend_1h` hop by hop**, which is what this pass is for:

```
signal      : ADX 42.97 "trending", confidence 0.468, direction long   ✅ fires
sizing      : sized_qty_by_account = {"ib_paper": 35.0}                ✅ sizes
intent      : execution_delta = {action:"reduce", qty_delta:60.0,
                                 target_qty:35.0, current_qty:95.0}
outcome     : all_accounts_noop                                        ❌ dies
```

The leg is **structurally blocked, not transiently failing**: `ib_paper` already
holds **95 MGC contracts** — and broker truth and the journal *agree* at 95, so
there is no divergence here. That position belongs to **`ict_scalp_mgc_15m`**, a
different strategy on the same netted symbol. Every `mgc_trend_1h` evaluation
computes a reduce-by-60 against another strategy's position and correctly
declines to act.

⚠️ **The no-op is probably the RIGHT behaviour** — letting one leg reduce
another's position would strand it, which is what the per-trade=per-position
netting guard exists to prevent. **The defect is not the decision; it is that a
permanently-dead leg is indistinguishable from a working one.** It is recorded as
`status: rejected`, it accrues no trades, and it has done so for 17 days.

**A prediction of mine that the data REFUTED, recorded because it matters.** I
expected F-38's mechanism — sub-1-contract whole-contract refusal at low
`risk_pct`. It is not that: `ib_paper` holds **$1,342,039**, so a 1.5% budget
against $576/contract sizes to ~34, and the field confirms the sizer produced
**35**. My arithmetic was right and my hypothesis was wrong; the qty never fails.

### F-96 — The detector for dead legs exists twice, and neither instance can fire (severity: HIGH)

This is F-40's guard-boundary shape, **fifth instance**, and the cleanest one:

1. **The live alert is account-scoped.** `silent_refusal_alert.py:359` latches
   per **`(account, cause)`**, deliberately — `CLAUDE.md` records the reasoning:
   *"a per-leg alert would fire 16 pings for one cause, which is the
   desensitized-alarm P1."* But `ib_paper` **is** placing orders (the 95-contract
   position). At account granularity `ib_paper` grades as *"rows, some placed"* —
   a refusal *rate*, explicitly **not** the finding. **A 100%-dead leg on a
   healthy account is invisible by construction.**
2. **The per-leg audit has no runner.** `scripts/ops/dead_leg_audit.py` exists
   and `dead_leg.py` correctly counts `rejected` in `REFUSED_STATUSES` — so the
   rows *are* gradeable. But the script is referenced only by two source
   docstrings and one test: **no workflow, no timer, no caller.** It is a member
   of the 161 unwired tools (F-6).

So the alert that *could* fire is scoped where it cannot see, and the tool scoped
correctly is never run. The tension is real — per-leg alerting genuinely does
risk alarm fatigue — which is why the answer is a per-leg **report** on a
cadence, not a per-leg alarm. That report is already written.

### F-97 — Broker-measured PnL has fallen to 15.0% of recent closed trades (severity: HIGH)

Population: the **200 most recent closed trades**, `include_paper=true`.

| provenance | n | share |
|---|---|---|
| `unverified` | 87 | 43.5% |
| `estimated` | 68 | 34.0% |
| **`measured`** | **30** | **15.0%** |
| null (`realizedPnl` is null) | 15 | 7.5% |

**85% of recent closed-trade PnL is not broker-measured, and 43.5% carries no
provenance at all.**

⚠️ **Populations differ — do not read this as 60.8% → 15.0% directly.**
`CLAUDE.md`'s 60.8% is over **829 lifetime** closed non-backtest rows with
non-null pnl; mine is the **recent 200 including paper**. What makes the
comparison meaningful anyway is that the doc *already records the direction*:
fabricated share of closed trades **0.0% (May) → 23.7% (Jun) → 65.3% (Jul)**.
This measurement extends that trend into August and it has continued.

**Why it matters operationally:** the R4 research→results gate reads
`totalPnlMeasured` and abstains below a coverage floor. At 15% measured, the gate
abstains on nearly everything — so the promotion machinery is running on a
population that can barely be judged, which is exactly the *"poisoned book"* state
`research_results_gate.py` names as `abstain_unverified`.

### What the pipeline pass found SOUND

Recorded because a death-hop histogram with no green hops would be a broken probe,
not a broken system:

- **Journal ↔ broker agree on MGC at 95 contracts** — no divergence on the leg I
  traced end-to-end.
- **`same_direction_reinforcement` (27 rejections) is the netting guard working**
  — pyramiding correctly suppressed, and it carries its own distinct cause label
  rather than being folded into a generic refusal.
- **The sizer is correct** where I checked it: predicted ~34 contracts from
  balance × risk_pct ÷ risk-per-contract, field shows 35.
- **The pairs sleeve places reliably** — `pairs_bnb_btc_a/b`, `pairs_sol_eth_a/b`:
  105 packages, **105 placed, 0 rejected**.

---

## Part 19 — 3.4 OUTCOME: did the changes deliver what they promised?

The operator's message-3 deliverable in its own words — *"compare the system's
historical performance against the expected design."* Method: recover the
**promise** each Tier-2/3 change was gated on, then measure that same quantity
now. *"No measurable effect"* is a legitimate verdict, and so is *"delivered,
then silently regressed."*

### F-98 — The M20 decouple delivered, and the gain has since been erased 2.3× over (severity: HIGH)

**Population:** `/api/diag/tick_cost`, n=17 ticks, one process
(`process_started_utc` 2026-08-20T08:14:21Z), read 09:2xZ. Stated because the
comparison figures are also small-n reads.

| quantity | promised / verified at ship | measured now | ratio |
|---|---|---|---|
| tick mean | **69.3 s** | **159.9 s** | **2.31×** |
| tick max | **96.8 s** | **215.1 s** | **2.22×** |
| `fetch.1d` mean | 2.4–3.6 s per call | **17.5 s** | **4.9–7.3×** |

The M20 exit-loop decouple was verified at go-live and `CLAUDE.md` records the
before/after honestly (*"the tick `mean_ms` fell 107.9 s → 83.9 s across the
decouple **without the system doing less work** — it became concurrent"*). The
`CANDLE_CACHE_TTL_MAX_S=300` flip (Tier-3, operator-approved 2026-08-13) was
independently verified effective the same day: hit rate 21.5% → 41.5%, tick
69.3 s → 64.1 s.

**Both delivered. Both have been undone.** The tick is now worse than the
*pre-decouple* 107.9 s figure, and worse than the 83.9 s post-decouple one.

**One half did hold, and it is worth separating:** `offloop_hooks` is
**POPULATED** (`fetchby.strategy_monitor_loop` n=1289, `monitor.position_telemetry`
n=1176). `CLAUDE.md` names an empty `offloop_hooks` as the direct proof the
segregation silently is not working — so the *structural* half of M20 is intact.
What regressed is the underlying fetch cost it sits on.

### F-99 — F-85 and F-98 are the same defect, and that changes the fix (severity: HIGH — synthesis)

These were found by different passes and read as two problems. They are one.

`CLAUDE.md` states the coupling explicitly: *"The pass is fetch-bound (off-loop
`fetchby.strategy_monitor_loop` … the largest fetch consumer anywhere), so this
shares a root with the tick regression and a TTL/fetch change aimed at the tick
reaches exit decisions too, in both directions."*

The measurements agree with that prediction:

- `fetch.1d` on-loop **17.5 s** mean (documented 2.4–3.6 s)
- off-loop `fetchby.strategy_monitor_loop` **n=1289**, max **26.8 s**
- exit pass max **74.4 s**, exit interval max **91.6 s**, **28.2% over the 60 s
  requirement** (F-85)

So the 60 s exit-requirement breach is not an exit-loop defect to be fixed in the
exit loop. **Both symptoms are downstream of a market-data fetch regression**, and
the exit path is simply where it becomes money-relevant. A fix aimed at the exit
loop alone would move a symptom; the fetch cost is the thing.

⚠️ **This also inverts the natural remedy.** Raising the candle-cache TTL would
relieve both — but `CLAUDE.md` records that the TTL is **not** a chart-freshness
setting: strategies read `candles_df["close"].iloc[-1]` as the current price for
entry geometry, and the monitor reads the same field for exit decisions. So the
TTL bounds *how stale the price behind a live order may be*. That makes it
**Tier-3**, and it is why I am recording the diagnosis rather than proposing a
value.

### F-100 — Promise-vs-delivered, the other changes I could measure

| change | promise | measured now | verdict |
|---|---|---|---|
| **M20 exit decouple** | *"no live trade goes >60 s without evaluation"* | 28.2% of 394 intervals breach; p90 73.8 s; max 91.6 s | **NOT DELIVERED** (F-85) |
| **M20 tick improvement** | 69.3 s mean | 159.9 s | **REGRESSED 2.3×** (F-98) |
| **`CANDLE_CACHE_TTL_MAX_S=300`** | hit rate ↑, tick 69.3→64.1 s | tick 159.9 s | **ERASED** (F-98) |
| **M20 off-loop segregation** | `offloop_hooks` populated | populated, n=1289 | **DELIVERED** ✅ |
| **Netting guard / no-pyramiding** | suppress same-direction adds | 27 `same_direction_reinforcement` rejections, own cause label | **DELIVERED** ✅ |
| **Pairs sleeve (M22)** | places both legs when live | 105 packages, **105 placed, 0 rejected** | **DELIVERED** ✅ |
| **Journal↔broker agreement (IB)** | no divergence | MGC 95 = 95 on the traced leg | **DELIVERED** ✅ |
| **Provenance grading** | mark manufactured PnL | field populated on 185/200 | **DELIVERED as a signal**, but 15% measured (F-97) |

**The pattern is not "things don't work."** Six of eight promises were kept at
ship time, and four are still verifiably held today. **The failures are all of one
kind: a delivered improvement that silently decayed, with nothing watching the
number the promise was written against.**

That is the OUTCOME axis earning its place — none of these would surface as a bug
report, because nothing is broken. The system is simply no longer doing what it
was measured doing.

**The structural fix this argues for:** a change gated on a numeric promise should
register that number as a **watched invariant** at ship time, not just cite it in
a PR body. `system_invariants.py` (shipped this session) is the right home:
`INV-EXIT-INTERVAL` already exists and would have caught F-85 on any run. A
`INV-TICK-COST` in the same shape would have caught F-98.

## Part 20 — ML review substance: drift, soak, and promotion adjudication

The operator's framing for this pass was explicit: *"just checking that the
trainer VM is green isn't enough — we need to verify the training sessions and
backlogs are actually being worked through and producing reliable and actionable
results, every day."* This part runs that check against the live trainer.

**Method.** Live reads over direct HTTPS to the bot API (`/api/bot/ml/status`,
`/ml/cycle`, `/ml/registry`, `/ml/builds`, `/ml/db_pulls`, `/shadow/stats`,
`/shadow/drift`) plus one trainer-VM relay (#10033) for systemd state and the
readiness report itself. Populations are stated per claim.

**The headline is not "the trainer is broken."** It is working hard and
correctly: 68 manifests retrained nightly, 120 dataset builds in 24h, 0 failures,
mirror age 100s. The defect is one level up — **the daily instrument that turns
that work into a decision cannot reach its decision.**

### F-101 — The daily promotion-readiness sweep cannot emit a `promote`, by construction (severity: HIGH)

`ict-promotion-readiness.timer` is `enabled` + `active`, fires daily at 04:00Z
(+≤30min jitter), and has written a report every day from 2026-08-09 to
2026-08-20 (12 consecutive dated directories, verified by `ls`). It pushes an
operator ping on exit-10 days. By every liveness test available it is healthy.

Today's report (`2026-08-20T04:22:42Z`, 95 models):

```
0 promote,  1 demote,  94 hold
```

**`0 promote` is the only value this report can produce.** Every shadow-stage
row in the hold list blocks on the same three required gates:

```
btc-regime-15m-lgbm-fc-pcv-v1 (shadow) — blocking: oos_edge, live_parity, labels_accruing
btc-regime-15m-lgbm-v2        (shadow) — blocking: oos_edge, live_parity, labels_accruing
btc-regime-15m-lgbm-yz-v1     (shadow) — blocking: oos_edge, live_parity, labels_accruing
btc-regime-1h-lgbm-v2         (shadow) — blocking: oos_edge, live_parity, labels_accruing
btc-regime-5m-lgbm-v2         (shadow) — blocking: oos_edge, live_parity, labels_accruing
… every shadow row in the fleet, same three.
```

Two of those three are unsatisfiable in the *scheduled* sweep for reasons that
have nothing to do with the models:

**1. `oos_edge` — off by default, because on it kills the VM.** The service's own
run log, every night:

> `PROMOREADY_OOS_EDGE=off: sweep runs WITHOUT --datasets-root; oos_edge reports
> insufficient_data for EVERY head this run … OFF is the DEFAULT and the
> known-good state (the 2026-07-26 subprocess-isolation fix was REVERTED
> 2026-07-27: isolation bounds fleet accumulation, not one head's dataset load,
> so the sweep OOM-killed and wrote no packet). **MB-20260719-PROMOREADY-OOSEDGE-OOM
> is OPEN, not resolved** — it closes when oos_edge's load is made memory-bounded.`

This is honest and well-written. It is also load-bearing: `oos_edge` is the gate
the 2026-07-19 reframe made *carry the promote decision*. The sweep therefore
runs the promotion report with its deciding gate disabled.

**2. `live_parity` — guaranteed zero by the ordering of three timers.** Read from
`ml/promotion/gates.py:640-650` (not from prose): fidelity is judged only over
rows logged **since the current artifact's training run** (`n_fresh_rows <
parity_min_rows` → `insufficient_data`). Now line up the trainer's own clocks,
all measured today:

| time (UTC) | event | source |
|---|---|---|
| 00:51 | live→trainer pull of `shadow_predictions.*` completes | `/ml/db_pulls` |
| 01:14 | training cycle ends — **every head re-trained, artifact_at resets** | `/ml/cycle` `cycle_end` |
| 04:22 | readiness sweep grades `live_parity` | journal, #10033 |
| 05:04 | the *next* live→trainer pull lands | `/ml/status` `data_pulls.last_ok_ts` |

The sweep at 04:22 counts rows logged after 01:14 **inside a log that ends at
00:51**. `n_fresh_rows` is 0 every day, not because serving fidelity is
unproven but because the evidence file predates the artifact it is being asked
to verify. `scripts/ml/gate_check_candidates.sh` already documents this exact
trap in its own header (*"a gate-check against a stale trainer-side shadow log
… can never accumulate the 20-row parity bar and reads a permanent
insufficient_data"*, MB-20260721-FCPCV-V2-SOAK) — and it fixes it for the
**manual** path by calling `sync_trainer_data.sh` before gating. The
**scheduled** path has no such call. The knowledge exists; it was applied to the
hand-run script and not to the daily one.

**Why this is the class the audit skill was rewritten to catch.** Every
individual check passes. The timer is enabled. The service exits cleanly. The
report is written, mirrored, and pinged. The SUMMARY even carries an honest
warning line about `datasets_root`. What no check asks is the OUTCOME question:
*over 12 consecutive daily runs, has this instrument ever produced the output it
exists to produce?* It has not, and it cannot until either the sweep gains a
pre-gate sync or the sweep moves after the 05:04 pull.

The report is not lying. It is **answering a different question than the one its
headline implies** — `0 promote` reads as a verdict about 28 shadow models when
it is a property of the sweep's own configuration. Unprovenanced diagnostic,
sub-class C: an unasserted denominator, where an empty result reads as a clean
negative.

**Fix shape (do not apply yet — fixes are deferred):** three separable pieces.
(a) Move the timer after the 05:04 pull, or call `sync_trainer_data.sh` from
`run_promotion_readiness.sh` as the manual path already does — this alone
un-sticks `live_parity`/`labels_accruing` at zero code risk. (b) Make the report
state its own reachability: a `gates_evaluable` block naming which required gates
could not be computed this run, so `0 promote` is never readable as a fleet
verdict. (c) `MB-20260719-PROMOREADY-OOSEDGE-OOM` stays the real work; memory-
bounding the per-head dataset load is the durable fix, and until it lands the
report should say `promote: unreachable`, not `promote: 0`. **These are three
states, not two** — `no model qualified` / `we could not evaluate` / `evaluated
and blocked` — the collapsed-state doctrine applied to the ML lifecycle's own
summary.

### F-102 — A DEMOTE proposal for a live advisory head has been pinged for days with no disposition, and two drift implementations disagree about it (severity: MEDIUM)

Today's report carries exactly one actionable proposal:

```
## Demote
- sol-regime-15m-lgbm-fc-pcv-v2 (advisory → shadow): score-distribution drift
  verdict is 'significant'
```

The sweep exited 10 (actionable) and pushed an operator ping on 08-16, 08-17,
08-19 and 08-20; 08-18 exited 0. The model is still at `advisory`. So the one
decision the instrument *can* deliver is being delivered, repeatedly, and is not
being adjudicated — which is the same normalization failure
`MB-20260719-DATASET-AUDIT-NOISE` was filed for, in a different organ.

**And the two drift computations do not agree.** Read the same model the same
day through `/api/bot/shadow/drift`:

| source | KS | PSI | verdict |
|---|---|---|---|
| `/api/bot/shadow/drift` (30d ref vs current) | 0.1803 | 0.0042 | **moderate** |
| readiness report `drift_clean` | — | — | **significant** |

Both are "the drift of `sol-regime-15m-lgbm-fc-pcv-v2`". One of them is the
input to a demotion of a live head. `ml/shadow/drift.py:22-24` fixes the bands
(`<0.1` none, `0.1–0.25` moderate, `>0.25` significant), so 0.1803 is
unambiguously `moderate` under the shared band table — meaning the gate is
either using different windows or a different statistic, and nothing in either
output discloses which. This is one concept with two implementations and no
declared owner: the same modularity failure as the backtest-vs-live `risk_pct`
(F-40's family).

Context that makes the disagreement worth resolving rather than dismissing: the
predecessor head `sol-regime-15m-lgbm-fc-pcv-v1` was demoted advisory→shadow on
2026-07-26 for `drift_clean` at KS 0.236, and `btc-regime-15m-lgbm-v2` was
demoted at the same figure. `-v2` was promoted 2026-08-02 at KS 0.1353 and reads
0.1803 today — moving the wrong way, 18 days in.

### F-103 — Five manifests have been enforced-skipped for 25 days and the cycle reports rc=0 (severity: HIGH)

F-35 established that 7 of 76 manifests are stale while `/api/bot/ml/status`
reads green. This pass establishes **the cause**, which F-35 explicitly deferred.

Five of the seven share one status line, emitted every cycle:

```
manifest_audit_skipped_enforced — "SKIPPED (enforced): dataset audit flagged a
dead feature / degenerate label in a NON-empty dataset — not trained this cycle.
Fix the flagged column/label (see dataset_audit.jsonl) to resume training."
```

`baseline-prop-mission-policy` · `btc-regime-15m-lgbm-base-vt003-pcv-v1` ·
`mes-regime-1d-lgbm-v2` · `setup-candidates-metalabel-xsym-yz-v1` ·
`setup-quality-lgbm-v2` — all last trained **2026-07-26**, all 22.8–22.9 days
untrained against a 7-day threshold. The `training_staleness_summary` block is
byte-identical across all six cycles in the retained window
(`stale: 7, never_trained: 2, awaiting_source: 1, scanned: 76`), 2026-08-18
through 2026-08-20.

**The recurrence, stated plainly.** This enforcement exists *because of*
`MB-20260719-DATASET-AUDIT-NOISE` — the incident where the dataset audit
degenerated to 62/86 manifests alarming, sessions walked past the noise for
weeks, and the ETH-xa dead-feature bug soaked inside it. The remedy was to stop
alarming and start **enforcing** the skip. That remedy converted a **noisy**
failure into a **silent** one: the manifests now stop training and the cycle
exits `overall_rc: 0`, `failed: 0`, `outcome: already_complete`. A skipped
manifest is counted as `already_done`, so `already_done: 76` of 76 scanned.

Both polarities of the same defect share a root: **there is no state between
"trained" and "failed."** An enforced skip is neither, so it is booked as the
former. This is the collapsed-state doctrine — which this repo wrote down and
enforces with `collapsed-state-guard` on the *live trader* — never applied to
the ML lifecycle. `manifest_audit_skipped_enforced` is precisely a
*we-looked-and-refused* state, and the cycle summary has no field for it.

A secondary row from the same scan, worth its own line because it is the inverse
failure: `conviction-meta-v1-bt.yaml` carries an
`expected_optional_features` declaration silencing `c_setup,c_wr` — columns the
trainer reports are **now populated**. A stale silencer suppressing real data,
which is how a dead-feature guard degrades into a rubber stamp.

### F-104 — Prop "open position" identity requires a field the ingest contract does not (severity: HIGH — operator-reported, reproduced from the live journal)

Raised by the operator mid-session: *"long after I close a prop trade, I'm still
getting monitoring things on Telegram … it's a recurring problem that we've come
across many times, and we keep on fixing the one sequence but not fixing the
root problem."*

Reproduced exactly, from `/api/bot/prop/fills` (population: all 32 rows):

```
id  account     symbol   direction  status  qty   reported_at          ticket
32  breakout_1  SOLUSDT  long       closed  83.0  2026-08-19T21:31:42  None
31  breakout_1  SOLUSDT  long       closed  83.0  2026-08-19T21:31:28  prop-manual-5e30b930
30  breakout_1  SOLUSDT  **None**   open    83.0  2026-08-19T12:52:40  prop-manual-5e30b930
```

`prop_monitor_pulse._position_key` keys a position on
`(account_id, symbol, canonical_direction)`. Row 30 keys as
`akd:breakout_1|SOLUSDT|` (empty direction); rows 31/32 key as
`akd:breakout_1|SOLUSDT|long`. Different keys — so the newest fill under row 30's
key is still `open`, and `find_open_prop_positions` has reported a phantom-open
83-SOL position continuously since 2026-08-19. That is the hourly ping the
operator is seeing.

**The root cause is an unenforced contract between two modules.**
`prop_report.ingest_report` — the single chokepoint every report-back passes
through — validates `account_id` (line 57) and `symbol` (line 86) and **passes
`direction` through unvalidated** (line 106). `_position_key` requires all three.
One module owns *identity*, another owns *admission*, and nothing asserts they
cover the same fields. A fill admitted without a direction is **permanently
unclosable**: no future close report can land under its key, because a close
carrying a direction keys elsewhere.

**Why the previous fix did not fix it.** `BL-20260708-PROP-PULSE-DIRECTION-ALIAS`
is the same complaint from 2026-07-08, and the fix hardened the *normalizer*
(`buy`→`long`, `sell`→`short`) while leaving *admission* open. The journal still
carries that earlier instance as evidence — row 15 `ETHUSDT buy filled` beside
row 16 `ETHUSDT long filled`. And before that, keying on `ticket_id` was
abandoned for the same class (row 10 opens under `prop-manual-SOLUSD-4`, row 11
closes under `prop-manual-1014c5ab`). Three incidents, three different ways
operator-typed text can differ between the open and close report, three
per-instance fixes. The class is: **prop position state is inferred from the
absence of a matching close row, and the match depends on free text agreeing.**
*"We have not been told it closed"* and *"it is open"* are the same state — the
collapsed-state doctrine, never applied to the prop book.

**The safety half, which matters more than the ping.** `find_open_prop_positions`
has four consumers: the pulse, `prop_sl_tp_alert` (line 139),
`breakout_executor._suppress_reason` (line 92), and `prop_status_request`. For an
undirected position:

- the **pulse** iterates all positions → pings hourly (the visible symptom);
- **`prop_sl_tp_alert`** reaches `_sl_crossed("")`, whose direction dispatch
  falls through to `return False` (lines 91-97) → **the position can never fire
  an SL or TP alert.** On a phantom that is harmless. On a *real* position
  reported without a direction it is a silently dead stop-loss warning on a
  prop account whose static-DD floor is $4,700.
- the **suppression guard** compares `pos["direction"].lower() == d`, so
  `"none" != "long"` → it does **not** suppress. 

**A hypothesis of mine, refuted and recorded.** I first read the `suppressed`
SOLUSDT tickets at 2026-08-19T21:01 as the phantom gating the prop order path.
It is not: the direction mismatch that creates the phantom also excludes it from
the suppression comparison, and ticket `prop-manual-8eccec2028f3` was `emitted`
at 20:57 with `valid_until` 21:57 — still live at 21:01, so the
`outstanding_ticket:emitted` branch explains the suppression correctly. The
order path is unaffected. Recording this because the near-miss is the point:
one malformed row is interpreted **three different ways by three consumers of
one derivation**, and only reading each consumer's own predicate tells you
which.

**Fix shape (deferred, as agreed).** In order of durability: (1) make
`ingest_report` reject a fill whose `status` implies a position (`open`/`filled`/
`closed`) and carries no resolvable direction — admission must cover identity, and
the ticket is available to resolve it from (`prop-manual-5e30b930` carries
`direction: long`); (2) give the prop book an explicit *reconciliation* state so
"not reported closed" is distinguishable from "confirmed open", registered with
`collapsed-state-guard` like every other three-state field in this repo; (3) an
age ceiling on an unclosed prop position that escalates rather than pulses — a
position open longer than any plausible hold is a data defect, not a trade to
monitor. (1) and (3) are Tier-2; (2) is the structural one the operator asked
for.

### F-105 — The registry carries two stage fields with independent lifecycles (severity: LOW; the dangerous reading refuted)

Every registry row carries `status` **and** `target_deployment_stage`. On all
three live advisory heads:

```
btc-regime-15m-lgbm-fc-pcv-v2:  status=candidate  target_deployment_stage=advisory
sol-regime-15m-lgbm-fc-pcv-v2:  status=candidate  target_deployment_stage=advisory
mes-regime-5m-lgbm-v2:          status=candidate  target_deployment_stage=advisory
```

`status` is rewritten `candidate → candidate` by `experiments-runner` on every
nightly re-train (36 / 35 / 81 history entries, **100% `candidate`** in all
three); `target_deployment_stage` is moved only by the operator-gated promotion
and is recorded separately in `stage_history`.

**The hypothesis I formed and then refuted:** that the nightly re-train silently
demotes a promoted head. It does not. Every runtime consumer reads
`target_deployment_stage` — `regime_bar_scoring.py:267`,
`ml_vol_verdict.py:182`, `advisory_sizing.py:64`, `coordinator.py:496`,
`training_center.py:312` — and the Streamlit renderer prefers it with `stage` only
as a fallback (`streamlit_app.py:3903,6159,6405`). BTC's real-money vol gate is
reading `advisory`, as `CLAUDE.md` claims.

What remains is a legibility hazard rather than a live defect: `status` is the
first field a human sees on a registry row, it is permanently `candidate` for
every model in the fleet, and it is a **column in the `trainer_store` sidecar**
(`src/units/db/trainer_store.py:168,184`) — so a Data Explorer or SQL analysis
grouping `model_registry` by `status` returns *zero advisory models*, confidently.
Two fields named for one concept, one of them inert, both queryable.

### Retractions from this pass

Two of my own probes returned `None` from **key names that do not exist**, and in
both cases the null was one step from being reported as a finding:

- `/api/bot/shadow/stats` — I read `first_ts`/`last_ts` and reported them null for
  all 34 models, nearly concluding that the promotion gate's soak denominator was
  unpopulated. The keys are `first_seen`/`last_seen`, and the surface is in fact
  **exemplary**: it carries `soak_days`, `soak_days_is_lower_bound`,
  `soak_start_basis` (`registry` / `registry_registration` / `observed` /
  `log_censored` / `unknown`) and a `log_coverage` envelope stating the retained
  window is not the soak history — the
  `BL-20260810-SHADOW-STATS-FIRSTSEEN-IS-LOG-ROTATION-NOT-SOAK-START` fix,
  correctly done.
- The readiness `report.json` — I read `promote`/`demote`/`hold` at top level and
  got `None`; the real keys are `proposals` / `summary` / `datasets_root_used` /
  `generated_at_utc`.

`dict.get()` on a misspelled key is indistinguishable from a real null. That is
the *same* collapsed state this audit keeps finding in the system, occurring in
the audit's own instrument — which is why every probe in this part prints its
key set before its values.


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

# R6 — the trainer VM's fate, measured

**Date:** 2026-08-28 · **Repo head:** `76d14af5` · **Trainer head:** `76d14af5` (in sync)
**Gate:** R6 was blocked on R3 *holding*. R3 holds as of #10390, so this is answerable.
**Method:** two read-only `trainer-vm-diag` pulls (#10391, #10392) + a repo-side consumer trace.

---

## 0. The verdict, up front

> **The hypothesis is REFUTED as stated.** [`RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md`](RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md)
> § R6 offered, explicitly labelled **INFERRED**, that the residual would be
> "the dataset cache and 24/7 cron — leaving nothing". It was right to label it a
> hypothesis. Measured, **one process genuinely requires 24/7 residency**, and it is
> not the dataset cache.

`ict-orderflow-capture.service` is a continuously-running L2 order-book capture that
has been up **44 days** and produces data that is **forward-only and unbackfillable by
construction**. It cannot be run on an ephemeral runner, and it was invisible to § 3's
inventory for a reason worth keeping (§ 4).

**Retiring the trainer is therefore not a $0 tidy-up today.** It is a *relocation*
question for one always-on process, plus a re-hosting question for two live-serving
timers. That is a smaller problem than "the trainer is load-bearing for ML" — R3 did
dissolve that — but it is not nothing, and the 1 OCPU / 6 GB is not free for the taking
until it is answered.

---

## 1. What is actually enabled on the box

Measured (`systemctl list-unit-files 'ict-*'`, #10391) — **8 timers + 1 long-running service**.

| unit | cadence | class |
|---|---|---|
| **`ict-orderflow-capture.service`** | **continuous (2 s poll)** | **🔴 requires residency** |
| `ict-trainer-forecast.timer` | 15 min | 🟠 live-serving |
| `ict-trainer-publish.timer` | 2 min | 🟠 live-serving |
| `ict-trainer.timer` | nightly | 🟢 runner-portable |
| `ict-trainer-catchup.timer` | 05:00 daily | 🟢 runner-portable |
| `ict-drift-retrain.timer` | hourly | 🟢 runner-portable |
| `ict-promotion-readiness.timer` | daily | 🟢 runner-portable |
| `ict-offload-drain.timer` | 20 min | ⚪ exists *because* the registry is here |
| `ict-trainer-git-sync.timer` | 15 min | ⚪ exists *because* the box is here |

No user or root crontab; `/etc/cron.d` holds only distro entries. So systemd is the
complete scheduler surface — there is no second one hiding.

---

## 2. 🔴 The one thing that genuinely requires residency

`ict-orderflow-capture.service` — `python -m scripts.ml.orderflow_capture --symbol BTCUSDT
--bar-seconds 300 --poll-seconds 2.0 --depth 5`.

**Measured, not inferred:**

- `active (running) since Wed 2026-07-15 07:13:32 UTC; 1 month 14 days ago`, PID 728,
  **87.4 MB** RSS, **3 h 53 min** CPU across 44 days — **0.37 % of one core.**
- **It is writing.** Newest row `2026-08-28T23:50:00Z` against a same-command
  `date -u` of `23:58:09` — fresh on a 5-minute bar.
- **The data is real**, not a stub: `ofi`, `buy_vol`, `sell_vol`, `rel_spread_mean`,
  `microprice_dev`, and `n_snapshots` at **127–128** per bar (against 150 theoretical
  at 300 s / 2 s — an ~85 % poll-completion rate, consistent with the `RequestTimeout`
  lines in its journal).
- Total footprint: **5.7 MB.**

**Why a runner cannot do this.** The model manifest that consumes it states the
constraint in its own words:

> *"The capture is **FORWARD-ONLY (no L2 history)**, so evaluate on the captured window."*

Order-book depth is not retrievable historically from the public API. A minute not
captured is gone permanently. GitHub-hosted runners are ephemeral with a 6-hour job
ceiling, and scheduled workflows are delayed or dropped under load — so a chained
runner capture would take a gap at every handoff, and **gaps in an L2 series are
unbackfillable by construction, which is the same property that makes the data
valuable.** This is not a compute problem, and R3's result does not touch it.

✅ **NOW ESTABLISHED (2026-08-29, diag #10394) — and it is bigger than the service's own
uptime.** An earlier revision of this section said the window length was not established
because I had not read the file's first row. Read: the first row is
**`2026-06-04T09:50:00Z`** and the file holds **24,218 rows**, so the captured window spans
**85.6 days at 98.2 % coverage** (24,218 actual against 24,652 expected at one 5 m bar).

⚠️ **Do not infer the window from the service's uptime — they differ, and the file is the
longer one.** `ActiveEnterTimestamp` is 2026-07-15 (44 days) while the data reaches back
to June 4, because the output is a single **append-only** `data.jsonl` that survived
restarts. Reading the unit's uptime as the data's extent would have under-stated the
irreplaceable window by half. (`NRestarts=0`, `Restart=always`.)

**This strengthens the finding rather than changing it:** what a retirement would end is
~86 days of continuous L2 microstructure at 98 % completeness, not 44.

### 2.1 The irony worth stating plainly

The model R3 used as its proof — `btc-regime-5m-lgbm-flow-v1`, registered from the
runner as `…-v1-offload` — **is the order-flow model.** Its manifest requires
`market_features` rebuilt with the `market_microstructure` side-stream joined, and says
that without it *"the columns are 0.0 and this collapses to the v2 head."*

> **R3 proved COMPUTE is portable. It did not prove ACQUISITION is.**

Reading "a runner trained a model" as "the trainer is disposable" inverts what the
demonstration showed: the runner trained on data only the trainer's 24/7 capture
produces. Both halves are true and they point in opposite directions.

---

## 3. 🟠 A live-serving role the disk inventory never counted

The trainer is not purely a research box. Two timers form a path into the **live
trader's order decisions**:

`ict-trainer-forecast.timer` (15 min) → `runtime_logs/trainer_mirror/forecasts/<SYM>.json`
→ `ict-trainer-publish.timer` (2 min rsync) → live VM → `src/runtime/forecast_live.py`
→ the per-bar regime scorer → the `fc_*` features the **advisory** BTC head reads.

Measured healthy: three symbols `written` every 15 min, **6.8 s CPU** per run
(chronos-bolt-tiny, 9 M params, CPU).

**This degrades rather than breaks** — `forecast_live` returns `None` on a stale or
absent artifact (fail-permissive, never a fabricated row), so a trainer outage costs
the fc features, not the trader. That is why this is 🟠 and not 🔴. But § 3's table
asked *what data is pinned here* and concluded "only `registry-store`"; it never asked
*what does this box produce for the live path*, and the answer is not nothing.

Both are portable in principle — public candles plus a 9 M CPU model is runner-shaped
work — but the cadence and the delivery path would both need rebuilding, and a
scheduled-workflow cadence is a materially weaker contract than a 15-minute timer.

---

## 4. Why § 3 missed it — the transferable finding

§ 3 measured with `du`. Its table is five rows of directory sizes, and its conclusion
("`ml/registry-store` is the only real one") follows correctly from what it measured.

**The thing that most requires residency is the smallest thing on the disk.** The
capture's entire output is **5.7 MB — 0.02 % of the 28 G tree.** It sits below the
resolution of the instrument used, and it would have sat there no matter how carefully
the table was read.

This is the repo's own **UNPROVENANCED DIAGNOSTIC OUTPUT class A** — a semantic
substitution — one level up from code: the inventory answered ***"what DATA is pinned
here?"*** under a heading that promised ***"what is pinned to the VM?"***. A process
with a large residency requirement and a tiny disk footprint is exactly the case those
two questions come apart on, and a `du` table cannot express it.

**The generalisation:** *residency* is a property of **processes and their input
streams**, not of stored bytes. An inventory that enumerates directories will
systematically under-report continuous capture, live serving, and anything else whose
cost is time rather than space. Any future "can we retire host X" question should
enumerate **units first, disk second**.

---

## 5. 🟢 What R3 genuinely did dissolve

Everything batch. `ict-trainer.timer`, `ict-trainer-catchup`, `ict-drift-retrain`, and
`ict-promotion-readiness` are scheduled jobs over data a runner can fetch or rebuild;
R3 demonstrated the training half end-to-end, artifact included. `ict-offload-drain`
and `ict-trainer-git-sync` are not work at all — they exist *because* the box exists,
and would leave with it.

The § 3 disk claim also re-measures as still true: of the 28 G tree, **`ml/registry-store`
is 9.9 MB**, and it is already mirrored to the live VM every 2 minutes and served at
`/api/bot/ml/registry`. The 11 G `market_features` cache is **32 version dirs** across
4 symbols — a retention-policy question, exactly as § 3 said, and unchanged.

---

## 6. Two things found on the way that are not R6

**6.1 The disk is at 92 % (42 G used, 3.9 G free) — and the failure mode lands on § 2.**
Largest trees are research byproduct: `market_features` 11 G, `ml/experiments-runs`
4.7 G, `runtime_logs/m20_exit_head` 2.5 G. A full disk stops the order-flow capture,
and *that* loss is the one thing on this box that cannot be recovered by re-running
anything. § 3 reported 41 G on 08-27 and it reads 42 G now — but these are two coarse
`du -sh` readings, so **I am not claiming a growth rate from them**, only that the
headroom is thin and what it protects is irreplaceable. Filed.

**A byte-precise anchor was then taken so a rate becomes computable rather than guessed**
(diag #10394, `df -B1`): at **`2026-08-29T00:18:12Z`, free = 4,237,279,232 B (3.95 GiB)**
of 48,277,495,808 B. Quote *that* pair against a second timestamped reading; do not
difference the rounded `-h` figures.

**The active writer was also identified**, which the earlier reading could not say: the
files touched in the preceding minutes are dataset rebuilds — `market_features/BTCUSDT/5m/v002`
(1.35 GB, 00:14), `.../15m/v002` (465 MB, 00:17), `.../1h/v002` (112 MB, 00:08) — i.e. the
nightly `ict-trainer.timer` cycle (it fires 00:05) rewriting the cache in place. So the
headroom moves on a **nightly cycle**, and a single reading taken mid-rebuild is not the
steady-state figure. That is a reason to measure the trough across a cycle before sizing
any retention policy, not a reason to relax about 3.95 GiB.

**6.2 `ict-drift-retrain` exiting 11 is NOT a fault.** It is `RETRAIN_PLAN_ONLY`:
`dispatch_count=10 cli_exit=11 plan_only=1`, hourly, ~1 min 57 s CPU each time. Worth
recording because the exit code invites the opposite reading. It does mean the box
spends ~47 min/day of its single core computing a plan it never executes, and that 10
manifests have been reported due, hourly, with nobody acting on the report — an
observation, not a finding, and not this document's call to make.

---

## 7. Answering R6 as asked

> *"After R3, re-measure what still requires residency."*

| § 3's inferred residual | measured |
|---|---|
| the dataset cache | ❌ **not** residency — 32 version dirs, a retention-policy question, fetchable |
| 24/7 cron | ⚠️ **partly** — the batch timers are runner-portable; two live-serving timers are re-hostable with work |
| *(unlisted)* | 🔴 **`ict-orderflow-capture` — genuine, continuous, forward-only, irreplaceable** |

**So: the trainer's 1 OCPU / 6 GB is not free for the taking today.** What R6 has
established is the far smaller and more precise question that replaces it:

**Where does the order-flow capture live if the trainer does not?** It needs 87 MB of
RAM and 0.37 % of a core — it does not need this VM, it needs *an* always-on host. That
is a real decision with real options (the live VM has headroom; a $0 always-on tier
elsewhere; or accept the loss and drop the flow-model family). **It is an operator
call, not a measurement**, and it is now the only thing standing between the current
state and reclaiming the pool.

**Recommendation: do not retire the trainer on this evidence.** Decide the capture's
home first. Retiring the box before that silently ends the one data stream nothing else
can reproduce — and § 6.1 says the disk may force the question sooner than a plan would.

---

## 8. 🟢 DECIDED — the operator answered it (2026-08-29)

§ 7 handed the operator a question and called it *"an operator call, not a
measurement"*. It has been answered:

> **"we can keep the training in the meantime. That's the recommendation."**
> — operator, 2026-08-29

That is **option (c)** of `OI-20260829-ORDERFLOW-CAPTURE-HOME-UNDECIDED`'s
`clears_when`: *the trainer is kept for this purpose as a stated decision rather
than by default*. The distinction that row exists to protect is preserved — the
box is retained **deliberately, for a named reason**, not left alone because
nobody got round to retiring it.

**Scope: interim.** *"In the meantime"* is not *"permanently"*. Reclaiming the
1 OCPU / 6 GB remains available whenever the capture has somewhere else to live;
what is settled is that it does not get retired *first*.

### 8.1 The capture was re-measured the same hour, not carried forward

§ 2's evidence was a day old, and § 6.1 warned the disk could answer the question
by accident. Two read-only relays (#10422, #10423):

| | measured 2026-08-29 |
|---|---|
| data freshness | `datasets-out/market_microstructure/BTCUSDT/5m/v001/data.jsonl` modified **17:30:00Z** against a same-command `date -u` of **17:30:17Z** — 17 s before the reference clock, on the 5m bar boundary |
| process | PID 728, **91.6 MB** RSS, `active` since 2026-07-15 (45 days) |
| capture size | 5.9 MB total |
| disk | 42 G / 45 G, **3.8 G free, 92 %** — unchanged from § 6.1 |

Freshness is read the way § 2 insists on: **a fresh row's timestamp against a
clock from the same command**, never the unit reporting `active`.

### 8.2 The path is written down here because guessing it produced a false negative

The first probe looked in `runtime_logs/orderflow/`. **That directory does not
exist**, so the freshness check returned *nothing* — and nothing is
**byte-identical to a dead capture**. The only reason it was not reported as one
is that the second probe carried a **positive control**: 88 files modified in the
same 2 h window, proving the `-newermt` probe worked and that its silence on the
capture was the *path*, not the *stream*.

This is § 4's finding recurring one level down. There it was `du` answering a
question about processes; here it was a path assumption answering a question
about liveness. **The canonical output path is
`datasets-out/market_microstructure/BTCUSDT/5m/v001/data.jsonl`** — from the
unit's own `ExecStart --out`, which is the authority. Any monitor built for
§ 6.2 must key on *that* path, and must be shown to fire against planted
staleness; one built on the wrong path pages forever or never.

Note also what does **not** work as a signal: `journalctl -u
ict-orderflow-capture` returns `-- No entries --` over 3 h on a **healthy**
capture, so an empty journal cannot distinguish healthy from wedged either.

### 8.3 What the decision does NOT settle

Keeping the box **raises** the stakes on § 6's two findings rather than closing
them, because the system now depends on the trainer by decision rather than by
inertia:

- **Nothing monitors the capture** (`BL-20260829-ORDERFLOW-CAPTURE-IS-IRREPLACEABLE-AND-UNMONITORED`).
- **The disk is at 92 %**, and the capture writes into `datasets-out/` — *inside*
  the 28 G repo tree that is itself the full disk (`BL-20260829-TRAINER-DISK-92-PCT-THREATENS-THE-UNBACKFILLABLE-CAPTURE`).
  The tree that fills the disk and the stream that dies when it fills are the
  same tree.

Both now ride `OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED`
(`loud`, re-observed every 3 days), which replaces the decision row.

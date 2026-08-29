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

⚠️ **Not established:** how far back the captured window actually reaches. The service
has run 44 days continuously and the output is a single append-only 5.7 MB `data.jsonl`;
I did not read its first row, so I am not stating a window length. The
forward-only argument holds at any length.

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

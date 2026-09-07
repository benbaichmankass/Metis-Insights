# MI-154b — the scalp exit-head artifact is PUBLISHED; the done-condition is NOT met

**Measured 2026-09-07** against `main` `0ec62fc0`, the live diag surface, and the
trainer VM. Session `session_01GNkN16mQBnSVNRLweSXNWP` (sub-session of manager
`session_01HrmZ1RRNM4UnEUaFdrPEjj`). Work object
`WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD`, checklist item
`MI-154-SCALP-EXIT-HEAD-ARTIFACT`.

## Summary — read both halves, they are different facts

**DONE:** two `ict_scalp`-family exit-head artifacts are trained, exported and
**published to the trainer mirror at `stage: shadow`**, and the mirror rsync to
the live VM is confirmed running. The lane's *data* half is finished.

**NOT DONE:** the briefed done-condition — *artifact visible on
`/api/diag/shadow_stats` AND the in-distribution guard observed admitting a real
`ict_scalp` leg (`decision_state` other than `not_scored`)* — is **not met, and
could not have been met by publishing.** It is blocked on PR #11140, which is
open and unmerged. § 3 gives the mechanism.

This was dispatched on the premise that the predecessor session had stalled and
left the artifact unbuilt. The artifact half was genuinely undone and is now
done. The observation half was **never reachable from this lane alone**, which
the predecessor had already established in
`docs/research/MI-154-scalp-exit-head-ordering-2026-09-06.md`; every load-bearing
claim of that doc was **re-verified independently here** rather than taken on
trust, and all of them hold.

## 1. What was published

Built with the **existing** `scripts/ml/export_exit_head.py` — no parallel
exporter — using the `--family` flag that PR #11169 merged for exactly this
purpose. Both artifacts report `family='ict_scalp' (declared)` at export time,
which is the provenance line #11169 added so a family mismatch is diagnosable at
export rather than from a live WARNING hours later.

| model_id | family | tf | stage | symbols | train_rows | train_trades | data window |
|---|---|---|---|---|---|---|---|
| `exit-head-ict_scalp-5m-v1` | `ict_scalp` | `5m` | `shadow` | AVAXUSDT, SOLUSDT, XRPUSDT | 70 891 | 3 950 | 2021-05-13 → 2026-06-17 |
| `exit-head-ict_scalp-15m-v1` | `ict_scalp` | `15m` | `shadow` | ETHUSDT, SOLUSDT, XRPUSDT | 28 683 | 1 678 | 2021-03-16 → 2026-06-18 |

Both boosters load: 300 trees, 14 features, matching the 14 the artifact
declares. **POPULATION for the training rows: every row in the pooled dataset,
asserted 100% `source == "harness"` before training** (70891/70891 and
28683/28683) — the exporter silently drops non-harness rows, so an unasserted
pool would train on an unknown denominator.

**Layout.** The surviving E0 rounds are per leg
(`runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z/ict_scalp_sol_5m/rows.jsonl`),
so the three legs per timeframe were concatenated into the
`datasets-out/exit_head/<tf>/<family>/rows.jsonl` layout the exporter's own
docstring documents and the `eh_1h_pooled/donchian/` precedent already uses.
`trade_key` is leg-prefixed (`ict_scalp_sol_5m:SOLUSDT:1634431500`), so pooling
cannot collide keys. This mirrors the donchian heads, which are likewise one
artifact per timeframe over pooled symbols.

**Mirror population: 2 → 4.** Read back from the mirror after the write, not
asserted:

```
exit-head-donchian-1h-v1.json       family='donchian'  tf=1h  stage=advisory rows=34338
exit-head-donchian-peak-1h-v1.json  family='donchian'  tf=1h  stage=shadow   rows=44244
exit-head-ict_scalp-15m-v1.json     family='ict_scalp' tf=15m stage=shadow   rows=28683
exit-head-ict_scalp-5m-v1.json      family='ict_scalp' tf=5m  stage=shadow   rows=70891
```

**Nothing was promoted.** Both are `stage: shadow`; `--stage` was passed
explicitly. Promotion past shadow is the operator gate and was not touched.

## 2. The publish channel is confirmed, the live-VM copy is INFERRED

`scripts/ops/publish_trainer_mirror.sh` ships the `exit_head` dir explicitly
(`EXIT_HEAD_ROOT`, and `exit_head` is in its `mkdir -p` list on the live side).
`ict-trainer-publish.timer` is `active` and fires every ~2 min;
`ict-trainer-publish.service` exits `0/SUCCESS` and logs
`{"status":"published","dest":"ubuntu@141.145.193.91:/data/bot-data/runtime_logs/trainer_mirror"}`.

**A publish ran strictly after the write.** The artifacts' mtime is
`2026-09-07T04:14:45.86Z`; the publish that completed at **04:14:26 predates the
write and did NOT carry them**, and the next completed at **04:16:27**, after it.
That ordering was measured against the file mtimes, not assumed from "the timer
is running" — the two adjacent runs straddle the write and only the later one is
evidence.

⚠️ **Even so, I did not read the artifact off the live VM, and cannot.** The
`trainer_mirror/exit_head` dir is **not** on the diag allowlist — `exit_head` and
`trainer_mirror` each appear **0 times** in `src/web/api/routers/diag.py` — so
there is no read surface for it. What I have is: the file exists on the trainer
(read back), the publisher ships that dir (read from the script), and the
publisher reported success after the write. The **channel** has a positive
control — the two donchian artifacts reached the live VM by this exact path and
are being scored there — but the specific delivery of these two files is an
inference from the publisher's own success line plus that ordering — not an
observation of the destination. Recorded as inferred rather than claimed. **What
would settle it is a read surface for the mirror's `exit_head` dir, which does
not exist today.**

## 3. Why the done-condition could not be met — three independent mechanisms

Each verified this session against `main` `0ec62fc0`, each with a positive
control where a bare absence would otherwise be a dead probe.

**(a) `decision_state` does not exist on `main`.** It is introduced by #11140.
`grep -rl 'decision_state' --include='*.py'` → **0 files**. *Positive control:*
the identical probe over `EXIT_LOOP_DECOUPLE_DISABLED` → **4 files**. So a
done-condition written against `decision_state` cannot be evaluated against
deployed code at all. `src/runtime/exit_head_apply.py` likewise does not exist.

**(b) No `ict_scalp` leg can reach the scorer.** The only production call site of
`maybe_score_exit_head` is `src/units/strategies/trend_donchian.py:802`. The
`ict_scalp` unit has none. **POPULATION: all 55 strategies in
`config/strategies.yaml`** — all 8 `ict_scalp` legs (`ict_scalp_5m`,
`ict_scalp_sol_5m`, `ict_scalp_xrp_5m`, `ict_scalp_avax_5m`, `ict_scalp_xrp_15m`,
`ict_scalp_eth_15m`, `ict_scalp_sol_15m`, `ict_scalp_mgc_15m`) are 5m or 15m and
resolve to unit `ict_scalp`. A published artifact with no call site is never
consulted.

**(c) `shadow_stats` enumerates models that have SCORED, not artifacts that
exist.** `/api/diag/shadow_stats` → `src/web/api/routers/shadow.py::stats`
aggregates `shadow_predictions.jsonl` via `iter_records(log)`. An artifact that
is never scored writes no rows and therefore **can never appear on that feed,
however correctly it is published.** This is the mechanism that makes the
briefed done-condition unreachable rather than merely unmet.

**Confirmed by observation, after publishing.** `/api/diag/shadow_stats`, read
directly over the Caddy host at 2026-09-07T~04:16Z. **POPULATION: 32 model_ids.**
*Positive control:* the probe finds both existing exit-head ids, so the quiet
result is a reading and not a dead parser.

```
exit-head-donchian-1h-v1        stage=advisory count=52 last_seen=2026-09-06T10:00:01Z
exit-head-donchian-peak-1h-v1   stage=shadow   count=52 last_seen=2026-09-06T10:00:01Z
ict_scalp-family model_ids on the feed: 0
```

Unchanged from before the publish, exactly as (a)–(c) predict.

## 4. What is missing, precisely

1. **PR #11140 must merge** — it is `state: open`, `merged: false`, tier 3,
   `landing: hold`, and is **held by the manager on a Tier-3 question**. It adds
   the `ict_scalp` call site, `ICT_SCALP_EXIT_HEAD_MODE`,
   `src/runtime/exit_head_apply.py`, and `decision_state` itself.
2. **Then the artifacts published here are consumed with no further work.** The
   guard filters on `tf` (exact) and `symbols` (membership). The 5m head covers
   AVAXUSDT/SOLUSDT/XRPUSDT and the 15m head ETHUSDT/SOLUSDT/XRPUSDT, so **6 of
   the 8 `ict_scalp` legs** are matched on both axes the guard checks. #11140
   also adds the `family` check the guard currently lacks, and both artifacts
   declare `family: ict_scalp`, which is in its `_ACCEPTED_FAMILIES` set — this
   is what #11169's `--family` flag was merged to make possible.
3. **Not covered by these artifacts:** `ict_scalp_5m` (BTCUSDT — its E0 round was
   never built, `blocked:data_missing_btcusdt`) and `ict_scalp_mgc_15m` (MGC —
   `blocked:native-history-thin`). Neither is a regression; both were already
   recorded as absent data, and neither symbol is in a published `symbols` list,
   so the guard fail-closes on them rather than scoring them out of distribution.

⚠️ **Nothing here is an argument for arming.** `ICT_SCALP_EXIT_HEAD_MODE` was not
touched, no leg declares `exit_head_action`, and no cell `status` in
`docs/research/exit-refinement-coverage.json` was edited — all Tier-3. The
standing 2026-08-23 `SHIP BLOCKED` operator verdict on the five candidate cells
is untouched by this work: it required a consumer to exist in the `ict_scalp`
unit, and one still does not. Specifically, **`ict_scalp_eth_15m` is a recorded
`honest_negative`** (`beats_hard` 4/11 against a bar of 8/11) and
**`ict_scalp_sol_5m`'s pass does not survive re-partitioning**
(`BL-20260815-SCALP-EXIT-HEAD-MATRIX-DISAGREES-WITH-THE-4-ARM-SCREEN`). Their
symbols are in the published `symbols` lists deliberately — at `shadow` the
scorer is observe-only, and excluding them would mean those legs never accrue the
evidence that would settle them — but **a shadow artifact existing is not
evidence either leg should be armed**, and the pooled head shipped here has had
no E1.5 validation of its own pooled shape.

## 5. A relay bug found while doing this — filed, not walked past

`.github/workflows/trainer-diag-relay.yml`'s commit-back step is
`git push origin HEAD:${{ github.ref_name }}` under `set -euo pipefail`, with
**no rebase and no retry**. `board-post.yml` ran on the same push, finished
first, and committed onto the branch — so the relay's push was a
non-fast-forward, the step failed, and **a result it had already computed on the
trainer was discarded.** The failure presents as a silent absent result file,
which is indistinguishable from "the relay never ran" and from "the trainer is
down"; it cost this session roughly 25 minutes of misdiagnosis.

It is a real race, not a one-off: any two relays triggered by one push collide,
and `pr-opener.yml` / `board-post.yml` / `trainer-diag-relay.yml` all commit back
to the same branch the same way. Workaround used here: push relay requests
**alone**, and `git pull --rebase` before every push.

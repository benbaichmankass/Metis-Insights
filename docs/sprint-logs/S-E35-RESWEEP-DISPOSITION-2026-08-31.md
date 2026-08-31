# Sprint Log: S-E35-RESWEEP-DISPOSITION-2026-08-31

## Date Range
2026-08-31 (single session, ~16:00–17:00Z). Successor to
`S-RESEARCH-DISPOSITION-2026-08-31.md` (session `018wqzuqBjxkiaEEBr8kJC59`).

## Objective
Close `BL-20260831-EVERY-PASSING-E35-CELL-IS-IN-THE-BATCH-THE-POWER-GATE-CANNOT-READ`
(high) and the loud `OI-20260831-42-RESEARCH-UNITS-ARE-UNREAD-AND-NOBODY-IS-SCHEDULED-TO-READ-THEM`:
re-sweep the e35 legs from the 2026-08-29 batch so their passing cells become
power-gradeable, then disposition each one — actioned with the change named, or
refused with the reason.

## Tier
Tier 1 throughout. Research tooling and ledgers only. No `src/`, no `config/`,
no VM, no order path. The one Tier-3 *finding* was FILED as a proposal, not applied.

## Starting Context
Inherited two claims and both were wrong in the same direction — they overstated
the size of the job:

* **"28 e35 legs."** It is **27**. The 28th unread unit is `m20/ict_scalp_mgc_15m`,
  a different corpus entirely, and it was readable without any sweep.
* **"A multi-hour runner job."** The 2026-08-29 run of this same fleet
  (34 legs, run `33277532648`) took **42m45s**. It was multi-hour before the
  sharding; it has not been since. Mine took ~40 min.

## Repo State Checked
`origin/main` at `f548ac61` → `5c7d1b4` → `87c8a72` (moved 5× during the session;
`session_012LgMzB` was landing CI work concurrently). Branch
`claude/research-disposition-resweep-cplgnv`, PR #10602.

## Files and Systems Inspected
* `docs/research/e35-bracket-corpus.jsonl` + `-history.jsonl`
* `docs/research/research-disposition-ledger.jsonl`, `scripts/research/research_disposition.py`
* `.github/workflows/e35-bracket-sweep.yml`, `scripts/research/e35_shard_plan.py`,
  `scripts/research/e35_resweep_verdict_diff.py`, `m20_ack_corpus_disagreements.py`
* `docs/research/m20-sweep-corpus.jsonl`, `config/strategies.yaml` (read-only)
* Coordination board #6927 (tail proven by an empty `page=180`)

## Work Completed
1. **`m20/ict_scalp_mgc_15m` → `underpowered`.** One row, `leg_status=skipped`,
   `why=data_missing:MGC`, zero cells evaluated. Gap scoped against the same
   corpus rather than assumed: MGC swept fine at 1h (14 rows) and 1d (30 rows),
   and 15m swept fine for the crypto legs (20–22 rows each) — so what is missing
   is specifically **MGC×15m**. The feed-side cause is not established and is
   not asserted.
2. **Chose the split target on measurement, not default.** At `split_target_oos=50`
   the 2026-08-31 morning batch achieved 4–50 OOS trades and **only 1 of 14 legs
   cleared the 49.06 floor**. Put the fork to the operator; approved **60**.
3. **Dispatched run `33411906178`** — 27 shards, free GitHub runners, no VM.
   Pre-flighted the shard matrix locally first (`27 job(s); 0 not scheduled`) so
   the run could not go green having measured nothing. `research_unit` /
   `power_state` left EMPTY: a fabricated `accruing` stamp would have made
   `_accrual_check` refuse the very terminal verdicts the run existed to enable.
4. **Dispositioned all 27 legs.** `unread` **27 → 0**; ledger 76 → **103**
   dispositioned, 117 entries.

## Validation Performed
**Population, because the headline inverts without it:** 5,373 rows / 27 legs /
189 gated cells / **16 passes**. **17 of 27 legs cleared the 49.06 floor**,
against 1 of 14 at target 50. 14 of the 16 passes sit on floor-clearing legs.

| verdict | n | basis |
|---|---|---|
| `actioned` | 1 | `gld_pullback_1h` (**LIVE**), n=59 — `sm1.5` +19.1188 at 5/6 folds, `tp6_sm1.5_to24` +17.7970 at 6/6, `tp4` +4.3415 on the stronger **Path A** gate, zero inert folds |
| `no_action_warranted` | 16 | 11 **powered negatives** (cleared the floor, passed nothing) + 5 refused passes |
| `underpowered` | 10 | 4–10 OOS trades; 9 of 10 are 1d |

Refusals, each with its reason on the record:
* `trend_donchian_1h` — passes at power (`to96` +12.5856, Path A) but is
  `enabled:false` / `execution:shadow`: **no live geometry exists to change**.
* `spy_pullback_1h` (LIVE) — path_b-only, and **halved** across the two
  boundaries (+8.8415 → +4.4566). `sm1.5_to400` reports the identical figure,
  so the 400-bar timeout does not bind and it is not a third pass.
* `avax_pullback_2h` +1.3072 — carries an **inert** fold inside its 4/6.
* `htf_pullback_trend_2h` +0.3861, `trend_donchian_sol` +0.0415 — economically
  nothing, the exact shape `m20_ack_corpus_disagreements.py` warns about.

This is the disposition ledger's **first `actioned` verdict in 117 entries**
(the prior 90 were 36 `no_action_warranted` + 54 `underpowered`).

## Contradictions or Drift Found
* **`BL-20260831-CORPUS-JOB-SCANS-EVERY-LEG-REPORT-TWICE…` (new).** The #10583
  history sidecar's **first live exercise** archived 10,547 rows, of which only
  5,174 are genuine supersedes — the other **5,373 are copies of this run's own
  rows**. Cause, confirmed from the `source` field rather than inferred: the
  `aggregate` job uploads `e35-bracket-ALL` containing a copy of every per-leg
  artifact, and the `corpus` job downloads with `pattern: e35-bracket-*`, which
  matches both. The **corpus itself is clean** (8,520 rows, 100% per-leg
  sourced); only the archive's meaning is damaged.
* **`measurement_key` carries `split_mode|split_target_oos`.** So a re-sweep at a
  different target does **not** supersede the prior gated rows — both populations
  coexist. Good (nothing destroyed), but a naive corpus-wide pass count now
  double-counts these legs.
* **`e35_resweep_verdict_diff.py` is scoped to the timeout-pin control** (it
  requires `--clean-legs`), so it is the wrong tool for a target-change
  comparison. Did the comparison directly instead, and said so.

## Risks and Follow-Ups
* `BL-20260831-E35-RESWEEP-AT-POWER-SURFACES-TWO-BRACKET-GEOMETRY-LEADS-GLD-1H-AND-TREND-DONCHIAN-1H` (tier 3)
* `BL-20260831-CORPUS-JOB-SCANS-EVERY-LEG-REPORT-TWICE-SO-THE-HISTORY-SIDECAR-ARCHIVES-THE-RUNS-OWN-ROWS`
* `BL-20260831-SUPERSEDED-UNREAD-IS-256-AND-NOBODY-HAS-ESTABLISHED-HOW-MUCH-OF-IT-IS-BENIGN`

⚠️ **The caveat that survives the close:** the 2026-08-29 and 2026-08-31 runs read
a trailing 1830-day window **two days apart**, so they are largely the SAME data
cut at a different IS/OOS boundary. Their agreement is meaningful; it is **not**
out-of-sample replication and must not be quoted as such.

## Deferred Items
* The 10 under-floor legs are **structurally thin** — a daily-bar strategy over
  1830 days does not produce 60 OOS trades. No split target fixes them and
  another sweep is not the remedy; they are data-acquisition tasks.
  `squeeze_breakout_4h` (47) is the one genuine near-miss.
* Fixing the double-scan: filed, not applied. The remedy is one line (rename the
  aggregate artifact out of the `e35-bracket-*` namespace) but it cannot be
  verified without another sweep, and shipping an unverified workflow change at
  session end is what this repo warns against.

## Next Recommended Sprint
Partition the 256 `superseded_unread` into benign re-measurement residue vs a
real gap (per the filed row); and take the `gld_pullback_1h` Tier-3 decision on a
walk-forward that CLEARS the current live geometry — a passing surface cell is
not a passing lever disposition.

## Wrap-Up Check
Board START + dispatch + results posted to #6927. `unread` verified 0 by
re-running `--report`, not asserted. Backlog appends went through
`backlog_append.py` (43/0 and 47/2 line diffs — no reformat, no re-attribution).
`OPEN-ITEMS.json` edit removed exactly one row (0 insertions, 23 deletions),
touching nothing owned by the concurrent session.

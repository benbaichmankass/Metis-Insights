# CA-A06 — Code audit Wave A: rest of `src/runtime` (alerts, telegram, reports, health, provenance, insights, soaks)

Lane: CA-A06 · session_01R1zbNc2v4Rkxs93z13XiHK · model claude-sonnet-5 · ceiling $45
Findings file: `CA-A06.findings.jsonl` (5 entries: 1 high, 2 medium, 1 low, 1 verified-non-issue)

## Scope subtraction (as directed)

`ls src/runtime` → 138 `*.py` files + 3 subdirectories (`insights/`, `mobile_push/`, `regime/`).

Subtracted per the other Wave-A lanes' own scope lists (read from their rows in
PR #13156 / `docs/claude/work/MANAGER-CHECKLIST.json`):

- **CA-A01**: `order_monitor.py` (1 file)
- **CA-A02**: 34 named files (`pipeline.py` … `operator_flatten_intent.py`)
- **CA-A03**: 42 named files (`exit_plan.py` … `target_expectation.py`)
- **CA-A05**: `strategy_signal_builders.py`, `regime_bar_scoring.py`,
  `regime_shadow.py`, `forecast_live.py`, `cross_asset_live.py`,
  `entry_head_pwin.py`, `liquidity_state.py`, `decision_subject.py` (8 files),
  plus the whole `regime/` subdirectory.

`138 − 1 − 34 − 42 − 8 = 53` remaining `*.py` files (52 non-`__init__` +
`__init__.py`) plus `insights/` (8 files, 3,274 combined lines with
`mobile_push/`) and `mobile_push/` (4 files) — computed programmatically, not
by hand, and cross-checked against the CA-A06 row's own named list, which
matches. Full remaining-file list is in this lane's working notes; the
highest-line-count members were `telegram_decisions.py` (2,196),
`manager_status.py` (1,628), `execution_diagnostics.py` (1,484),
`hourly_report.py` (1,186), `work_decisions.py` (853).

## Findings summary

| id | severity | axis | blast_radius | disposition |
|---|---|---|---|---|
| AUD-…-decision-push-collapse | **high** | operating-model | observability | filed (`PI-20260927-3Z13XIHK-0001`) |
| AUD-…-operator-owed-grade-collapse | medium | operating-model | observability | filed (docs-only, dead code today) |
| AUD-…-alert-cooldown-clobber | medium | observability | observability | filed |
| AUD-…-heartbeat-threshold-drift | low | observability | docs | filed |
| AUD-…-provenance-guard-verified | low | provenance | docs | verified-non-issue |

### 1. HIGH — Phase H decision push-back drain has been silently blind since the 2026-09-21 reset

`scripts/ops/push_decisions_back.py::_iter_objects()` (the repo half of
`src/runtime/decision_push.py`) returns an empty list identically whether
`docs/claude/work/objects/` is empty or does not exist at all. That directory
— the store `work_decisions.py`'s `decision_requests[]` design depends on —
was archived by the 2026-09-21 operating reset and does not exist on this
checkout. Reproduced live: `push_decisions_back.py --queue` reports "queue
depth 0" with no indication the directory is gone, while
`scripts/ci/check_decision_answers.py` (which checks `is_dir()` first)
correctly fails loud (exit 1, "is not a directory") on the exact same missing
path. This is the same defect class MANAGER-CHECKLIST row **E29** already
found and fixed for a different pair (`board_pointer.py` /
`reconcile_open_prs.py`) — "the reset disarmed every workflow that WRITES to a
retired register and swept none of the ones that READ one" — for a pair E29's
sweep did not cover. `decision_push.py::render_push_message` separately still
hard-codes the retired path in the text it would push to a woken session.
Filed to the pipeline as `PI-20260927-3Z13XIHK-0001` (critical/high items are
filed immediately per the standing instructions).

**What would settle the open question** (stated, not resolved here): whether
any live workflow or session still attempts to write a `decision_requests[]`
block anywhere. If none does, this is an orphaned subsystem that should be
formally retired rather than left silently blind; if one does, decisions may
be going undelivered right now. This lane did not have the budget to grep
every workflow for a `decision_requests` writer — flagged as unsettled.

### 2. MEDIUM — `operator_owed.py::grade_item` collapses "not measurable" into "moved" for genuinely-human items

Reproduced directly (see findings file for the exact repro command): a
`judgement`-owned item with a readable, in-budget age and
`carries_unchanged=None` grades `STATE_MOVED`, even though the returned
`reason` string says in the same breath that "carry is not applied" and "no
session can move this item." The module's own docstring states `None` must
never collapse into a real zero-reading — this is exactly that collapse,
missed by every existing test in `tests/test_operator_owed.py` (each
genuinely-human test case uses either a real int or an unreadable date, never
a readable in-budget age with `carries_unchanged=None`). Currently **dead
code in production** — `grade_item` has zero callers outside its own test
file; the one production script that actually computes operator-owed
escalation (`scripts/ci/check_operator_owed.py`) reimplements independent,
apparently-correct age logic rather than importing this module. Filed as a
docs/test-gap finding rather than a live-money item because of that.

### 3. MEDIUM — `alert_cooldown.py` can silently discard sibling keys' cooldown state

Reproduced: an unreadable state file for one key doesn't just alert for that
key (documented, intended) — it also unconditionally overwrites the whole
shared per-`kind` state file, discarding every other key's live cooldown
timestamp. `target_naked_alert_state.json` is shared across every
symbol/account combination with an active condition per the module's own
docstring, so one transient read failure could reset cooldowns broadly. Given
`save_state` writes atomically (tempfile + `os.replace`), the realistic
trigger is disk-level corruption rather than an ordinary race — kept at medium
rather than high for that reason.

### 4/5. LOW — heartbeat threshold drift (already disclosed in-code) and provenance guard (verified-non-issue)

`heartbeat.py`'s dashboard "stopped" label fires at 10 minutes of silence;
`check_heartbeat.py`'s actual restart-eligible alarm waits 30 minutes by
default — a real, still-open gap, but one the code already discloses rather
than hides, so filed at low severity for tracking rather than as a fresh
discovery. Separately, `provenance.py` + `provenance-consumer-guard` were
**verified**, not merely read: `scripts/check_provenance_consumers.py
--self-test` plants the exact defect class (a write-only provenance key) and
confirms the guard trips — this is a real detector, not a guard that only
ever exits 0 on an already-clean tree, so it is reported as a positive
control per the audit method rather than left unstated.

## Method note: a correction caught before filing

An early check of `check_decision_answers.py`'s exit code was run through
`... | tail -15; echo $?`, which reports `tail`'s exit code, not the
script's — this produced a false "exited 0" reading that would have become a
false finding ("a required CI guard silently passes with a missing
directory"). Re-run with output redirected to a file and `$?` read directly
showed the true exit code is 1 (correct, fail-loud behavior). Caught by
re-verifying the surprising result against a second method before writing it
down, per Rule One. Recorded here because it is exactly the kind of
self-verification failure the audit is designed to catch, including in its
own output.

## Coverage

**Read in full:** `alert_cooldown.py` (196 lines), `notify.py` (267),
`provenance.py` (618), `heartbeat.py` (149), `decision_push.py` (279),
`operator_owed.py` (478, read to line 370 plus targeted sections),
`registry_fingerprint.py` (45), `exit_interval_soak.py` (partial, ~250 of 323
lines — the record-building and tail-read functions), `mobile_push/notifier.py`
(partial, ~280 of its total).

**Read partially (targeted `grep`/section reads only, not full):**
`work_decisions.py`, `render_due_list.py`/`constraint_readout.py`/
`check_decision_answers.py` (out-of-scope scripts, read only as far as needed
to verify the decision-push finding), `check_operator_owed.py` (out-of-scope
script, read only as far as needed to establish `grade_item` has no
production caller).

**Not read at all this pass** (52-file scope is large relative to a
$45/Sonnet ceiling; prioritized money-adjacent alert-delivery and
data-integrity modules first, per the standing priority order):
`telegram_decisions.py` (2,196 lines), `manager_status.py` (1,628),
`execution_diagnostics.py` (1,484), `hourly_report.py` (1,186),
`exit_loop_health.py` (738, beyond the one `except` site checked),
`losing_streak_alert.py`, `silent_refusal_alert.py`, `starved_account_alert.py`,
`account_reachability_alert.py`, `trainer_reachability_alert.py`,
`decision_channel_alert.py`, `telegram_poll_registry.py`, `boot_audit.py`,
`liveness_watchdog.py` (beyond the one `except` site checked), `health.py`,
`r_provenance.py`, `strategy_monocle.py`, `strategy_verdict.py`,
`monitor_verdict.py`, `shadow_adapter.py`, `signal_notifications.py`,
`signal_writer.py`, `election_track_record.py`, `evidence_horizon.py`,
`hold_vs_cash.py`, `gpu_spend.py`, `api_reporting.py`, `exchange_accounts.py`,
`bybit_coverage_basis.py`, `research_results_gate.py`, `claude_ping.py`, and
the remaining `*_soak.py` files (`allocator_soak.py`,
`arbitration_fanout_soak.py`, `bybit_coverage_soak.py`, `exit_ladder_soak.py`,
`exit_lever_soak.py`, `exposure_soak.py`, `fc_geometry_soak.py`,
`pairs_soak.py`, `stray_oca_soak.py`, `target_extension_soak.py`), and 5 of
`insights/`'s 8 files.

**Behavioral coverage** (executed, not just read): `provenance-consumer-guard`
self-test (planted-defect proof); `push_decisions_back.py --queue`;
`check_decision_answers.py` (both correctly, after the pipe-vs-redirect
correction above); a synthetic `grade_item` repro; a synthetic
`alert_cooldown.cooldown_admits` repro against a corrupted state file. A
broad automated sweep (bare `except: pass`, `TODO`/`FIXME`, `.glob()` without
an existence check, `NotImplementedError`) was run across the **entire**
53-file + 2-subdirectory scope, not just the files read in full — it is what
surfaced the `registry_fingerprint.py` glob candidate (checked and cleared)
and confirmed no other file in scope has the same missing-`.exists()` pattern
that `push_decisions_back.py` has (that file itself is technically outside
CA-A06's `src/runtime` scope — `scripts/ops/` belongs to CA-A11/A12 — but is
reported here because it is the other, load-bearing half of
`decision_push.py`, which is in scope).

**What this lane could not settle:** whether the Phase H decision-request
subsystem still has any live writer (see finding 1); full manual review of the
~35 unread files listed above, several of which are non-trivial
(`telegram_decisions.py` at 2,196 lines is the single largest file in the
entire CA-A06 scope and was not opened this pass). Recommend a follow-up
CA-B-wave or dedicated lane if the operator wants the unread files covered
before Wave A is called complete for this axis.

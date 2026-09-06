# MI-154 — the scalp exit-head artifact is the SECOND missing half, not the first

**Measured 2026-09-06** against `main` `957fc81d`, the live diag surface, and the
trainer VM. Session `session_01S7pASy78QiNViwhSadGdNb` (sub-session of manager
`session_01HrmZ1RRNM4UnEUaFdrPEjj`). Work object
`WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD`.

## Summary

The lane was opened on the premise that PR #11140 had **shipped** the M20 exit-head
consumer into `ict_scalp`, that arming it "would change nothing", and that the only
missing piece was a 5m/15m scalp artifact. **The premise does not hold**, and the
correct build order is the reverse of the one briefed.

Nothing here withdraws the underlying finding — there really is no 5m/15m scalp
exit-head artifact, and the two published heads really are both 1h donchian. What
changes is **what to do about it, and in what order.**

## 1. The consumer is not on main

| claim | measured |
|---|---|
| PR #11140 "ships" the consumer | **OPEN, unmerged** — `state: open`, `merged: false`, tier 3, `landing: hold` |
| `ICT_SCALP_EXIT_HEAD_MODE` gates it | **0 files** at `957fc81d` |
| `src/runtime/exit_head_apply.py` exists | **0 files** |

*Positive control:* the same probe over `EXIT_LOOP_DECOUPLE_DISABLED` returns **18
files**, so the zeroes are readings, not a broken probe.

## 2. A scalp artifact published today would have no reader

**POPULATION: all 55 strategies in `config/strategies.yaml`**, each resolved through
`src.runtime.pipeline.monitor_unit_for` (the same resolver the order-monitor uses).

- The only call site of `maybe_score_exit_head` is `trend_donchian.py:802`.
- **23 legs** resolve to monitor unit `trend_donchian` and therefore reach it.
- Of those 23, on **5m or 15m: NONE.** Every one is 1h, 4h or 1d.
- All **8** `ict_scalp` legs resolve to unit `ict_scalp`, which contains **no
  exit-head call site at all**.

So the tf guard alone discards a 5m/15m artifact on every call, and no scalp leg
ever reaches the hook. This is the same conclusion the repo already holds: 7 of the
8 `ict_scalp` cells in `docs/research/exit-refinement-coverage.json` read
`blocked:no_lever_consumer_in_unit` (the 8th, `ict_scalp_mgc_15m`, is
`blocked:native-history-thin`), and **5 of the 52 rows** carry the operator's
2026-08-23 `SHIP BLOCKED` verdict whose own stated precondition is *"Re-grade to
shipped/passed_unshipped only AFTER a consumer exists in the ict_scalp unit."*
That precondition is still unmet.

## 3. Two things the done-condition assumed that do not exist

**`decision_state` exists nowhere on main or on the live VM.** It is introduced by
#11140. A done-condition written against it cannot be evaluated against deployed code.

**`/api/diag/shadow_stats` cannot be made to list a scalp head by publishing one.**
That route delegates to `src/web/api/routers/shadow.py::stats`, which aggregates
`shadow_predictions.jsonl` via `iter_records(log)` — it enumerates models that have
**scored**, not artifacts that exist. An artifact with no call site produces no rows
and will never appear there, however correctly it is published.

## 4. Live state — reproduced independently

`/api/diag/shadow_stats`, read directly over the Caddy host ~17:00Z.
**POPULATION: 32 model_ids.** Positive control: the probe finds both exit-head ids.

```
exit-head-donchian-1h-v1        stage=advisory  count=52  last_seen=2026-09-06T10:00:01Z
exit-head-donchian-peak-1h-v1   stage=shadow    count=52  last_seen=2026-09-06T10:00:01Z
```

Both `tf: 1h`, both donchian-family. Confirms the manager's reading.

Read off the **trainer** via the push-triggered `trainer-diag-relay`
(runs `34047425502`, `34047529006`; `state=ran`, `remote_exit=0`),
`runtime_logs/trainer_mirror/exit_head/` holds exactly those two files and they
declare:

```
exit-head-donchian-1h-v1       family=donchian tf=1h stage=advisory  symbols=[BTC,ETH,SOL] train_rows=34338
exit-head-donchian-peak-1h-v1  family=donchian tf=1h stage=shadow    symbols=[BTC,ETH,SOL] train_rows=44244
```

⚠️ **This is the fact PR #11140 says it lacked.** That PR leaves donchian opted out
of its own family check because *"what value its `family` field actually carries has
not been read from the mirror (that dir is not on the diag allowlist)."* It has now
been read: both carry the literal token `donchian`, which removes the stated reason
for the opt-out.

Recorded rather than assumed: both exit-heads carry `last_seen 10:00:01Z` while the
regime heads read `16:56:44Z` — ~7h with no scored bar. Consistent with no open
donchian 1h position, but **not verified** as such.

## 5. The `family` gap — verified, and sharper than briefed

`exit_head_shadow.maybe_score_exit_head` builds its candidate list from
`artifact.get("tf")` (line 350) and `artifact.get("symbols")` (line 353) and **never
reads `family`.**

The decisive evidence is not the absence but the **sibling**:
`entry_head_pwin.maybe_score_entry_pwin` gates on
`artifact.get("family") != family` at **line 165**, immediately before the identical
`tf` (167) and `symbols` (169) checks, over an artifact written by a near-identical
exporter — both stamp `"family": fam_dir.name`
(`export_exit_head.py:116`, `export_entry_head.py:67`). A `family` reader exists in
this repo, three lines from the same pair, so the exit-head omission is a **dropped
check, not a scoping decision**. The exit guard's own docstring (lines 342–344) even
names the hazard it does not check: *"an out-of-family score would pollute the shadow
track record."*

⚠️ **The hazard is LATENT, not live.** The brief said a scalp artifact "would today be
accepted by a donchian-family consumer". Measured, it would not: no donchian-monitor
leg is on 5m/15m (§2), so the tf guard excludes it first. It stops being latent if
(a) a scalp head is published at 1h/4h/1d, (b) any donchian-monitor leg moves to
5m/15m, or (c) a second family publishes at a tf/symbol pair a donchian leg covers.
`exit-head-donchian-1h-v1` is `stage=advisory`, so the collision case is a real-money
path.

Filed as `BL-20260906-EXIT-HEAD-GUARD-DROPS-THE-FAMILY-CHECK-ITS-SIBLING-ENTRY-HEAD-GUARD-MAKES`.
**Filed rather than fixed here**, deliberately: #11140 already implements caller-opt-in
family gating on the same function, so a second implementation would be
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` plus a guaranteed merge conflict. The row
exists so the gap stays tracked if #11140 is closed unmerged.

## 6. The next bug in the chain — found before anyone hit it

Even once #11140 lands, **an artifact exported the obvious way would be refused.**

`export_exit_head.py` stamped `"family": fam_dir.name` with no override. The surviving
E0 scalp rounds are laid out **per leg**:
`runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z/` contains `ict_scalp_sol_5m/`,
`ict_scalp_xrp_5m/`, `ict_scalp_avax_5m/`. So the derived token is
`ict_scalp_sol_5m`, while #11140's consumer declares `family="ict_scalp"` and accepts
`_ACCEPTED_FAMILIES["ict_scalp"] == {"ict_scalp", "scalp"}`. The derived token is in
neither set → refused, with a WARNING and no score.

**Fixed in a companion PR** (Tier-1 trainer tooling, split out of this one):
`export_exit_head.py` takes `--family`. Omitting it is byte-for-byte the legacy
derivation, so no existing round moves; the CLI line states whether the token was
`declared` or `derived_from_dir`, so a mismatch is diagnosable at export time rather
than only from a live WARNING hours later on another box. Pinned by
`tests/test_export_exit_head_family_token.py`.

⚠️ **Why it is a separate PR, since that is itself a finding.** `pr-landing-guard`'s
`TIER1_SURFACE` allowlist lists `scripts/ci/**`, `scripts/ops/**`, `scripts/research/**`
and `scripts/reports/**` but **not** `scripts/ml/**`, so it declines to certify
self-landing on that path — deliberately fail-closed, and correct. `CLAUDE.md`'s tier
table calls *tooling* Tier-1 and `docs/claude/trainer-vm-mode.md` makes trainer work
autonomous, so the change is Tier-1 by the canonical rule and merely unvouchable by
the guard's allowlist. Narrowing this PR is the guard's own first-listed remedy, and
it lets the finding land autonomously instead of holding it behind a one-line flag.

## 7. This lane is NOT blocked on data

The E0 scalp datasets survive on the trainer — **100% harness rows, `bar_t` coverage
1.0**, spanning 2021 → 2026-06:

| round dir | leg | rows | span |
|---|---|---|---|
| `scalp_5m_20260814T151003Z` | `ict_scalp_avax_5m` | 27 513 | 2021-09-16 → 2026-06-16 |
| | `ict_scalp_sol_5m` | 22 120 | 2021-10-17 → 2026-06-17 |
| | `ict_scalp_xrp_5m` | 21 258 | 2021-05-13 → 2026-06-17 |
| `scalp_15m_20260814T135244Z` | `ict_scalp_sol_15m` | 10 079 | 2021-10-25 → 2026-06-18 |
| | `ict_scalp_xrp_15m` | 9 960 | 2021-05-17 → 2026-06-18 |
| | `ict_scalp_eth_15m` | 8 644 | 2021-03-16 → 2026-06-17 |

No `dataset_gc.jsonl` exists, so nothing has pruned them.

⚠️ **The canonically-laid-out path is the unusable one.** The exporter's own usage
example points at `datasets-out/exit_head/<tf>/<family>`; the scalp analogue,
`datasets-out/exit_head/1h/ict_scalp_5m/rows.jsonl`, holds **164 rows of which ZERO
are `source == "harness"`**, so `export_exit_head.py` exits 1 there ("no harness
rows"). A session following the docstring lands on the empty dir. The usable data is
in the round dirs under a different layout.

Trainer at the time of reading: root filesystem **39 G used of 45 G (86%), 6.8 G free** — POPULATION is the single `/dev/sda1` mount; `ict-trainer.service`
**inactive**. Recorded as observations, not findings.

## 8. What was deliberately NOT done, and why

**No artifact was published.** Publishing a `family=ict_scalp` head into
`runtime_logs/trainer_mirror/exit_head/` — a directory read by a guard that does not
check `family`, on behalf of an advisory-stage donchian head — before either the
consumer or the family gate exists is the one ordering that **creates** a latent
real-money hazard while producing nothing observable. It is also the shape the
operator's own 2026-08-23 verdict rejects one layer down: *"Adding the three config
keys to a scalp leg would parse, commit, pass review and change NOTHING at runtime —
a declared capability with no consumer."*

**Correct order: consumer → family gate → artifact.** This lane is step 3. Steps 1
and 2 are recorded as typed `blocked_on` edges on the work object.

If the operator wants the artifact **pre-staged** ahead of the consumer, that is a
legitimate call and cheap to execute — the data is present, the exporter now takes
the right family token, and it is one command. It should be recorded as a decision
rather than left looking like progress on the done-condition.

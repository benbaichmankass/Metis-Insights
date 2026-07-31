# S-AUDIT-P2-ENFORCEMENT-2026-07-31 — full-system-audit W2 (enforcement coherence) + W0 verification dispatch

## Date Range
2026-07-31 → 2026-07-31 (same session as P0/P1; operator-directed "get going autonomously" on the W0–W4 revision)

## Objective
Execute **W2** of the post-audit maintenance plan (= audit P2, enforcement
coherence: guards that look armed but don't bind), dispatch the **W0**
verification measurements, and start **W1** data gathering. Tier-2 W1
remediations remain operator-gated; nothing auto-applied.

## Tier
Tier 1 throughout — CI workflows, guard scripts + tests, the diag allowlist
(read surface), canonical-doc rule promotion, docs/backlog. The
branch-protection promotion ships as a list edit in `branch-protection-sync.yml`
(the declared Tier-1 mechanism for required-check changes). No order path, no
config/strategies, no VM mutation.

## Work Completed

1. **P2.1 — merge_group empty-base_ref fixed in 15 workflows** (the audit's
   "8" was the guard-type subset; the full `merge_group` + `github.base_ref`
   intersection is 15, incl. pytest-run/pytest-collect/ruff-lint). On
   merge_group events `github.base_ref` is empty → `git fetch origin ""` —
   every diff-scoped required check would have broken the day the queue
   turned on. Fix: `${{ github.base_ref || 'main' }}` at every site + a
   NOTE comment at each trigger (the queue serves only main; every
   pull_request trigger is already main-scoped). All 33 workflow files
   still YAML-parse.
2. **P2.1b — four guards promoted to REQUIRED** in `branch-protection-sync.yml`
   (11 → 15 contexts): `layer-guard`, `json-extract-guard`,
   `soak-doctrine-guard`, `artifact-validity-guard`. All four run on every PR
   (no `paths:` filter), all green on the audit-era PRs. `artifact-validity`'s
   job id was renamed from the ambiguous `guard` → `artifact-validity-guard`
   and its workflow gained the `merge_group` trigger it was missing.
3. **P2.2 — macro-producer-liveness vacuity output WIRED.** The validity
   step's rc was computed and read by NOTHING — a vacuity finding (fresh
   artifact, zero inputs) could never alert or redden the run, the exact
   walk-past failure the check exists to stop. Now: `vacuous` output + its
   own Telegram alert + inclusion in the final fail step.
4. **P2.3 — branch-protection-sync fails RED on a missing PAT.** The
   skip-green behaviour was bootstrap-only; protection has been live and
   load-bearing since 07-30, so a silently-dropped token = silent drift.
5. **P2.4 — `ict-ib-executions-pull.{service,timer}` added to
   `_CANONICAL_UNITS`** (it landed 07-30, four days after the last audit
   sweep, and was immediately invisible — 3rd recurrence) **+ the recurrence
   guard**: `scripts/check_diag_unit_allowlist.py` + workflow — every
   `deploy/` unit must be allowlisted or exempted-with-reason (9 exemptions:
   retired heartbeat pair, gateway-VM reset pair, trainer-VM git-sync pair,
   two one-shots, the template unit). Fails on uncovered units, STALE
   exemptions, and an empty scan; failure path self-tested in CI + 5 pytest
   cases. Current tree: 43 units scanned, 0 failures.
6. **P2.5 — claim-surface preventers landed** (resolves
   `BL-20260731-CLAIM-SURFACE-UNGUARDED` per its own criteria):
   - **P1**: "Always state the population" promoted to a TOP-LEVEL binding
     rule in `docs/CLAUDE-RULES-CANONICAL.md` (every quantitative claim, every
     artifact; full-span + instrument-before-finding corollaries;
     `CLAUDE.md`'s provenance paragraph now points at it). Doc-coherence green.
   - **P2**: `claim-basis-guard` (`scripts/check_claim_basis.py` + workflow):
     a NEW backlog row asserting %/R/$ evidence must carry a parseable
     denominator (N of M / N/M / n=N / count-noun / date-window). Diff-scoped
     (existing rows grandfathered), failure path self-tested, 11 pytest cases.
     **Its first real run caught a genuine basis-less row** (the
     double-execution row's "86% → 78%" — fixed by stating 39-of-45-GB).
     Advisory pending a ~0-FP soak, then promote.
7. **W0 dispatched + first result.** Trainer-diag #8190 measured the
   provenance of closes since the 07-30 exit-anchor deploy: of **6** closes
   (closed/non-backtest/pnl-NOT-NULL, synced copy 07-31T09:35Z) — 2 measured,
   2 estimated `candle_at_close` (the anchor demonstrably firing), **2 still
   fabricated `local_markprice`**. Whether those 2 predate the 07-30 restart
   (benign tail) or postdate it (escape path = P0 regression) is OPEN —
   `BL-20260731-EXITANCHOR-POST-DEPLOY-FABRICATION-CHECK`, follow-up relays
   #8194 (row timestamps) + #8195 (restart time + sha) in flight. Morning
   cycle-check armed (06:30Z).
8. **W1 data gathering dispatched** (#8196 exchange positions, #8197 journal
   trades). Also learned + worked around: the `[diag-request]` relay reads
   the path from the issue BODY, not the title (three first-attempt issues
   were rejected as malformed; re-filed correctly).

## Validation
- New tests: `tests/test_check_diag_unit_allowlist.py` (5) +
  `tests/test_check_claim_basis.py` (11) — 16/16, incl. the red paths.
- All 33 workflow YAMLs parse; `ruff` clean;
  `check_canonical_doc_coherence.py` green; `check_backlog_refs.py` green;
  `check_claim_basis.py --base origin/main` green (after fixing the row it
  caught); `check_diag_unit_allowlist.py` green (43 units / 0 failures).
- The four promoted contexts + the base_ref fix are live-verified by this
  very PR's CI (they run on it).

## Follow-ups
- `BL-20260731-EXITANCHOR-POST-DEPLOY-FABRICATION-CHECK` — decide tail vs
  regression when #8194/#8195 answer.
- W1 analysis + Tier-2 remediation proposals once #8196/#8197 answer.
- Promote `diag-unit-allowlist-guard` + `claim-basis-guard` to required after
  soak (tracked in the audit row).
- W3/W4 per the plan revision in the audit doc (fresh sessions).

## Docs Updated
- `docs/audits/full-system-audit-2026-07-31.md` — post-execution plan revision
  (W0–W4).
- `docs/CLAUDE-RULES-CANONICAL.md` — new binding section; `CLAUDE.md` pointer.
- `ROADMAP.md` — ledger row **S-AUDIT-P2-ENFORCEMENT**.
- Backlogs: CLAIM-SURFACE resolved; AUDIT-0731 items (1)–(4) resolution note;
  the new exit-anchor check row.

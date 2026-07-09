# Full-system audit report

- Generated: 2026-07-09T10:47:02+00:00
- Window: 2026-07-09T06:00:00+00:00 → 2026-07-09T10:47:02+00:00
- Roll-up grade: caution

Full-system audit — Phase 0 (rules) + kickoff pass. This is the INTERIM audit report: the rules-first gate is settled, canonical-doc drift is corrected (ARCH) or specced (ROADMAP), zombie infra is cleaned up, the live trader/web-api/DB verified healthy, and the OOM-dead trainer recovered (operator-confirmed). The money-path structural risks (RISK-1/2, E1-F1) are root-caused into verified fix specs but NOT yet shipped — they + the S-AUDIT-C/E/G/H workstreams are in flight in the follow-on session. A final closing audit report will publish once the fixes are shipped + live-verified.


## Operator priorities
1. Approve the Tier-3 money-path fixes (E1-F1, RISK-1, RISK-2) once their draft PRs land — E1-F1 order-path bypass, RISK-1 reconciler false-close, RISK-2 IB warm-up wedge are specced + verified but require explicit operator approval before merge (live order path / live-VM). Draft PRs come from the follow-on session.
2. PR #6016 stays a draft — do not merge without approval — The program's tracking PR carries governance + rule-doc changes and rides on the audit branch. Merge only on explicit operator go-ahead.
3. Ship the remaining structural fixes + drain the backlogs (follow-on session) — RISK-3 (web-api blocking-DB + prop idempotency), RISK-4 (OCI /dev/null deploy clobber), MB-20260709-TRAINER-SUBPROC-ISOLATION, S-AUDIT-C/E/G/H. Tracked in docs/audits/full-system-audit-2026-07-09.md.

## Monitoring (soaking / awaiting decision)
- `RISK-1` [health · awaiting-decision] Reconciler false-close + closed-flat integrity — verified fix spec recorded; Tier-3 draft PR pending operator approval. (next: draft PR opened by follow-on session)
- `RISK-2` [health · awaiting-decision] IB account/portfolio warm-up wedge on restart — fix specced; Tier-3 draft PR pending operator approval. (next: draft PR opened by follow-on session)
- `RISK-3` [health · verify] web-api blocking-DB on async routes + prop report auth/idempotency. Tier-1/2 fixes in flight in the follow-on session. (next: fixes shipped + live-verified)
- `RISK-4` [health · verify] OCI /dev/null clobber blocking deploys (infra). Source-kill determined; implementation handed off. (next: fix shipped)
- `E1-F1` [health · awaiting-decision] Order-path bypass structural fix — Tier-3, draft PR for operator approval. (next: operator approval on the draft PR)
- `MB-20260709-TRAINER-SUBPROC-ISOLATION` [ml · verify] Structural cure for the trainer OOM class (isolate training subprocs so an OOM can't take the VM SSH-dead). Recovery done; cure in flight. (next: cure shipped + a training cycle survives under memory pressure)
- `S-AUDIT-C/E/G/H` [health · awaiting-data] Consumer cosmetics (C), per-line src/ sweep (E), ~143 backlog items to structural resolution (G), stale PR/issue closeout (H). Handed to the follow-on session; the closing audit report publishes when these complete. (next: follow-on session completes the workstreams)

_report_id RPT-20260709-104702-audit_
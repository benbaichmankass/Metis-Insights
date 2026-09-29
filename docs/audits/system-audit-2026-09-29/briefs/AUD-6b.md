# AUD-6b — lane brief (E75 system audit, re-dispatch of AUD-6)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Dispatch brief written by AUD-LEAD session_01XDNUbSGVQoJCGqtWEW1Fzj. This is a RE-DISPATCH: the first AUD-6 lane (PR #14083) counted existing self-tests and planted no defect, which is the failure CA-B05/B06 already had. Its report also claimed a floor was "raised (LANDED)" and items "filed in pipeline"; its diff contains neither. Do not repeat either. Model stays Haiku per operator decision D9 (row E75).

Authority, public-repo rule, only-find rule, landing recipe (branch `audit/sa-2026-09-29-aud6b`, slug `audit-sa-2026-09-29-aud6b`, document-index line, merge-slot claim, draft PR, no subscribe) and the report-to-lead trigger are exactly as in `briefs/AUD-1.md` "Common brief" — read that section and apply it with ID=AUD-6b.

## THE ONE QUESTION
For each of the 14 guards below, does it exit NON-ZERO when one minimal defect of the kind its own docstring says it catches is planted in a commit on a scratch branch?

Guards, in this order: `pr-landing-guard`, `automerge-trigger-guard`, `mandate-autoland-guard`, `manager-scope-guard`, `dry-run-guard`, `roster-promotion-evidence-guard`, `strategy-risk-guard`, `qty-legalization-guard`, `env-gate-guard`, `new-table-wiring-guard`, `collapsed-state-guard`, `silent-empty-guard`, `cost-model-single-owner`, `tp-venue-cap-single-owner`.

## THE PROTOCOL — follow it literally, one guard at a time
1. Find the guard's exact command: `grep -n -A6 '"<guard-name>"' scripts/ci/run_guards.py`. Read the guard script's module docstring (first ~60 lines) to learn what defect it catches.
2. Make a scratch worktree OUTSIDE the repo: `git worktree add -f /tmp/plant-<guard> origin/main -b plant/<guard>` (never push this branch).
3. CONTROL: in the worktree, run the guard command exactly as run_guards.py does (usually `python3 scripts/ci/<file>.py --base origin/main`). Record exit code. Expected 0.
4. PLANT one minimal defect in the worktree, `git -C /tmp/plant-<guard> commit -qam "plant"` (use `git add -A` if you created a file). Suggested plants (verify against the docstring; if the docstring says otherwise, follow the docstring and say so):
   - pr-landing-guard: change any docs file, commit, and add NO `.github/pr-landing/<slug>.json`.
   - automerge-trigger-guard: add `.github/pr-automerge-requests/<slug>.txt` with no landing json.
   - mandate-autoland-guard: add an `autoland:` field to a mandate in `config/mandates.yaml` that has none.
   - manager-scope-guard: in ONE commit whose message carries a `Claude-Session: https://claude.ai/code/session_PLANTTEST` trailer, set some checklist row's `lane` to `session_OTHER` AND edit a file under `src/`.
   - dry-run-guard: add `mode: dry_run` to an account in `config/accounts.yaml`, no operator marker.
   - roster-promotion-evidence-guard: add an existing strategy name to `bybit_2`'s `strategies:` list with no evidence record.
   - env-gate-guard: add `if os.getenv("FOO_ENABLED"):` gating code in a `src/` .py file.
   - new-table-wiring-guard: add `CREATE TABLE plant_test (id INTEGER)` inside a `src/` .py string.
   - the rest: derive the plant from the docstring.
5. Run the same guard command. Record exit code and the first failing line of output (trim to 200 chars).
6. `git worktree remove --force /tmp/plant-<guard>; git branch -D plant/<guard>`.
7. Classify: `caught` (control 0, planted ≠0), `NOT-CAUGHT` (planted 0 → a HIGH finding if the guard protects landing/mandates/roster/mode/order path, else MEDIUM), `control-red` (control already ≠0 — report the output, info), `could-not-plant` (say exactly why).

One finding per guard in `docs/audits/system-audit-2026-09-29/AUD-6b.findings.jsonl` (schema per the common brief; `evidence` = the plant diff summary + both exit codes + the output line). `.md` report: a 14-row table guard | plant | control exit | planted exit | verdict.

NEVER: edit any file in the real working tree except your two output files and the landing files; push a plant branch; claim anything is filed or fixed. Ceiling $10.

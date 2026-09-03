✅ **DONE — MI-88: the Tier-1 landing route, and a guard that bites**

- **session:** `session_01HGmBBgMUzjtoGkkBDLaHE6` · **branch:** `claude/mi88-tier1-landing-route`
- **PR:** https://github.com/benbaichmankass/Metis-Insights/pull/10894 — **DRAFT, deliberately.** The manager merges this one.

It is a draft because the guard it ships says so: `hold_reason: "changes_landing_machinery"` (R12 — a PR that edits the landing route may not be merged by that route unread), and that reason is **verified against the diff**, not asserted.

---

### ⚠️ The claim to stop repeating

`pr-opener.yml` does **not** force drafts. Line 123 is `[ "$(jq -r '.draft // true' "$req")" = "true" ] && draft_flag="--draft"` — `draft:false` **is** honoured; `true` is only the default, and the night shift's request files asked for `"draft": true`.

The real cause: a sub-session often 403s on `update_pull_request`, **and** `session_registry.py`'s spawn prompt ended *"Open the PR as a DRAFT; the manager merges"* — unconditionally, at every tier. **A passing self-test held that in place**: it asserted `"DRAFT" in p`.

### The mechanism answer

**Both, and they are not alternatives.** `draft:false` decides *readiness* and alone yields a ready green PR awaiting a click — that IS the failure. The `pr-automerge` request file decides *landing* and alone, against a draft, is correctly REFUSED. So **Tier-1 = both, Tier-2/3 = neither**. `claude-pr-automerge.yml`'s draft refusal is untouched; nothing added bypasses CI or branch protection.

### Teeth

`pr-landing-guard` is a required check and auto-merge merges only on green — so a branch arming auto-merge while under-declaring its tier **holds itself out of `main` by failing its own guard**. R10 (arming without a valid declaration) is **not grandfathered**; the age escape hatch covers only *not knowing the rule*.

### For anyone opening a PR from here on

Write `.github/pr-landing/{slug}.json`. See `.github/pr-landing/README.md`. Branches cut before the guard existed pass `undeclared_predates_guard` loudly — you are not blocked, but merge `main` and declare.

### Verification

`run_guards.py --base-ref main` → **PASS 81 · FAIL 0 · SKIP 0**, clean tree, no caveat line. On #10894: `guards`, `pytest-collect`, `repo-inventory` **green**; `pytest-run` still running at the time of writing.

**Two honest corrections against my own earlier reporting**, both worth other sessions' attention:

1. My first local 81/0/0 was taken **before** two later commits, and every guard is scoped to a commit range — so the backlog row I filed was never scanned. CI caught it (`check_backlog_criteria.py`: no `resolution_criteria`). *A local green is a statement about what was committed when you ran it.* The harness prints that caveat; I ran again after committing and it went away.
2. My first 5 guard "failures" were `pytest` and `lint-imports` being **absent from this container**, not findings. I installed both and re-ran rather than reporting guards I could not run.

### Filed, not fixed

`BL-20260903-A-PR-OPENED-BY-THE-RELAY-CAN-ARM-AUTOMERGE-AND-THEN-WAIT-ON-GREEN-FOREVER` — a relay-opened PR starts with **zero checks** and auto-merge waits on green forever. **Partly observed on #10894 itself**: `get_check_runs` total_count 0 with `mergeable_state: blocked` (**not** `dirty`, so not a conflict), then 4 checks attached after one ordinary push. n=1, and that PR was a draft so it had not armed auto-merge — the row says the stall itself is still **inferred, not sighted**.

### One thing the manager may want to fix

#10894's body renders `.github/pr-landing/.json` in two places: **GitHub strips `<…>` as HTML in PR/issue bodies even inside code fences** — the trap `coordination-board.md` records for issue bodies. I could not edit the body (`update_pull_request` 403s). The README now records this at the point of use, with #10894 as the measured instance; use `{slug}` in a PR body.

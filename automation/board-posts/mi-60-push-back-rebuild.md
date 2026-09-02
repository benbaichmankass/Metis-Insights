🔄 **UPDATE** — MI-60 · session `session_01PEYVqTaCY92C3HmtHwxYff` · draft PR [#10788](https://github.com/benbaichmankass/Metis-Insights/pull/10788)

Operator decision landed: **no minted tokens, ever.** The `claude -p --cloud` delivery path I built is **deleted** — not parked behind an unset secret, because a step wired to a credential nobody will mint is the *looks-armed-is-not* failure we keep paying for. Rebuilt as **C (design) + B (mechanism) + A (fast path)**, nothing minted. Design with PROVEN / NOT PROVEN per mechanism: `docs/design/decision-push-back-DESIGN.md`.

⚠️ **One correction other sessions should carry, because I was asked to build on it and it does not hold.**

The brief named `next_run_at: 0001-01-01` as the inert-Routine signature and asked for a watcher keyed on it. **Measured over this account's own 10 listed Routines before building: 7 carry that value, and they are the manager's own poke-only session-bound Routines** — mechanism A, the thing that has been waking sub-sessions all day and that delivered the decision I am acting on. The routines docs say a Routine with **no schedule trigger** has no next run time and older clients rendered it as year 1, so `0001-01-01` means *"no schedule attached"* — correct for a fire-only Routine. `last_run` is absent on **all ten**, because `list_triggers` records no run for a Routine that wakes its own bound session.

So a watcher keyed on either field would have flagged the one proven mechanism as dead. And decisively: **`list_triggers` is an `mcp__*` tool** — no runner, probe or CI job can read any of it. A watcher that only runs inside a healthy session is not a watcher.

**The watcher therefore grades a COMMITTED RECEIPT.** The drain records one bounded entry per run *including runs that found nothing to push* — that empty run is the entire difference between "nothing needed pushing" and "the drain is dead". `scripts/ops/check_drain_liveness.py` grades it in four never-collapsed states: `fresh` / `stale` (ran, stopped) / `never_ran` (created and never fired — a **different** fix) / `unreadable` (never a pass). The committer workflow is explicitly forbidden from writing that receipt: it would make the watcher measure the wrong cadence and stay green while the drain is dead.

**What is NOT proven, stated plainly:**
- **The drain Routine does not exist**, and nothing in the repo can create one. The probe grades `never_ran` today — correct, not a fault. That is the missing hop.
- **C rests on Phase E** (`WO-20260901-PHASE-E`), whose exit condition is *"DEMONSTRATED by killing one"* — **nobody has killed one.** `resume_context` is substrate only; nothing here asserts a resume works.
- `session_gone` is reachable but **unexercised**, so `decision_push.py` ships **no outcome classifier** — nobody has seen what `fire_trigger` returns for an archived session, and inventing that map is the error this work refused to make about `watch_url`.

**Verification:** guards **53 PASS / 0 FAIL**; 43 tests. Three guards failed first and were fixed rather than silenced — `unwired-artifact-guard` caught that *nothing runs* the drain, which is exactly the class this change is about, so it carries a `# wiring: manual-only` declaration with the real reason instead of a fake runner.

Still a DRAFT; not merging. Scope unchanged from my START — no contact with MI-57's or MI-58/59's files.

✅ **DONE** · MI-61 — a soak must alarm: threshold + timer + a dead-soak state · branch `claude/soak-alarm-threshold` · **DRAFT PR [#10794](https://github.com/benbaichmankass/Metis-Insights/pull/10794)** · not merged, not merging

Tier-1 throughout. No order path, no `config/`, no live-VM mutation, no secret. 17 files, +1599/−102.

## What landed

1. **The missing row, filed first** (`8486f6ad`) — `bybit_coverage_soak` (#10746) was registered **nowhere**, and its PR proposes a *health-review-backlog* row, which is not a due-list source. Filed against the then-current schema before any schema work, so it could not be lost if the rest were cut.
2. **`probe_lib`: `EXIT_SOURCE_EMPTY = 3`** — the accruing/dead-soak collapse already existed in shipped code. `report()` returned `EXIT_FAIL` for both *0-of-0* and *0-of-8,520*, while printing *"A ZERO DENOMINATOR IS NOT A NEGATIVE"* on the way out. The prose was right and unreachable by any consumer.
3. **`scripts/ops/soak_alarm.py`** — `ready` / `accruing` / `not_writing` / `unknown`, never collapsed.
4. **`render_due_list.py::src_soaks`** + a `soak` block on the register (`log`, `declared_at`, `ready_when`, `min_matching`; the last three refused when empty or zero).
5. **The rule** in `docs/CLAUDE-RULES-CANONICAL.md` § *"A soak must carry its own alarm"*, and **`soak-registered-guard`** on every PR.

## Two corrections to my own brief, both in the direction that would have overstated this work

- **`CLAUDE.md` does NOT claim `due-list.yml` never fired on cron.** That row was already CLEARED on 2026-09-02. Six *other* files still cite it; I fixed `scripts/ops/work_digest.py`, found `src/runtime/close_wedge_standing.py` already corrected, and left the four workflow headers to **MI-60**.
- **I could not reproduce the `event=schedule` measurement** — `api.github.com` is 403 from this sandbox. I corroborated from git history instead, which is in-repo and checkable: `due-list.yml` commits its own output, `50722d1e` at 10:31:01Z and `f292f7a9` at 09:57:37Z against a `50 5 * * *` cron — **4h41m and 4h07m late, 23h27m apart.** It fires; it does not fire on time. A commit does not prove the trigger was `schedule`, so that half stays labelled as relayed.

## ⚠️ For MI-60 — one handoff, deliberately not taken

`OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` is CLEARED, and these four still cite it as live: `work-digest.yml` (lines 17, 145), `strategy-review-packets.yml` (40), `sunset-pass.yml` (45), `work-decision-commit.yml` (21). I did not touch a single `.yml` — that is your territory. The measured replacement text is in #10794's body if it is useful.

## ⚠️ What is NOT proven, stated plainly

**A green CI is not the done-condition here.** The done-condition is a real soak row crossing its threshold and appearing as due, and that has not happened:

- **No soak has gone `ready` on real data.** Every `ready`/`not_writing` verdict came from a fixture; the mechanism is exercised, the phenomenon is not.
- **The one real declared soak grades `unknown` today**, correctly — #10746 is unmerged, the gate unarmed, and no row exists yet to serve as a positive control. So on the live tree this ships surfacing one quiet row saying nobody is looking. That is the honest state, not a working alarm.
- **16 soak logs remain unregistered**, carried as dated debt in the guard's `BASELINE`. This makes the number visible and countable on every CI run; it does not reduce it. Writing 16 `ready_when` criteria I cannot verify would be the decorative declaration the guard exists to refuse.
- **The guard's escape hatch is real**: adding a name to `BASELINE` is cheaper than writing a row. It is not *silent* — a visible diff line, a fail on a stale entry, a debt count printed every run — but that is not a proof of good faith.

## Verification

`soak-alarm` 28 planted controls · `soak-registered` 11 (including the planted positive: a new unregistered soak MUST fail) · `due-list` 26→40 · `probe-lib` 25→37 · `probes` 20→22 · `probe-soak` 3→4. Full local guard suite **69 PASS / 3 FAIL**, and all three failures are missing local tooling (`pytest`, `lint-imports`), reproduced identically on a clean `origin/main` checkout — which is how they were told apart from a regression.

**The guard was proven against the real case**, not only a fixture: with #10746's actual writer dropped into the tree it passes with my register row and **fails without it**, naming the soak.

Not merging. Handing back for review.

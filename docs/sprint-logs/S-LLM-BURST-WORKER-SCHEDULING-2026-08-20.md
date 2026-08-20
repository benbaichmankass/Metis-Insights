# S-LLM-BURST-WORKER-SCHEDULING — 2026-08-20

## Date Range
- **Start:** 2026-08-20T08:50Z
- **End:** 2026-08-20T09:30Z

## Objective
- **Primary:** the two M37 next-steps, with their falsifiers intact — (1) schedule
  `oci-inventory --fail-on-drift`, but only after showing it can go RED on a real
  induced drift; (2) point the `llm-delegate` burst worker at real backlog items
  rather than its own source, and report precision **against its own denominator**.
- **Secondary:** verify the predecessor sprint log's "handled" findings against the
  files rather than the write-up (operator-requested).

## Tier
**Tier 1** throughout — GitHub Actions workflows, docs, tests, tooling and the
backlog. No `src/`, `config/`, `ml/`, no unit file, no order path, nothing running
on either VM. The four `src/runtime/exchange_fills_ib.py` findings were **filed,
not fixed**, precisely because fixing them is Tier-2.

## Starting Context
- ROADMAP **M37** — both tracks shipped 2026-08-18, never used since (15 workflow
  runs, all that day, zero after).
- Prior sprint: [`S-LLM-BURST-WORKER-2026-08-18.md`](S-LLM-BURST-WORKER-2026-08-18.md).
- **Known risk, stated by M37 itself:** the pilot's precision (17/19 claims over 5
  tasks) was graded on the delegate's **own source code** — an existence proof with a
  stated denominator, not a hit rate.
- Concurrent sessions: `e2-exit-mechanism-info` (E2, disjoint) and
  `system-review-trade-mechanics-falsp8` (closing; left handoff notes on #6927).

## Repo State Checked
- Branch `claude/llm-burst-worker-scheduling-ajajre`, reset onto `origin/main`
  **`e4c274af`** (#10026). No pinned SHA — `main` moved seven times on 2026-08-20.
- **Live VM confirmed current with `main`:** `/api/diag/version` → `git_sha e4c274af`.
- Canonical docs read: `CLAUDE.md`, `docs/claude/coordination-board.md`, ROADMAP M37,
  `.claude/skills/llm-delegate/SKILL.md`, `.claude/skills/sprint-format/SKILL.md`.
- Board #6927 tail read and **proven to be the tail** (`perPage=20` returned 11).

## Files and Systems Inspected
- **Workflows:** `.github/workflows/oci-inventory.yml`, `bootstrap-labels.yml`,
  `macro-producer-liveness.yml` (the alerting pattern copied), `llm-delegate.yml`.
- **Code:** `scripts/ops/oci_inventory.py` (read in full), `scripts/llm/delegate.py`,
  `scripts/llm/scope_guard.py`, `scripts/check_claim_basis.py`,
  `scripts/ops/check_backlog_refs.py`, `src/units/strategies/ict_scalp.py`,
  `src/web/api/routers/strategies.py`, `src/runtime/exchange_fills_ib.py`.
- **Data:** `comms/cloud/expected_topology.json`, `docs/claude/health-review-backlog.json`.
- **Live:** OCI compute inventory (read-only `list_instances`, ×3 runs);
  `https://ict-bot.duckdns.org/api/health` + `/api/diag/version`.

## Work Completed

### 1. `oci-inventory --fail-on-drift` — positive control FIRST, then the schedule
Both arms against the **real** cloud, on the same tenancy, minutes apart:

| arm | run | result |
|---|---|---|
| **red** — expectations deliberately wrong on a throwaway branch | 32351336623 | `drift 1 · match 1 · missing 1 · undeclared 1`, **exit 1** |
| **green** — same flag, unmodified `main` | 32351503893 | `3 match`, **exit 0** |

The single `match` in the red arm is the **denominator assertion**: it proves the
tool genuinely read the cloud rather than failing before the inventory ran. Drift was
induced on the **declared** side deliberately — `diff()` compares symmetrically, and
terminating a live VM to test a checker is not a trade worth making. Stated in the
workflow header so nobody later reads the control as stronger than it is.

**The control found a real defect.** The runner's default shell is `bash -e {0}` and
the job adds `set -o pipefail`, so on the drift path the failing `python3 … | tee`
pipeline aborted the script **at that line** — before `rc=${PIPESTATUS[0]}` and before
the `$GITHUB_OUTPUT` write. Measured on both arms: `RC: 0` green, **`RC:` EMPTY red**,
so the posted report read **"exit `unknown`" on precisely the runs that found
something**. Never seen because the drift path had never run end-to-end. The
`emit-expected` branch's `rc=$?` (that is `tee`'s status) had the same latent bug.

Then shipped: weekly `schedule:` (**chosen, not measured** — labelled as such),
`fail_on_drift` defaulting ON for `schedule` only, a four-state verdict derived from
the **report** not the exit code (`clean` / `drift` / `not_declared` /
`could_not_check`), Telegram + a deduped labelled issue with auto-resolve, and a
deliberate final-fail step. `could_not_check` never renders as clean **and never opens
a "topology drifted" issue** — that would be a confident accusation on an unasserted
denominator.

### 2. The `oci-inventory` issue-label path, exercised for the first time
Run **32351518408**, `event=issues`, job ran (not `skipped`) — issue #10030. The label
was verified to **exist** via `get_label`, not inferred from #10026 merging.

### 3. The delegate on unfamiliar code — 5 real open backlog items
| | result |
|---|---|
| substantive claims | **20 / 20** |
| line citations | **0 / 20** |

**Deliberately not pooled with the pilot's 17/19.** Different populations; the second
is the one that generalises. Substance held, including a planted trap
(`exchange_flat_reconciled` does **not** match a `startswith("reconciler")` test — it
noted that unprompted) and **two correct refusals** to assert about files it had not
been given, which is the documented weak mode.

**Every line number was wrong** (off by 3, 24, ~214) while every quoted snippet was
verbatim correct. **The cause is ours:** `build_prompt` sent raw file content, so
"cite the line number" could only be answered by counting. Fixed with `number_lines()`
(`cat -n` shape, **tab**-separated so a quoting model can strip the prefix
unambiguously — a space is indistinguishable from indentation) plus a SYSTEM_PROMPT
clause. **The pilot never saw this because its prompts asked for the "exact
expression" rather than a line number** — the prompt shape hid it.

### 4. The board-body clobber, fifth occurrence — and I caused this one
`issue_write method=update` replaces an issue **body**; I destroyed #6927's pinned
protocol header at 08:52Z reaching for it to post a comment. Restored from
`coordination-board.md` (the board's designated body of record) and **labelled as a
reconstruction**. Then found it had happened four times before — and each time was
recorded only in the offending session's own sprint log, while
`coordination-board.md`, the **binding** doc, never named the tool. It does now, as
does the `session-coordination` skill.

### 5. `CLAUDE.md` reachability correction (measured)
```
http://141.145.193.91:8001/api/health   -> 000   firewalled, as documented
https://ict-bot.duckdns.org/api/health  -> 200   {"ok":true}
…/api/diag/version + bearer             -> 200   {"git_sha":"e4c274af"}
```
Default-`Trusted` session, no Full/Custom change. "Egress to the VM is firewalled and
the issue relay is the only channel" was **half** wrong and cost every session a
needless relay hop.

### 6. The direct diag path was dead, and a "fix" for it was the reason (PR #10036)

Follow-on work after #10031 merged, prompted by the operator rejecting a claim I had
made: that `DIAG_BASE_URL` is *"an environment variable, not a repo file, so no session
can fix it."* **That claim was wrong, and I had written it into a backlog row.**

Investigating it found something worse than a stale variable.
`scripts/ops/diag_fetch.sh` already carried a self-heal — added for
`BL-20260705-ENV-DIAG-BASE-URL-STALE` — that rewrote the retired micro
`158.178.210.252` to the raw live IP `141.145.193.91`. But the sandbox proxy
allowlists by **scheme + hostname**, so plain-http to a raw IP is dropped at the
default `Trusted` level. Run live this session against the real env:

```
diag_fetch: … rewriting to the live Ampere host 141.145.193.91 …
curl: (28) Connection timed out after 10002 milliseconds
exit=3
```

So the heal fired, produced an unreachable host, timed out, and exited 3 — while
its own log line reported that it had healed the setting. Every session since has
paid the 30–60 s issue-relay hop because a green that checked nothing sat in the file.

Replaced with an **ordered candidate list**: canonical HTTPS
(`https://ict-bot.duckdns.org`) first whenever the configured value is plain-http or
names a known VM IP, a deliberately-set https base keeps priority, candidates de-duped,
and the serving base printed to stderr. The gate now requires only the bearer.

**Verified live in the same session, same process, with the stale env still set:**
`exit 0`, real JSON, `served by https://ict-bot.duckdns.org`. The returned
`git_sha: 9f65cd5d` independently confirmed the live VM had already deployed #10031.

`tests/test_diag_fetch_sh.py` — 11 tests, no network: `curl` is shimmed onto `PATH`
and logs attempted URLs, so candidate **order** is asserted directly; the parametrized
retired / raw-IP / plain-http case is the regression test for this defect.

**Both backlog rows corrected rather than quietly closed** — the false operator-only
claim is recorded *on the row* as false, so it is not repeated. The same correction
went into `CLAUDE.md` § "Reaching `/api/diag/*`".

**Note the shape:** this is the second time this session that a thing which *looked*
verified was not — the deduped-but-not-deduped backlog rows the parent session caught,
and now a self-heal that logged success while failing. Both were found by running the
thing rather than reading it.

### 7. The gaps above now carry timers, not just prose

Operator ask: *"make a backlog note with a timer for things that need to be checked
later on."* The four items under **Gaps not yet verified** were only prose in this log,
which is exactly how a stated limitation decays into an assumed fact. Each is now a
backlog row using the existing convention (`snoozed_until` + `trigger_condition` +
`what_to_check`), and each names what would **falsify** it rather than what would
confirm it:

| row | wakes | what it asks |
|---|---|---|
| `BL-20260820-OCI-INVENTORY-CRON-HAS-NEVER-FIRED` | 2026-08-24 | confirm a run with `event: schedule` exists; a healthy cloud must classify `clean`, never `could_not_check` |
| `BL-20260820-DELEGATE-LINE-CITATIONS-UNMEASURED-AFTER-NUMBERING-FIX` | 2026-08-27 | re-grade the **number** independently of the quoted expression — checking only the quote is what hid it |
| `BL-20260820-EXCHANGE-FILLS-IB-DELEGATE-FINDINGS-NOT-DATA-VERIFIED` | 2026-09-03 | put an incidence figure with a stated denominator on each of the four |
| `BL-20260820-DIAG-FETCH-CANONICAL-BASE-VERIFIED-FROM-ONE-SESSION-ONLY` | 2026-08-22 | a session *other than the one that wrote the fix*, at the default network level |

### 8. The schedule TRIGGER, proven the same day (PR #10037 in, removal out)

The operator asked whether Monday's run could be verified now. It could — but only
partly by dispatch, and the part that could not was the part that mattered.

**A dispatch cannot reach the schedule branch by construction.**
`inputs.fail_on_drift` carries `default: "false"`, so a dispatched run always has a
non-empty input and takes the first branch of the resolution. `EVENT_NAME = schedule`
is unreachable from a dispatch, and `github.event_name == 'schedule'` in the job `if:`
had **never been evaluated by GitHub** — every run in this workflow's history was
`workflow_dispatch` or `issues`.

So the links were tested separately, each by something that actually reaches it:

| link | how | result |
|---|---|---|
| end-to-end vs the real cloud, enforcing | dispatch `fail_on_drift=true`, run 32358587200 | **clean**; Auto-resolve fired; alert/issue/fail correctly skipped |
| the check can go red / green | 32351336623 / 32351503893 | exit 1 on induced drift, exit 0 clean |
| `fail_on_drift` defaults ON for schedule | block **extracted from the committed YAML**, `EVENT_NAME=schedule`, empty input | `true`, with a **planted-defect control** resolving `false` when the branch is deleted |
| **the trigger itself** | **two one-off probe crons, merged and removed the same day** | **run 32362843960, `event=schedule`, job RAN, verdict clean** |

**⚠️ The single most reusable thing this produced: GitHub scheduling lag is real and
normal.** The 11:00Z window fired at **11:14:30Z — 14.5 minutes late.** Setting *two*
windows was a hedge against GitHub dropping one; what it actually bought was
protection against a **reader** concluding at 11:05Z that the cron was broken. A late
scheduled run is not a missed one. Recorded on the backlog row and at the cron itself,
because the next person to stare at an empty 06:00Z Monday will otherwise re-derive it
as a defect.

**The probe was built to be safe if forgotten**, which is the design point worth
keeping: the crons were **day-of-month + month scoped** (`0 11 20 8 *`), not
day-of-week. A delayed or forgotten removal degrades to one stray run in *August 2027*,
never a second weekly cron racing the real one. A cleanup that must happen for the
change to be safe is not a cleanup.

Removal verified byte-identical to the pre-probe file (`git diff e33e6a8` → empty),
with the proof written into the comment beside the surviving weekly cron so nobody has
to re-run the probe to know the trigger works.

**What this does NOT claim.** One firing proves the trigger and the gate — not that the
check will catch a future drift. That half was proven earlier and separately. The two
together are what make the weekly cron a check rather than an intention.

## Validation Performed
- **Positive control, live, both directions** — runs 32351336623 (red) / 32351503893
  (green). This is the M37 falsifier, satisfied **before** the cron was added.
- **Verdict classifier extracted from the committed YAML** and run against **six**
  fixtures — clean · the *actual* red-arm report · no output · `not_declared` ·
  unparseable · `emit-expected`. All six classify correctly.
- **`number_lines` plant-proven:** removing the numbering fails 2 of the 6 new tests.
  Restored and re-verified green.
- `pytest tests/scripts/test_llm_delegate_scope.py` → **48 passed**.
- `scripts/ci/run_guards.py` with the work **committed** → **PASS 29 · FAIL 0** after
  installing the two missing sandbox deps.
- **Three guard failures investigated, not assumed:** `artifact-validity-guard`
  (`No module named pytest`), `layer-guard` (`lint-imports` exit **127**). Both are
  sandbox dependency gaps — installed each and re-ran: 57 passed, and 6 contracts kept
  / 0 broken respectively.
- **Backlog integrity re-read after every write:** 718 → 722 rows, ids unique.
- **Every delegate claim re-derived by grep** before being believed or filed.

### Gaps not yet verified
- **The scheduled run has never fired** — the cron's first firing is Monday. The
  workflow's `schedule` path is exercised only by inference from the `workflow_dispatch`
  runs; the `EVENT_NAME = schedule` branch that defaults `fail_on_drift` ON is **not**
  live-verified.
- **The line-citation column is unmeasured AFTER the fix.** It is 0/20 on the pre-fix
  prompt; nothing yet establishes the numbering actually lands.
- **The four `exchange_fills_ib.py` findings are code-verified but not
  data-verified** — in particular, whether `execution.shares` is ever absent on a real
  IB fill is unmeasured, so `cumQty` double-counting is a **latent** path.
- **`SSHORT` is the delegate's hypothesis, not a fact** — broker behaviour this repo
  holds no evidence on. Filed as such.
- The Telegram alert path in the new workflow is **untriggered** (no drift since).

✅ **The first of these is CLOSED** — the scheduled run fired 2026-08-20T11:14:30Z
(run 32362843960, verdict clean, job not skipped). See § 8. The Telegram path remains
untriggered, which is correct: nothing has drifted.

**All five are now backlog rows with timers** (see § 7) rather than prose in this
log only. The Telegram-path gap rides inside the cron row, since the first real
scheduled run is what would exercise it.

## Documentation Updated
- **Roadmap:** M37 row updated with both results and their denominators.
- **Canonical:** `CLAUDE.md` — the reachability correction (2 sites).
- **Subsystem:** `docs/claude/coordination-board.md` (new top-level `issue_write`
  section + the tool named inline at step 2), `.claude/skills/session-coordination/SKILL.md`,
  `.claude/skills/llm-delegate/SKILL.md` (the two gradings, un-pooled).
- **Backlog:** 4 new rows, 2 updated. 718 → 722.
- **Not applicable:** no pipeline stage changed, so `docs/TRADE-PIPELINE.md` is
  untouched and no dashboard visual check was needed.

## Contradictions or Drift Found
1. **`CLAUDE.md` on web-session VM reachability — half wrong**, corrected above. The
   predecessor sprint log recorded this finding on 2026-08-18 and **it landed nowhere
   durable**: not in `CLAUDE.md`, not as a backlog row. Found only because the operator
   asked for the log's claims to be checked against the files.
2. **`DIAG_BASE_URL` still points at the micro terminated 2026-06-16**, re-confirmed
   live two days after filing. Operator env-var fix; no session can do it.
3. **`oci-inventory`'s report said "exit `unknown`" exactly when it found drift** —
   found by the positive control, fixed here.
4. **The board-body clobber is systemic, not a one-off** — five occurrences, four
   documented only where no future session reads.
5. **Not caused here, confirmed:** the `DIAG-BASE-URL` duplicate rows the predecessor
   log claimed were "deduped at wrap" were **not** — the closing session fixed them in
   #10026 on 2026-08-20. Verified: exactly one row remains.

## Risks and Follow-Ups
**Technical risks:**
- A **weekly** cadence means up to 7 days of blind window on topology drift. Chosen
  against alarm fatigue; revisit if topology starts moving.
- The new alert opens an issue on `could_not_check` too. If OCI credentials expire
  this becomes a weekly issue comment — correct (it IS a failure) but worth watching
  for the desensitized-alarm shape.

**Tier-3 product decisions:** none arising.

**Blockers:** none.

## Deferred Items
- The `oci-inventory` **issue path still cannot request `fail_on_drift`** (it takes the
  `inputs.* || default` fallbacks; the body is not parsed). Deliberate — body parsing is
  untrusted-input surface neither work item needed.
- **Deferred indefinitely, unchanged:** the local-GGUF delegate backend (design Phase 3).
  No privacy driver exists and the hosted path is cheaper and better.

## Next Recommended Sprint
1. **Re-grade the delegate's line citations after the numbering fix.** *Expectation:*
   citations land now. *Failure signal:* still wrong ⇒ the cause was **not** the missing
   numbers and the fix is cosmetic — say so and stop asking it for line numbers at all.
   Cheap: re-run the same five `task_id`s and diff.
2. **Settle `BL-20260820-IB-FILL-QTY-FALLS-BACK-TO-ORDER-CUMULATIVE-QTY` from data.**
   Query the fills store for IB rows sharing an `order_id` whose qty sums above any
   single row's. *Failure signal:* zero such rows ⇒ latent, close it as latent rather
   than leaving it to look like a live corruption.
3. **Verify the scheduled `oci-inventory` run actually fires on Monday** and defaults
   `fail_on_drift` ON. *Failure signal:* no run, or a run whose report shows the flag
   off ⇒ the `EVENT_NAME = schedule` branch is wrong and the whole schedule is inert.

## Wrap-Up Check
- [x] Code inspected directly — every file above read, not inferred; every delegate
      claim re-derived by grep.
- [x] Canonical docs reviewed and updated (`CLAUDE.md`, `coordination-board.md`, two skills).
- [x] `docs/TRADE-PIPELINE.md` — **not applicable**, no pipeline stage changed.
- [x] ROADMAP checked and updated (M37).
- [x] Contradictions recorded, including the two I did not cause and the one I did.
- [x] Unknowns stated in *Gaps not yet verified* rather than papered over — notably
      that the schedule itself has never fired and the post-fix citation accuracy is
      unmeasured.
- [x] **Errors made and disclosed in-session:** I overwrote coordination board #6927's
      body with `issue_write method=update` (restored, labelled, and turned into the
      durable fix); and a first draft of the clobber count read "2026-07-30 ×2" by
      misreading that log's "twice damaged" clause, which refers to
      `monitor_miss_analysis.py` — corrected before it reached the backlog or the docs.

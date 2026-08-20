🔓 **MERGE SLOT RELEASE** · session `session_014myC5S5VacHNuzzBR8dGBC` — **all three merged. Slot free.**

| PR | merged as | contents |
|---|---|---|
| **#10078** | `70196ac5` | #9919's disposition — its row re-landed on current main |
| **#10077** | `a62c6962` | #9924's disposition — its test file, reduced |
| **#10076** | `2be01dea` | the declared-vs-resting bracket detector + the board relay |

**Verified on `main` by running it, not by reading the merge SHA:** detector `--self-test` **19/19** · `pytest tests/ops/test_broker_bracket_reconcile.py tests/ops/test_attach_ib_target.py` **36/36** · backlog **754 rows** with all three new rows present and `MONITOR-MANAGES-ONLY-THE-LINKED-LEG` reading `fixed`.

**And re-run from merged `main` against live state at 22:05:14Z** — it still passes MHG and still flags both:

```
MHG  4796  29 | stop 29  target 29 | clean
MGC  4773  95 | stop 95  target  0 | target_naked_declared (declared TP 4393.02071429)
MES  4350  15 | stop 15  target  0 | target_naked_declared + stop_price_diverges
                                     (declares 7533.696429, nearest resting 7516.50 -- 69 ticks)
ib_live: COULD NOT LOOK -- nothing was graded    exit 3
```

⚠️ **#9924 and #9919 still need CLOSING** — I cannot (MCP 403 on writes). Their content is on `main`; the PRs are now redundant, not pending.

---

## ⚠️ The two live hazards this session did NOT fix, and deliberately did not

**1. MES 4350 is protected at a level no strategy chose.** Journal declares `stop_loss` 7533.696429; the only resting stop is 7516.50 — **69 ticks, $1,289.73 on 15 contracts**. It grades *fully stop-covered* because quantity and side are both right. It got that way because the over-cover repair cancelled order 375 (which matched the journal to within a tick) and kept 338 (which does not) — the outcome `BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS`'s own criterion #1 exists to prevent. **Tier-2 to repair, and the level must be READ from `trades.stop_loss`, never supplied by the repairer.** Filed as `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`.

Note for whoever does it: MGC's and MHG's legs are held by `client_id` **597**, which is absent from `/api/diag/ib_state`. Per #9919's finding a cancel from a dead clientId goes `PendingCancel` — expect that, do not read it as failure.

**2. MGC 4773 is 150 points past its declared target with no resting target and no `tp_cross` in `ict_scalp`.** No Tier-3 diff is proposed, and that is the deliberate part: `BL-20260818-ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH`'s criterion (1) says *"MEASURE FIRST: determine why 4487 had no resting target… Do not build a fix on top of an undetermined cause."* That cause is **not** established. All I have is a correlation on n=1 — the two target-naked positions carry repo-minted `oca-protect-<id>` groups while the fully-covered MHG carries an IBKR-assigned numeric one. Suggestive; not a finding.

**3. `EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT` is escalated** — 114/398 intervals breach (**28.6%**), **p90 71.2 s**, max 83.7 s, across 3 processes. It was filed as "1.1 s of margin." Anyone planning M20 work should re-read it before quoting the old figure.

---

## Self-report — three things I got wrong

**1. I posted four near-duplicate STARTs on this board.** Corrected in the claim comment above. Cause: I polled for the relay's *receipt file* and inferred "did not post" from its absence, three times, when the board itself was one API call away. The relay had posted every time; only its commit-back failed. **The receipt is a proxy; the board is the thing.** Same error shape as the cluster itself — grading by a correlate instead of the quantity that matters.

**2. I misattributed the PRs' zero check runs** to the documented `BL-20260730-PR-CI-NOT-ATTACHING`. It was self-inflicted: the relay's results commit carried a CI-skip directive, and then my *fix commit's own message* named the directive and so triggered it. Blaming a known-flaky subsystem was the most comfortable available wrong answer.

**3. I swallowed two push failures with `2>/dev/null`** and briefly believed work had landed that had not — the exact idiom the rules forbid on a load-bearing step.

Also fixed at source: this clone's `remote.origin.fetch` was pinned to `main` only, so no `claude/*` tracking ref ever updated and every "unpushed commits" warning was a stale-ref artifact.

## For other sessions

- **`board-post.yml` is on `main`** — if your MCP is 403 on writes, push `automation/board-posts/<name>.md` to a `claude/**` branch. **Its receipt is not proof; read #6927.** Still undocumented in root `CLAUDE.md`.
- ⚠️ **`pr-opener.yml` has an unfixed bug of the same shape**: it `git rm`s its request from its own trigger path and stamps a CI-skip directive, so **any PR whose last commit is its results commit starts with zero required checks** — unmergeable, but rendering as no-red rather than red. Demonstrated on #10077 and #10078. Filed, not fixed.
- **The new detector has no scheduled caller.** It runs when someone remembers to. That is the written-and-never-read shape `provenance-consumer-guard` exists for, and it is filed as an explicit criterion rather than left implied.

✅ **DONE** — area released, slot free.

---
_Generated by [Claude Code](https://claude.ai/code)_

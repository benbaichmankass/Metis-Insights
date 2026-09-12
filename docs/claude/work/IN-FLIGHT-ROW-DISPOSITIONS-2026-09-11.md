# Dispositioning the 17 unsupported `in_flight` rows — per row, against each row's own done-condition

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Author:** `session_01NFnmLiu9zNDppY3qFXcJJA` (MI-265, ops lane) · **registry key** `pending-20260911T082606Z`
⚠️ **The branch that landed this was named `claude/mi264-…` and that is a MISNOMER — this work is
`MI-265`** (`MI-265-DISPOSITION-THE-17-UNSUPPORTED-IN-FLIGHT-ROWS-…`). **`MI-264` is a different,
unrelated item** — `MI-264-THE-BLOCKED-LANE-WATCHER-CANNOT-EXPRESS-THE-COMMONEST-BLOCKER-ITS-OWN-
REGISTRY-ROW`, `state: ready`, owner `unassigned`, about adding a sixth `blocked_on` kind. Verified
in `MANAGER-CHECKLIST.json` on `main`, not taken on trust. Caught by the manager at 08:34Z, after
#11775 had already merged under the wrong id. Every reference in this document, in the four
dispositions, in the backlog row and in the `DOCUMENT-INDEX` row now says **MI-265**; only the
merged branch name and #11775's title still carry `mi264`, and those are immutable history. A
later reader attributing this work by that branch name would mis-file it — the drop pattern this
lane exists to fix, in miniature.

**Measured:** 2026-09-11T08:3x–09:0xZ against `origin/main` @ `1cd5e211e`, the live host
`https://ict-bot.duckdns.org`, and the GitHub API.

## What this adds to MI-236, and what it deliberately does not redo

[`#11771` comment `5631004616`](https://github.com/benbaichmankass/Metis-Insights/pull/11771#issuecomment-5631004616)
named all 17 rows, resolved them to 14 distinct owners, graded each owner **POKE vs RE-ROUTE**, and
corroborated the registry against git `Claude-Session:` trailers — a source the registry does not write.
**That work is good and is not redone here.** It also stated its own limit plainly:

> *"I am recommending POKE vs RE-ROUTE — a statement about the OWNER. I am deliberately NOT recommending
> a landing state for any row, because that requires checking each row against its own `done_condition`,
> which I have not done."*

**That reading is this document.** I grade **rows**, not owners.

⚠️ **The two gradings are orthogonal and neither overrides the other.** A row can be `done` while its
owner is dormant — that is the common case here, because the work landed and only the *record* went
stale. Where I recommend RE-ROUTE I am making a claim about the ROW (the work as scoped did not
converge), not contradicting the owner grading.

⚠️ **`done` means MERGED AND OBSERVED. `landed_unproven`/`waiting` means the code is in and its effect
has NOT been seen on the fleet.** Every `done` below names the observation, not just the merge.

⚠️ **A lower unsupported count clears nothing by itself** — it is trivially achievable by marking rows
`done`, which is the harm. Each disposition below carries its evidence; read those, not the delta.

## Headline: the WIP ceiling is cleared, 8 → 5

`check_wip_ceiling.py` counts **work objects**, and only four of the 17 rows were objects. All four are
dispositioned:

| object | was | now | ground |
|---|---|---|---|
| `WO-20260909-EVERY-PR-IN-THE-REPO-IS-RED` | `in_flight` | **`done`** | every clause met; class fix OBSERVED on 3 scheduled runs |
| `WO-20260909-THE-PARTIAL-CLOSE-PRODUCER-IS-BUILT-AND` | `in_flight` | **`done`** | answered by #11514, merged, measured against live config |
| `WO-20260909-GATE-API-BOT-DB-THE-MONEY-DB` | `in_flight` | **`waiting`** | refusal half observed by me; SPA half owed and *unknown* |
| `WO-20260909-THE-HEALTH-BACKLOG-GREW-BY-49-WHILE` | `in_flight` | **`in_flight`** | **moved backwards** — 853 unresolved vs a 765 done-condition |

`python3 scripts/ci/check_wip_ceiling.py` → `OK — 5 work object(s) in flight, ceiling 8`.

⚠️ **`waiting` is not a euphemism for `done`.** `check_wip_ceiling.py` excludes it *deliberately* —
its own source says folding `waiting` into `in_flight` "would make the ceiling punish honesty" about
work blocked on someone else. Using it here is the sanctioned way to free a slot without asserting
finished work.

---

## 1. `WO-20260909-EVERY-PR-IN-THE-REPO-IS-RED` → **`done`**

Four clauses, each re-established first-hand rather than inherited from the object's own annotations.

1. **"The repo is GREEN."** `check_trainer_capture_watch.py` run directly, exit code read not inferred:
   `liveness: fresh / newest recorded run is 3.6h old (window 15h)`, **EXIT=0**. Both sibling receipt
   guards run in the same pass also exit 0 — `check_pr_queue_watch` FRESH (3.2h/30.0h),
   `check_digest_liveness` `state=fresh` (3.24h). So this is not one guard happening to be inside its window.
2. **"Land the stale automation PRs / close the superseded ones."** **The queue is empty.**
   POPULATION: every open PR in the repo (`list_pull_requests(state=open)`) — **n=2**, both `claude/*`
   session PRs (#11767, #11738). **Zero** carry an `automation/*` head ref, against the **44** this
   object's own first sweeper run graded on 2026-09-10. Dispositioned by #11739 (MI-259), not by this lane.
3. **"Fix the CLASS — say plainly which of (a)/(b)/(c)."** Answered plainly on the object: **(a)**, the
   automation workflow should rebase-or-reopen, qualified that the actor must **outlive the producing
   run** — hence a cadence job (`stale-automation-sweep.yml` + `sweep_stale_automation_prs.py`, both
   confirmed on `origin/main`) rather than another input to `commit-to-main`.
4. **OBSERVED, not merely deployed** — the half a merge cannot establish, so I checked it hardest and
   from the Actions API rather than the object's prose. `stale-automation-sweep.yml` has **three**
   `event: schedule` runs, all `conclusion: success`: `34518595813` (2026-09-10T19:08:22Z),
   `34538411238` (22:38:07Z), `34563441234` (2026-09-11T04:46:13Z). Run 1's **effect** confirmed on the
   PR object itself: **#11710** (`automation/session-reaper-34501786024-1`) reads `merged: true`,
   `merged_at 2026-09-10T19:11:26Z` — **3m04s** after the scheduled run started, no session in the loop.

⚠️ **What `done` does NOT claim.** The guard is a freshness window, so it can always go red again; `done`
means the refreshing mechanism is shipped and observed, never that the clock is retired. A future red is
the next receipt being late — what the sweeper exists to catch — not a regression of this object.
⚠️ **n=3 is a small denominator**: one run landed a strand, two found nothing to sweep. Correct behaviour
on an empty queue, not three independent confirmations of the refresh path.

## 2. `WO-20260909-THE-PARTIAL-CLOSE-PRODUCER-IS-BUILT-AND` → **`done`**

Answered by **`fba7c48dd`** — *"MI-209c: why the partial-close producer has never fired — it is routed to
zero accounts"* **(#11514)**, merged.

It answers the done-condition's own clause **(b)** ("reachable in principle but unreachable as configured,
naming the config and the account(s)") and names both: `_apply_partial_close` has one call site, reachable
only from a verdict carrying `close_qty_pct` < 1, emitted at exactly one site in `src/`
(`turtle_soup.monitor()`, `turtle_soup.py:537`); that leg is routed to **0 of 11 accounts** on the live
`/api/bot/config` and is additionally pinned `execution: shadow`. **The zero carries a positive control** —
`trend_donchian` → 3 and `ict_scalp_5m` → 1 on the same read — so it is a measured zero, not an empty query.
Census re-established **independently** (trades 5589, order_packages 4500, matching MI-209b), which closes
clause (d) rather than assuming it. The margin the done-condition asked for is reported (peak_r ≥ 1.0R on
43/118), correctly labelled a **fleet proxy** of **ESTIMATED** provenance since turtle_soup has zero
telemetry rows.

**Propose-never-enact was honoured:** *"MEASUREMENT ONLY — no param, no lever, no re-grade, no leg retired,
no live position touched. Every read was a GET."* Its recommendation is to **stop** — the path is dormant
for a decided reason (operator-approved Tier-3 demote on net-negative evidence). That is the sanctioned
**"A NULL IS AN ANSWER"** outcome and must not be mistaken for work left undone.

⚠️ **A method note that changes a verdict.** `git log --grep "#11514"` searches commit **bodies**, and PRs
cite each other's numbers — it wrongly reported **#11105** and **#11565** as merged. Matching the squash
subject **suffix** `(#N)$` resolves them correctly (#11105 is closed-unmerged; #11565 merged as `9246bc8d9`).
⚠️ **Not cleared by this:** the commit's own §0 records that MI-188b already answered this on 2026-09-08 and
that **~2/3 of the unit was re-derivation**. That duplication is a real routing finding and is *not*
dispositioned here. Two backlog rows it filed remain open on their own merits.

## 3. `WO-20260909-GATE-API-BOT-DB-THE-MONEY-DB` → **`waiting`** (and one new backlog row)

**The refusal half is OBSERVED**, exactly as the done-condition demanded ("an actual credential-free HTTP
call against the live host … not by reading the route decorator"). No credentials sent:

| call | result |
|---|---|
| `GET /api/bot/db/tables` | **401** `{"detail":{"error":"invalid_session"}}` |
| `GET /api/bot/db/table/trades?limit=1` | **401** `{"detail":{"error":"invalid_session"}}` |
| control `GET /api/health` | **200** — host up, so 401 is a refusal not a dead host |
| control `GET /api/diag/version` | **401** — known-gated route, same shape |

**Population enumerated**, as required: exactly **two** routes exist under `/api/bot/db/*`
(`db_explorer.py:256`, `:295`) and both are gated. The gating commit enumerated the same two by AST and
ships a test asserting the count. Both carry their `docs/api-tier-policy.md` row (lines 214–215).

⚠️ **What is still owed — and it is alarming.** The done-condition also says *"The SPA's Data Explorer must
still work … verify that, do not assume it."* The gating commit **`284cbc6cf` (#11517)** is titled
*"HELD for operator OK"* and its body states, under **"HELD, not armed"**, that both preconditions were
**measured FALSE** and that *"merging before they are fixed takes the SPA's Data Explorer from working to
permanently 401"*: (1) the SPA cannot send a bearer — zero matches for `Authorization|Bearer|jwt` across all
40 source files, no login screen, no token store; (2) the host cannot mint one — `POST /api/auth/login`
returned 500 `auth_unavailable`. It adds *"Neither fix is in this diff."* **It merged anyway.** So either
both were fixed elsewhere, or the **single live consumer's** Data Explorer has been broken for two days.
**Nobody has established which.** Filed as
`BL-20260911-DB-EXPLORER-GATE-MERGED-WITH-BOTH-ITS-OWN-HELD-PRECONDITIONS-MEASURED-FALSE` (high).

⚠️ **Two negatives I refused to over-read** — the tempting misreading closes this row on a false positive.
`POST /api/auth/login` with an **empty body** now returns **422 Field required** rather than 500
`auth_unavailable`. **That is not evidence precondition 2 is fixed:** Pydantic validation runs *before* the
handler, so an empty body never reaches the `auth_unavailable` branch. Symmetrically, `require_session`
raises 401 on a missing `Authorization` header *before any env read*, so **my own 401s also cannot
distinguish "auth configured" from "auth envs unset"**. Read either as status about auth configuration and
you have UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A.

**Why it stayed invisible:** a 401 from the gate is *exactly what correct behaviour looks like* to an
anonymous caller, so the broken and healthy states render identically from inside this repo.

⚠️ **SETTLED 2026-09-11T09:3xZ — and the answer is the bad one. THE DATA EXPLORER IS UNREACHABLE**, and
has been since the gate deployed on 09-09. `POST /api/auth/login` with a **well-formed** body (an
obviously non-existent address, so no real account could be locked out) returns **HTTP 500
`auth_unavailable`** — the JWT envs are unset and the host cannot mint a session. Verified in code rather
than inferred: `require_session` accepts only a Bearer satisfying `decode_token`, which needs
`_signing_key()`, so there is **no static-token bypass** and `/api/auth/login` is the only mint path.

⚠️ **My own empty-body 422 could not have established this, and I flagged that at the time** — Pydantic
validation runs before the handler, so it never reaches the `auth_unavailable` branch. The well-formed
body is what reaches it. That is the whole difference between the two probes.

⚠️ **The security call was RIGHT and must not be reverted.** `db_explorer.py` states the consequence in
terms — the gate *"fails CLOSED when auth is unconfigured, and that is deliberate"*, and the tab is
*"unreachable until the auth envs are set"*, which it calls *"a real OPERATIONAL PRECONDITION"*. Before
the gate, an unauthenticated read returned real trade rows over 41 columns and 21 table schemas on a
public host. **What failed is the follow-through, not the judgement.**

⚠️ **And one half is still genuinely unknown:** even with the envs set, the SPA must be able to *send* a
bearer, and the gating commit measured zero matches for `Authorization|Bearer|jwt` across all 40 of its
source files. **Setting the envs alone may not restore the tab** — do not read this as one env change away.

**Cheapest settlements, in order:** (a) read the JWT envs off `/proc/<MainPID>/environ` via `get-env`;
(b) `POST /api/auth/login` with a **well-formed** body so the handler runs — 401 `invalid_credentials`
(configured) vs 500 `auth_unavailable` (unset); (c) load the deployed SPA. **No test suite settles it** —
#11517 already ships 34 passing tests and a harness cannot reach a deployed front end.

## 4. `WO-20260909-THE-HEALTH-BACKLOG-GREW-BY-49-WHILE` → stays **`in_flight`**, recommend **RE-ROUTE**

The one genuinely unfinished row of the four — and it has **moved backwards**.

POPULATION: all **1450** rows of `docs/claude/health-review-backlog.json` at `origin/main` @ `1cd5e211e`,
read 2026-09-11T08:4xZ: `open 672 · resolved 580 · kept_open 181 · wont_fix 8 · superseded 6 · invalid 3`.
**UNRESOLVED (open + kept_open) = 853**, against a done-condition of *"measurably LOWER than 765"* —
**higher by 88**, +11.5% over the two days the row sat with a dormant owner.

⚠️ **This is not a verdict on the lane's work.** **#11510 IS merged** (`c49948127`, *"close 3 rows on
evidence, and answer the mechanism question"*), so it landed evidence-carrying closes **and** addressed the
done-condition's second clause. What has not happened is the arithmetic half.

⚠️ **The direction is the finding, not the level.** The done-condition's own warning — *"a one-off drain
that leaves that unchanged buys one week"* — is now **measured rather than predicted**: a merged drain of 3
rows against a net **+88** means the **fill rate**, not the drain rate, is the binding term. A successor
that opens by draining rows repeats the move this measurement already shows does not work. **RE-ROUTE**
rather than poke, and re-scope around the fill rate.

---

## The 13 checklist rows (no ceiling slots, proposals for the manager)

⚠️ **I do not edit `MANAGER-CHECKLIST.json`** — the manager has #11773 and this tick's PR open against it,
so a shared-register conflict is guaranteed. These are proposals.

⚠️ **Confidence is marked per row.** `[established]` = I checked the artefact myself this session;
`[row-record]` = graded on the row's own written record plus a merge check, which is weaker.

| row | proposed | ground |
|---|---|---|
| `MI-136-ZERO-FOR-THIRTEEN` | **`done`** | `[row-record]` #11111 merged (`603d5c113`, exact suffix match). The question is answered in the row: e35 **refuted** as primary cause (13.5% of loss; the run predates it); regime collapse, median MFE/D 2.11 → 0.15, 0 of 13 reached +1R. |
| `MI-138-RELAND-1C` | **`done`** | `[established]` #11113 merged (`29d20ce0e`), and the remaining clause — *"#11105 is superseded and must be closed"* — is satisfied: #11105 reads `state: closed`, `merged: false`. |
| `MI-143-ICT-SCALP-5M-SHADOW` | **`done`** | `[row-record]` **the row's own note already says `CLOSED 2026-09-07T17:35Z` on merged output** — the `state` field is simply stale. A record-keeping fix, not a work decision. |
| `MI-174-DIGEST-GENERATOR-HEADER` | **`done`** | `[established]` #11311 merged (`e0867710d`) **and the effect is observed**: `check_digest_liveness` reads `state=fresh`, 3.24h — i.e. the digest this row says was "silently frozen" is demonstrably not frozen. ⚠️ Its two addendum questions (sibling `render_due_list.py`; the LANDED denominator since R3) are *not* answered — if the manager wants those, re-file them as their own row rather than holding this one open. |
| `MI-193-LANE-2-RESEARCH-ACTIVE-MANAGEMENT-MECHANICS` | **re-route or close** | `[not established]` The row is a *files-findings* mandate over four measured failure classes. I did not verify which findings landed. Lowest-information row of the 13 — needs the manager's intent, not more measurement. |
| `MI-195-OPERATOR-TP-DECISIONS-AND-EXAMINE-QLD-TQQQ` | **POKE, stays open** | `[not established]` Carries three *operator-answered* decisions plus an explicit operator instruction (*"Why don't we just examine them?"*). ⚠️ **The approval is recorded — do not re-seek it.** I did not verify application; a Tier-3 param is involved, so this must not be closed on inference. |
| `MI-210-LANE-1-BACKLOG-DRAIN` | **RE-ROUTE** | `[established]` Same owner and same substance as object row 4 above: **853 vs 765**. One decision covers both. |
| `MI-211-LANE-2-PARTIAL-CLOSE-NEVER-FIRED` | **`done`** | `[established]` Same substance as object row 2 above — answered by #11514, merged, measured against the live config with a positive control. |
| `MI-212-LANE-4-STALE-AUTOMATION-LANDING` | **`done`** | `[established]` Same substance as object row 1 — the class fix shipped **and** is observed over three scheduled runs, one of which landed a strand with no human. Its own note warned *"luck is not a mechanism"*; the mechanism now exists and has been seen working. |
| `MI-221-BYBIT2-ETH-PHANTOM-CLOSE-UNPROTECTED` | **stays open** (acute condition cleared) | `[established]` **The money-at-risk half is gone:** `/api/diag/exchange_positions?account_id=bybit_2` reads `positions: []`, `count: 0`, `error: null`, and `bybit_open_orders` reads `order_count: 0` with **`read_state: orders_read`** — an explicit *we did look*, so this is not a failed read graded flat. ⚠️ **But a position closing makes the divergence UNOBSERVABLE, not RESOLVED** — the row's actual question (when was the protective leg cancelled, and why did journal 5471 record `sl` while the venue held it) is untouched. #11565 merged the instrument (`9246bc8d9`). **Do not close on the flat read.** |
| `MI-230-M20-EXIT-EVAL-NEAR-MISS-AND-RESTART-GAP` | **`landed_unproven`** | `[established]` Both halves shipped: `src/runtime/exit_restart_gap.py` is on `main` and `exit_loop_health.py` carries 7 `near_miss` references. ⚠️ Neither state has been **observed emitting** on the fleet, which is the difference between this and `done`. |
| `LANE4-RECOVERY-11519-STALE-AUTOMATION-SWEEPER` | **`done`** | `[established]` #11519 merged (`a1ec4a5c0`), and the recovered sweeper is the very mechanism observed running three times above. |
| `MI-232-ROOT-CAUSE-EXIT-EVAL-60S-BREACHES` | **`landed_unproven`** | `[row-record]` The root cause **is** established (complete census, 1218/1218 breaching intervals are slow passes; the residual concentrates in the 04:00–05:00Z IBKR reset window). The **remedy** is #11738, still **open** and needing one human merge click — Tier-2, R4 bars self-landing. ⚠️ **Its approval is already recorded** (`DEC-20260910-EXIT-EVAL-60S-REMEDY`, `r2_only`) — do not re-seek it; what is outstanding is the click. |

## What I did not establish

- **Whether the SPA's Data Explorer currently works.** The SPA is a different repo, outside this session's
  scope, and a deployed page cannot be rendered from here. Named as the owed observation, not guessed.
- **MI-193 and MI-195**, marked `[not established]` above rather than graded on inference. MI-195 touches a
  Tier-3 param, so closing it on a guess would be the worse error.
- **Whether any owner is genuinely abandoned.** That is MI-236's question and it answered it; I did not
  re-derive it, and nothing here is closed *because* an owner is dormant.

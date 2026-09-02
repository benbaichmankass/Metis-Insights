# PRs whose own bodies declared them held, merged by `github-actions[bot]`

**MI-82 · 2026-09-02 · measurement + assessment only.** Nothing was reverted,
merged, disarmed, or re-drafted by this session. Every mutating decision below
is left to the operator.

---

## 1. The population, stated

**124 pull requests merged to `main` on 2026-09-02.** Established twice,
independently:

| source | result |
|---|---|
| `git log origin/main --since=2026-09-02 --until=2026-09-03` | 124 commits, **all 124** carrying a `(#N)` squash suffix, **0** direct pushes |
| GitHub search `is:pr is:merged merged:2026-09-02` | `total_count: 124`, `incomplete_results: false` |

All 124 were then read **individually** with `pull_request_read` method `get`.
No PR was skipped and none failed to read.

### Method — and the trap that invalidates the obvious route

⚠️ **`mcp__github__list_pull_requests` cannot answer this question.** Re-measured
this session with a positive control: it returned `"merged": false` for **#10793,
#10764, #10788** while populating `merged_at` in the same row, and **omitted
`merged_by` entirely** even when explicitly requested in `fields`. Any count
built on it is wrong in the direction that hides the finding.

Every number below comes from per-PR `pull_request_read` `get`, with **#10788 as
a positive control run five times independently** (once per work slice). It
returned `merged_by: "github-actions[bot]"`, `merged: true` on all five.

### Grading

| axis | count |
|---|---|
| merged by `github-actions[bot]` | **23** |
| merged by `benbaichmankass` | **101** |
| self-declared held / draft / not-for-merge in **its own** title or body | **33** |
| ⚠️ **both — bot-merged AND self-declared held** | **11** |
| of those, **documented as operator-permitted** (#10746) | 1 |
| ⚠️ **unauthorised: bot-merged, self-declared held, no recorded permission** | **10** |

**The 23 is not the finding and must not be quoted as one.** The relay
legitimately merges the `chore(ops): …(auto)` automation PRs — that is its job.
The number that matters is **10**.

### ⚠️ `merged_by: benbaichmankass` is NOT evidence a human decided

Every Claude session in this repo authenticates as the operator's account, so
`benbaichmankass` covers both "the operator clicked merge" and "a manager
session merged via the API". They are **indistinguishable in this field**. That
is *not* a defect for this measurement — the PRs saying *"the manager merges"*
were **asking** for exactly that, and 22 of the 33 held PRs were merged that way,
i.e. the declared process working. But the field cannot be used to assert human
review anywhere else, and this audit does not use it that way.

---

## 2. The ten unauthorised merges, with blast radius

**All ten are on the live VM now.** `/api/diag/version` read at 19:05:39Z:
`git_sha` = `git_sha_on_disk` = **`36df56d8`** (HEAD of `main`), `restart_pending:
false` — i.e. the *running* trader carries every one of these.

Ordered by how live the change is, not by number.

| # | merged (UTC) | its own words | what landed | activation |
|---|---|---|---|---|
| **10793** | 18:53:42 | TITLE: *"HELD, must not reach the trader bot"* | `operator_commands.py` (new), `telegram_query_bot.py`, `manager_status.py` (+803) | 🔴 **ALREADY ACTIVE — see §3** |
| **10758** | 08:30:34 | *"Opened as a DRAFT for the manager to merge."* | ⚠️ **`src/runtime/order_monitor.py` +101**, `alpaca_client.py` +58, `execution_diagnostics.py` +284, `close_wedge_standing.py` (new, 623) | Live — trader is at HEAD. Re-routes close-wedge alarms off the pager into the digest |
| **10789** | 13:01:33 | *"Draft, and not to be merged by me."* | `claude_decision_bot.py` (new), `deploy/ict-claude-decision-bot.service`, `install_systemd_units.sh`, `telegram_poll_registry.py` (new, 444) | Live — `ict-claude-decision-bot.service` reads **active** on `/api/diag/services` |
| **10694** | 18:49:51 | *"Tier-2, DRAFT, not merged, not deployed."* | `cloud_notifier.py` +120, `telegram_query_bot.py`, `diag.py`, `install_systemd_units.sh` | Live — changes ping ownership / failover between two drainers |
| **10788** | 16:12:21 | *"Draft. Not for merge — it carries an operator decision."* | `decision_push.py` (new), `work_decisions.py` +242, `routers/work.py`, **`claude-pr-automerge.yml`**, `pr-automerge-disable.yml` (new) | API half live on `ict-web-api`. Delivery half **inert** — needs a Routine that does not exist plus an unset `CLAUDE_CODE_OAUTH_REFRESH_TOKEN`. ⚠️ **The operator decision it was drafted to carry was never answered — it merged instead** |
| **10794** | 18:48:24 | *"DRAFT — please do not merge on my account."* | `soak_alarm.py` (new, 395), `check_soak_registered.py` (new, 340) **registered into `run_guards.py`** | Live in CI — now fires on every PR in the repo |
| **10783** | 13:37:14 | *"DRAFT — Tier-1 (tooling + CI)."* | `reconcile-open-prs.yml` (new), `open_pr_record.py` +466, `reconcile_open_prs.py` (new) | Live — the `automation/reconcile-open-prs-*` PRs merging today are its output |
| **10757** | 07:52:50 | *"Draft for the manager to merge."* | `ci-settled.yml` (new), `ci_settle.py` (new, 747) | Dormant until invoked |
| **10764** | 18:53:47 | *"Left as a DRAFT. The manager owns the merge."* | CLAUDE.md −34.4% split into `docs/reference/`, `check_canonical_doc_coherence.py` | Docs + CI. ⚠️ Merged carrying the `test_every_allowlisted_log_file_is_documented` failure #10833 diagnosed |
| **10765** | 08:15:53 | *"Draft — the manager merges."* | 3 files, `OPEN-ITEMS.json` only | Register text only |

### Two things worth separating

- **#10746 is correctly excluded.** Titled DRAFT, body *"the manager merges; I
  hold no merge slot and have enabled no auto-merge"*, order-path Tier-2 — and
  `CLAUDE.md` records the merge as operator-permitted *"precisely BECAUSE it arms
  nothing"*. Bot-merged at 11:30:06Z, graded **authorised**. It was used as the
  control: a method that flagged it would be wrong.
- **#10758 splits substance from process.** `CLAUDE.md` records the MI-34
  downgrade as an operator decision — *"The operator chose to DOWNGRADE this
  class out of the paging channel"* — so the **change** has a recorded decision
  behind it. The **merge** still bypassed the manager the PR asked for. Those are
  different facts; only the second is this audit's finding.
- **Three of the ten merged AFTER the fix** (#10810, 18:49:29Z): #10694 at
  18:49:51, #10793 at 18:53:42, #10764 at 18:53:47 — 22 seconds, 4 minutes and 4
  minutes later. This is **consistent with**, not contrary to, #10810's own
  statement that it *"stops NEW mis-arming. It cannot retract auto-merge already
  enabled."*

### Borderline, recorded rather than absorbed

**#10766** (bot-merged 07:53:26Z) was graded **NO**: it addresses *"findings the
manager must read"* and carries an unticked `- [ ] CI green`, but never states
its own merge status. On a looser reading the count is 11 rather than 10.
**#10811** and **#10738** graded UNCLEAR; both were merged by `benbaichmankass`,
so neither changes the finding either way.

---

## 3. The sharpest item — #10793's constraint is violated **now**, and it is observed, not predicted

The operator's constraint was *"must not reach the trader bot"*.

**The code path holds.** `src/bot/operator_commands.py::install_operator_commands`
registers the handlers **outside every branch**, and says so in its own docstring:

> *"The handlers are registered in ALL THREE cases. Registering nothing on the
> third would make the command silently absent…"*

`ict-telegram-bot.service` runs `src.bot.telegram_query_bot`, which polls
`TELEGRAM_BOT_TOKEN` — the **trader** bot — and calls
`install_operator_commands(..., polled_token=TELEGRAM_BOT_TOKEN)` at line 788.

**And it is no longer a prediction.** From the live VM's journal
(`/api/diag/journalctl?unit=ict-telegram-bot.service`):

```
18:53:42Z  #10793 merges
18:53:44Z  systemd: Stopped/Started ICT Trading Bot - Telegram UI
18:59:02Z  systemd: Stopped/Started ICT Trading Bot - Telegram UI
18:59:03Z  WARNING  operator_commands: the answerable route (TELEGRAM_CLAUDE_BOT_SECRET)
                    is NOT the bot this process polls — /status and /decisions are
                    registered here …
18:59:03Z  INFO     operator_commands: registered /status, /decisions
                    (route_state=answerable_elsewhere)
```

**`/status` and `/decisions` have been live on the trader bot since
2026-09-02T18:59:03Z**, and the code logged a WARNING naming the violation as it
did it. Note also that **merge→restart was 2 seconds** here — on this system a
merge to `main` is very nearly a deploy, so "a merge is not a deploy" gives far
less protection than the phrase implies.

⚠️ **The taps are live too, not inert.** `telegram_query_bot.py:539` carries an
`elif action == "wdec":` branch and declares `_polled_prefixes = (CB_PREFIX,
"propexp")`. So a decision button pressed in the **trader** chat is received and
processed — the constraint is violated functionally, not just cosmetically.

---

## 4. The still-armed set

### ⚠️ A stated measurement gap, not a clean answer

**`auto_merge` is not exposed by this session's GitHub MCP.** A full
`pull_request_read` `get` payload on an open PR (#10833) contains no `auto_merge`
key, and `list_pull_requests` does not offer it in `fields`. So *"which open PRs
have auto-merge enabled"* **cannot be read back from the API available here.**
Recorded as *we could not look* rather than reported as *none*.

What **was** measured:

| PR | title | `draft` (API read) | own request file on branch |
|---|---|---|---|
| 10845 | MI-80 digest cadence | `true` | NONE |
| 10843 | MI-81 trainer capture | `true` | NONE |
| 10841 | MI-77 escalation exit code | `true` | NONE |
| 10780 | `[RECOVERED — DO NOT MERGE]` MI-56 | `true` | NONE |
| **10776** | error-feed duty pass | **`true`** ✅ | NONE |
| **10748** | R-metric contamination | **`true`** ✅ | NONE |
| 10833 | manager MI-78 spawn | `false` | NONE — `mergeable_state: blocked` |
| 10835 / 10398 | `chore(ops)`/`chore(m1)` `(auto)` | `false` | n/a — relay's legitimate job |

1. **The manager's claim on #10776 and #10748 HOLDS** — both read `draft: true`
   from the API, verified by reading, not by trusting the write. #10748 also
   reports `total_count: 0, state: pending` on its statuses, so there is no green
   for an auto-merge to fire on.
2. **No open `claude/**` branch carries its own auto-merge request file**, so the
   *fixed* relay (#10810) cannot arm any of them on a future push.
3. **`draft: true` is a real block but it is a different measurement.** GitHub
   will not merge a draft, so these are protected — but that is protection by a
   second mechanism, not a read of the first. If any of these is un-drafted, the
   auto-merge state underneath is **unknown**.

---

## 5. What this session did NOT do

No revert, no merge, no disarm, no re-draft, no register edit, no operator ping.
Whether anything here should be reverted is the operator's call.

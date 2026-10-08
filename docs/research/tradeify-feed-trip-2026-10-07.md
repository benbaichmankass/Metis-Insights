# tradeify_1 feed trip — why the prop executor placed nothing since 2026-10-06 18:27:45Z

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

Lane TRADEIFY-FEED-TRIP (checklist row of the same name; pipeline `PI-20261007-APBY4NTV-0006`).
Read-only diagnosis via the live-VM diag relay (`journalctl` for `ict-prop-feed@tradeify_1` and
`ict-prop-executor@tradeify_1`) plus the code. **Merged ≠ deployed ≠ observed:** everything below
about the VM is OBSERVED in the journal as of 2026-10-07 20:43Z; nothing here was changed on the VM.

## 1. Answer

**It is not a CAPTCHA, 2FA, rejected password or missing credentials.** The recorded sub-reason is
**`unknown_page`**: after submitting a credential login the Tradeify DXtrade terminal never reached a
recognisable page — it sat on a one-line `Loading...` screen for ~59 s. `prop_feed_tick.sh` maps *every*
`FeasibilityError` (incl. `unknown_page` and `timeout`) to exit 4, and exit 4 trips the feed on the
**first** occurrence, permanently. A terminal that was slow or unavailable for ~4 minutes therefore
stopped a live prop account for 26+ hours.

Exact lines (UTC, `ict-prop-feed@tradeify_1`, 2026-10-06; account/platform/credential values are not printed by the tool):

```
18:21:12  session: reused / login: ok                      <- last healthy feed tick
18:26:46  session: saved state not accepted (error TimeoutError); deleted, logging in
18:26:46  session: login_attempt
18:27:45  feasibility: unknown_page (no terminal marker after submit (at https://dx.tradeify247.co))
18:27:45  page_shape.title: Tradeify 247 | page_shape.buttons: [] | page_shape.text: 1 non-empty lines: "Loading..."
18:27:45  [prop_feed_tick] session: credential login attempt 1/12 today (UTC 20261006, succeeded=false)
18:27:45  [prop_feed_tick] TRIPPING feed: feasibility stop (challenge/CAPTCHA/2FA/login rejected/login timeout/no creds) — never retried (exit 4)
```

The executor saw the same degradation first: its 18:23:00 tick (`code_sha 2865b3473`, mode `live`) died with
`TimeoutError: Page.goto: Timeout 45000ms exceeded` on the terminal URL (exit 1, `OnFailure=` fired).
Its last clean tick was 18:18 (`no_ticket`, `account_status` posted). From 18:28:10Z every executor tick logs
`feed is TRIPPED (...); the executor does not run without a maintained session` — still true at 20:43Z today.
Terminal state at the last healthy read: balance = equity (no open position, no working order).

**Screenshot / dump:** none. The feed does not pass `--dump-dir` (by design: nothing about the page is written
to disk); only the redacted `page_shape` above exists. So "what the page actually was" beyond `Loading...` is
not recoverable from the VM.

**Not proven:** *why* the terminal hung (Tradeify-side outage vs. our session expiring into a slow SPA boot).
Two independent timeouts at 18:23 and 18:26 on the same host point at the broker side being degraded for a few
minutes, but one journal cannot separate that from a persistent break. A bare `GET` of the terminal root from
this sandbox returns HTTP 200 today — that says only the static shell is up, **not** that login works.

## 2. What the trip is, what resets it

| | |
|---|---|
| Trip logic | `scripts/ops/prop_feed_tick.sh::record_failure()` → `trip()` (rc 4 → immediate; any other rc → after `PROP_FEED_MAX_FAILURES`=3 consecutive) |
| State file | `~/.cache/metis-prop-browser/accounts/tradeify_1/feed/tripped` (`$PROP_BROWSER_BASE`; breakout_1 uses `.../feed/tripped`). Content: `<UTC ts> rc=4 <reason>` — no secrets |
| Who honours it | the feed (`prop_feed_tick.sh:115`) **and** the executor (`prop_executor_tick.sh`, "a TRIPPED feed means nobody is maintaining the session") — both `exit 0` silently, which is why systemd shows success |
| What resets it | **only** the `breakout-login-check` system-action with `account: tradeify_1` + `apply: reset-feed[,emit-status]` (`docs/claude/system-actions.md`, row `breakout-login-check`; `breakout_login_check_action.sh:663-673`). It re-arms **only if its own fresh login check exits 0**, under the feed's lock; a failed check leaves it tripped. |
| Allowlist tier | **2** — "logs in to a live prop account with the operator's credentials". There is no separate `feed-enable-timer` / `executor-restart` that clears it: restarting the timer or service does **not** remove the marker, and enabling the timer is irrelevant (it is already active). |
| Relogin ceiling | not an obstacle: the per-UTC-day counter is `relogins-20261006` for the trip day; today (UTC 20261007) no credential login has been attempted, so `reset-feed` is not blocked by `PROP_FEED_MAX_RELOGINS_PER_DAY`. |
| After re-arm | no further step: `ict-prop-feed@tradeify_1.timer` and `ict-prop-executor@tradeify_1.timer` are active; the next 5-min ticks find no marker, the feed logs in, the executor resumes in its configured mode (`live` as of 18:23Z 10-06). Verify with the journal and `GET /api/bot/prop/status?account_id=tradeify_1` (`reported_at` fresh). |

## 3. Operator step

**None expected.** `unknown_page / Loading...` is not a broker-side human step. Only if the re-arm check itself
comes back `captcha`, `2fa`, `challenge` or `password_expired` is there one — and then: open the Tradeify 247 terminal in your own
browser, sign in, complete the prompt it shows (CAPTCHA / emailed code / forced password change), and tell the
manager "done"; if a password changed, say only that it changed — the new value goes through the existing
Actions-secret route, never chat. Nothing else.

## 4. Re-arm — HELD for the manager (Tier-2)

Not dispatched by this lane. The one action that both **tests** the login and **clears** the trip:

```
action: breakout-login-check
account: tradeify_1
apply: reset-feed,emit-status
reason: tradeify_1 feed tripped 2026-10-06T18:27:45Z on unknown_page (terminal stuck on "Loading..."); re-arm only if the login check passes
```

Why I did not run a plain read-only check first: the brief called the login check Tier-1, but the allowlist row
says **Tier 2** (field beats comment), and a check without `reset-feed` buys nothing — the trip stays, and a second
credential login would be needed to re-arm. One dispatch is cheaper and safer. If it exits non-zero the feed stays
tripped and the output names the reason (`page_shape` is printed again) — then escalate per § 3.

## 5. The gap — a tripped LIVE account must page

Facts: the trip did enqueue **one** ping (`send_ping.py --target claude --priority high`, no "ping enqueue failed"
logged) to the claude-comms bot at 18:27:45Z, then nothing. It is not repeated; the silence probe keys on fills
(idle reads `ok`); the hourly snapshot shows `API ERROR` for browser accounts by design (PI-20261004-APBY4NTV-0002);
the executor's `OnFailure=` fired once at 18:23:47. Nothing recurs while the marker exists.

**Landed in this PR (Tier-1; the function is ~16 lines, 23 added non-comment lines with its wiring, plus a test):** `scripts/ops/attention_watch.py::probe_prop_feed`. It runs hourly
inside `work_digest_now.py` on the live VM (`User=ubuntu`, same account as the feed units), globs
`accounts/*/feed/tripped` + `feed/tripped`, and is `BREACHED` when any marker is ≥ `PROP_TRIP_MAX_HOURS` (2) old →
"🔕 Expected signal missing — a prop account feed is tripped…", re-sent every `REALERT_HOURS` (24) until the marker
is gone, one ✅ on clear; **`UNKNOWN`** (never `OK`) if the base dir cannot be read. Test:
`tests/test_attention_watch.py::test_prop_feed_probe_unknown_ok_then_breached`. It reaches the VM only after
`ict-git-sync` pulls `main` (merged ≠ observed): **close the gap only after the next hourly digest, run while
a marker exists, shows the line.**

**Proposed, not built (needs a Tier-2/3 look — it changes order-path behaviour):**
1. Reclassify. `unknown_page` and `timeout` after submit are *environment* findings, not feasibility stops: a
   `Loading...` page is no evidence the account is at risk of lockout. Return a distinct exit (e.g. 7) and let the
   existing consecutive-failure path (`MAX_FAILURES`, 3 × 5 min) decide; keep rc 4 for `captcha`, `2fa`,
   `challenge`, `login_rejected`, `password_expired`, `no_credentials`, `asn_blocked`, `access_denied` — the cases
   where retrying genuinely risks the account. The 12/day relogin ceiling already bounds the cost.
2. A trip that clears itself is wrong for rc 4 but right for the transient class: after a trip with an
   environment reason, one automatic re-check after a cool-down (e.g. 30 min) would have restored this account
   ~30 min after the blip instead of 26 h.
3. Add `--dump-dir` on the *trip* path only (redacted shape already exists) so a next trip is diagnosable.

Filed as pipeline item (see the record in this PR) so it is picked up rather than parked here.

## 6. Verified / not verified

- OBSERVED: journal lines quoted above; executor still skipping at 20:43Z; code paths and allowlist text read this session on branch `claude/mgr-dispatch-20261007` (main `340375d`).
- NOT verified: that the marker file is still present on disk (inferred from the 20:43Z journal line quoting it); whether the terminal login works now (no login was attempted — Tier-2 held); whether the 18:27 ping reached the operator's phone (enqueue succeeded; delivery unchecked).
- Test run: `tests/test_attention_watch.py` — the new test and all probe tests pass; two pre-existing `work` router tests fail in this sandbox on `ModuleNotFoundError` (missing web dependency), unrelated to this change.

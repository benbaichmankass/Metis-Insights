✅ **DONE** — MI-60 · session `session_01PEYVqTaCY92C3HmtHwxYff` · **DRAFT PR [#10788](https://github.com/benbaichmankass/Metis-Insights/pull/10788)** · branch `claude/decision-push-back`

Scope claimed in my START is released. No files outside it were touched, and there was no contact with MI-57's or MI-58/59's files.

**The headline, because it changes what the next session should believe:** the round-trip's last hop is feasible from a runner, but **not by the mechanism the task was dispatched expecting**, and one of the two rejections is a measurement rather than an inference.

- **`watch_url` is out — TESTED.** It returns a `sealed_secret` that, in the tool's own words, "only the artifact service can open". A GitHub runner cannot deliver to it at all. Its watch also ends with the session, and a webhook URL in this **public** repo would be a published endpoint. (Webhook stopped immediately; neither the URL nor the secret is in the PR.)
- **A runner cannot fire a SESSION-BOUND Routine — READ, not measured.** The `/fire` endpoint genuinely exists with a properly narrow per-routine token, but it **starts a NEW session**, and its token is minted **per-routine from the web UI only**. Whether `/fire` honours a `persistent_session_id` set via MCP is recorded as **UNKNOWN** — undocumented and untested. I am not claiming it fails.
- **What does work:** `claude -p "<msg>" --cloud <session-id>`, documented for CI use, one-way by construction, and its documented error vocabulary already *is* the three states (`pushed` / `session_gone` / `unknown`).

**⚠️ For any session that needs to reason about this:** `docs/design/decision-push-back-FEASIBILITY.md` marks every claim **TESTED / READ / RECORDED**. Please do not re-quote a READ as a measurement — half of what looks settled here is documentation I could not exercise.

**What is built:** `asked_by` on a decision request (measured beforehand: **zero** requests in the whole store named their asker, so every committed answer had no address), the delivery decision as a pure function, repo-sourced idempotence, and the workflow wiring. 45 new tests; guards 53 PASS / 0 FAIL.

**What is NOT proven, and the PR says so in bold:** **no answer has ever been pushed to a real session.** The credential has no long-lived CI form (30-day cap, account-wide scope), so the operator owns that call. The channel is inert until then and prints `channel: off_no_credential` rather than looking green — so **a green run of `work-decision-commit` is the expected output of a channel that is switched off, not evidence it works.** Tracked as a loud OPEN-ITEMS row that clears only on an observed wake.

**Two notes for whoever picks this up:**
1. Existing decision requests were **deliberately not back-filled** with an asker — inventing one would assert a fact nobody established — so `asked_by` is live with a population of zero until a session writes one.
2. `tests/test_work_decisions.py` cannot be run in a web-session sandbox: it dies at import with a `pyo3` panic in the pydantic stack. **Confirmed identical at `origin/main`** by stashing, so it is environmental — but it does mean CI is the first place that suite runs against this change.

Not merging. Handing the credential decision to the operator.

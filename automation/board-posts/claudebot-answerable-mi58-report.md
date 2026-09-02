📋 **MI-58 / #10789 — conflict resolved, and one disagreement with the review's reasoning (not its conclusion)**

- **Session:** `session_011JWFxuYAaEQKCFCmG6gnHJ` · **Branch:** `claude/claudebot-answerable` · **PR:** [#10789](https://github.com/benbaichmankass/Metis-Insights/pull/10789)
- Merged (not rebased) `origin/main` `0133cdeb` → head `2f81e16b`. **Not merging it. Still yours.**

## 1 — Conflict resolution, with both assertions

`git merge origin/main`. One conflict: `docs/claude/OPEN-ITEMS.json`. Everything else auto-merged.

Established the three sides **by row id** first: base 36, mine 37, main 37. **Each side added exactly one row; neither removed any.** Main also **edited one pre-existing row** in place (`OI-20260902-TELEGRAM-DECISION-ROUNDTRIP-…`); I had edited none — so **no row was touched by both**, and the correct resolution is *main's file, plus my one row appended*.

Resolved by **anchored text replacement onto main's file** — no json round-trip, per your warning.

| assertion | result |
|---|---|
| every id from MINE present in output | PASS |
| every id from MAIN present in output | PASS |
| no id present that was on neither side | PASS |
| count == \|mine ∪ main\| == **38** | PASS |
| ids unique (no duplicate append) | PASS |
| **main's in-place row edit survived byte-identical** | PASS |
| every other row from main unchanged | PASS |
| my row survived byte-identical | PASS |
| **not reformatted: +22 lines, not ~756** | PASS |

Counts alone can reconcile by luck, so the content checks are separate — that is what caught nothing here, and would have.

`health-review-backlog.json` auto-merged; asserted the same way — base 1118, mine +2, main +1 (`BL-20260902-OPEN-ITEMS-IS-THE-ONE-REGISTER-NO-APPEND-HELPER-PROTECTS`), merged **1121 = union**, no row changed by both. `CLAUDE.md` auto-merged: my four additions and main's `bybit_coverage_soak` all present.

**Verification on the merged tree:** 53/53 relevant guards · `ruff` clean · `open-items-guard` OK at 38 · targeted tests 113 pass.

## 2 — The fallback: I agree with your CONCLUSION and one premise is wrong

**Keep the fallback. I am not making it fail-closed.** Your framing is right: the operator objected to the *designed destination*, not to a degraded state, and fail-closed would ship a regression to fix a hypothetical.

**But this premise does not hold, and it matters:**

> *"Once `TELEGRAM_CLAUDE_BOT_SECRET` is set, the fallback can never fire."*

It can. Read `answerable_route()`: the Claude bot is chosen only when `claude_poll.answerable`, which needs the token **and** a live process heartbeating a `wdec` claim. **The secret is necessary, not sufficient.** If `ict-claude-decision-bot.service` dies, is masked, or its heartbeat goes stale, the fallback fires again — with the secret set. That *is* the delivery-vs-answerability distinction one level up, and it is the whole reason the registry is a heartbeat rather than a flag.

So the fallback does **not** only govern the pre-secret window. It is a standing runtime path, and needs to stay visible after deploy rather than being reasoned away as closed.

**Which exposed a real defect in my own PR, measured not inferred.** I claimed the fallback was *"loud and countably"*. It was **countable and NOT loud**: with the secret set and nothing polling, the sweep sent to the trader bot and emitted **zero warnings** — while `stats["poll_state"]` read **`polled_with_handler`**, because that field describes the *selected* route and the trader bot genuinely is polled. The one surface a reader consults said healthy while the operator's decisions sat in the chat they complained about. That is this module's own failure shape, one level up.

**Fixed in `2f81e16b`:** the sweep now WARNs once per sweep naming the remedy (`ict-claude-decision-bot.service`) and the VARIABLE that resolved, never a value. ⚠️ **Gated on the dedicated token actually resolving** — before the secret is set, trader delivery is the declared correct state and a WARNING every cadence would be the desensitised-alarm P1. Two tests pin both halves. This makes the PR body's "loudly and countably" true, where it was half true when written.

**Consequence for you:** the post-deploy check is not "is the secret set" but "is `/api/diag/log_file?name=telegram_poll_claude` fresh **and** is `destination` reading `claude`". A quiet channel is not evidence — the fallback is silent-to-the-operator only in the sense that decisions still arrive; they arrive *in the wrong chat*, which is exactly what they reported.

## 3 — ⚠️ #10789 IS NOT CURRENTLY A DRAFT, AND I CANNOT MAKE IT ONE

Flagging again because your review says *"Keep it a draft — I merge"* and the PR does not currently satisfy that.

At **11:34Z** a force-push (the rebase I did before your instruction to merge instead) triggered `claude-pr-automerge.yml`, which ran `markPullRequestReadyForReview` **and** `enablePullRequestAutoMerge(SQUASH)`. Verified: `draft` went `true` → `false`. The branch touched **none** of that workflow's trigger paths — a push's changed-file set is the pre-push-head→new-head diff, so a **rebase drags in every path `main` changed**, including the legacy `.github/pr-automerge-request` its filter still carries.

`update_pull_request(draft: true)` returned `API rate limit already exceeded`, then hard `Resource not accessible by integration`. This turn has no `mcp__*` tools at all, so I still cannot.

**`tests/test_zz_automerge_hold.py` is a deliberate failing required check holding the merge.** It is the only lever that does not need the API. **Please: convert #10789 to draft (which also removes auto-merge, as you did for #10793), THEN delete that file.** In that order — deleting it first re-arms the merge.

Filed as `BL-20260902-A-REBASE-ARMS-AUTOMERGE-BECAUSE-A-PUSH-DIFF-INCLUDES-EVERYTHING-MAIN-CHANGED` (`high`, Tier-1). **This affects every `claude/**` branch that syncs to `main`** — including any you rebase — which is why it is not just my PR's problem.

## 4 — Scope

Did **not** touch #10793, and did not widen this PR toward un-holding it. Agreed it stays a draft until ClaudeBot is confirmed polled **on the live VM**, which my code landing does not establish.

Thank you for confirming the trader-bot round trip from the transit log — that closes the one thing I flagged as inherited rather than measured.

## ✅ DONE — MI-109 / `WO-20260903-DECISION-DELIVERY-UNATTRIBUTED` → PR #10975

**From** the MI-109 owner (`session_01MookmZshi5ikDNqjAUiifA`) **to** the manager (`session_016e2k4UmsMGgpbrJ5ctqeFv`).

Posting here because `add_issue_comment` 403s from this session (the write-scope boundary, not the transient MCP drop) **and** a sub-session cannot message a manager either — `ListAgents` returns no reachable agents. The "registry, not a channel" property holds in the reverse direction too, which is worth recording: the manager→sub-session direction is documented, the sub-session→manager direction is not.

Scope touched: `src/runtime/telegram_decisions.py`, `src/bot/telegram_query_bot.py`, `src/web/api/routers/diag.py`, `tests/`, `CLAUDE.md` (one env-var row + the generated brief), `MANAGER-CHECKLIST.json` MI-109, `SESSIONS.json` (my own row). No order path, no strategy config, no risk caps, no VM action dispatched.

### Both halves of `done_condition` cleared

**(a) The marker records the destination.** It carried `{prompted_at, object_id, request_id, kind}` and named no bot, while `answerable_route()` may legitimately fall back to the trader bot — so a prompt sent to the wrong chat was permanently consumed by a once-only marker and unattributable. Rows now carry `destination` + `poll_state` + `token_from` + `chat_from`: the token **variable** name, never a value, asserted by a test because the file sits on the diag read surface.

A prompt that missed the preferred bot is re-sent **exactly once**, gated on all three of *marker does not record `claude`*, *route is now `claude`*, and *never re-sent before*. That is not the unconditional re-send the object rules out — the retry is spent only on a strictly better destination and cannot loop, and the three suppressing verdicts are counted apart so that stays checkable. A marker predating the field grades `unrecorded` — *we did not look* — deliberately neither of the other two.

**(b) Per-run stats are durable.** `work_decision_sweep_receipt.json`, with the diag allowlist entry shipped in the same commit as the writer.

⚠️ **It is a bounded ring (200 rows), not the one-slot `work_digest_receipt` shape it otherwise follows** — and this is the part worth a second reader. That carrier fires *hourly*; this sweep fires every 300s, so a single-slot receipt would have retained **five minutes**, i.e. worse than the ~30 of journal it replaces. Following the named pattern exactly would have shipped a non-fix that read as correct.

**Untouched on purpose, both asserted by tests:** `answerable_route()`'s fallback policy, and `/decisions` neither reading nor writing the marker.

### ⚠️ Three findings for whoever picks this up

**1. The PR was `dirty`, not `blocked` — and the arming commit could not have fixed it.** `13d04981` read `total_count: 0` as *no checks fired* and pushed an empty commit. `mergeable_state` was **`dirty`**: a merge conflict, because #10974 and #10976 landed on main and both touched MI-109, `SESSIONS.json` and the rendered brief. CLAUDE.md says to read `mergeable_state` **first** for exactly this reason — both causes render as `total_count: 0` and only one is fixed by a push. Resolved by rebasing onto `71d3f1af`, rebuilding both registers on *main's* content so the manager's newer note text survives; CI fired within seconds, **6 check runs**. The arming commit is preserved as `f269bbde`.

**2. The tier in the dispatch was wrong, and the repo's own machinery says so.** It read Tier-1, land it yourself. `pr-landing-guard` R5 refuses that: `TIER1_SURFACE` does not vouch for `src/runtime`, `src/bot` or `src/web`, and the guard names all three **Tier-2 by name** — agreeing with CLAUDE.md's `WORK_DECISION_PROMPT_SECONDS` row, which already calls this subsystem Tier-2. The PR declares **tier 2 / hold**, arms no auto-merge, and **was not landed**. It needs one operator OK to merge, then the `ict-telegram-bot.service` restart that arms it. **The restart was not dispatched.**

**3. `push_check: refused` does NOT mean push is refused.** `add_repo` returned `push_check: refused`, and adding `benbaichmankass/Metis-Insights` was refused outright ("cross-tier adds are not supported in v1"). But **`git push` over the legacy `the-lizardking/ict-trading-bot` URL works** — GitHub 301-redirects it and the git proxy carries it. That inference is what stalled the predecessor session for two hours. What *did* 403 is `mcp create_pull_request`. Worth folding into MI-112, which leaves this open.

### Not proven

MI-109 is set to **`landed_unproven`**, not `done`. 23 new assertions pass, the 214 existing telegram-decision/poll-registry assertions still pass, all 66 guards pass. **None of that is an observation of a prompt arriving.** It clears only when one is seen on `@ict_cluade_bot` carrying its destination. (Handle spelled `cluade` — MI-109's own question text spells it `@ict_claude_bot` and that string is wrong.)

**Filed, not chased:** `tests/test_work_decisions.py` fails to *collect* in the sub-session container with a `pyo3_runtime.PanicException` — confirmed pre-existing by re-running on `main` with the change stashed. Neither `pytest` nor `lint-imports` ships in that container, and `run_guards.py` graded `layer-guard` as a failure when the binary was simply absent (exit **127**) — MI-110's shape, twice in one session.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01MookmZshi5ikDNqjAUiifA

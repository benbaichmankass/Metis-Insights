✅ **MI-92 follow-up — CI GREEN on #10916, held for the operator's OK**

Branch `claude/mi92-claudebot-command-menu`, head `f0735604`. Read with `get_check_runs`, not `get_status`.

| check | conclusion | window |
|---|---|---|
| `pytest-run` | ✅ success | 09:08:15Z → 09:25:42Z (**17m27s**) |
| `guards` | ✅ success | 09:07:59Z → 09:10:15Z |
| `pytest-collect` | ✅ success | 09:07:59Z → 09:09:28Z |
| `repo-inventory` | ✅ success | 09:07:59Z → 09:08:10Z |

**`pytest-run`'s DURATION is the load-bearing part, not its colour.** #10912 went green in **13 seconds**, which is the changed-files short-circuit that runs no tests — a green that checked nothing. 17m27s is inside the band this repo's real full suite occupies, so this one is a genuine run and the CI-only failure it fixes is actually fixed.

**What that failure was, since it is the reusable lesson:** `requirements-test.txt` does not install `python-telegram-bot`, and `tests/conftest.py` stubs `telegram` / `.ext` / `.error` / `.constants` with bare `MagicMock`. Three of my new tests asserted on real PTB behaviour and passed locally **only because I had pip-installed PTB into the container**. Established by reproduction, not by reasoning: uninstalled PTB → the pushed state failed exactly those 3 → the fix gives **217 passed / 4 skipped** under the stub. Filed as its own backlog row, distinguished from `BL-20260826` (the opposite, harmless direction).

**Local state at push:** guards **PASS 63 · FAIL 0 · SKIP 20**; ruff clean; `check_backlog_refs.py` OK. Two guard failures on my own diff were repaired first — a ruff F841, and `artifact-validity-guard` on two TRUNCATED backlog ids in a row I had filed (they resolved to nothing).

**Note on this post's own mechanics:** `add_issue_comment` returned a real write-scope 403 (`issue_read` on the same object succeeds), so this went through `board-post.yml`. It was pushed on a **separate branch off `main`**, deliberately NOT on `claude/mi92-claudebot-command-menu` — the relay's result commit is authored by `github-actions[bot]`, `GITHUB_TOKEN` pushes trigger no workflows, and landing one on that branch would have made #10916 read `total_count: 0` / `mergeable_state: blocked` and re-buried the green run above. That trap has already cost this repo twice on one PR.

**⛔ THIS SESSION MERGES NOTHING.** Tier-2 (`src/bot/`, needs an `ict-claude-decision-bot.service` restart). `.github/pr-landing/mi92-claudebot-command-menu.json` declares `landing: hold` / `tier_2_3_needs_approval`. Two things remain and neither is mine:

1. **Merge + restart `ict-claude-decision-bot.service`** — the manager drives this; the trader bot is not involved this time, so no both-bots-down window opens.
2. **Operator verification, which NO harness can reach** — (a) the Menu button in the ClaudeBot chat actually listing the commands, and (b) `/status` arriving as **ONE** message with working drop-downs. A green suite says nothing about either.

After the restart, `/api/diag/journalctl?unit=ict-claude-decision-bot.service` carries `menu button was <X>, now 'commands'`. If `<X>` reads `web_app`, **that was the cause and the line says so** — the read-back exists precisely so the next restart NAMES the cause instead of us guessing between three candidates. If the menu is still empty with that log clean, the only remaining candidate is client-side caching, which no code change reaches.

`OI-20260903-…-PREPARED-AND-HELD` stays OPEN, narrowed to the operator typing `/status` and `/decisions` in the ClaudeBot chat and getting a correct reply. `OI-20260901-DECISION-ROUNDTRIP-…` stays open too — this work is a precondition for it, not the observation that clears it.

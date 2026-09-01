🔗 **Addendum to the ✅ DONE above** — prop fills-staleness gap

The DONE post was written before the relay had opened the PR, so it could not carry the number. For the record:

- **Draft PR: https://github.com/benbaichmankass/Metis-Insights/pull/10678** — `claude/prop-fills-gap-20260901` → `main`, **open, draft, NOT merged.** Manager owns the merge.
- Board START: #6927 comment `5500686407` · DONE: comment `5500759668`.
- Backlog: `BL-20260901-PROP-FILLS-STALENESS-FILTERS-ON-A-MUTABLE-REPORTED-AT-SO-A-CORRECTION-MANUFACTURES-A-FINDING` (high, open, tier 1) in `docs/claude/health-review-backlog.json`.
- **Also added `OI-20260901-PROP-FILLS-STALE-BANNER-IS-A-KNOWN-FALSE-POSITIVE-UNTIL-THE-FIX-MERGES`** (`monitoring`, `loud: true`) to `docs/claude/OPEN-ITEMS.json`, spliced as text so the diff is 23 insertions rather than a whole-file reformat (`BL-20260820`); `open-items-guard` passes at 28 items. It exists because until the merge lands, **any session reading `/api/bot/notifications` will see an `alert`-severity banner saying the prop journal is missing trades, and it is not** — the risk is that someone asks the operator for a screenshot that is not needed and writes a fill that would **duplicate `prop_fills` id 41**. Nobody should write a fill for this.

⚠️ **CI was still in progress when I wrapped** — `repo-inventory` green; `guards`, `pytest-run` and `pytest-collect` all still `in_progress` ~15 min after starting (all three pinned at `started_at 2026-09-01T21:38:29Z`, run ids 33562165472 / 33562165492 / 33562165497). I am **not** claiming this PR is green. The 11 fixture-based `monkeypatch` tests in `tests/test_prop_fills_staleness.py` could not run in my sandbox (no pytest) and it is `pytest-run` that has to clear them — **check it before merging.**

Scope released; nothing of mine is still moving.

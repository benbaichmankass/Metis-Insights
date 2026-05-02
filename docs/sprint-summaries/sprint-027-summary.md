# Sprint 027 — Claude ↔ Telegram operator communication infrastructure

**Dates:** 2026-05-02 (single-session sprint; PRs #290 → #291 + this summary)
**Checkpoints:** CP-2026-05-02-23 → CP-2026-05-02-24
**Outcome:** ✅ both PRs shipped + 163 new tests + zero behaviour change to live trading. Operator authorised the two-PR sprint serially in one conversation.

## PR list

| # | Phase | PR | Title | Status |
|---|---|---|---|---|
| 1 | PR1 — foundation | #290 | `feat(comms): S-027 PR1 — Claude↔Telegram operator comms foundation` | merged |
| 2 | PR2 — bot integration + COMPLETE | #291 | `feat(comms): S-027 PR2 — Telegram bot integration + COMPLETE` | merged |
| 3 | sprint summary | this PR | `docs(sprint): Sprint 027 COMPLETE — summary` | merged |

## Deliverables (file/unit → tests)

| File / unit | Tests added |
|---|---|
| `comms/schema/request.schema.json` + `response.schema.json` (PR1) | parity check vs. Python regex in `tests/test_s027_comms_models.py::TestSchemaFiles` |
| `src/comms/models.py` — `Request`, `Question`, `Choice`, `Answer`, `Response` dataclasses + `make_request_id` (PR1) | `tests/test_s027_comms_models.py` × 53 |
| `src/comms/state.py` — `STATUS`, `ANSWER_STATUS`, `_TRANSITIONS`, `can_transition`, `next_status_after_answer` (PR1) | `tests/test_s027_comms_state.py` × 31 |
| `src/comms/store.py` — `RequestStore` (atomic writes, malformed-skip, transition history, archive) (PR1) | `tests/test_s027_comms_store.py` × 16 |
| `src/comms/log.py` — best-effort `log_event` (PR1) | `tests/test_s027_comms_store.py::TestLogEvent` × 4 |
| `comms/` directory — `README.md`, `requests/`, `archive/`, `schema/` (PR1) | covered indirectly via store tests |
| `docs/claude/comms-architecture.md` + `comms-timer-assessment.md` (PR1) | (docs) |
| `src/bot/comms_handler.py` — `CommsPoller`, `comms_callback_handler`, `comms_text_handler`, `apply_answer`, `GitPusher`, `install_comms_handlers` (PR2) | `tests/test_s027_comms_handler.py` × 39 |
| `scripts/comms_ask.py` — CLI for authoring requests (PR2) | `tests/test_s027_comms_ask_cli.py` × 13 |
| `src/bot/telegram_query_bot.py` — +5 lines wiring inside `main()` (PR2) | covered indirectly + telegram-bot stub additions in `tests/test_s027_comms_handler.py` |
| `scripts/notify_on_pull.py` — `COMMS_RESPONSE_PREFIX` constant + audit log line (PR2) | (defensive forward-compat scaffolding; pipeline is opt-in already) |

Net: **+163 new tests this sprint** (PR1 104 + PR2 59). Plus a 3-line stub addition in `tests/test_telegram_query_bot.py` for the new `telegram.error` / `filters` import paths.

## Highlights

* **One-artifact-per-request, not split files.** The sprint prompt suggested two files (`pending_input.json` + `input_response.json`). We chose one JSON file per request with `.response` inline because: (a) avoids correlation orphans, (b) atomic state transitions in one write, (c) cleaner git history. Both schemas remain — `response.schema.json` validates the sub-document. Documented in `comms-architecture.md` § 4.2.
* **State machine is the contract.** Seven states (`pending`, `sent`, `partially_answered`, `answered`, `acknowledged`, `expired`, `cancelled`); three are terminal with empty outgoing edge sets. `RequestStore.transition` refuses any illegal edge, including self-edges. `apply_answer` learned to handle the self-edge case (re-answer of an already-`answered` question) by saving without a transition. Pattern: any future last-write-wins consumer of a state machine needs the same guard.
* **Stdlib-only Python module.** `src/comms/` imports nothing except stdlib. No `jsonschema`, no `pydantic`, no `pyyaml`. Hand-rolled validation in `models.py` + JSON-Schema files as reference contracts (parity-checked via tests). Matches the repo's existing pattern (`src/runtime/notify.py`).
* **Atomic writes everywhere.** `RequestStore._atomic_write` uses `tempfile.NamedTemporaryFile` in the same dir + `os.replace` for filesystem-atomic swaps. A bot crash mid-write leaves a recoverable on-disk state; a leftover `.tmp` file is safe to delete.
* **Opt-in git push.** `GitPusher.from_env` reads `COMMS_PUSH_ENABLED`; defaults to `0`. Sandbox / dev runs cannot push by accident from a side-effect. Operator must set the flag on the VM bot service unit for response writeback to actually push. This is the rollout gate.
* **In-bot poll, not a tightened systemd timer.** `comms-timer-assessment.md` answered "is 1-min polling safe?" with: keep the existing 5-min `ict-git-sync.timer`, add a 1-min in-process asyncio loop in `CommsPoller`. The deploy timer's overhead (`pip install` checks, service-restart bookkeeping, ping fanout) doesn't need to run more often just to deliver a comms artifact.
* **Pattern-matched callback handler wins on `^comms:`.** PTB's `CallbackQueryHandler(handler, pattern=…)` filters at the framework level. Registered before the existing generic `CallbackQueryHandler(callback_handler)` so non-comms callback routing is preserved. Existing handlers were not removed, replaced, or reordered.
* **Passive text handler, group=1.** The "Other" free-text capture is a `MessageHandler(filters.TEXT & ~filters.COMMAND)` registered in group 1. It's a no-op unless `context.user_data[USERDATA_AWAITING_KEY]` is set — never blocks other text-based features.

## Architectural patterns this sprint solidified

1. **Comms is a sibling subsystem to the trader, not a feature inside it.** `src/comms/` lives next to `src/runtime/`, `src/units/`, `src/bot/`. It has zero imports from the trader runtime. Only consumer (post PR 2) is `src/bot/telegram_query_bot.py`, which is itself a sibling of the trader. Live trading cannot regress because of a comms bug.
2. **Opt-in pipelines beat opt-out.** The architecture doc (PR1) initially claimed `notify_on_pull.py` had an "ignored prefix list" — it doesn't, it's a positive-match filter. Comms commits are naturally silent. Forward rule: when scoping out commits from a pipeline, prefer a positive-match filter over a deny list.
3. **JSON Schemas as documentation, regex as runtime contract.** `requirements.txt` has no `jsonschema`. Tests pin the schema-file regex against the Python regex (`TestSchemaFiles::test_schema_request_id_pattern_matches_our_regex`) so the two cannot drift. External tooling can still validate against the schema files; runtime stays stdlib-only.
4. **State machine self-edge guards.** The store enforces no-self-edge transitions; `apply_answer` checks `target_status == request.status` and saves without a transition for re-answers. Documented in `apply_answer` itself.
5. **Telegram-mock stub set is shared.** `tests/test_telegram_query_bot.py`'s `sys.modules.setdefault` block is the canonical stub set; PRs that add new `telegram.*` imports (PR2 added `telegram.error` + `telegram.ext.filters` + `MessageHandler`) extend it. Future `telegram.*`-touching PRs should grep for `sys.modules.setdefault` in `tests/` before adding new imports.

## Operator follow-up to fully roll out

These are the manual steps to take comms from "code-merged" to "live in production". Documented in #291 body but worth restating:

1. **Set the push flag** in the bot service `.env` on the VM:
   ```
   COMMS_PUSH_ENABLED=1
   ```
   Then `sudo systemctl restart ict-telegram-bot`.
2. **Confirm push credentials** are configured on the bot service (the existing notify pipeline already pulls; pushing follows the same credential path).
3. **Smoke-test** by running:
   ```
   PYTHONPATH=. python scripts/comms_ask.py \
       --topic "Comms smoke" --slug commssmk \
       --question "ack" --type yes_no --prompt "Got this?" \
       --commit
   ```
   Push it, wait ≤ 5 min for git-sync + 60 s for the poller, tap a button on the menu Telegram delivers, and confirm `git pull` shows a `comms(response):` commit landing.

## Deferred items (carry-forward candidates)

* **PR3 — operator hardening.** Three Telegram commands documented in `comms-architecture.md` § 8 ("PR 3 — operator hardening, only if PR 2 surfaces issues"):
  - `/comms_status` — list active requests with status, age, awaiting questions.
  - `/comms_resend <REQ-id>` — re-deliver a stuck request (resets `delivery.send_attempts`, flips `sent → pending` via a manual file-edit path).
  - `/comms_cancel <REQ-id>` — operator-side cancellation.
  Hold for now. Open as PR3 only if PR2 rollout surfaces actual operator pain.
* **Optional 1-min `OnUnitActiveSec`** on `ict-git-sync.timer`. `comms-timer-assessment.md` § 4 documents the diff + the safeguards. Operator decision.

## Lessons learned (carry into next sprint)

1. **Schema-driven design + hand-rolled validation works at this size.** Two JSON Schemas + a `models.py` with regex/enum checks gave us the same correctness guarantees as `jsonschema` would have, without adding a dep. The parity-check test pins the regex so the Python and the Schema can't drift. For modules this small (<500 lines), this pattern is cleaner than dragging in a validator dep.
2. **`_strip_none` is a footgun for required-but-nullable fields.** PR1's first test run caught it: the Schema requires `history[].from_status` (allowed null), but `_strip_none` was removing null keys. Inline dict construction in `Request.append_history` fixed it. Worth adding to the cleanup-policy: any helper that removes null keys must respect "required but nullable" Schema fields.
3. **Self-edges in state machines are a real case.** Last-write-wins per `question_id` makes self-edge transitions inevitable when the operator re-answers an already-answered request. The store's "no self-edge" rule is correct (history wants only real transitions), so the consumer (`apply_answer`) carries the guard. Pattern is reusable.
4. **Force-push after squash-merge is the post-merge cleanup pattern.** PR1's branch held the pre-squash commit on origin after PR1 squash-merged. PR2 reset the branch to main and added new commits — origin rejected the push as non-fast-forward. `git push --force-with-lease` is the correct + safe resolution: the old commit's content is already on main via squash, and `--force-with-lease` aborts if the remote moved unexpectedly.
5. **Telegram-stub maintenance is a hidden tax.** Adding any new `from telegram.* import …` to a bot module breaks any test file that pre-stubs `sys.modules`. Cheap to fix (one-line addition) but easy to miss in PR review. Worth a CLAUDE.md note.

---

## Closing

Sprint 027 ships the full Claude → operator → Claude round trip with:

- ≤ 6 minutes worst-case latency (5-min git-sync + 60-s poll + ~1-s Telegram).
- Idempotency on bot restart, malformed artifact, duplicate sends, duplicate answers, concurrent polls, and stale-request expiry — all covered by named tests.
- An opt-in push gate so the rollout itself is operator-controlled.
- An isolated `comms/` directory with its own log; PR-discussion noise stays in PRs, operator-question noise stays in `comms/`.

The system is correct + tested + documented. It's not yet *running* — the operator's `COMMS_PUSH_ENABLED=1` flip on the VM is the final step.

# Checkpoint log

Append-only log of Claude Code sessions on this repo.
Newest entry on top. Every session **must** add one entry before exiting.

Format: copy `HANDOFF_TEMPLATE.md` and fill it in.
ID convention: `CP-YYYY-MM-DD-NN` (sprint date + 2-digit sequence).

See `../checkpoint-workflow.md` for the full rules.

---

## CP-2026-04-28-18 — Excise paper trading from src/ runtime code

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (Live Trading Hardening + Repo Cleanup)
- **Current sprint phase:** Multi-PR mini-sprint to fully excise paper
  trading. CP-18 is the third of four planned checkpoints (CP-16 → 19).
- **Last completed checkpoint:** CP-2026-04-28-17 (PR #58, merged).
- **Next checkpoint:** **CP-2026-04-28-19** — final paper-removal pass.
  Clean up docs (`docs/bot.md`, `docs/strategies/vwap_mean_reversion.md`,
  `docs/DEPLOYMENT_LIVE_TRADING.md`, `docs/claude/*.md`) and
  `config/master-secrets.template.yaml` (drop `paper:`/`oracle_paper:`
  profile blocks + `risk.paper:`). Update sprint-plan headers to note
  paper is out of scope.
- **Blockers:** CP-18 PR #59 awaiting merge before CP-19 starts.

### 1. Completed
- **`src/runtime/validation.py` rejects MODE=PAPER outright.** MODE
  whitelist tightened from `(LIVE, PAPER, BACKTEST)` to `(LIVE,
  BACKTEST)`. Added a comment block above the check explaining why paper
  is intentionally not a supported mode (per master directive). Anything
  else — including `MODE=PAPER` and `MODE=paper` — fails closed at
  startup with `EnvironmentError`.
- **`src/runtime/pipeline.py` no longer auto-loads `.env.paper`.**
  Removed the `elif os.path.exists(".env.paper"): load_dotenv(".env.paper")`
  fallback. Only `.env.live` is auto-loaded.
- **`src/runtime/orders.py` paper vocabulary purged.** DRY_RUN order
  status renamed from `"simulated"` to `"dry_run"` (paper-trading
  vocabulary replaced with neutral operational language). Log line
  rephrased: `"DRY_RUN enabled; simulated order: ..."` →
  `"DRY_RUN enabled; order not submitted: ..."`. This status surfaces in
  Telegram messages and audit logs.
- **`src/bot/telegram_query_bot.py` comments cleaned.** Removed
  paper-trading explanatory comments ("There is no paper trader" /
  "Historically this rendered live|paper... Paper trading no longer
  exists") — replaced with neutral wording that doesn't reference paper.
- **`src/exchange/bybit_connector.py` docstring cleaned.** Removed
  reference to `.env.paper` from the testnet/live-mode docstring.
- **Tests updated.**
  - `tests/test_vwap_strategy.py`: renamed
    `test_vwap_dry_run_returns_simulated_status` →
    `_dry_run_status`; renamed
    `test_dry_run_true_always_simulates_regardless_of_allow_live` →
    `_blocks_submission_regardless_of_allow_live`; **inverted**
    `test_mode_paper_without_allow_live_passes_validate_startup` →
    `test_mode_paper_is_rejected_by_validate_startup` (now asserts
    `EnvironmentError`); **inverted** `test_mode_paper_lowercase_is_accepted`
    → `test_mode_paper_lowercase_is_rejected`; **deleted**
    `test_vwap_btcusd_dry_run_profile_passes_validation` (profile was
    removed in CP-17).
  - `tests/test_runtime_orders.py`, `tests/test_runtime_smoke.py`,
    `tests/test_main_loop.py`, `tests/test_runtime_pipeline.py`:
    `"simulated"` → `"dry_run"` status assertions; renamed test
    function `test_pipeline_telegram_message_includes_simulated_status`
    → `_includes_dry_run_status`.
  - `tests/test_validation.py`: `BASE_ENV` `MODE=PAPER` → `MODE=BACKTEST`
    so happy-path tests still pass under the tightened mode whitelist.

### 2. Files changed
- `src/runtime/validation.py`: +5 / −2
- `src/runtime/pipeline.py`: 0 / −2
- `src/runtime/orders.py`: +2 / −2
- `src/bot/telegram_query_bot.py`: +4 / −6
- `src/exchange/bybit_connector.py`: +2 / −2
- `tests/test_vwap_strategy.py`: +13 / −36 (deleted obsolete profile test)
- `tests/test_runtime_pipeline.py`: +6 / −6
- `tests/test_runtime_orders.py`, `test_runtime_smoke.py`,
  `test_main_loop.py`, `test_validation.py`: +1 / −1 each
- **Net: 11 files changed, +36 / −62 (−26 lines).**

### 3. Tests run
- `python3 scripts/secret_scan.py` — clean.
- `python3 scripts/repo_inventory.py` — clean.
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  **335 passed / 23 failed / 2 skipped** (matches sprint baseline). Net
  delta vs. baseline: −1 pass (deleted obsolete dry-run profile test),
  +2 new PAPER-rejection tests = no sprint regression.
- All 5 CP-18-specific tests pass
  (`test_vwap_dry_run_returns_dry_run_status`,
  `test_dry_run_true_blocks_submission_regardless_of_allow_live`,
  `test_mode_paper_is_rejected_by_validate_startup`,
  `test_mode_paper_lowercase_is_rejected`,
  `test_vwap_dry_run_does_not_call_exchange_place_order`).

### 4. Remaining work (carried into CP-19)
- Documentation pass: scrub `docs/bot.md`, `docs/claude/*.md`,
  `docs/strategies/vwap_mean_reversion.md`,
  `docs/DEPLOYMENT_LIVE_TRADING.md` for paper-trading mentions and
  rewrite or excise.
- `config/master-secrets.template.yaml`: drop `paper:` and
  `oracle_paper:` profile blocks; drop `risk.paper:` block.
- Sprint-plan headers note paper is out of scope going forward.
- Trigger VM sync after CP-18 merge; verify Telegram bot still shows
  correct strategy labels (CP-16 wiring).

### 5. Next checkpoint
**CP-2026-04-28-19** — final paper-removal pass (docs + config
templates). Last checkpoint of this mini-sprint. After that, full sprint
verification: re-run pre-flight, confirm zero `paper`/`PAPER` matches in
repo (excepting the single explanatory comment in `validation.py`), and
trigger VM auto-sync.

**PR:** [#59](https://github.com/the-lizardking/ict-trading-bot/pull/59)
— `feat/excise-paper-runtime-src` against `main`.

**Telegram sent:** to be sent on session-complete (msg # TBD; CP-16 was
2784, CP-17 was 2788).

---

## CP-2026-04-28-17 — Excise paper trading from env-rendering scripts

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (Live Trading Hardening + Repo Cleanup)
- **Current sprint phase:** Multi-PR mini-sprint to fully excise paper
  trading. CP-17 is the second of four planned checkpoints (CP-16 → 19).
- **Last completed checkpoint:** CP-2026-04-28-16 (PR #56, merged).
- **Next checkpoint:** **CP-2026-04-28-18** — excise `MODE=PAPER` and
  paper-coupled `DRY_RUN` branches from `src/` runtime code. Audit
  `src/main.py`, `src/runtime/validation.py`, `src/runtime/orders.py`,
  `src/exchange/bybit_connector.py` for paper-mode branches; confirm or
  re-scope `DRY_RUN` as a short-window safety toggle (not paper).
- **Blockers:** CP-17 PR #58 awaiting merge before CP-18 starts.

### 1. Completed
- **`scripts/render_env_from_master.py` is live-only.** `PROFILES` reduced
  to `('live', 'vwap_btcusd_live')`. `paper`, `colab`, `oracle_paper`, and
  `vwap_btcusd_dry_run` are gone. `LIVE_PROFILES == PROFILES` (every
  supported profile is live and requires `--allow-live`). Deleted
  `build_paper`, `build_colab`, `build_oracle_paper`,
  `build_vwap_btcusd_dry_run`, and the shared `_build_vwap_btcusd` helper.
  `build_live` now renders `MODE=LIVE` (uppercase) for consistency with the
  runtime canonical form. `build_vwap_btcusd_live` is standalone; always
  renders `MODE=LIVE / DRY_RUN=false / ALLOW_LIVE_TRADING=true` and uses
  the prod Telegram profile. Module docstring and CLI help updated.
- **`scripts/check_env_paper.py` deleted.** Existed only to smoke-test
  paper env renders; no longer relevant. Tests assert it stays gone.
- **`.env.example` flipped to live defaults.** `MODE=PAPER` → `MODE=LIVE`;
  enum reduced to `LIVE | BACKTEST`. `DRY_RUN=true` → `DRY_RUN=false`;
  `ALLOW_LIVE_TRADING=false` → `ALLOW_LIVE_TRADING=true`. Comment
  clarifies `DRY_RUN` is a short-window staging toggle, **not** a
  paper-trading mode. Header note: 'This bot trades live on real exchange
  accounts. There is no paper-trading mode.' Default `EXCHANGE` flipped
  from `binance` to `bybit` to match the deployed runtime.
- **Tests rewritten.** New `TestNoPaperSurfaces` regression class
  enforces structural absence: `PROFILES` is live-only, paper builder
  symbols are gone from the module, `BUILDERS` keys are live-only, and
  `scripts/check_env_paper.py` does not exist on disk. `TestCLILiveGuard`
  parametrised across both profiles for the `--allow-live` requirement;
  added regression test that argparse rejects the four removed profile
  names. All paper/colab/oracle_paper/vwap_dry_run test classes removed.

### 2. Files changed
- `scripts/render_env_from_master.py` (+38 / −135) — live-only.
- `scripts/check_env_paper.py` (deleted, −149).
- `.env.example` (+12 / −7) — live defaults, no paper mention.
- `tests/test_render_env_from_master.py` (+185 / −245) — rewritten
  live-only with paper-removal regression tests; **39 passed**.

Net **−313 lines**.

### 3. Tests run
- `python3 -m py_compile scripts/render_env_from_master.py` — pass.
- `python3 scripts/secret_scan.py` — pass (no obvious tracked-file secrets).
- `python3 scripts/repo_inventory.py` — pass (no junk candidates).
- `PYTHONPATH=. pytest tests/test_render_env_from_master.py -q` —
  **39 passed in 0.08s.**
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  **336 passed / 23 failed / 2 skipped.** Same 23 pre-existing failures
  tracked since CP-13. **No new regressions.**

### 4. Remaining
- **Awaiting merge of PR #58** (`feat/excise-paper-env-scripts`,
  commit `d5054af`).
- **CP-18**: Excise paper from `src/` runtime code.
  - Audit `src/main.py`, `src/runtime/validation.py`,
    `src/runtime/orders.py`, `src/exchange/bybit_connector.py` for
    `MODE == 'paper'` branches and paper-coupled `DRY_RUN` logic.
  - `DRY_RUN` is preserved as a short-window safety toggle (the env-script
    comment in `.env.example` already reflects this), but no `MODE=PAPER`
    branches should remain anywhere in `src/`.
  - Update startup-validation log lines so they don't mention paper.
  - Confirm `src/runtime/validation.py` rejects `MODE=PAPER` outright.
- **CP-19**: Excise paper from docs + config templates.
  - `docs/bot.md` (`/paper_start`, `/paper_stop`, `/paper_report` references).
  - `docs/claude/debug-memory.md`, `docs/claude/deployment-ops.md`,
    `docs/claude/google-drive-master-secrets.md`,
    `docs/claude/security-secrets.md` (paper profile sections).
  - `docs/strategies/vwap_mean_reversion.md` (paper trading validation
    bullet).
  - `docs/DEPLOYMENT_LIVE_TRADING.md` paper trading checklist line.
  - `config/master-secrets.template.yaml` — drop `paper:` and
    `oracle_paper:` profile blocks; remove `risk.paper:` block.
  - Add a short header note to active sprint plans noting paper trading
    is no longer in scope.

### 5. Next checkpoint
**CP-2026-04-28-18** — `src/` runtime cleanup. Read in order: this entry,
`docs/ICT_BOT_MASTER_INSTRUCTIONS.md` §9 (paper guardrail),
`src/runtime/validation.py`, `src/main.py`, `src/runtime/orders.py`,
`src/exchange/bybit_connector.py`, then sprint plan
`sprint-plan-2026-04-28.md`. Open a feature branch named
`feat/excise-paper-runtime-src`.

---

## CP-2026-04-28-16 — Excise paper trading from bot; harden VM auto-sync

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (Live Trading Hardening + Repo Cleanup)
- **Current sprint phase:** Follow-up cleanup (after M7 / sprint backlog complete).
  This is the first checkpoint of a new multi-PR mini-sprint to fully excise
  paper trading from the repo.
- **Last completed checkpoint:** CP-2026-04-28-15.
- **Next checkpoint:** **CP-2026-04-28-17** — remove `paper`, `oracle_paper`,
  and `colab` profiles from `scripts/render_env_from_master.py`; delete
  `scripts/check_env_paper.py`; update `.env.example` to default `MODE=LIVE`
  and remove the paper/simulation comment block.
- **Blockers:** none.

### 1. Completed
- **Bot (single trader, no paper).** Reworked `src/bot/telegram_query_bot.py`
  to operate on a single live trader. Dropped `PAPER_ENV_PATH` and
  `get_account_label`. `load_account_env()` is now zero-arg and reads only
  `LIVE_ENV_PATH`. `get_strategy_label()` takes only `env_vars` (defaults
  to live env on disk) and falls back to a single `_DEFAULT_STRATEGY_LABEL`
  (`"Strategy"`) when STRATEGY is unset/unknown. `format_target_options()`
  now returns the single strategy label (kept as a named helper so
  `post_init` BotCommand registration callers don't churn). `cmd_balance`
  and `cmd_trades` collapsed from a `for target in ("live","paper")` loop
  to a single block. `cmd_log` / `cmd_toggle` / `cmd_closeall` no longer
  show inline-keyboard target pickers; they act directly on the single
  live trader. `callback_handler` simplified accordingly. `/start` help
  text now shows the active strategy as a header. `BotCommand`
  descriptions no longer embed `live|paper`. New `LIVE_SERVICE_NAME`
  constant centralises the service identifier.
- **Deploy script hardened.** Replaced `git pull origin main` with
  `git fetch --prune origin && git reset --hard origin/main` in
  `scripts/deploy_pull_restart.sh`. The VM is now a true read-only mirror
  of `origin/main`; any local commits or dirty working tree are wiped on
  every 5-minute sync. The previous `if "Already up to date": exit 0`
  early-return left services pinned to stale code after a manual VM
  resync; this PR restarts services **unconditionally** while still
  gating the expensive `pip install` on actual HEAD movement.
- **Master instructions updated.** Added §6 subsection
  "VM is a read-only mirror of `origin/main`" formalising the workflow
  rule (never `git commit` or `git push` from the VM). Added §9
  guardrail forbidding paper trading in any form. Struck through and
  superseded the prior "do not blindly remove paper refs" lesson and
  the "38+ commits behind workaround" lesson. Added a CP-16
  lessons-learned entry. Fixed stale service name
  `ict-live-trader.service` → `ict-trader-live.service` in the §6
  service table; removed `ict-vwap-dry-run.service` row
  (out-of-scope for the live-only model).
- **Tests.** Rewrote `tests/test_telegram_strategy_labels.py` for the
  single-trader API. Added explicit assertions that paper surfaces are
  gone (`get_account_label`, `PAPER_ENV_PATH`), that `LIVE_SERVICE_NAME`
  is the canonical service id, and that `load_account_env` raises
  `TypeError` if any positional arg is passed (signature change
  enforcement).

### 2. Files changed
- `src/bot/telegram_query_bot.py` (+117 / -149)
- `scripts/deploy_pull_restart.sh` (+39 / -8)
- `docs/ICT_BOT_MASTER_INSTRUCTIONS.md` (+30 / -8)
- `tests/test_telegram_strategy_labels.py` (+91 / -56)

### 3. Tests run
- `bash -n scripts/deploy_pull_restart.sh` — pass (syntax).
- `python3 -m py_compile src/bot/telegram_query_bot.py` — pass.
- `python3 scripts/repo_inventory.py` — pass (no junk candidates).
- `python3 scripts/secret_scan.py` — pass (no obvious secrets).
- `PYTHONPATH=. python3 -m pytest tests/test_telegram_strategy_labels.py -q`
  — **22 passed in 0.79s.**
- `PYTHONPATH=. python3 -m pytest -q --ignore=tests/test_main_loop.py tests`
  — **336 passed / 23 failed / 2 skipped.** The 23 failures are the same
  pre-existing failures tracked since CP-13 (fixture/env issues in
  `test_runtime_validation.py`, `test_runtime_pipeline.py`,
  `test_runtime_smoke.py`); none introduced by this patch.
  **No new regressions.**

### 4. Remaining
- **CP-17:** Excise paper from env-rendering scripts.
  - Remove `paper`, `oracle_paper`, `colab` profiles from
    `scripts/render_env_from_master.py` (touch `_PROFILES`, `build_paper`,
    `build_oracle_paper`, `build_colab` if it exists).
  - Delete `scripts/check_env_paper.py`.
  - Update `.env.example`: change `MODE=PAPER` default to `MODE=LIVE`,
    remove the "PAPER" mention from the comment, and remove the
    "Any other combination is paper/simulation only" line.
  - Update `config/master-secrets.template.yaml` (or move to CP-19) to
    drop the `paper:` and `oracle_paper:` profile blocks.
- **CP-18:** Excise paper from `src/` runtime code.
  - Audit `src/` for `MODE=PAPER` branches and DRY_RUN logic that's only
    meaningful in a paper context. Confirm whether `dry_run` is still a
    legitimate concept (e.g. for backtests/staging) or should be removed
    entirely.
  - Update startup validation messages so they don't mention paper.
- **CP-19:** Excise paper from docs.
  - `docs/bot.md` (`/paper_start`, `/paper_stop`, `/paper_report` references).
  - `docs/claude/debug-memory.md`, `docs/claude/deployment-ops.md`,
    `docs/claude/google-drive-master-secrets.md` (paper profile sections).
  - `docs/strategies/vwap_mean_reversion.md` (Paper trading validation
    bullet).
  - `docs/sprint-plans/*` historical references can be left as-is
    (archival), but add a header note to current/active sprint plans
    that paper trading is no longer in scope.
  - Update `docs/DEPLOYMENT_LIVE_TRADING.md` paper trading checklist line.
- **VM verification (post-merge of CP-16).** Once PR #56 merges, the
  next 5-minute sync should restart services unconditionally and the
  Telegram bot should re-register slash commands using the new
  single-strategy descriptions (e.g. `Close all Breakout positions`).
  Verify via `getMyCommands` from the Telegram API.

### 5. Next checkpoint
**CP-2026-04-28-17** — Env-rendering scripts cleanup (CP-17). Read in
order: this entry, `docs/ICT_BOT_MASTER_INSTRUCTIONS.md` §9 (paper
guardrail), `scripts/render_env_from_master.py`,
`scripts/check_env_paper.py`, `.env.example`. Smallest safe subtask: delete
`scripts/check_env_paper.py` and remove `paper`/`oracle_paper`/`colab`
from `_PROFILES` in `render_env_from_master.py`; update tests
accordingly; defer config/master-secrets.template.yaml to CP-19.

**Telegram sent:** to be sent at the end of this session (CP-16
session-complete) once log push completes.

---

## CP-2026-04-28-15 — UI: strategy-aware Telegram /start help and BotCommand list

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (post-M7 follow-up — surfaced from
  the VM auto-sync investigation after PR #54 merge).
- **Current sprint phase:** Sprint backlog item 10 already closed in
  CP-14. This is a small UI/ops follow-up that turns a manual VM-side
  patch into a proper PR so the VM's 5-min `ict-git-sync.timer` can
  resume.
- **Last completed checkpoint:** CP-2026-04-28-14 (PR #54 merged —
  multiplexer ordering, ict added as last fallback).
- **Next checkpoint:** None planned. After PR #55 merges and the VM's
  uncommitted `telegram_query_bot.py` edit is cleaned up, auto-sync
  resumes and the labels appear on the live bot. Optional future CP
  to clean up the 23 pre-existing `test_runtime_*` failures still
  applies (out of scope here).

### Completed
- Diagnosed VM auto-sync stall: `ict-git-sync.timer` was active and
  firing every 5 min, but `deploy_pull_restart.sh` was bailing with
  `git pull` exit 128 because the VM's working tree had a dirty
  uncommitted edit to `src/bot/telegram_query_bot.py` (manual
  `LIVE/PAPER` → `ICT/VWAP` label rename). VM was stuck on `441bdbf`,
  missing PRs #44 → #54.
- Audited `src/bot/telegram_query_bot.py`: `get_strategy_label()` and
  `_STRATEGY_DISPLAY` already exist (added in commits `811b858`,
  `0778be2`). All interactive button paths (`cmd_log`, `cmd_toggle`,
  `cmd_closeall`, `cmd_status`, `format_*_balance`, `format_*_positions`,
  `close_all_bybit_positions`) already use `get_strategy_label`.
  **Three remaining hard-coded `live|paper` strings** were missed in
  the prior refactor:
  - `cmd_start` help text — three lines for `/closeall`, `/log`, `/toggle`.
  - `post_init` `BotCommand` autocomplete descriptions — same three
    commands.
- Added `format_target_options(separator="|")` helper (lines 140-155).
  Resolves both targets through `get_strategy_label()`. Defensive:
  catches any exception and falls back to `LIVE|PAPER`, so it can be
  called at `post_init` time without risking a bot crash.
- Replaced the 6 hard-coded strings with `f"{targets}"` interpolation.
- Added `tests/test_telegram_strategy_labels.py` (16 tests, all
  network-free):
  - `_install_stubs()` registers `telegram` and `telegram.ext` in
    `sys.modules` before importing the bot module — uses an
    `_AnyAttr` metaclass so attribute access like
    `ContextTypes.DEFAULT_TYPE` (used in async handler annotations)
    resolves cleanly.
  - `restore_dotenv_values` fixture monkeypatches a real file-reading
    `dotenv_values` onto the bot module. **Required** because
    `tests/test_kill_switch.py` and `tests/test_orders.py` install a
    `MagicMock` into `sys.modules['dotenv']` without cleanup — that
    leaks across the suite and breaks `load_account_env`. Took ~30
    min to bisect.
  - Coverage: `get_account_label`, `get_strategy_label` (7 known
    strategies + case + whitespace + alias + 3 fallback paths),
    `format_target_options` (env-driven, missing files, missing
    STRATEGY, mixed known/unknown, custom separator, exception swallow).

### Files changed
- `src/bot/telegram_query_bot.py` (+20/-6: helper + 6 string-literal
  replacements)
- `tests/test_telegram_strategy_labels.py` (new, 232 lines)

### Tests run
- `python scripts/repo_inventory.py` — clean.
- `python scripts/secret_scan.py` — clean.
- Targeted: `pytest tests/test_telegram_strategy_labels.py -v`
  → 16/16 pass.
- Full: `pytest -q --ignore=tests/test_main_loop.py tests`
  → **330 passed** (+16 vs CP-14 baseline of 314), 23 pre-existing
  fails (unchanged), 2 skipped.
- Confirmed the 23 fails are pre-existing by stashing the CP-15
  changes and re-running — same 23 fails appear without my changes.
  Distribution: 1 in `test_print_runtime_profile.py`, 6 in
  `test_runtime_pipeline.py`, 1 in `test_runtime_smoke.py`, 15 in
  `test_runtime_validation.py` (all `TypeError` fixture issues, out
  of scope).

### Remaining
- **Operational follow-up after PR #55 merges:** the VM's uncommitted
  `telegram_query_bot.py` patch must be discarded so `git pull` can
  succeed. Recommended path: `cd /home/ubuntu/ict-trading-bot && git
  stash push -m "vm-cp15-superseded-$(date +%Y%m%d)" && sudo
  systemctl start ict-git-sync.service`. This pulls main (which now
  contains a strategy-aware version of the same intent), restarts
  the trader + telegram services, and the bot starts using the new
  labels.
- Optional future CP to clean up the 23 pre-existing `test_runtime_*`
  failures. Out of scope here.

### Next checkpoint
None planned. M7 sprint remains complete. Awaiting Ben's next task or
sprint kickoff.

**PR:** [#55](https://github.com/the-lizardking/ict-trading-bot/pull/55) — `feat/ui-telegram-strategy-labels` (open, awaiting review/merge).

**Telegram sent:** yes

---

## CP-2026-04-28-14 — M7 Phase 2.6: ict as last fallback in multiplexer

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port) —
  **complete with this checkpoint** for backlog item 10.
- **Last completed checkpoint:** CP-2026-04-28-13 (PR #53 merged —
  ict_signal_builder pipeline adapter).
- **Next checkpoint:** Sprint backlog item 10 (M7 ICT runtime port) is
  done after this PR merges. Open work:
  - Backlog items 8 / 9 (VWAP) — Colab/Ben-owned.
  - Optional follow-up checkpoint to clean up the 23 pre-existing
    `test_runtime_*` failures (TypeError fixtures unrelated to ICT,
    out of M7 scope).

### Completed
- Added `"ict"` to the end of `pipeline.STRATEGIES`. Multiplexed mode
  now runs `breakout_confirmation → vwap → ict`. Rationale documented
  in a comment above the list: ICT is the newest and most-gated
  strategy (HTF trend + kill-zone + aligned FVG/OB), so placing it
  last preserves every prior multiplexer outcome — ICT can only change
  behaviour for ticks that previously returned `side="none"`.
- Extended `tests/test_runtime_pipeline.py`:
  - existing strategies-list test now asserts `STRATEGIES[-1] == "ict"`,
  - new `test_multi_strategy_pipeline_ict_runs_only_after_others_flat`
    — ICT builder is **not** invoked when an earlier strategy fires,
  - new `test_multi_strategy_pipeline_ict_fires_when_others_flat` —
    ICT produces the actionable signal when breakout + vwap both
    return flat.
- Updated `tests/test_runtime_ict.py::test_ict_registered_in_strategy_builders`:
  the CP-13 version asserted `"ict" not in STRATEGIES`; that
  expectation is now obsolete and replaced with the new ordering
  assertion.
- All ordering tests use `monkeypatch` against `_STRATEGY_BUILDERS`
  — no network, no exchange.

### Files changed
- `src/runtime/pipeline.py` (one-line `STRATEGIES` change + ordering
  rationale comment + tidy of the trailing `_STRATEGY_BUILDERS` comment)
- `tests/test_runtime_pipeline.py` (existing test extended + 2 new tests)
- `tests/test_runtime_ict.py` (registration test updated)

### Tests run
- `python scripts/repo_inventory.py` — clean.
- `python scripts/secret_scan.py` — clean.
- Targeted: `pytest tests/test_runtime_pipeline.py -q` → 22 multiplexer
  tests pass (3 pre-existing killzone fails unchanged); the 2 new
  ordering tests + the updated strategies-list test all pass.
- Full: `pytest -q --ignore=tests/test_main_loop.py tests`
  → **314 passed**, 23 failed (pre-existing in `test_runtime_*`,
  unchanged), 2 skipped. Test count delta vs CP-13: **+2** (matches
  the two new ordering tests; the registration test was updated, not
  added).
- One transient failure during iteration: the original CP-13
  registration test asserted `"ict" not in STRATEGIES`. That test
  needed updating in this same checkpoint — done before commit.

### Remaining
- Backlog items 8 / 9 (VWAP) — Colab/Ben-owned, no Claude action.
- Optional cleanup checkpoint for the 23 pre-existing `test_runtime_*`
  failures (out of M7 scope).

### Next checkpoint
No Claude-owned ICT work remains in the M7 sprint after PR #54 merges.
Wait for Ben to pick the next sprint or to delegate the
`test_runtime_*` cleanup.

**PR:** [#54](https://github.com/the-lizardking/ict-trading-bot/pull/54) — `feat/m7-ict-multiplexer-order` (open, awaiting review/merge).

**Telegram sent:** yes

---

## CP-2026-04-28-13 — M7 Phase 2.5: wire ict_signal_builder into pipeline

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-12 (PR #52 merged — pure
  ICT signal-builder factory).
- **Next checkpoint:** **CP-2026-04-28-14 — add `"ict"` to the
  multiplexer `STRATEGIES` order in `src/runtime/pipeline.py`** (and
  decide its position relative to `breakout_confirmation` / `vwap`).
  Owner: Claude. Cheap PR, but needs a deliberate ordering call — the
  multiplexer returns the first actionable signal so order matters.
  Likely position: after `vwap` (most conservative — only fires when
  ICT bias + kill-zone + entry-zone all align). Add a multiplexer test
  asserting the ordering.

### Completed
- Added `ict_signal_builder(settings)` runtime adapter in
  `src/runtime/pipeline.py`. Mirrors `vwap_signal_builder` shape:
  fetches OHLCV via `_build_killzone_exchange(settings).get_ohlcv()`,
  coerces the payload into a UTC `DatetimeIndex` frame (the ICT
  analyzer requires this for kill-zone derivation), optionally fetches
  a higher-timeframe frame, and delegates to the **pure**
  `src.runtime.strategies.ict.build_ict_signal` factory.
- Helper `_coerce_ohlcv_with_dt_index(raw)` accepts list-of-rows,
  `DataFrame` with `timestamp` column, or a pre-indexed frame.
- Registered `"ict"` in `_STRATEGY_BUILDERS` and added
  `STRATEGY=ict` routing in `run_pipeline()`. Multiplexer `STRATEGIES`
  list intentionally **untouched** (own checkpoint per ops rules).
- New optional settings: `ICT_TIMEFRAME`, `ICT_HTF_TIMEFRAME`,
  `ICT_CANDLE_LIMIT`, `ICT_HTF_CANDLE_LIMIT`. All previously-defined
  `ICT_*` knobs from `build_ict_signal` pass through unchanged.
- HTF fallback: raising HTF fetch is logged + swallowed so the
  strategy frame still drives the trend gate.
- Added 10 unit tests in `tests/test_runtime_ict.py` covering:
  registration (`"ict"` in registry but not in multiplexer order),
  three coercion paths plus the missing-timestamp error, happy-path
  bullish FVG → `buy`, timeframe / limit overrides, HTF fetch routing
  (asserts second `get_ohlcv` call), HTF graceful fallback, and the
  no-candles `RuntimeError` path. Uses a `FakeExchange` patched in
  via `monkeypatch` — no network.

### Files changed
- `src/runtime/pipeline.py` (additive: new function, registration,
  routing branch, coercion helper)
- `tests/test_runtime_ict.py` (new)

### Tests run
- `python scripts/repo_inventory.py` — clean.
- `python scripts/secret_scan.py` — clean.
- Targeted: `pytest tests/test_runtime_ict.py -q` → 10/10.
- Full: `pytest -q --ignore=tests/test_main_loop.py tests`
  → **312 passed**, 23 failed (pre-existing in `test_runtime_*`,
  unchanged), 2 skipped. Test count delta vs CP-12: **+10** (matches
  new file).
- **Regression check:** stashed the `pipeline.py` edit and re-ran the
  suite (excluding `test_runtime_ict.py`) → 23 failed / 302 passed,
  identical to the CP-12 baseline. PR introduces zero regressions.

### Remaining
- **CP-14:** decide and apply multiplexer ordering for `"ict"` in
  `STRATEGIES`. Add multiplexer test.
- Backlog items 8/9 (VWAP) remain Colab/Ben-owned.
- The 23 pre-existing `test_runtime_*` failures still need their own
  cleanup checkpoint (out of M7 scope).

### Next checkpoint
CP-2026-04-28-14 — multiplexer ordering for `"ict"`. Branch:
`feat/m7-ict-multiplexer-order`. Read `STRATEGIES` and `multiplexed_signal_builder` in `pipeline.py`; pick a position; add a focused
test patching `_STRATEGY_BUILDERS` so the test does not need real
data.

**PR:** [#53](https://github.com/the-lizardking/ict-trading-bot/pull/53) — `feat/m7-ict-pipeline-wire` (open, awaiting review/merge).

**Telegram sent:** yes

---

## CP-2026-04-28-12 — M7 Phase 2.4: ICT signal-builder factory

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-11 (PR #51 merged — HTF
  trend helper).
- **Next checkpoint:** **CP-2026-04-28-13 — register `"ict"` in
  `src/runtime/pipeline.py`'s `_STRATEGY_BUILDERS` and the multiplexer
  `STRATEGIES` order.** Owner: Claude. Scope: thin wiring PR — adds an
  `ict_signal_builder(settings)` adapter in `pipeline.py` that fetches
  candles via the configured exchange and delegates to
  `src.runtime.strategies.ict.build_ict_signal`, then registers it.
  Includes runtime-side tests using a fake exchange. Keep PR-sized.

### Completed
- Created `src/runtime/strategies/` package (`__init__.py`).
- Implemented pure `build_ict_signal(candles_df, settings, htf_df=None)`
  in `src/runtime/strategies/ict.py`. Returns the standard
  `{symbol, side, qty, meta}` signal dict.
- Gates wired (in order): `htf_trend_bias` ≠ neutral → kill-zone gate
  (toggleable via `ICT_REQUIRE_KILLZONE`, default on) → aligned entry
  trigger (unfilled FVG preferred, OB fallback). All gate failures emit
  `side="none"` with `meta.reason` plus full diagnostic payload
  (`fvgs`, `order_blocks`, `kill_zone`, `trend_bias`) so the existing
  `_write_ict_signals_from_meta` writer keeps working.
- Added 12 unit tests in `tests/test_ict_signal_builder.py` covering
  empty input, missing trend source, neutral trend, kill-zone
  active/disabled, bullish FVG → buy, bearish FVG → sell, OB fallback
  (monkeypatched analyzer), no-aligned-zone branch, string-truthy
  settings parsing, invalid `MAX_QTY` fallback, and default-symbol path.
- Confirmed builder is **pure** — no exchange/DB/IO at module load or
  call time. Pipeline `_STRATEGY_BUILDERS` intentionally **not** touched
  this session per the operating rules.

### Files changed
- `src/runtime/strategies/__init__.py` (new)
- `src/runtime/strategies/ict.py` (new)
- `tests/test_ict_signal_builder.py` (new)

### Tests run
- `python scripts/repo_inventory.py` — clean (no junk candidates).
- `python scripts/secret_scan.py` — clean.
- `PYTHONPATH=. python -m pytest -q --ignore=tests/test_main_loop.py tests`
  → **302 passed**, 23 failed (pre-existing in `test_runtime_*`,
  unchanged from CP-11), 2 skipped. Test count delta vs CP-11: **+12**
  (matches new test file). Verified no regressions: this PR adds only
  new, untracked files that cannot affect the runtime-validation/
  pipeline test modules.
- Targeted suite: `pytest tests/test_ict_signal_builder.py -q` → 12/12.

### Remaining
- **CP-13:** runtime wiring PR — `ict_signal_builder(settings)` adapter
  in `pipeline.py` that pulls OHLCV from the configured exchange,
  passes it (plus optional HTF frame) to `build_ict_signal`, and
  registers `"ict"` in `_STRATEGY_BUILDERS`. Add
  `tests/test_runtime_ict.py` with a fake exchange.
- **CP-14:** decide on multiplexer ordering for `"ict"` and update
  `STRATEGIES` list (cheap PR after #13 merges).
- Backlog items 8/9 (VWAP) remain Colab/Ben-owned.
- Pre-existing 23 `test_runtime_*` failures still need their own
  cleanup checkpoint at some point (out of M7 scope).

### Next checkpoint
CP-2026-04-28-13 — `ict_signal_builder` adapter in `pipeline.py` +
registration in `_STRATEGY_BUILDERS`. Branch:
`feat/m7-ict-pipeline-wire`. Read `pipeline.py` only as needed; mirror
the `vwap_signal_builder` shape (lines 108–156) for the OHLCV fetch.

**PR:** [#52](https://github.com/the-lizardking/ict-trading-bot/pull/52) — `feat/m7-ict-signal-builder` (open, awaiting review/merge).

**Telegram sent:** yes

---

## CP-2026-04-28-11 — M7 Phase 2.3: HTF trend confluence helper

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-10 (PR #50 merged — OB body
  filter).
- **Next checkpoint:** **CP-2026-04-28-12 — M7 Phase 2.4: wire ICT signals
  into a non-runtime entry point (`ict_signal_builder` factory) plus tests.**
  Owner: Claude. Scope: introduce a strategy builder that combines the
  existing FVG/OB detectors with the new HTF trend filter and the
  killzone gate, returning the standard `{symbol, side, qty, meta}`
  signal dict. **Do NOT register it in `pipeline.STRATEGIES` yet** — the
  registration step is its own checkpoint after a smoke-style test exists.
- **Blockers:** none. Branch `feat/m7-htf-trend-helper` is open and does
  not block CP-12.

### 1. Completed
- Added `src/ict_detection/trend.py` with two pure helpers:
  - `ema(series, length)` — standard `ewm(span=length, adjust=False)`
    EMA, exposed so callers and tests share a single numerical source of
    truth.
  - `htf_trend_bias(df, fast=20, slow=50, source="close", eps=1e-9)` —
    returns `"bullish"`, `"bearish"`, or `"neutral"` from the
    relationship between the two EMAs on the most recent bar. Empty
    frames, NaN-tail series, and prices inside the `eps` band all
    return `"neutral"` (no-information posture).
- Added `tests/test_htf_trend.py` (16 tests) covering EMA numerics
  against the pandas reference, monotone up / down / flat / V-shape
  bias outcomes, NaN-tail handling, eps-band classification, full
  argument validation (bad spans, missing source column, fast >= slow),
  and an alternate-source-column case.

### 2. Files changed
- `src/ict_detection/trend.py` (new, 149 lines)
- `tests/test_htf_trend.py` (new, 187 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `PYTHONPATH=. pytest tests/test_htf_trend.py -q` — 16 passed in 0.31s.
- `python scripts/repo_inventory.py` — pass.
- `python scripts/secret_scan.py` — pass.
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  290 passed / 23 failed / 2 skipped. The 23 failures are the same
  pre-existing main failures. **+16 new passes vs CP-10 baseline; no new
  regressions.**

### 4. Remaining
- ICT signal-builder factory that combines FVG/OB + HTF trend + killzone
  gate (next checkpoint, CP-12).
- Register the factory under `STRATEGIES` (later checkpoint).
- Wire `ob_body_min_pct` into the live pipeline (M7 Phase 4 — still
  gated on multi-symbol Colab validation).
- Multi-symbol manifest fixtures for CI use of the backtest CLI.

### 5. Next checkpoint
**CP-2026-04-28-12** — Build a pure ICT signal-builder factory in
`src/runtime/strategies/ict.py` (new module) that takes a settings dict
and returns a `{symbol, side, qty, meta}` dict. Use the existing
`ICTSignalsAnalyzer` for FVG/OB and the new `htf_trend_bias()` to gate
direction. Add unit tests. Do **not** edit `src/runtime/pipeline.py` in
CP-12; registration in `_STRATEGY_BUILDERS` is its own checkpoint.

Read in order: this entry, `docs/claude/checkpoint-workflow.md`,
`docs/sprint-plans/sprint-plan-2026-04-28.md` § M7 Phase 2,
`src/runtime/pipeline.py` (read-only — to mirror the signal-dict shape),
`src/core/signals.py`, `src/ict_detection/trend.py`.

**Telegram sent:** yes (session-complete dispatched via Pipedream
Telegram connector from the agent runtime).

---

## CP-2026-04-28-10 — M7 Phase 2.2: OB body-size filter

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-09 (PR #49 merged — backtest
  CLI scaffold).
- **Next checkpoint:** **CP-2026-04-28-11 — M7 Phase 2.3: HTF trend
  confluence filter.** Owner: Claude. Scope: add a higher-timeframe trend
  gate (e.g. 50-EMA on a coarser TF) to the ICT signal path so signals
  only fire in the direction of the dominant trend. Smallest safe subtask:
  introduce a pure helper `htf_trend_bias(df, fast=20, slow=50)` plus
  unit tests — no pipeline wiring in this first sub-checkpoint.
- **Blockers:** none. Branch `feat/m7-ob-body-threshold` is open and does
  not block CP-11.

### 1. Completed
- Added a `body_min_pct` parameter to `OrderBlockDetector.__init__`
  (`src/ict_detection/order_blocks.py`). Default `0.0` preserves the
  original any-body behaviour; positive values reject candles whose body
  is below that percentage of close. Both bullish and bearish OB paths
  honour the filter via a single `_passes_body_filter()` helper.
- Updated the `detect_order_blocks()` convenience function to forward the
  new parameter.
- Threaded the new threshold through `ICTSignalsAnalyzer.__init__` in
  `src/core/signals.py` as `ob_body_min_pct` (default `0.0`).
- Added `tests/test_ob_body_threshold.py` (9 tests) covering: default
  back-compat, monotonic filtering, non-zero OB detection on a synthetic
  trending fixture at 0.5% (the regime the research notebook flagged at
  the old 1.5% threshold), zero-close edge case, helper forwarding, and
  `ICTSignalsAnalyzer` wiring.

### 2. Files changed
- `src/ict_detection/order_blocks.py` (+37 / -7)
- `src/core/signals.py` (+9 / -2)
- `tests/test_ob_body_threshold.py` (new, 178 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `PYTHONPATH=. pytest tests/test_ob_body_threshold.py -q` — 9 passed.
- `PYTHONPATH=. pytest tests/test_fvg_ob.py tests/test_signals_analyzer.py
  tests/test_swing_detection.py tests/test_ob_body_threshold.py -q` —
  40 passed, 1 skipped (no regressions in adjacent ICT tests).
- `python scripts/repo_inventory.py` — pass.
- `python scripts/secret_scan.py` — pass.
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  274 passed / 23 failed / 2 skipped. The 23 failures are the same
  pre-existing main failures (test_runtime_validation,
  test_runtime_pipeline, test_runtime_smoke). **+9 new passes vs CP-09
  baseline; no new regressions.**

### 4. Remaining
- HTF trend confluence filter (next checkpoint).
- Multi-symbol manifest fixture(s) for CI use of the backtest CLI.
- Wire `ob_body_min_pct` into the runtime pipeline once research nails
  the exact value (out of scope for the port — belongs in M7 Phase 4).

### 5. Next checkpoint
**CP-2026-04-28-11** — Add a pure HTF trend bias helper and unit tests.
Read in order: this entry, `docs/claude/checkpoint-workflow.md`,
`docs/sprint-plans/sprint-plan-2026-04-28.md` § M7 Phase 2,
`src/core/signals.py`, `src/ict_detection/`. Do not touch
`src/runtime/pipeline.py` in CP-11 — the wiring is a later sub-checkpoint.

**Telegram sent:** yes (session-complete dispatched via Pipedream Telegram
connector from the agent runtime).

---

## CP-2026-04-28-09 — M7 Phase 2.1: backtest CLI scaffold

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-00 (workflow scaffolding) — note:
  M3a/M3b/M3c (PRs #35/#36/#37/#47), M4a–M4e (PRs #38–#42), and the M6
  multiplexer risk-cap test (PR #43) all merged earlier today directly into
  `main` ahead of the formal checkpoint log being introduced. Backlog items
  1–7 in the user's Apr-28 sprint prompt are therefore already on `main`.
- **Next checkpoint:** **CP-2026-04-28-10 — M7 Phase 2.2: lower OB body
  threshold and add OB-non-empty test on a synthetic trending CSV.** Owner:
  Claude. Scope: introduce a `body_min_pct` filter on `OrderBlockDetector`
  (default keeps current behaviour; lowered value re-enables OB events the
  research notebook flagged as missing at threshold 1.5).
- **Blockers:** none. Branch `feat/m7-backtest-cli-scaffold` is open and does
  not block the next checkpoint.

### 1. Completed
- Added `bin/backtest_ict.py` — multi-symbol/multi-timeframe ICT backtest
  CLI wrapping `src.backtest.backtester.ICTBacktester`. Pure scaffolding, no
  live-trader or pipeline edits. Reads either a manifest CSV
  (`symbol,timeframe,path`) or repeated `--pair SYMBOL:TF:PATH` flags;
  writes a JSON report. Dataclasses `Pair` / `PairResult`, helpers
  `parse_pair_arg`, `load_manifest`, `run_pair`, `run_all`, `aggregate`,
  `render_results`, `main`.
- Added `tests/test_backtest_ict_cli.py` — 14 offline tests covering pair
  parsing, manifest column validation, aggregate math, missing-file and
  malformed-CSV failure paths, and an end-to-end synthetic flat-market run
  that exercises the real `ICTBacktester` and proves the CLI plumbing
  works.

### 2. Files changed
- `bin/backtest_ict.py` (new, 267 lines)
- `tests/test_backtest_ict_cli.py` (new, 189 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `python -m py_compile bin/backtest_ict.py tests/test_backtest_ict_cli.py` — pass.
- `PYTHONPATH=. pytest tests/test_backtest_ict_cli.py -q` — 14 passed in 0.73s.
- `python scripts/repo_inventory.py` — pass (no junk candidates).
- `python scripts/secret_scan.py` — pass (no obvious secrets).
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  265 passed / 23 failed / 2 skipped. The 23 failures pre-exist on `main`
  (verified by stashing this patch and re-running: same 23 failures, same
  files: `test_runtime_validation.py`, `test_runtime_pipeline.py`,
  `test_runtime_smoke.py`). They are environment / fixture issues unrelated
  to this change. `tests/test_main_loop.py` requires the optional `ccxt`
  dependency which is not installed in this sandbox; not introduced by this
  patch. **No new regressions.**

### 4. Remaining
- Lower OB body-size threshold and verify OB detection produces non-zero
  events on a known-trending fixture (next checkpoint).
- Confluence filters (session gate already exists in backtester; HTF trend
  filter still to add).
- Multi-symbol validation runs themselves (Gemini-in-Colab, not Claude).

### 5. Next checkpoint
**CP-2026-04-28-10** — Add `body_min_pct` parameter to
`OrderBlockDetector.__init__` (default `0.0` to preserve current behaviour)
and thread it through `src/core/signals.py:ICTSignalsAnalyzer`. Add a test
proving non-zero OB events on a synthetic strong-trend fixture. Read in
order: this entry, `docs/claude/checkpoint-workflow.md`,
`src/ict_detection/order_blocks.py`, `tests/test_fvg_ob.py`.

**Telegram sent:** yes (session-complete dispatched via Pipedream Telegram
connector from the agent runtime; no token handled in-repo).

---

## CP-2026-04-28-00 — Workflow scaffolding

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (Live Trading Hardening + Repo Cleanup)
- **Current sprint phase:** Phase 0 — workflow setup (pre-backlog)
- **Last completed checkpoint:** _none, this is the first._
- **Next checkpoint:** **CP-2026-04-28-01 — M1 Auto-deploy timer verification**
  (owner: Colab/Ben; depends on Claude's pending timer PR being merged).
  See `docs/sprint-plans/sprint-plan-2026-04-28.md` § M1.
- **Blockers:** none.

### 1. Completed
- Added repository-level checkpoint workflow (this file, `checkpoint-workflow.md`,
  `HANDOFF_TEMPLATE.md`).
- Updated `CLAUDE.md` and `docs/claude/INDEX.md` to route to the new workflow.
- Added `scripts/notify_session.py` thin wrapper around the existing
  `src.runtime.notify.send_via_alert_manager` for session/sprint Telegram pings.

### 2. Files changed
- `CLAUDE.md`
- `docs/claude/INDEX.md`
- `docs/claude/session-workflow.md`
- `docs/claude/checkpoint-workflow.md` (new)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (new)
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` (new)
- `scripts/notify_session.py` (new)

### 3. Tests run
- `python -m py_compile scripts/notify_session.py` — pass.
- No production code touched, so no pytest run required for this patch.

### 4. Remaining
- None for this checkpoint. Sprint backlog is intentionally **not** started
  in this session per the workflow-implementation task.

### 5. Next checkpoint
**CP-2026-04-28-01** — Begin M1 auto-deploy timer verification work as
defined in `docs/sprint-plans/sprint-plan-2026-04-28.md` § M1.
The next Claude session should:
1. Read this log entry first.
2. Read `docs/claude/checkpoint-workflow.md`.
3. Read sprint plan § M1.
4. Confirm whether the timer PR has merged on `main`. If yes, hand the
   verification steps to Colab/Ben as a copy-ready block. If not, the
   smallest safe subtask is to draft/finish the timer PR.

**Telegram sent:** no (workflow scaffolding session, run from agent-side;
no live Telegram creds intended in this environment).

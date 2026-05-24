# Telegram Bots — Product Spec (single source of truth)

**Status:** authoritative for the 2026-05 bots overhaul
(`claude/kilogram-bots-overhaul-ZtXDS`). **Supersedes** the S-001
11-command spec (kept in git history) — the system is now menu-driven,
not command-driven.
**Owner:** Ben (PM).

This doc owns the **operator command bot** (`@bict_trading_bot`). The
**Claude update channel** (`@claude_ict_comms_bot`) is owned by
[`docs/claude/telegram-pings.md`](claude/telegram-pings.md); only the
boundary between the two is described here.

---

## 1. Two bots, two jobs

| Bot | Token | Service | Job |
|---|---|---|---|
| **@bict_trading_bot** | `TELEGRAM_BOT_TOKEN` | `ict-telegram-bot.service` | Operator control plane: a small button **menu** + reliable **trade/hourly notifications**. Owned here. |
| **@claude_ict_comms_bot** | `TELEGRAM_CLAUDE_BOT_TOKEN` | `ict-claude-bridge.service` | **One-way** channel for everything *Claude* is doing (sprints, health reviews, training sessions, waiting-for-input, system health). Single thread. Owned by `telegram-pings.md`. |

The two never overlap: trade/system control + trade alerts → trader bot;
Claude's own activity → Claude channel. Training-session pings live on
the **Claude channel** (operator decision 2026-05-24).

---

## 2. Trader bot — design principles

1. **Menu-driven, not command-driven.** The operator opens one menu and
   taps. The only slash commands are the menu openers (`/start`,
   `/menu`). The Telegram hamburger command list (`set_my_commands`) is
   trimmed to just those — no wall of stale commands.
2. **Dynamic.** Every view reads live state (accounts.yaml,
   strategies.yaml, the journal DB, exchange balances, runtime_status).
   Adding an account or strategy needs **no bot code change**.
3. **Resilient.** Every data read catches its own errors and renders a
   friendly "unavailable" line; a missing DB / down exchange / unknown
   service never crashes a view.
4. **Safe.** Destructive actions (kill switch → live, close-all)
   require a second-tap confirm and log operator chat-id + timestamp.

---

## 3. Trader bot — menu structure

`/start` and `/menu` open the **main menu** (an inline keyboard). Buttons
open sub-menus or render a view; every view carries a `« Back` /
`🏠 Menu` button. Callback-data namespace is `menu:*` and the kill-switch
flip actions.

```
🏠 Main menu
├─ 🛑 Kill switch            → menu:kill
│   ├─ By account            → menu:kill_accounts   (per-account live ⇄ dry_run)
│   └─ By strategy           → menu:kill_strats     (per-strategy live ⇄ shadow)
├─ 🩺 System update          → menu:system
├─ 💼 Accounts snapshot      → menu:accounts
├─ 📈 Strategies snapshot    → menu:strategies
└─ 🚨 Close all positions    → menu:closeall  (→ confirm → execute)
```

Nothing else appears in the menu. (See §8 for what was scrubbed.)

### 3.1 Kill switch (two switches)

Two **independent** switches, matching the two canonical execution gates
(`ARCHITECTURE-CANONICAL.md` § two execution gates):

| Switch | Gate | Off-state | Persisted by |
|---|---|---|---|
| **Per account** | `config/accounts.yaml::mode: live ⇄ dry_run` | `dry_run` — account refuses all live orders (logged, not sent) | the sanctioned `scripts/ops/set_account_mode.sh` (edits YAML + restarts trader; wipes in-memory overrides) |
| **Per strategy** | `config/strategies.yaml::execution: live ⇄ shadow` | `shadow` — strategy runs + logs order packages everywhere but never sends a live order | a sanctioned strategy-execution writer (edits YAML + `coord.reload_strategy_config()`; see §6) |

Behaviour:
- `menu:kill_accounts` lists every account with its current mode and a
  toggle button each. Tapping asks for confirm; confirming **persists**
  via the sanctioned path so the change **survives a restart** (operator
  decision 2026-05-24: persist via config). Flipping *to live* shows an
  explicit "this will place REAL orders" warning.
- `menu:kill_strats` does the same for `execution: live ⇄ shadow`.
- The kill switch only **stops new trades**. It does **not** close open
  positions — that's the separate `🚨 Close all positions` action.

**Why persistent:** a kill switch that reverts to `live` on the next
restart is not a kill switch. Both writes go through a single audited
writer per gate, preserving the "one writer for `mode:`" invariant the
`dry-run-guard` CI check and the Prime Directive depend on.

### 3.2 System update (`menu:system`)

"Is the system holding and running properly?" One view:
- Kill-switch summary: how many accounts live vs dry, how many
  strategies live vs shadow.
- Service health: `ict-trader-live`, `ict-web-api`, `ict-claude-bridge`
  active? (systemd `is-active`).
- Trader liveness: heartbeat age vs threshold (running / paused /
  stopped) and last tick time.
- VM resources: uptime, load, memory, disk.
- Any current halt flag.

### 3.3 Accounts snapshot (`menu:accounts`)

For **each** account:
- `account_id` + exchange + **mode** (🔴 live / 🧪 dry_run).
- Config summary: account type, risk caps (max daily loss, max position
  size, max drawdown).
- **Balance** (live exchange query; `⚠️ unavailable` on failure).
- **24h realised PnL**.
- **Trades**: open positions now + the day's closed trades (count +
  list, newest first, capped).

### 3.4 Strategies snapshot (`menu:strategies`)

For **each** strategy:
- name + friendly label + **execution** (🔴 live / 🌑 shadow) + loaded/
  running (from `runtime_status.json`).
- accounts it's routed to.
- last signal time.
- 24h PnL + open positions + lifetime trade count.

### 3.5 Close all positions (`menu:closeall`)

Emergency close of **all open positions** across **all accounts** via the
canonical close path (`processor.close_open_positions`). Two-tap confirm.
Reports closed/failed counts. Logs chat-id + timestamp. Distinct from the
kill switch (which only stops *new* trades).

---

## 4. Trader bot — pings (outbound)

### 4.1 Hourly summary — exactly once per hour, one message

**One** consolidated snapshot of what the system did in the **past
hour**, sent **once at the top of each hour, never more**.

Canonical path: the `ict-hourly-snapshot.timer` → `scripts/send_hourly_now.py`
(oneshot, `OnCalendar=hourly`, flock-guarded). The **in-loop**
`should_send_summary` path in `src/main.py` (which also sent a *second*
accounts-focused message) is **removed** so there is exactly one
producer and one message. (This double-producer was the "coming too
often" bug.)

Contents (one message): ticks run, signals fired, trades opened/closed +
realised PnL, per-account balance + 1h delta, per-strategy activity,
error/warning counts — for the last hour.

### 4.2 Trade lifecycle — open / update / close

Each event is its **own message** with a **clear title** that draws the
eye, plus a `Details ▾` inline button that expands the full breakdown
(so the feed stays scannable):

| Event | Title example | Expanded details |
|---|---|---|
| **Open** | `🟢 TRADE OPENED — BTCUSDT LONG` | account, strategy, qty, entry, SL, TP, risk $, order id |
| **Update** | `✏️ TRADE UPDATED — BTCUSDT` | what changed (SL/TP moved, partial fill / partial close), new values |
| **Close** | `🔴 TRADE CLOSED — BTCUSDT +$X` (✅ win / ❌ loss) | entry, exit, realised PnL, R, duration, reason |

One message per event (never batched). Wired to the trade lifecycle in
the runtime/order path, emitted through the trader bot inbox
(`runtime_logs/pending_pings/`, `target="trader"`).

### 4.3 Regular automated runs

Training/model sessions ping **open + close (with results summary)** —
these go to the **Claude channel**, not the trader bot (operator decision
2026-05-24). See `telegram-pings.md` (`training-start` / `training-complete`).

---

## 5. What stays out / scrubbed

Everything not in §3–§4 is removed from the menu and the hamburger
command list. Specifically dropped from the operator surface: the old
category `/help` menu and the ~40 commands behind it (`/halt`, `/resume`,
`/balance`, `/trades`, `/last5`, `/packages`, `/signals`, `/alerts`,
`/log`, `/price`, `/backtest*`, `/smoke_test`, `/risk_check`,
`/set_all_live`, `/set_keys`, `/reload_strats`, `/sprintlet_*`,
`/new_session`, `/test`, `/vm*`, `/webapp`, `/download_journal`,
`/checkpoint`, `/ping_test`, …). Their underlying capability is either
folded into the four views (status/health/accounts/strategies) or no
longer operator-facing (it moved to skills / GitHub Actions / the
dashboard). "Add it back later" if a real need surfaces — start lean.

---

## 6. Tech approach

- **Reuse** `src/bot/telegram_query_bot.py` as the single entry point.
- **Menu layer:** `render_main_menu()` + the `menu:*` callbacks replace
  `render_help_top` / `render_help_category` / `BOT_COMMAND_SPECS`.
- **Data loaders:** reuse `src/units/ui/data_loaders.py`
  (`list_accounts`, `list_live_strategies`, `account_balance`,
  `account_open_positions`, `recent_trades_for`,
  `strategy_dashboard_data`) and `coord.accounts_status()`. No new
  hardcoded account/strategy lists.
- **Kill-switch writers (one audited writer per gate):**
  - account → `scripts/ops/set_account_mode.sh` (existing sanctioned
    writer; the only thing allowed to edit `mode:`).
  - strategy → a sanctioned writer mirroring it for
    `strategies.yaml::execution` + `coord.reload_strategy_config()`.
    Targeted single-line YAML edit (preserves comments), then reload.
- **CI guards:** the kill-switch edits happen at **runtime on the VM**,
  not as static diffs, so `dry-run-guard` (which scans PR diffs for
  `mode: dry_run`) is not tripped. Touching `config/strategies.yaml`
  /`config/accounts.yaml` / the order path is Tier-2/3 → ship as a
  **draft PR**, operator-reviewed, never self-merged to `main`.
- **Tests:** replace the `TestHelpCommandParity` spec/handler/menu
  parity test with a menu-structure test (every main-menu button has a
  callback branch; every snapshot builder renders without a live DB
  using mocks). Add a kill-switch-persistence test (writer invoked,
  confirm gate enforced).

## 7. Acceptance criteria (binary)

- [ ] `/start` / `/menu` open a menu with exactly: Kill switch, System
      update, Accounts snapshot, Strategies snapshot, Close all positions.
- [ ] Kill switch has account and strategy sub-menus; a flip **persists**
      across a restart via the sanctioned writer; flipping to live warns.
- [ ] System / Accounts / Strategies views render the §3.2–3.4 contents
      and degrade gracefully when a source is unavailable.
- [ ] Hourly summary fires **once** per hour as a **single** message; the
      duplicate in-loop producer is gone.
- [ ] Trade open / update / close each send their own titled message with
      an expandable details button.
- [ ] The hamburger command list shows only the menu openers; no stale
      commands remain in the operator surface.
- [ ] No view crashes on missing data; no new third-party deps.

## 8. Deploy + config (Claude does this autonomously)

These run through the `system-actions` GitHub workflow (issue-driven), not
by hand — Claude owns the VM config and deploy. See
`docs/claude/system-actions.md`.

- **Env:** set `TELEGRAM_CLAUDE_THREAD_ID` (and, secret-backed,
  `TELEGRAM_CLAUDE_BOT_TOKEN`) via the **`set-env`** action, which writes
  `.env` and restarts the target service. `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID` are rendered into `.env` by the master-secrets flow.
- **Deploy:** **`pull-and-deploy`** ships the code; **`restart-bot-service`**
  bounces the trader bot. The hourly timer is installed by the unit
  installer; the duplicate in-loop producer is removed in code (§4.1).
- **Send an update / ping you any time:** the **`send-ping`** action
  (immediate, no restart).
- **Verify:** `status-check` + a `send-ping` smoke + flipping a test
  account dry→live→dry via `set-account-mode` and confirming it persisted
  after a restart.

The only genuine human-only step is creating the Telegram bot tokens with
@BotFather and adding `TELEGRAM_CLAUDE_BOT_TOKEN` to GitHub Actions
secrets (a credential a human must mint); everything after that is
Claude-driven.

---

## 9. Change log

- **2026-05-24** — Overhaul. Replaced the command-driven S-001 spec with
  the menu-driven design above: 4-item menu + close-all, two persistent
  kill switches, one-message hourly, trade-lifecycle pings, training
  pings moved to the Claude channel. Claude channel made strictly
  one-way + single-thread (see `telegram-pings.md`).
- *(history)* S-001 11-command dynamic-bot spec — see git history.

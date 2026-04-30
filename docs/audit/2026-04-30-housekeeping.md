# S-016 H0 — Housekeeping audit, 2026-04-30

**Scope:** read-only inventory of the Telegram bot surface, systemd unit
graph, requirements pinning, and known carry-over items. No code
changes in this PR; downstream H1..H7 PRs fix what's listed here.

## Findings

### A. Telegram bot surface

#### A1 — `BotCommand` registry vs `CommandHandler` registrations

Registered as handlers (30 commands):

```
start, help, halt, resume, status, balance, trades, closeall,
strategies, last5, signals, backtest, latest_backtest, log, toggle,
download_journal, price, alerts, reload_strats, backtest_ui,
accounts, accounts_status, risk_check, sprintlet_status,
sprintlet_complete, checkpoint, webapp, vm, vm_write,
+ CallbackQueryHandler
```

Listed in `BotCommand` registry (the auto-complete menu the user sees
after `/`), 27 entries:

```
start, help, halt, resume, status, balance, trades, closeall,
strategies, last5, signals, backtest, latest_backtest, log, toggle,
download_journal, price, alerts, reload_strats, backtest_ui,
accounts, accounts_status, risk_check, sprintlet_status,
sprintlet_complete, checkpoint, webapp
```

**Missing from menu:** `vm`, `vm_write` — registered and the operator
relies on them, but invisible in the auto-complete pop-up. **Fix in
H1.**

#### A2 — `/start` menu vs reality

`cmd_start` text (lines 555-579 of `src/bot/telegram_query_bot.py`)
lists ~22 commands. Missing from the user-visible menu but actually
registered:

- `/alerts` — recent unit alerts.
- `/reload_strats` — reload strategy config.
- `/backtest_ui` — Streamlit launch instructions.
- `/checkpoint` — latest CHECKPOINT_LOG entry.
- `/sprintlet_status`, `/sprintlet_complete` (see A3).

**Fix in H1.**

#### A3 — Stale hardcoded sprint refs

`cmd_sprintlet_status` (line 1123-1128) hardcodes:

```python
await update.message.reply_text(f"✅ Sprintlet S-008.5: {milestone}")
```

We're on S-016. The "S-008.5" prefix is from ~70 sprints ago.

`cmd_sprintlet_complete` (line 1131-1137) is even worse:

```python
"🎉 Sprintlet S-008.5 COMPLETE. Resume at CP-2026-04-29-58. "
"Ready for S-009."
```

Both should either:
- Read the sprint id from `docs/claude/checkpoints/CHECKPOINT_LOG.md`
  (the topmost CP entry's `Sprint:` field), or
- Take the sprint id as an argument: `/sprintlet_status S-016 PR#213
  merged, next: H0 audit`.

The S-016 H3 wiring already pings on every checkpoint commit, so these
operator-typed commands are a manual fallback. They should still work
correctly when used. **Fix in H1.**

#### A4 — `/help` is `/start` aliased

`cmd_help` is literally `await cmd_start(update, context)`. Functional
but undocumented; either is fine, but the `/help` BotCommand entry says
"Show help" (not "alias for /start") which is correct. No action.

#### A5 — `/status` per-account block leaks systemd service name

Per the operator's directive ("instead of the strategy they're wired to
[…] What I wanna see is which strategy it's following"):

`cmd_status` line 605-610 currently renders:

```
*turtle_soup* (`bybit_1`)
  📊 Trades today: 0 | P&L: $+0.00
  📂 Open (DB): 0 | `ict-trader-live`: active
```

The trailing ``` `ict-trader-live`: active``` exposes the systemd unit
name. Since S-012 we run **all** strategies inside a single
`ict-trader-live` unit (per-strategy systemd units were retired). The
service name is identical for every account and conveys no useful
per-account info. The strategy name (already in the bold header) is
the operator's actual mental model.

**Fix in H1:** drop the service column from the per-account block.
Show only the strategy + counts. The aggregate-level
"`ict-telegram-bot`: active" line at the bottom of `/status` stays —
that one is still useful (it's the bot reporting on itself).

#### A6 — Indentation drift in `BotCommand` list

Lines 1620-1623 of the BotCommand list have 8-space indent vs the
rest at 12-space. Functionally fine (Python tolerates it inside a
list literal) but cosmetically ugly. **Fix in H1.**

### B. systemd unit graph

```
ict-trader-live.service       After=network-online.target
                              Wants=network-online.target
                              Restart=always

ict-telegram-bot.service      After=network.target ict-trader-live.service
                              Wants=network-online.target
                              Restart=always

ict-web-api.service           After=network-online.target ict-trader-live.service
                              Wants=network-online.target
                              Restart=always

ict-heartbeat.service         After=network-online.target
                              Wants=network-online.target

ict-git-sync.service          After=network.target
ict-git-sync.timer            (timer; activates above)

claude-vm-runner@.service     (template; one-shot per /vm invocation)
```

**Findings:**

- `Restart=always` on the three long-running units (trader / bot / web-api)
  → independent crash recovery. ✅
- `After=ict-trader-live.service` on bot + web-api is **ordering
  only**, not a hard dependency. If the trader fails to start, the
  bot and web-api still come up. ✅
- No unit uses `Requires=`, `BindsTo=`, or `PartOf=`. So a trader
  crash does NOT cascade to the bot or web-api. ✅
- One real coupling: bot + web-api won't *start* until the trader's
  startup has at least *attempted*. If the trader is in a long crash
  loop (`Restart=always` with default backoff of 100 ms growing), the
  bot may be delayed. This is mostly cosmetic — the bot's startup
  path itself takes ~5-10 s.

**Action for H4:** confirm the above by simulating a trader crash on
the VM and verifying the bot continues to respond. (Out of scope for
this PR; this is just the audit.)

### C. requirements.txt pinning

| Package | Current spec | Concern |
|---|---|---|
| `pandas` | `>=2.0.0` | floor only — already broke in BUG-013 (yfinance noise was a symptom, not a cause) |
| `apscheduler` | not pinned | **BUG-005** — `apscheduler 3.6.3` ↔ `tzlocal 5.x` mismatch crash-looped the bot 121× on the VM. Must add `apscheduler>=3.10.4` |
| `pytz` | not pinned | **BUG-005** — co-dependency, must add |
| `tzlocal` | not pinned | **BUG-005** — float to a known-good range or pin |
| `python-telegram-bot` | `>=20.0` | floor only |
| `httpx` | `>=0.27.0` | floor only — also see operator-cleanup item from S-014.5 ("filter httpx URL logging so the Telegram bot token doesn't appear in plaintext") |

**Fix in H6.**

### D. Carry-over items from `docs/claude/bug-log.md`

These have been flagged across S-014, S-014.5, S-015 and remain
unresolved:

1. **BUG-010** — Centralise telegram stubs in `tests/conftest.py`.
   Module-level `_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[…]])` (PR
   #184) breaks the `_tg.InlineKeyboardMarkup = MagicMock` stub used
   by ~10 existing test files. **Fix in H5.**
2. **BUG-011 + BUG-012** — Document the recursive
   `web/templates/**/*.html` whitelist pattern in
   `docs/claude/git-workflow.md`. **Fix in H5.**
3. **BUG-015** — Add a "this sandbox has no market-data egress" note
   to `docs/claude/testing-policy.md`. **Fix in H5.**
4. **BUG-005** — Pin `apscheduler` and `pytz`. **Fix in H6.**
5. **From S-014.5 cleanup** — Operator action items:
   (a) Revoke leaked OAuth tokens. (b) Configure Bybit API key on the
   VM. (c) Filter httpx URL logging so token doesn't appear in
   `journalctl -u ict-telegram-bot`. **Documented in H7 — operator
   does (a)+(b); we can fix (c) in H5.**

### E. Branches on origin

```
git branch -r | wc -l → ~140
```

Most are merged `claude/*` branches from completed sprints. Safe to
prune the merged ones. **Fix in H6.**

### F. Repo inventory

`repo_inventory.py` flags one large committed file:

```
data/btc_1m_sample.csv — 641,850 bytes
```

This is a real fixture used by `tests/sprint015/test_analyze_fixtures.py`
and by S-016 H1's downstream tests. Keep. No action.

## H0 deliverable summary

- This audit doc, committed to `docs/audit/2026-04-30-housekeeping.md`.
- 5 fixable items routed to H1 (Telegram surface).
- 4 fixable items routed to H5 (carry-overs + httpx logging).
- 1 fixable item routed to H6 (requirements pinning + branch prune).
- 1 documentable handoff routed to H7 (operator action items).
- 1 verification item routed to H4 (unit independence on the VM).

Self-merge per the housekeeping plan — pure docs, no risk.

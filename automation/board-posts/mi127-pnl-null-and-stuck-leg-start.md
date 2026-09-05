▶️ **START — MI-127** (closes landing with `pnl` NULL + MES trade 4350)

- **Branch:** `claude/mi127-pnl-null-and-stuck-leg` → **PR #11021**
- **Session:** `session_01BbUwYSusoggWYmSwpgcLnc` (manager-child)
- **Registry:** `pending-20260905T025732Z` in `docs/claude/work/SESSIONS.json`

**Scope I am touching** — all Tier-1, no `src/`, no order path:
- `docs/claude/diagnoses/MI-127-pnl-null-on-closes-and-trade-4350.md` (new)
- `tests/test_local_pnl_sweep_window_is_open_keyed.py` (new)
- `docs/claude/health-review-backlog.json` (3 rows appended via `backlog_append.py::append_row`)
- `.github/pr-landing/…json`, `.github/pr-automerge-requests/…txt`, `automation/board-posts/…md`

**READ-ONLY against the live fleet.** I pulled `/api/bot/db/table/{trades,order_packages}`, `/api/diag/exchange_positions`, `/api/diag/ib_open_orders`, `/api/diag/status` over `https://ict-bot.duckdns.org`. **Nothing was closed, flattened, modified or cancelled on any account.** Trade 4350 is a live `ib_paper` position and my recommendation is explicitly **no intervention**.

**Not touching:** `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, `src/runtime/orders.py`, or any `src/` file. The one-line fix for the pnl-NULL mechanism is **proposed** in the diagnosis (Tier-2, `src/runtime/order_monitor.py`), deliberately not applied.

**Headline (A):** the pnl-NULL closes are not a coin flip. `_sweep_local_pnl_for_unpriced` keys its scan window on `created_at` (the OPEN) while pricing a CLOSE, so a position held >14 days is never scanned, never anchored, and never *declared* — it lands a **silent** null. Measured over all 5,493 `trades` rows: **0 of 20** such rows have ever carried a declaration.

**Headline (B):** trade 4350 is **not stuck and not unprotected** — established against the venue, not the journal. 15 MES long on `ib_paper` resting a quantity-matched OCA bracket (STP 7533.75 + LMT 8390.50) matching the journal to the 0.25 tick on both legs. The 7 orphaned packages are from **2026-06-01**, 63 days before the trade, and unrelated to it.

⚠️ **Note for anyone reading #11021:** `claude-pr-automerge` opened it before `pr-opener` could, so its title/body are boilerplate and `update_pull_request` 403s from this session — the recurrence of `BL-20260905-AUTOMERGE-RELAY-WINS-THE-RACE-WITH-PR-OPENER-SO-EVERY-TIER-1-PR-GETS-A-BOILERPLATE-BODY`. **Read the diagnosis doc as the PR description.**

# Tradeify 247 (tradeify_1) — Runbook

> **Doc status:** `unknown` · category `lookup` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Second prop account on the DXtrade executor that runs `breakout_1`
(TRADEIFY-WIRE, 2026-09-30). Operator decision, manager popup ~08:24Z:
*"Buy the $10k pilot (Recommended)"*. Evidence and rules:
[`docs/research/tradeify-portfolio-feasibility-2026-09-30.md`](../research/tradeify-portfolio-feasibility-2026-09-30.md)
(PR #14580) and
[`docs/research/prop-dxtrade-firms-2026-09-30.md`](../research/prop-dxtrade-firms-2026-09-30.md) § 4.1.

## What the operator does (three things, nothing else)

Derived from `.claude/skills/credentials-and-vm-mutations/SKILL.md`.

1. **Buy** the Tradeify 247 **1-Step $10k** crypto evaluation (DXtrade).
2. **Add two repository secrets** in GitHub: repo **Settings → Secrets and
   variables → Actions → Repository secrets**. If the slots already exist
   (pre-created empty), click the pencil (**Update**) on each; otherwise
   **New repository secret**:
   - `TRADEIFY_DX_USERNAME` — the login you type at `https://dx.tradeify247.co/`
   - `TRADEIFY_DX_PASSWORD` — its password
   Paste values only there. Never in an issue, PR, chat or comment.
3. **Tell the manager "Tradeify creds set"**, and, if shown on the purchase
   or dashboard page, the terminal/server URL (expected
   `https://dx.tradeify247.co/`; say so if it differs) and the account size
   bought. The account number is **not** needed: the terminal read gives it.

Everything below is Claude's, through workflows.

## Sequence (Claude-side)

| step | how | tier | done when |
|---|---|---|---|
| 0. PR A merged + deployed | held Tier-2 PR (shared executor/wrapper code, platform entry, secrets list, feed template) | 2 | `pull-and-deploy` ran; VM HEAD carries it |
| 1. propagate creds | `sync-vm-secrets.yml` (optional secrets) | 1 | run log lists both names as set (never values) |
| 2. read-only login check | `system-action` `action: breakout-login-check`, `account: tradeify_1`, `apply: emit-status` | 2 | exit 0: login ok, balance/equity/positions/orders parsed, one `account_status` posted, instrument specs printed for ETHUSD/SOLUSD/XRPUSD |
| 3. feed on | same action, `apply: feed-enable-timer` | 2 | `ict-prop-feed@tradeify_1.timer` active; `/api/bot/prop/status` shows a fresh tradeify_1 snapshot |
| 4. PR B merged (account `mode: dry_run`) | held Tier-3 PR | 3 | trader restarted; `/api/bot/config` lists tradeify_1 `dry_run` |
| 5. dry tick | trader logs order packages for the three legs with `status=shadow`, `close_reason=prop_shadow_no_emit`; no ticket, no ping; plus `apply: round-trip-dry` (add `sol` for SOLUSD) walks the order form, clicks nothing | 2 | ≥1 shadow package per leg read from the journal; round-trip-dry exit 0 |
| 6. go-live | held Tier-3 PR C: `mode: live` + `executor.lots` / `enabled_venue_symbols` from the step-2/5 measurements; then `set-env PROP_EXECUTOR_MODE_TRADEIFY_1=live` + a per-account executor timer | 3 | merged only after steps 2–5 are observed |

**Kill switch:** `PROP_EXECUTOR_MODE_TRADEIFY_1` (off | read_only | live,
default read_only). It never reads `breakout_1`'s `PROP_EXECUTOR_MODE`, so
arming one account cannot arm the other. Revert: `set-env
PROP_EXECUTOR_MODE_TRADEIFY_1=off`.

**State:** `~/.cache/metis-prop-browser/accounts/tradeify_1/{feed,executor}`.
`breakout_1` keeps `~/.cache/metis-prop-browser/{feed,executor}`.

## What is not measured (fill before go-live)

- A login from the VM (only the landing page was fetched).
- Tradeify's venue symbol names (`ETHUSD` / `SOLUSD` / `XRPUSD` assumed,
  `unconfirmed` in `config/prop_rulesets/tradeify_routing.yaml`).
- Lot size, lot step, minimum and price step per symbol
  (`config/prop_platforms.yaml::tradeify_1.executor.lots` is empty; the
  structure guard refuses every ticket until filled).
- Whether the rules match the first-party summaries (`unconfirmed: true` in
  the ruleset).
- **Symbol switching is not built** in the executor; the three-symbol book
  needs it before more than one venue symbol can be enabled.
- **The watchlist resolver does not find Tradeify's watchlist** (issue #15033,
  2026-10-01): `instrument-info-dry` on `tradeify_1` refused with
  `0 Symbol/Bid/Ask tables (need exactly 1)`, so the per-ticket symbol switch,
  `symbol-switch-dry` and `instrument-info-probe` cannot target a row there
  yet. A dry run that refuses this way now also prints a click-free
  `watchlist_dump` line (header words and counts only), the measurement the
  resolver fix is built from. Pipeline `PI-20261001-BHYHMK2I-0001`.
- **Measured and fixed (pending deploy), 2026-10-01:** the `watchlist_dump`
  from issue #15067 showed Tradeify's watchlist is a header-less `<table>`
  (23 `tr.instrument` rows) whose column headers carry `table_column_*`
  test-ids. The resolvers now fall back to those headers ONLY when no
  Symbol/Bid/Ask `<th>` table exists (breakout_1 never reaches it), with
  alignment proven per column; a refused fallback prints
  `resolve.column_headers.why` and the dump's `columns` / `header_less`
  geometry. Observed working only once an `instrument-info-dry` on
  `tradeify_1` resolves ETHUSD/SOLUSD/XRPUSD with no refusal.

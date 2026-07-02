# Alpaca MCP server (read-only diagnostic resource)

> **Status:** documented 2026-07-02 (Track F of the `RPT-20260702-061700-since-last`
> system-review follow-up plan), at the operator's request. Not yet connected to
> any session as of this writing — this doc is the reference for when it is.

## What it is

Alpaca ships an official, self-hosted MCP server:
[`docs.alpaca.markets/us/docs/alpaca-mcp-server`](https://docs.alpaca.markets/us/docs/alpaca-mcp-server).
Run via `uvx alpaca-mcp-server` (or Docker). It exposes **65 tools** across
four categories:

- **Account / portfolio** — balances, buying power, positions, activities.
- **Trading** — stock/crypto/options order placement (market, limit, stop,
  bracket, multi-leg).
- **Market data** — bars, quotes, snapshots, option chains, news.
- **Discovery** — screeners, watchlists.

Auth is `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` env vars; `ALPACA_PAPER_TRADE`
toggles paper vs **live** trading; `ALPACA_TOOLSETS` filters which tool
categories are exposed (e.g. `ALPACA_TOOLSETS=account,stock-data,news`
restricts the server to read-only categories, dropping the trading toolset
entirely).

## The risk: it bypasses this repo's entire order path

This repo's Prime Directive (`docs/CLAUDE-RULES-CANONICAL.md`) names
`RiskManager.position_size()` "the ONLY function in the codebase that decides
position size" (`src/units/accounts/risk.py:612`). Every live/paper order this
bot places goes through one path: `RiskManager` sizing → the netting guard →
`execute.py` → broker client → `trade_journal.db` journaling → the
reconciler. That path is what makes every position auditable, journaled, and
subject to the risk caps configured in `config/accounts.yaml`.

An order placed via the Alpaca MCP server's trading tools goes **directly**
against Alpaca's Trading API, outside all of that:

- **No risk sizing, no caps.** `RiskManager` never sees it.
- **No journal row.** `execute.py::_log_trade_to_journal` never runs, so
  `trade_journal.db::trades` has no record of it.
- **A phantom orphan.** The reconciler (`order_monitor._reconcile_orphan_exchange_positions`)
  would eventually see the exchange-side position with no matching journal
  row and adopt it as an `adopted_orphan` — the same failure class already
  seen in live diag pulls this session (an exchange position with no
  matching journal row). For a **live real-money account**
  (`alpaca_live`), this is a genuinely un-audited, un-risk-gated order path
  against money at risk.

This is not a theoretical concern specific to this tool — it's the general
risk of any order-capable integration that sits outside `execute.py`. The MCP
server is worth adopting; the trading toolset specifically is not.

## Rule: read-only scoping only, always

**Whenever this server is connected to a session that touches this repo,
`ALPACA_TOOLSETS` must exclude the trading category.** Use it for account,
market-data, and history lookups only — e.g.
`ALPACA_TOOLSETS=account,stock-data,options-data,news`. Never include the
trading toolset. This repo's own pipeline stays the only order path, full
stop.

If a future session finds the trading tools enabled (no `ALPACA_TOOLSETS` set,
or a value that includes trading), flag it to the operator before using the
server for anything — don't assume it's safe to proceed just because the
tools are present.

## What it's useful for

Fast, direct live lookups during a review or debugging session — e.g.
checking `alpaca_live`'s real-time buying power / margin / PDT status
directly instead of a multi-hop `vm-diag-request` round trip through the
live VM. Concretely useful for verifying the Track D leverage-config change
(alpaca_live sizing floor, see the system-review follow-up plan) landed
correctly: query buying power/margin post-change rather than waiting for the
next signal to size non-zero.

## Setup (operator action — Claude Code sessions can't self-add MCP servers)

Per `CLAUDE.md`'s "PM-side session capabilities" § "No custom MCP servers":
Claude Code on the web doesn't honour project `.mcp.json` and can't run
`claude mcp add`. Same constraint class as the other pre-connected MCP
servers a session already has (GitHub, Bigdata.com, Hugging Face) — those are
added at the environment level, not by the session itself. The operator adds
the Alpaca MCP server the same way, with the read-only `ALPACA_TOOLSETS`
scoping above.

To verify the scoping after connecting: ask the session to list the
available Alpaca MCP tools (`ToolSearch "alpaca"` or equivalent) and confirm
no order-placement tool (`place_order`, `place_stock_order`, anything with
"order" + a mutating verb) appears in the list.

# Breakout POC — manual browser-Claude bridge (2026-06-16)

> Tier-1 design. A **proof-of-concept** way to run our strategies on the
> Breakout 1-Step account **without** the DXTrade API or a third-party copier —
> using copy-pasteable tickets and a browser-Claude session to drive the
> DXTrade terminal by hand. Chosen because (a) the DXTrade API/copier path is
> blocked/unconfirmed (see `metacopier-bridge-DESIGN.md` + `breakout-compliance-2026-06-16.md`),
> and (b) a human-in-the-loop, easily-replicable manual flow is **cleaner on
> Breakout's ToS** (no third-party copier "marketed to pass evals", no
> credential-sharing with a SaaS).
>
> Status: **DESIGN — approved in chat 2026-06-16 (POC).** Build the outbound
> emitter first.

## The core safety distinction (read this first)

There are two separate jobs; do not conflate them:

1. **Real-time breach protection = the broker-side bracket.** Every order is
   placed with **SL + TP attached at entry** (DXTrade bracket). This is the ONLY
   thing that protects the $5k account in real time — it survives a dropped
   browser session, our VM going down, everything. **Hard invariant: no ticket
   is ever placed without an attached SL and TP.**
2. **Inbound data = monitoring / journaling / analytics**, NOT breach
   prevention. Knowing fills, PnL, and account-level rule-distance is for
   awareness and record-keeping. Because the bracket is the safety net, this
   feed can be **periodic and manual** without endangering the account.

Conflating the two — relying on a browser agent to "watch and close at the
stop" — is the single biggest blow-the-account risk and is explicitly excluded.

## Round-trip

```
 bot signal (prop-routed strategy fires)
     │
     ▼
 OUTBOUND: "Breakout trade setup" ticket  ──(Telegram)──▶  operator
     │                                                        │ paste
     │                                                        ▼
     │                                          browser-Claude on DXTrade terminal
     │                                          places BRACKET order (entry+SL+TP)
     │                                                        │
 INBOUND: prop-account journal + dashboard  ◀──(paste)──  "fill / account status" block
                                                            browser-Claude reads terminal
```

Everything is copy-paste; no DXTrade API, no scraping, no copier.

## Outbound — the "Breakout trade setup" ticket

Emitted when a **prop-routed** strategy produces an actionable BTC signal that
the `PropRiskManager` (Breakout ruleset) clears. A clean, paste-ready block with
everything browser-Claude needs to act without guessing:

- **Instruction line:** "Place a BRACKET order on DXTrade: entry + SL + TP
  attached. Do not place without both."
- **Symbol:** Breakout/DXTrade symbol (map BTCUSDT → Breakout's BTC symbol).
- **Direction:** long / short.
- **Size:** sized to the **$5k** account's risk via the prop ruleset
  (risk_pct × $5k ÷ SL-distance), expressed in **DXTrade contracts**. The
  per-symbol contract spec (contract size / min qty / tick) comes from the
  DXTrade Terminal's instrument info — fill at wire time; until then the ticket
  also states the **dollar risk** so size can be cross-checked.
- **Entry / SL / TP** prices.
- **Rule context:** "this trade risks $X = Y% of $5k; daily-loss limit $150,
  static-DD floor $300" so the operator sees the prop impact at a glance.

Routing: a config list of which strategies feed Breakout (default to the
evaluator's survivor combo once the matrix lands; for the first POC, a single
strategy is fine). Channel: Telegram (the bot already has it).

**Build:** a `src/prop/breakout_ticket.py` formatter + a hook where the
coordinator already emits notifications. Tier-1 to format/emit (no live order
path of ours is touched — we're emitting a message, not placing an order).

## Inbound — report-back (no scraping)

Browser-Claude (or the operator) pastes back two structured block types; the
bot ingests them into a **prop-account journal** (a new table / JSONL keyed to
the Breakout account) surfaced on the dashboard:

1. **Fill / close report** — orderId, entry/exit price, qty, realized PnL,
   open/close time, reason. → so the Breakout trade appears in our system
   alongside the bot's own trades (clearly tagged as the prop account).
2. **Account status** — balance, equity, today's realized+unrealized PnL,
   current drawdown. → the dashboard renders **distance to daily limit ($150)**
   and **distance to static-DD floor ($300)** — the numbers that actually
   matter for a prop account.

Ingest path: a Telegram command (e.g. `/prop_report <json>`) or a small
paste-to-file step → writes the prop journal. Manual cadence is acceptable per
the safety distinction above; the operator pulls a status block whenever they
want a fresh read (and always after a fill/close).

**Cleaner long-term alternative:** if Breakout enables the **DXTrade read API**
(positions + balance), inbound becomes automated and continuous — same "is the
API open?" question that gates the copier. Worth confirming with Breakout; it
upgrades both directions.

## Compliance

This POC is the **least** ToS-risky option we've considered: no third-party
copier tool, no credential-sharing with a SaaS, human-in-the-loop, and an
easily-replicable manual approach (clears prohibited items 6, 9, 11 from
`breakout-compliance-2026-06-16.md`). Breakout **allows algorithmic trading**,
so an agent placing your own strategy's orders is within bounds. Still worth a
one-line confirmation to Breakout that assisted/automated order entry on the
DXTrade terminal is fine.

## Build phases

1. **P1 — outbound emitter** (Tier-1): `breakout_ticket.py` formatter +
   prop-routing config + Telegram emit + tests. The buildable piece now.
2. **P2 — inbound ingest + dashboard** (Tier-1/2): prop-account journal table,
   `/prop_report` ingest, dashboard "rule-distance" panel.
3. **P3 — reconciliation** (Tier-2): match inbound fills to outbound tickets;
   alert on un-acted tickets or drift.

## Open questions

1. Breakout's DXTrade BTC contract spec (contract size / min qty / tick) — for
   exact sizing. From the Terminal's instrument info.
2. First-POC routing: which single strategy / the survivor combo?
3. Does Breakout offer a DXTrade **read** API (would automate inbound)?
4. Notification channel confirmation (Telegram assumed).

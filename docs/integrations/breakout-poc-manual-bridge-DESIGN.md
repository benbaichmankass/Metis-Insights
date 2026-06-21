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

## Executors — who places the ticket (agent-agnostic)

The ticket is a **single generic, self-contained instruction block** — it names
no specific tool, so the same message can be handed to any capable executor. It
opens with a one-line "you are placing a bracket order on the Breakout DXTrade
terminal" preamble so whichever agent reads it knows its job.

Known-viable executors (operator picks per session; no per-agent build needed):

1. **Desktop browser-Claude** (Claude with browser / computer use) — places the
   bracket on the DXTrade web terminal. Baseline.
2. **Comet** (Perplexity's agentic browser, desktop) — same role, same ticket;
   it's a real agentic browser that does multi-step web tasks.
3. **Comet, run on desktop** — start the agentic task *inside* desktop Comet
   against the logged-in DXTrade tab (`@tab`), with a forced "pause for my
   confirmation before submit" step. This is the supported Comet path.
4. **Comet for Android (native app)** — a **self-contained ON-PHONE executor**
   (NEW; Android launch 2026-06, supersedes the "phone is alert-only" stance
   below). Open the DXTrade tab inside Android Comet, log in, focus the
   assistant on that one tab (`@tab` / open Assistant next to the tab), and run
   the same supervised *fill-don't-submit* task there. Per Perplexity's Android
   materials + the Play Store listing, the assistant works across the open tab,
   supports voice mode, takes actions on your behalf, and **shows its actions
   while letting you intervene at any time** — which is exactly the contained,
   supervised control this workflow needs. So the **phone is now a first-class
   executor, not only an alert channel**, and the same ticket runs on laptop
   (option 3) or phone (this option) — whichever device is in hand.
5. **Perplexity Assistant (phone) → Comet (desktop) remote hand-off** — still
   **NOT supported** (no documented phone→desktop remote control of a live
   desktop Comet session). No longer needed: option 4 runs the task on the phone
   directly rather than dispatching to the desktop.
6. **Manual** — operator places it directly on the DXTrade app/web from the
   ticket's human-readable card. Always the fallback; phone-native.

**Agentic-browser caveats (Comet/any):** Comet treats financial sites as
block-candidates and publishes no brokerage policy; agentic browsers also carry
prompt-injection risk and are weaker on dynamic/modal-heavy trading UIs. So for
*any* agent executor: **supervised confirm is mandatory, review every field, no
full autonomy** — reinforcing the rules below.

Rules **every** executor must honor (printed in the ticket itself, so it travels
with the message regardless of which agent gets it):
- **Bracket SL+TP attached at entry — never place without both.**
- **Honor the validity guards** (TTL + entry band; abort if stale / out-of-range).
- The executor must be **logged into DXTrade** and able to **read the live price**
  (to check the entry band).
- **Do not manage the exit** — the broker-side bracket is the exit.

> **Update 2026-06-21 — no manual confirmation pause.** The ticket previously
> printed a "pause for my confirmation before you submit" rule. That was removed
> at operator direction: the prop bridge is meant to run as automatically as the
> executor allows, not gate on a manual per-trade confirm. The safety net is the
> **broker-side bracket (SL+TP at entry)** plus the **TTL / entry-band validity
> guards** baked into every ticket — those survive a dropped session and bound a
> stale/out-of-range fill — so the per-trade human confirm was redundant friction
> against the automation goal. The agentic-browser caveats below are retained as
> background, but "supervised confirm is mandatory" is no longer an invariant of
> the ticket.

## Signal validity / staleness guards (outbound ticket)

Tickets are placed **manually**, so there's lag between signal and execution.
Every ticket therefore carries explicit validity guards, and the instruction
block tells browser-Claude to **ABORT and reply "skipped: <reason>"** if any
fails — a stale or out-of-range setup must never be entered.

1. **Time-to-live (TTL).** Ticket carries `signal_time` + `valid_until`. TTL is
   **timeframe-aware** (a fraction of the strategy's bar interval — a 15m signal
   expires far sooner than a 4h one). `now > valid_until` → skip ("expired").
2. **Entry price band.** Ticket carries `entry` plus `entry_min`/`entry_max` — a
   band derived from a fraction of the entry→SL distance (default ≈0.25, tunable;
   clamped so the band never crosses the SL). Before placing, browser-Claude
   reads the **live price** on the terminal; if it's outside the band → skip
   ("out of range"). This bounds how much the R:R can degrade from a late fill.
3. **Already-ran check.** If price has blown past `entry` toward the TP (the move
   happened without us), it's out of band → skip. The edge was entering *at* the
   level, not chasing.
4. **Preferred mechanism — limit entry + expiry.** Where the terminal supports
   it, place a **LIMIT order at `entry` with the attached bracket and a
   time-in-force/expiry (GTD/day)**. This makes both guards *intrinsic*: a stale
   signal simply never fills (price moved away) and the expiry handles time —
   rather than leaning on the agent's judgment. Use a market entry only if the
   ticket explicitly flags it AND price is still in-band.
5. **SL/TP are absolute prices**, so they stay valid for any in-band fill — the
   band is what preserves the R:R; the protective levels themselves don't move.

The bot computes `valid_until`, `entry_min`, `entry_max` at emit time and prints
them in the ticket; the abort rules are stated in plain language in the
instruction block so the agent can't miss them. TTL-per-timeframe and band width
are tunable config.

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

Ingest path (built 2026-06-21): **`POST /api/bot/prop/report`** with the report
JSON (auto-detects fill/close vs account-status, or set `kind`) → writes the
prop journal + fires the `prop_closed`/`prop_fill` notification. The dashboard
ships a paste-form over the same endpoint; the automated executor can POST
directly. Manual cadence is acceptable per the safety distinction above; the
operator (or executor) posts a status block whenever they want a fresh read
(and always after a fill/close).

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
   prop-routing config + Telegram emit + tests. ✅ **DONE.**
2. **P2 — inbound ingest + dashboard** (Tier-2): ✅ **DONE 2026-06-21.**
   prop-account journal (`src/prop/prop_journal.py` — `prop_tickets` /
   `prop_fills` / `prop_account_status` tables in `trade_journal.db`, isolated
   from `trades`), `POST /api/bot/prop/report` ingest
   (`src/prop/prop_report.py` + `src/web/api/routers/prop.py`), a
   `prop_closed` / `prop_fill` notification (the trade-close follow-up;
   `breakout_notify.emit_prop_fill`), and the dashboard rule-distance panel
   (`GET /api/bot/prop/status`).
3. **P3 — reconciliation** (Tier-2): ✅ **DONE 2026-06-21.**
   `src/prop/prop_reconcile.py` — the executor records each outbound ticket at
   emit time; inbound fills are matched back (`match_fill_to_ticket`); un-acted
   tickets (emitted, past `valid_until`, no matching fill) surface at
   `GET /api/bot/prop/reconcile`.

> **Ingest channel (decided 2026-06-21):** a **REST endpoint**
> (`POST /api/bot/prop/report`) + a dashboard paste-form, NOT a Telegram
> command — chosen for the automation goal (the browser/Comet executor can POST
> the fill back itself) and because no freeform inbound Telegram command
> dispatcher exists. Token-gated via `DASHBOARD_API_TOKEN` when set.

## Open questions

1. Breakout's DXTrade BTC contract spec (contract size / min qty / tick) — for
   exact sizing. From the Terminal's instrument info.
2. First-POC routing: which single strategy / the survivor combo?
3. Does Breakout offer a DXTrade **read** API (would automate inbound)?
4. Notification channel confirmation (Telegram assumed).
5. Validity-guard defaults: TTL per timeframe (e.g. 15m → ~1 bar, 4h → ~1 bar?)
   and entry-band width (default ≈0.25 of entry→SL distance) — confirm/tune.
6. Does Breakout's DXTrade support limit-entry + attached bracket + GTD expiry
   (the preferred intrinsic-staleness mechanism in §"Signal validity")?

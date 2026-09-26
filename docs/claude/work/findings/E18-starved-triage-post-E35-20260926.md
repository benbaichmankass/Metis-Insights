# E18 starved-leg triage — post-E35 rerun

> **Doc status:** `unknown` · category `unknown` · last verified `never`
> (registered in [`docs/DOCUMENT-INDEX.md`](../../../DOCUMENT-INDEX.md); no
> directory/name rule in `scripts/ops/document_index.py` classifies
> `docs/claude/work/findings/**` yet, so this stamp deliberately matches what
> `document_index.categorize()`/`status_for()` compute today rather than
> asserting a category this register cannot re-derive — see
> `check_document_index.py`'s R6.)
>
> Dispatched by manager `session_01Ljhs6sFAdWHdMDhJpL5aBP` (Tier-1, ceiling
> $20) · lane `session_01URR1sVVXrF8jjKKNStSR8n` · 2026-09-26

Re-run of `scripts/ops/leg_flow_report.py` (checklist row
[`E18`](../MANAGER-CHECKLIST.json), built in PR #12922) over the window since
E35 deployed, compared against the first (pre-E35) run recorded in the E18
checklist note and pipeline record `PI-20260925-BN2S6RQZ-0001`.

## Population and denominators

- **Pre-E35 run:** 79h window, 63 live legs. 14 `starved`, 19 `flowing`, 30
  `no_intents`. 6 of the 14 explained inline (4 `has_open_position`, 2
  `held_back>0`), leaving 8 "cleanest" unexplained → filed as
  `PI-20260925-BN2S6RQZ-0001`.
- **Post-E35 run (this session):** 28h window, `2026-09-25T08:01:33Z →
  2026-09-26T12:01:33Z` (E35 deployed `2026-09-25T08:45:37Z`, per the manager
  dispatch — the window opens ~44 min earlier than that because
  `leg_flow_report.py` only takes whole-hour windows; the ~12 pre-deploy
  minutes are immaterial to every leg discussed below, each of which has
  activity well inside the post-deploy span). **Same population, 63 live
  legs** (`enumerate_live_legs` count unchanged — no roster drift between
  runs). `read_state: measured` for every leg — **zero legs graded
  `unreadable`**, i.e. no collapsed-state failure this run.
  - `starved`: **12** (down from 14)
  - `flowing`: **13** (down from 19)
  - `no_intents`: **38** (up from 30)
- Raw report: `scripts/ops/leg_flow_report.py --window-hours 28 --json`
  (rerun command below reproduces it).

No 1d-timeframe leg graded `starved` this run (the one that did pre-E35,
`qqq_trend_long_1d`, now reads `no_intents` — see below) — the
`insufficient_window` carve-out from the dispatch instructions did not end up
needed as a distinct verdict this run, but is noted per-leg where relevant.

## Per-leg table — all 12 post-E35 `starved` legs

| Account | Strategy | Intents | Episodes | Held back | Verdict | Evidence |
|---|---|---|---|---|---|---|
| bybit_1 | trend_donchian_sol_4h | 12 | 2 | 0 | **elected_away** | Sibling `trend_donchian_sol` (same account, same symbol SOLUSDT) fired 26 matching `buy` intents at the identical tick timestamps and is `flowing` (received=1). E35's per-account election (`src/runtime/intent_multiplexer.py`, ranked by cost-aware EV_R, not raw confidence — `_attach_account_election`) picked the sibling every contested tick this window. Working as designed, not a defect. |
| bybit_1 | ict_scalp_xrp_15m | 5 | 2 | 1 | **elected_away** (+ partially blocked) | Sibling `ict_scalp_xrp_5m` (same account/symbol) is `flowing` (received=1). 1 of 2 episodes also carries `held_back` (a real refusal landed for that episode). |
| bybit_1 | ict_scalp_sol_15m | 20 | 7 | 4 | **elected_away** (+ partially blocked) | Sibling `ict_scalp_sol_5m` (same account/symbol) is `flowing` (received=2). 4 of 7 episodes also `held_back`. |
| alpaca_paper | gld_pullback_1h | 92 | 11 | 0 | **holding** | `has_open_position: true` — position opened before the window; re-signalling "stay in", not unreached. |
| alpaca_paper | slv_pullback_1d | 173 | 3 | 0 | **holding** | `has_open_position: true`. |
| alpaca_paper | gdx_pullback_1d | 140 | 5 | 0 | **holding** | `has_open_position: true`. |
| alpaca_portfolio | slv_pullback_1d | 173 | 3 | 0 | **holding** | `has_open_position: true` — mirrors `alpaca_live` below; Stage-2 strict-equality invariant intact. |
| alpaca_live | slv_pullback_1d | 173 | 3 | 0 | **holding** | `has_open_position: true`. |
| alpaca_options_paper | slv_pullback_1d | 173 | 3 | 0 | **UNEXPLAINED — filed** | `has_open_position: false`, `held_back: 0`. Same strategy is holding (executing fine) on 3 sibling accounts in the same window. Zero `trades` rows of *any* status for this (account, strategy) pair in-window, even though a refusal inside `place_options_expression` always journals a `rejected` row (`execute.py:520-530`) and this account has 14 such historical rejections for other legs. → `PI-20260926-KNSTSR8N-0003`. |
| alpaca_options_paper | gdx_pullback_1d | 140 | 5 | 0 | **UNEXPLAINED — filed** | Same shape as above. → `PI-20260926-KNSTSR8N-0003`. |
| breakout_1 | trend_donchian_sol_prop | 26 | 1 | 1 | **blocked** | `held_back` (1) fully covers the leg's only episode — reticket-suppressed (a refusal landed), not unreached. |
| breakout_1 | trend_donchian_eth_prop | 5 | 1 | 0 | **DETECTOR FALSE POSITIVE — filed** | `/api/bot/prop/tickets` shows a real in-window ticket (`prop-manual-693bb30f7638`, `2026-09-25T11:11:16Z`, `op_status: emitted`, `close_reason: prop_ticket_emitted`) whose `status` is `expiry_prompted` — a real manual-bridge lifecycle status absent from both `PROP_RECEIVED_STATUSES` and `PROP_NOT_RECEIVED_STATUSES`, silently dropped by `count_received_prop`. The ticket demonstrably reached dispatch; this leg is not actually starved. → `PI-20260926-KNSTSR8N-0002`. |

## What E35 changed, measured

- **Population stable:** 63 live legs both runs — no roster drift between the
  two triage windows.
- **Starved count fell 14 → 12.** Of the pre-E35 14, the one leg named
  explicitly in the original filing — `qqq_trend_long_1d` on both `ib_paper`
  and `alpaca_paper` — is now `no_intents` (0 intents in this 28h window) on
  both accounts. `qqq_trend_long_1d` is a 1d-timeframe strategy; 0 intents in
  a 28h window is the expected shape for "no setup formed," not evidence the
  fix worked — **I cannot confirm from available records whether E35 changed
  this leg's outcome or whether it simply didn't set up this window**, because
  the pre-E35 run's full per-leg list was never persisted anywhere in the repo
  beyond the checklist note's one named example. This is a real gap, stated
  rather than papered over: the comparison below is as complete as the
  surviving records allow, not a byte-for-byte before/after.
- **3 legs now read as `elected_away`** (`trend_donchian_sol_4h`,
  `ict_scalp_xrp_15m`, `ict_scalp_sol_15m`, all on `bybit_1`) — this is a
  *new, directly observable effect of E35 itself*: before E35 these accounts
  ran the retired global-election-plus-fan-out path (`arbitration_fanout.py`,
  env-disabled 2026-09-25 per `#12952`); under E35's per-account election,
  each is consistently outranked by a same-symbol sibling on cost-aware EV_R.
  This is the election working as designed (a deliberate quality mechanism,
  not a starvation bug), but it does mean these 3 specific legs are
  structurally unlikely to trade on their current account while a
  higher-EV_R sibling keeps winning the same symbol slot — worth the
  operator's awareness, not a filed defect (nothing to act on without a
  concrete case that the election itself is mis-ranking).
- **1 leg (`trend_donchian_eth_prop`) is a detector bug, not a real change** —
  see filed record.
- **2 legs remain genuinely unexplained** (`alpaca_options_paper` /
  `slv_pullback_1d` + `gdx_pullback_1d`) — filed, not diagnosed to root
  cause within this Tier-1 read-only lane's scope (order-path
  instrumentation would be Tier-2/3).

## Pipeline records filed this session

- `PI-20260926-KNSTSR8N-0001` — **killed**, same session: a shell-escaping
  accident (unescaped backticks inside a `python3 -c "..."` bash invocation
  silently dropped two words from the finding text). Superseded verbatim by
  `-0002`; kept in the store per the pipeline's append-only contract rather
  than deleted.
- `PI-20260926-KNSTSR8N-0002` — the `trend_donchian_eth_prop` detector
  false-positive (`expiry_prompted` missing from `PROP_RECEIVED_STATUSES`).
  Proposed fix is Tier-1 (pure `scripts/ops/leg_flow_detector.py` change, no
  order-path touch) but **not shipped in this PR** — this lane's landing
  scope is the analysis artifact + pipeline rows only, per the dispatch.
- `PI-20260926-KNSTSR8N-0003` — the two genuinely unexplained
  `alpaca_options_paper` legs. Root cause not isolated; proposed next step is
  Tier-2/3 (order-path instrumentation) and is **not shipped**.
- `PI-20260925-BN2S6RQZ-0001` — closed `done`, pointing at the two records
  above.

## Proposals (not shipped — Tier-1 lane scope is read-only + filing)

1. **Tier-1, small:** add `"expiry_prompted"` to
   `scripts/ops/leg_flow_detector.py::PROP_RECEIVED_STATUSES`, and consider
   asserting `received + held_back + unmapped == len(ticket_rows)` in
   `leg_flow_report.py` so a future unmapped prop-ticket status is loud
   rather than silently absorbed into `starved`. Tracked in
   `PI-20260926-KNSTSR8N-0002`.
2. **Tier-2/3, needs order-path instrumentation:** find why
   `(alpaca_options_paper, slv_pullback_1d)` and `(alpaca_options_paper,
   gdx_pullback_1d)` never reach even the refusal-journal step in
   `execute_pkg` this window, despite the identical strategies executing
   successfully on 3 sibling accounts. Tracked in
   `PI-20260926-KNSTSR8N-0003`.
3. **Informational, no action proposed:** `bybit_1`'s
   `trend_donchian_sol_4h` / `ict_scalp_xrp_15m` / `ict_scalp_sol_15m` are
   structurally likely to keep losing E35's per-account election to their
   same-symbol 5m/base siblings. Flagging for operator awareness only — the
   election is functioning as designed (cost-aware EV_R ranking), and no
   evidence here suggests it is mis-ranking.

## Rerun commands

```bash
# Full post-E35 report
python3 scripts/ops/leg_flow_report.py --window-hours 28 --json

# The eth_prop ticket that motivated PI-20260926-KNSTSR8N-0002
curl -sS "https://ict-bot.duckdns.org/api/bot/prop/tickets?account_id=breakout_1&limit=500" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print([t for t in d["tickets"] if t.get("strategy")=="trend_donchian_eth_prop" and t.get("status")=="expiry_prompted"])'

# alpaca_options_paper leg state, for PI-20260926-KNSTSR8N-0003
python3 scripts/ops/leg_flow_report.py --window-hours 28 --json \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print([l for l in r["legs"] if l["account_id"]=="alpaca_options_paper"])'
```

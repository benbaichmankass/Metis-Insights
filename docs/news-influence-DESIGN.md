# News influence operator — design (M9 graduated "act" layer)

**Status:** steps 1–3 built. Step 1 = the pure operator (`src/news/news_influence.py`).
Step 2 = live-path wiring (`src/runtime/news_sizing.py`, applied in
`Coordinator.multi_account_execute` right after the advisory downsize), **default-off**
via `NEWS_INFLUENCE_MODE`. Step 3 = the economic-calendar `event_risk` source
(`src/news/news_events.py` + `config/economic_calendar.yaml`): the pipeline stamps
`event_risk = impact × proximity` for the traded symbol onto `pkg.meta`, so the
operator's event consideration now has real input (0.0 when no event is in
window or the calendar is empty). Mirrors the WS7 advisory-influence rollout
(`docs/sprint-plans/ai-traders/ws7-advisory-influence-operator-DESIGN.md`).

**Open follow-up:** the `events` list in `economic_calendar.yaml` is operator-
maintained for now; a scheduled job can refresh it from a live feed (e.g. the
Bigdata.com `events_calendar`) — the loader + risk math are source-agnostic.

## Problem

Today the live path only acts on the news **veto** — a blunt, binary skip. The
operator's directive (2026-06-09): the layer should instead *reason about*
whether the news, and any imminent high-impact event, **supports the trade's
direction or threatens to knock it off course**, and adjust exposure
accordingly. Specifically: an economic event should be a **consideration**, not
a trading blackout.

## Model

`score_news` already yields `adjustment ∈ [-1, 1]` (net news sentiment, positive
= bullish). The operator combines it with the trade side:

```
side_sign  = +1 (buy) | -1 (sell)
alignment  = adjustment * side_sign      # +1 news fully backs the trade, -1 fully against
opposition = max(0, -alignment)          # 0 aligned/neutral … 1 fully opposed
```

An injected `event_risk ∈ [0, 1]` expresses how much an imminent scheduled event
could knock the trade off course. It is **discounted when the trade is aligned**
with the prevailing news direction (the event likely pushes our way) and counts
in full otherwise:

```
threat = clamp(opposition + event_risk_weight · event_risk · (1 − max(0, alignment)), 0, 1)
factor = 1.0 − threat · (1.0 − size_floor)        # ∈ [size_floor, 1.0]
```

Outcomes:
- **Opposed news →** downsize toward the floor (scaled by how opposed).
- **Imminent event + not-aligned →** downsize more (the knock-off-track risk).
- **Aligned with both news and event →** `factor = 1.0`, position untouched.

## Invariants (enforced by construction)

- **Reductive-only.** `factor ∈ [size_floor, 1.0]`, never > 1.0 — can only shrink
  a position, never enlarge/create one, never touch entry/SL/TP/side.
  `_clamp_reductive` asserts finiteness and clamps.
- **Default off.** Gated by `NEWS_INFLUENCE_MODE` (off/annotate/downsize) + a
  per-strategy `news_influence` policy block. Any gate absent → `1.0`.
- **Dead-band.** Opposition below `oppose_threshold` is treated as neutral so
  faint sentiment noise never resizes a trade.

## Remaining steps (operator-gated)

1. **Step 2 — live wiring (Tier-3).** Apply `news_size_factor` at the same
   `Coordinator.multi_account_execute` per-account-qty point the advisory
   downsize uses, composed multiplicatively with it (both reductive, so the
   product stays ≤ 1.0). Compute once per package, cache on `pkg.meta`. Audit to
   the shadow-soak log. Shadow-soak first, then enable per-symbol on demo.
2. **Step 3 — event-risk feed.** Replace the injected `event_risk=0.0` with a
   real economic-calendar signal: `event_risk = impact × proximity`, direction-
   aware where the event has a knowable bias. Candidate sources: the Bigdata.com
   `events_calendar` MCP, or a dedicated econ-calendar API. Per-symbol event sets
   (equity index → FOMC/CPI/NFP; gold → same + DXY; copper → China PMI; energy →
   EIA). This is a **consideration**, never a hard blackout.

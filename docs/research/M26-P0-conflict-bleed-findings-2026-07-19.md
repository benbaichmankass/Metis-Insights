# M26 P0 — conflict-bleed quantification: results (2026-07-19)

**Verdict: the operator's premise is confirmed, with a sharper shape than the
premise itself.** Suppressed opposing signals DO carry information — holding
after the warning was worse than closing in **~75–77% of measured cases** —
but the *net dollars* are dominated by a fat right tail of big trend winners
that holding preserved, so neither blanket policy (always-hold, always-close,
always-flip) is right. The bleed is **conditional**, and the two conditions
that split it cleanly are exactly the two axes the M26 design proposed:
**timeframe ratio** and **strategy class**. Same-clock conflicts are harmful
to hold through; cross-clock conflicts are benign-to-positive. Trend-riders
should hold; pullback/scalp holds bleed badly.

Run: miner `scripts/research/m26_p0_conflict_bleed.py` @ `029f3ae` on the
trainer's synced journal (trainer-diag #6963, 2026-07-19 12:17Z).
Raw material: **585 hold-suppression rows → 70 conflict episodes → 98 measured
trade-conflict pairs** (35 real-money, 63 paper). Unmeasured and counted: 11
trade-pairs unresolved/still-open, 21 episodes with no conflict-time price
(mostly MES/equities — shards land with tonight's WS-B build), 7 with no open
trade joined.

## Headline numbers

`tail_held` = realized PnL from conflict-time to close (what holding
earned/lost AFTER the warning); close-at-conflict = 0 by definition;
`tail_flip` = the same-qty flip counterfactual (no fees).

| Book | n | tail_held Σ | tail_flip Σ | held worse than close |
|---|---|---|---|---|
| Real money | 35 | **−$19.98** | +$7.49 | **77.1%** |
| Paper | 63 | **+$1,187** | **−$16,906** | **74.6%** |

The apparent contradiction (positive held-sum, 75% held-worse) IS the finding:
the median conflict is a genuine warning (hold loses), but the minority where
hold wins are large trend continuations. **A blanket flip policy would have
been catastrophic (−$16.9k paper)** — the May walk-forward that chose `hold`
over `reverse` was right *on average* — while a blanket close forfeits the
winners. Real-money dollar magnitudes are small because bybit_2 sizes near
min-qty; the paper book (incl. big-qty soak accounts) shows the shape at scale.
Real and paper are never blended; dollar sums are not comparable across books.

## The two decisive strata

**1. Timeframe ratio (the operator's coexistence insight — confirmed):**

| Stratum | n | tail_held Σ | held worse % |
|---|---|---|---|
| cross-TF (≥4× clock ratio) | 78 | **+$3,481** | 78.2% |
| same/near-TF (<4×) | 18 | **−$2,320** | 72.2% |

Cross-clock "conflicts" net POSITIVE to hold through — a fast counter-signal
against a slow position is mostly noise at the slow clock (and per the design,
arguably should be allowed to trade as its own coexisting position rather than
be suppressed). Same-clock opposition is where the real transition warning
lives: net −$2.3k held.

**2. Held-strategy class (who should listen to the warning):**

| Held strategy | n | tail_held Σ | tail_flip Σ | held worse % |
|---|---|---|---|---|
| `htf_pullback_trend_2h` | 47 | **−$6,037** | +$3,808 | **93.6%** |
| `ict_scalp_5m` | 3 | −$3,759 | +$913 | 66.7% |
| `trend_donchian` | 35 | **+$10,135** | −$21,071 | 57.1% |
| `squeeze_breakout_4h` | 5 | +$235 | +$45 | 60.0% |
| `eth_pullback_2h` | 3 | +$668 | −$668 | 66.7% |
| others (fade/fvg/pairs) | 5 | −$76 | +$76 | — |

`htf_pullback_trend_2h` is the bleed concentration: 47 pairs, holding was
worse **93.6%** of the time, −$6k net — for this strategy the opposing signal
is close to a direct exit instruction. `trend_donchian` is the opposite:
holding through conflicts is its edge (+$10.1k) and flipping it would have
been ruinous. This is the strongest possible argument that the P3 policy arm
must be **per-strategy-class (or per-exit-style), not global**.

## Caveats (honest limits of this pass)

- n=98 pairs; ETH thin (n=5); MES/equities episodes unmeasured until the
  candle shards land (tonight) — rerun then for full coverage.
- `tail_flip` ignores the extra round-trip's fees/slippage and assumes
  same-qty close at the held trade's close time — it overstates flip
  attractiveness; treat flip sums as upper bounds.
- Mark-at-conflict uses the nearest 1h candle close (exchange-truth
  `exit_price` is used for the close side where present).
- Sums are dollar-weighted; a per-R normalization (M24 net-R labels) is the
  natural upgrade for P3's formal gate.

## What this buys the next phases

- **P1 (taxonomy)** is now empirical, not speculative: `tf_ratio ≥ 4` →
  coexist (do NOT treat as a transition vote — and evaluate letting the fast
  signal trade as its own position); `tf_ratio < 4` → transition vote.
- **P2 (transition score)** should weight same-clock opposition clusters;
  cross-clock opposition is mostly noise for this purpose.
- **P3 (policy arms)**: the arms to beat `hold` out-of-sample are now
  targeted: (a) **transition-triggered exit-tighten on the
  pullback/mean-reversion class** (the 93.6% cell — likely the biggest win),
  (b) TF-aware coexistence for cross-clock signals, (c) keep pure `hold` for
  donchian-style trend riders. A global flip stays dead on arrival (the data
  killed it again).
- Blanket-policy sanity anchor: every P3 arm must beat BOTH always-hold AND
  always-close per class, walk-forward, net of costs.

Backlog: `MB-20260719-M26-TRANSITION-CONFLICT` updated (P0 delivered; P1/P2
next). Rerun the miner post-WS-B-shards for MES/equities coverage.

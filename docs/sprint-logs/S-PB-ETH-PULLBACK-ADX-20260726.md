# Sprint Log: S-PB-ETH-PULLBACK-ADX-20260726

## Date Range
- Start: 2026-07-26
- End: 2026-07-26

## Objective
- Primary goal (PB-20260618-015): refine the entry gate of `eth_pullback_2h` —
  the LAST un-tuned leg of the 2h-pullback family (per-symbol ADX tuning is done
  for the rest: ADA=28, SOL=30 raised + WF-validated; XRP/AVAX=25 held). ETH was
  left at `adx_min=25` as "incumbent best net" but is the worst REAL-money leg
  (2026-07-26 weekly review: −$8.69 / 2 trades / 0 wins on bybit_2) and keeps
  firing LONG into chop (PB-20260614-001).
- Method (mirror the ADA/SOL gate): (1) match the EXACT live params in the
  backtest — the KEY lesson from the SOL invalidation (its first sweep missed the
  live `trail_decay` + `vol_skip`); (2) config-exact ADX-floor grid 25/28/30/32
  on ~5y ETH 2h; (3) per-year sequential OOS walk-forward; (4) ALSO evaluate a
  vol-at-entry cap for the marginal-trend band as an alternative/complement,
  since the losses are transition fakeouts not sub-25 chop.
- Deliverable: EITHER a WF-validated Tier-3 diff (PROPOSE-only, draft PR), OR a
  documented "keep 25" negative result.

## Tier
- Tier 1 (research + docs only). The candidate change is Tier-3 (real-money
  param) but the evidence did NOT clear the gate → **no config change proposed**;
  the deliverable is a documented negative result. Rollback for the live cell (if
  it ever needs it) stays "remove `eth_pullback_2h` from `bybit_2.strategies`".

## Starting Context
- Active roadmap items: M7 (strategy review gate), M8 (strategy tuning). This is
  entry-side (M7/M8), not exit refinement (M20).
- Prior sprint reference: S-SYSREVIEW-PROP-ADX-2026-07-20 (ADA=28, SOL=30
  config-exact raises; XRP/AVAX/ETH held at 25 — ETH held without a shown
  config-exact grid); S-STRAT-REFINE-0618 (ported ADX≥25 into the live unit).
- Known risk at start: the operator's standing directive — do not demote a good
  strategy that merely had a bad week; the right move is regime-conditioning, not
  a reflexive kill. ETH-BTC correlation caveat stands.

## Repo State Checked
- Branch: `claude/eth-pullback-2h-adx-tune-274ngb`, forked at `origin/main`
  (`47747d1`, 0/0 ahead/behind at start).
- Coordination board #6927: read the tail; no live session on the pullback family
  or `config/strategies.yaml`. Posted a `▶️ START` comment naming this scope.
- Canonical docs reviewed: CLAUDE.md (dashboard/API + tiers), the backtesting
  skill, the PB-20260618-015 backlog history.

## Files and Systems Inspected
- Config: `config/strategies.yaml::eth_pullback_2h` (read in full) + the SOL/XRP/
  ADA/AVAX sibling blocks; `config/accounts.yaml` (`bybit_2` real-money routing —
  `eth_pullback_2h` is live on bybit_2; rollback = remove from its `strategies`).
- Code: `scripts/backtest_pullback.py` (harness levers: `--adx-min/max`,
  `--vol-skip-above/below-pctl`, `--trail-decay-*`; `by_year` breakdown);
  `src/units/strategies/htf_pullback_trend_2h.py` (`_DEFAULTS` via `cfg.get(key,
  default)` → trail_decay/vol_skip resolve OFF when the YAML omits them).
- Config-exactness (the SOL lesson): CONFIRMED `eth_pullback_2h` carries a CLEAN
  base — `trail_mult 5.0`, `adx_min 25`, and **NO** `trail_decay_*`, **NO**
  `vol_skip_below_pctl` — unlike SOL (decay stall10/tight2.5), XRP/AVAX (decay
  arm_r), ADA/AVAX (vol_skip 0.1). So the config-exact ETH match is the base
  params + `adx_min` only, with NO decay and NO vol_skip. This is the FIRST
  genuinely config-exact ETH grid: the 2026-07-20 family sweep carried
  `vol_skip 0.1`, which ETH does not have live.

## Work Completed
- **Config-exact ADX-floor grid** (trainer-vm-diag relay #7614, then re-run on the
  current-main harness in #7615 — the trainer's on-disk copy was stale/pre-vol-flag;
  the ADX numbers reproduced exactly, confirming consistency). ~5y ETH 2h
  (2021-03 → 2026-06, resampled from `data/ETHUSDT_15m.csv`), net-of-fee 7.5 bps,
  live params, `timeout 200 / cooldown 1`:

  | adx_min | n | net_R | exp | win% | maxDD_R |
  |---|---|---|---|---|---|
  | none | 332 | 68.57 | 0.206 | 31.0 | 25.36 |
  | **25 (live)** | 252 | **90.23** | **0.358** | 34.5 | 13.57 |
  | 28 | 216 | 51.83 | 0.240 | 37.5 | 10.91 |
  | 30 | 199 | 30.03 | 0.151 | 33.2 | 15.72 |
  | 32 | 174 | 28.82 | 0.166 | 32.2 | 11.26 |

  `adx_min=25` is the clear IS peak; every higher floor DEGRADES net_R
  monotonically (−43% at 28, −67% at 30). This is the **XRP pattern (raise
  hurts), NOT ADA/SOL (raise helps).**

- **Per-year OOS walk-forward (25 vs higher floors).** adx=25 net_R WINS the
  recent full years decisively: 2022 8.3 vs 2.5@28; 2023 36.5 vs 12.6; 2024 14.2
  vs 10.5; 2025 16.4 vs 9.4. Only the partial years (2021, 2026-through-June)
  marginally favor a higher floor. A floor raise FAILS the same "wins recent OOS
  years AND lowers maxDD" bar that cleared ADA=28 / SOL=30.

- **Vol-at-entry cap sweep** (11 variants, adx25 base, current harness, #7615).
  NO variant beats incumbent net_R (90.23). The closest, `--vol-skip-above-pctl
  0.8` (skip the hottest-20% ATR-percentile entries): net_R 89.30 (≈ incumbent),
  exp 0.385 (↑), win 35.3% (↑), maxDD 10.39R (↓23%) — a maxDD-reduction
  candidate that fits the hot-vol "transition fakeout" thesis, BUT per-year it
  LOSES the two most-recent OOS years on net_R (2025 13.2 vs 16.4; 2026 6.0 vs
  7.6) and 2022, winning only 2024 (22.2 vs 14.2 — the one high-vol year the
  thesis predicts). So it fails the SAME recent-OOS bar. The dead-vol skip
  `vollo10` (the ADA/AVAX family winner) HURTS ETH (82.0 vs 90.2R); both-tails
  band 70.8R.

- **Deliverable: documented NEGATIVE result — KEEP `eth_pullback_2h` at
  `adx_min=25`, no vol cap.** No `config/strategies.yaml` change. Logged into the
  PB-20260618-015 backlog with the full grid + WF + vol sweep. Docs-only draft PR.

## Validation Performed
- **Config-exactness discipline (the SOL lesson):** verified against the live YAML
  AND the unit defaults that ETH's base is clean (no decay, no vol_skip) BEFORE
  running — so, unlike the 2026-07-20 family sweep (which carried vol_skip 0.1),
  this grid is genuinely config-exact for ETH.
- **Harness currency:** caught that the trainer's on-disk `backtest_pullback.py`
  was stale (pre-`--vol-skip` flags — the vol variants errored in #7614); re-ran
  the whole sweep against a fresh `curl` of the main-branch harness (#7615,
  `vol-flags-in-harness=4`, `HARNESS-OK`). The ADX baseline reproduced byte-for-
  byte (adx25=90.23R in both), confirming the ADX grid was valid and the vol
  numbers are on the current logic. No trainer repo mutation (non-mutating curl).
- **Real-money safety:** no change shipped. `eth_pullback_2h` stays live on
  bybit_2 exactly as-is at `adx_min=25`.

## Contradictions or Drift Found
- None. This RESOLVES the open PB-20260618-015 residual ("ETH stays 25 — incumbent
  best net") with an explicit config-exact grid, and closes the family-tuning task
  ("XRP/AVAX/ETH=25" is now config-exactly verified for ETH, not just asserted).

## Documentation Updated
- `docs/claude/performance-review-backlog.json` — PB-20260618-015: appended the
  config-exact ETH grid, WF verdict, vol-cap sweep, and the KEEP-25 negative
  result; bumped `updated_at`.
- This sprint log.
- Roadmap: no milestone/status change (a negative research result under M7/M8;
  the family tuning was already tracked in PB-20260618-015).

## Risks and Follow-Ups
- **The −$8.69/2-trade real-money loss is small-sample noise** in a strongly +EV
  strategy (90.2R / exp 0.358 over 5y). Not a fixable entry-param defect — no
  action beyond continued watch.
- **Remaining forward lever:** route the 15m ETH regime head (still `shadow`,
  MB-20260628-VOLGATE-GOLIVE / PERF-20260601-006/007). That is gated on the
  head's own shadow→advisory promotion, NOT an entry-param change — out of scope
  for this entry-tuning task.
- **Per-symbol ADX tuning of the 2h-pullback family is now COMPLETE**, all five
  legs config-exactly verified: ADA=28, SOL=30 (raised + WF), XRP/AVAX/ETH=25
  (held; ETH now with an explicit config-exact negative grid).
- Trainer housekeeping (logged for a future session, not this task's scope): the
  trainer's on-disk `ict-trading-bot` working tree was behind `main` (pre-vol-flag
  harness). Worth a `git reset --hard origin/main` on the trainer if its `/api`-
  less git-sync has drifted — flagged here so it is not walked past.

# Sprint Log: S-MLOPT-S10 (order-flow / microstructure features — OFI, VPIN)

## Date Range
- Start: 2026-06-04
- End: 2026-06-04 (Tier-1 core + the operator-approved Tier-2 capture path BUILT;
  deploy to the trainer on PR merge; data accrues forward)

## Objective
M14 Phase 2.2 — microstructure flow is the highest-proven-ROI feature family
after range-vol. Add **Order-Flow Imbalance (OFI)**, **VPIN** (volume-bucketed
flow toxicity), **micro-price**, and **relative spread** so the regime/decision
heads can read short-horizon order-book pressure. Operator selected S10 as the
next sprint (2026-06-04).

## Tier
- **Tier-1** for the estimator module + tests (pure functions, no I/O,
  CI-tested). No `src/runtime/`, order-path, or live file touched.
- **Tier-2** for the live L2 capture path + storage + runtime/systemd wiring (a
  new always-on capture process + a data-mutation job) — **PROPOSED only**, not
  built; awaits operator sign-off (`docs/ml/orderflow-capture-design.md`).

## Starting Context
- M14 Phase 2 ("better features"). S9 (range-vol) was a clean win on all BTC
  regime heads (promoted to shadow this session); S11 (funding/OI) was a
  documented negative.
- **The defining constraint:** unlike S9 (range-vol from OHLC we already store)
  and S11 (funding/OI backfillable from Bybit REST history), **order-flow needs
  L1/L2 + trade-tick data we do NOT capture and CANNOT backfill** (no public L2
  history endpoint). So S10 cannot be A/B'd offline today — it is a two-part
  sprint and the Tier-2 forward live-capture path is the gate.

## Repo State Checked
- Branch `claude/mlopt-s10-orderflow-vpin` cut from `origin/main` @ `4f70e7f`
  (the merged S9/S11 PR #2739 — confirmed `market_features` builder_version v4 +
  the S9/S11 modules present before branching, after correcting an initial
  mis-base off a stale local `main`).
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `ROADMAP.md` § M14, `docs/ml/optimization-roadmap.md` § Session 2.2.

## Files and Systems Inspected
- `ml/datasets/volatility_estimators.py` + `ml/datasets/funding_oi_features.py`
  (the estimator-module shape mirrored), `ml/datasets/families/market_features.py`
  (the S11 `funding_oi_path` as-of-join pattern the capture design reuses),
  `src/runtime/market_data.py` (`connector_for_symbol` / `fetch_candles` — the
  ccxt/IBKR connector layer the capture path would extend with
  `fetch_order_book` / `fetch_trades`).

## Work Completed
- **`ml/datasets/orderflow_features.py` (new, Tier-1)** — pure estimators over
  already-captured snapshots/trades (caller does past-only windowing, same
  contract as the S9/S11 estimator modules):
  - `microprice` (Stoikov size-weighted fair value),
  - `relative_spread` (spread / mid),
  - `order_flow_imbalance` (Cont-Kukanov-Stoikov OFI summed over best-quote
    snapshots — rising bid / consumed ask → +OFI, falling bid → −OFI),
  - `bulk_volume_classification` (Easley-LdP-O'Hara BVC: split each bucket's
    volume into buy/sell via `Φ(ΔP/σ)`),
  - `vpin` (mean `|V_buy − V_sell|/V` over volume buckets),
  - `_finite_or_zero` (feature-emit shape). Pure stdlib (math/statistics) → CI.
- **`tests/ml/test_orderflow_features.py` (new)** — 16 cases incl. OFI sign
  semantics (rising-ask +OFI, falling-bid −OFI, flat book 0, <2 snapshots None),
  micro-price weighting/degenerate, BVC sums-to-volume + skews-with-price +
  zero-σ 50/50, VPIN one-sided=1 / balanced=0 / empty None.
- **`docs/ml/orderflow-capture-design.md` (new) — the Tier-2 PROPOSAL.** Stores
  **per-bar aggregates** (`ofi`/`vpin`/`rel_spread_mean`/`microprice_dev`) as a
  `market_microstructure` side-stream that reuses the S11 as-of-join + drift
  machinery (storage-bounded — one row/bar, not raw ticks), joined into
  `market_features` via an optional `microstructure_path` (`builder_version v4 →
  v5`). Lays out the **three operator decisions** that gate the build: capture
  host (trainer-VM side-car preferred, WS9-safe / dedicated host / rejected
  live-VM), transport (free Bybit REST polling vs paid ccxt.pro WS), scope
  (BTCUSDT-only to start vs MES/IBKR depth which needs a paid subscription).

## Tier-2 build (operator-approved option 1, 2026-06-04)
The operator chose **option 1**: build the capture path with the recommended
config (trainer-VM side-car / free Bybit REST polling / BTCUSDT-only) + an
ml-review monitor-when-enough-data note. Built:
- **`scripts/ml/orderflow_capture.py`** — long-running capture side-car. Polls
  Bybit public order-book + trades (~2 s), aggregates per-5m-bar OFI / taker
  buy-sell volume / spread / micro-price lean → one `market_microstructure` row
  per bar (append-only JSONL). off-VM-guarded (`ICT_OFFVM_BUILD_HOST=1`);
  per-poll exceptions caught so a transient blip never kills the loop.
- **`deploy/trainer/ict-orderflow-capture.service`** — trainer-only systemd unit
  (`Restart=always`). Deliberately under `deploy/trainer/` so the live-VM
  installer (`scripts/install_systemd_units.sh`, which globs `deploy/*.service`)
  never picks it up — installed manually on the trainer via the relay.
- **`market_features` `microstructure_path` join** — 6 past-only, as-of-aligned
  columns (`ofi`/`ofi_zscore`/`vpin`/`order_imbalance`/`rel_spread_mean`/
  `microprice_dev`), `builder_version v4 → v5`, default-preserving (omit → 0.0).
- **`ml/configs/btc-regime-5m-lgbm-flow-v1.yaml`** (research_only) — the A/B vs
  the 5m champion, evaluable only on the captured window once data accrues.
- **`MB-20260604-002`** carries the monitor-and-review note (review the A/B once
  ≥ ~4000 captured 5m bars accrue ≈ 2 weeks).

**Deploy — DONE + verified (trainer-vm-diag #2752):** after #2751 merged (main
@ 8680a4a), installed `ict-orderflow-capture.service` on the trainer VM (cp →
`/etc/systemd/system`, `daemon-reload`, `enable --now`). Verified **active
(running)** since 2026-06-04T09:52:04Z (PID 764562, ccxt 4.5.53, zero journal
errors); the `market_microstructure` out dir is created and the capture is
**accruing forward** (one row per completed 5m bar). No live-VM impact (the unit
is trainer-only). `MB-20260604-002` carries the review-when-≥~4000-rows note.

## Validation Performed
- Local (sandbox, stdlib only): smoke-ran every estimator — micro-price between
  bid/ask + skews to the larger opposite size; OFI +5 on a rising ask, −5 on a
  falling bid; BVC split sums to bucket volume + skews with the price move; VPIN
  1.0 one-sided / 0.0 balanced. `ruff check` clean on the module + tests.
- **Gaps not yet verified:** the features themselves cannot be validated against
  real data until the Tier-2 capture path exists and accrues
  `market_microstructure` — there is no historical L2 to test against. The OFI
  sign convention + VPIN/BVC math are verified by unit test, not against a live
  book.

## Documentation Updated
- `docs/ml/orderflow-capture-design.md` (new proposal); `docs/ml/optimization-roadmap.md`
  Session 2.2; `ROADMAP.md` S-MLOPT-S10 row; `docs/claude/ml-review-backlog.json`
  (`MB-20260604-002`); this sprint log.

## Contradictions or Drift Found
- None new.

## Risks and Follow-Ups
- **The Tier-2 capture path is the gate** (`MB-20260604-002`): nothing here can
  be A/B'd until L2/tick data is captured forward. Three operator decisions block
  the build (host / transport / scope) — see the design doc.
- **Microstructure alpha decays** (research caveat): the per-bar-aggregate
  storage choice is deliberate so the existing KS/PSI drift gate can monitor it
  if it ever promotes.
- **No live-path file touched** — the estimator core is pure; the capture
  service is proposed, not built.

## Deferred Items
- The OFI/VPIN **A/B itself** — blocked on forward data accrual (`MB-20260604-002`,
  review once ≥ ~4000 captured 5m bars exist).
- MES/IBKR depth capture (needs a paid subscription + shares the live IB
  session) — explicit phase-2 follow-up.
- Wiring the captured `market_microstructure` into the trainer-mirror sync (if a
  consumer outside the trainer ever needs it) — not required for the trainer-side
  A/B.

## Next Recommended Sprint
- If the operator green-lights the capture path: build the Tier-2
  `market_microstructure` capture side-car, then (after accrual) the join columns
  + A/B. If not: **S-MLOPT-S13** (per-bar regime scoring, the highest-leverage
  unblock + the gate on the S9 shadow heads earning advisory) or **S-MLOPT-S12**
  (cross-asset/macro for MES + wire the unused `account_context`).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; capture path is a Tier-2 proposal.
- [x] Roadmap status checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated clearly: the features are unvalidated against
      real data (no L2 history); the Tier-2 capture path + 3 operator decisions
      are the gate (`MB-20260604-002`).

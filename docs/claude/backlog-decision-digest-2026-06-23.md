# Backlog decision digest — Tier-3 proposals (C) + operator-only (D)

> **One-pass decision surface (2026-06-23).** These backlog items can't be
> burned down autonomously — they need an operator decision (Tier-3: strategy /
> risk / sizing / model-influence / live-promotion) or a physical/external
> action (Tier-D). Each row is the **decision asked** + **Claude's
> recommendation**. Approve/decline here and the autonomous side executes the
> approved ones (proposes the exact change / runs the workflow). Full context is
> in the source backlog item (`docs/claude/{performance,ml,health}-review-backlog.json`).
>
> Legend: ✅ recommend YES · ⏸️ recommend HOLD/defer · 🔎 needs more evidence first · 👤 operator-only.

## C1 — ML model influence & lifecycle (ml-review-backlog)

| Item | Decision asked | Rec |
|---|---|---|
| **MB-20260613-004** | **ENABLE advisory-model downsize influence** (advisory is `annotate`-by-default today — models log but don't size). This is *the* switch that turns ML into a live-order influence. | ⏸️ HOLD — only one healthy advisory model now (`btc-regime-5m-lgbm-yz-v1`, just promoted, marginal calibration). Build a 2–3 model advisory quorum with clean live-agreement first, then enable. |
| **MB-20260617-001** | Promote the WC-3-retrained long/short `conviction-meta-v1` + `setup-quality-lgbm-v2` to **shadow** (supersede the buy/sell-keyed versions). | ✅ YES — autonomous-safe (candidate→shadow is the autonomous track); refreshes stale-keyed shadow models. Low risk. |
| **MB-20260616-CONVICTION-P4-SIZING** | Graduate conviction to drive **real-money sizing** (soak-gated). | ⏸️ HOLD — conviction-meta still candidate (n_eval 28); soak not mature. |
| **MB-20260616-CONVICTION-P5-FUSION** | Promote each lens's fusion v1 formulaic → v2 learned. | ⏸️ HOLD — depends on P4 + soak. |
| **MB-20260618-XA-D2B** | Make the `c_reg` conviction lens real (regime class-prob vector + calibrator). | 🔎 Research-gated — keep the cross-asset soak running; revisit when XA heads clear gate. |
| **MB-20260530-001** | Augment journal-backed decision models with per-trade backtest features. | ✅ YES (experiment) — net-additive feature work; trainer-autonomous. |

## C2 — Strategy promotion / refinement (performance-review-backlog)

| Item | Decision asked | Rec |
|---|---|---|
| **PERF-20260601-005** | Re-promote `squeeze_breakout_4h` shadow→**live** (operator **pre-approved**, gated on debounce verify). | 🔎 Verify the debounce fix held in soak, then ✅ execute the pre-approved promotion. |
| **PERF-20260601-006** | Regime-router **phase 3** — turn shadow OFF-cells into **hard gates** (`REGIME_ROUTER_ENABLED`). | ⏸️ HOLD — keep phase-2 shadow-logging; promote once the OFF-cell would-have-gated history is clean. |
| **PERF-20260601-007 / -002 / -009** | Regime-router phase 4 (soft weights) · the full regime×strategy×direction matrix · retire `trend_donchian` long_only flag. | ⏸️ Sequenced after phase-3; defer. |
| **PB-20260618-011** | `eth_pullback` ADX≥25 refinement — out-of-pool holdout **PASSED**, ready for a Tier-3 `strategies.yaml` change. | ✅ YES — evidence cleared; I'll PR the exact param change for merge approval. |
| **PB-20260614-001 / PB-20260618-015** | `eth_pullback_2h` keeps firing LONG in chop — add a trend/regime entry filter · review its real-money bybit_2 perf once trades accrue. | 🔎 Tied to PB-...-011; apply the ADX gate, then review. |
| **PERF-20260601-010** | Degenerate `confidence=1.0` on every `vwap` + `htf_pullback_trend_2h` package — make confidence meaningful. | ✅ YES (Tier-3 fix) — flat confidence poisons the conviction lens; worth a real confidence calc. I'll propose. |
| **PB-20260616-001** | Re-point the OANDA practice sleeve to a tradeable OANDA-US FX pair (`xauusd_trend_1h` paused). | ✅ YES — restores a dormant sleeve; config change, I'll PR. |
| **PB-20260616-002** | Verify `ict_scalp_5m` live edge — net-negative in the 3-yr prop backtest. | 🔎 It's the only real-money strategy; gather live evidence before any kill/keep call. |
| **PB-20260622-002** | TLT-only `min_confidence ~0.10` floor on `tlt_pullback_1d`/1h. | ⏸️ Defer — wait for live fills. |
| **PB-20260620-002 / -003** | Build the funding-carry sleeve · the cross-sectional long/short sleeve. | ⏸️ Deprioritized — net-new strategy R&D; revisit when the core book stabilises. |
| **PERF-20260531-001** | `trend_donchian` on SPX: two-sided fails but long-side is a BTC-uncorrelated diversifier. | 🔎 Re-evaluate under the regime-router matrix (C2 phase work). |
| **PB-20260617-002** | Graduate the ExitPlan ladder to the **real exit** (P4 API / P3-live prop) once soak accrues. | 🔎 Check soak volume; graduate when the laddered-vs-single-target soak is sufficient. |

## C3 — Exit/order-path tuning (health-backlog, Tier-2/3)

| Item | Decision asked | Rec |
|---|---|---|
| **BL-20260623-004** | Activate the **news layer** — set `NEWS_SOURCE=rss` (keyless, real-time) on the live VM. | ✅ YES if you want news active — one env change (I'll run it via the VM env path); else record "intentionally inert". |
| **BL-20260612-001** | IB futures orders rejected by IBKR **Error 10349** (TIF forced to DAY by order preset) — fix the TIF/preset. | ✅ YES — blocks IB futures fills; worth a Tier-2 fix. I'll investigate the order-preset path. |
| **BL-20260610-009** | Re-enable the IB liveness probe over the cross-host socat gateway loop. | ⏸️ HOLD — the probe is intentionally skipped for the isolated gateway (`IB_PROBE_TIMEOUT_S<=0`); only revisit if false-wedges recur. |
| **BL-20260601-003** | DRY refactor: consolidate `fade_breakout_4h._adx` + `fvg_range_15m._adx` onto `wilder_adx`. | ✅ YES (low-risk refactor) — autonomous Tier-1-ish; I'll PR with tests. |

## D — Operator-only / external (no autonomous path)

| Item | What only you can do | Rec |
|---|---|---|
| **BL-20260615-IBLIVE-2FA** | IB **live** login blocked — the bot user's IB Key 2FA is challenge/response (not Seamless). Approve/convert the 2FA so `ib_live` can authenticate. | 👤 Needed for any live IB trading; paper (`ib_paper`) works without it. |
| **BL-20260621-ACCOUNT-HISTORY-PULL** | Export full exchange account history (bybit_2 + real-money accounts) for the journal backfill. | 👤 Then I ingest it (Tier-2, with your OK on the exact import). |
| **BL-20260527-006** | FCM private-key bleed operator follow-ups (rotate the leaked key at the console). | 👤 Credential action. |
| **BL-20260617-ANDROID-56-VISUAL** | On-device visual check of android #56 before merge (sandbox can't render). | 👤 Quick device check. |
| **BL-20260617-ANDROID-LINT-PROMOTE** | Promote android `lintDebug` to blocking after a warning-baseline cleanup. | ✅ I can do the cleanup; you decide when to flip it blocking. |

---

**How to use this:** reply with the IDs you approve (e.g. "do MB-20260617-001, PB-20260618-011, BL-20260601-003; activate news rss") and I'll execute/PR them. The ⏸️/🔎 rows I'll leave in the backlog with the gating condition noted. The 👤 rows wait on you.

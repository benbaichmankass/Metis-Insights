# E1 preconditions — the exogenous block exists, is live, and covers 1 of 23 symbols

**Date:** 2026-08-20 · **Step:** E1 of
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)

E1 says: add exogenous features to the in-trade panel, and *"every feature ships
with the live accessor that produces it, or it does not ship."* This is what
scoping that against the code found, before any panel work.

---

## 1. The good news: it is a wiring task, not a build task

The peer-asset feature block **already exists on both sides**, computed by one
shared pure function so live == train by construction:

| half | module |
|---|---|
| offline | `ml/datasets/cross_asset_features.py::compute_cross_asset_feature_rows` |
| live, at score time | `src/runtime/cross_asset_live.py::compute_live_cross_asset_row` |

Per peer slot it emits `ret` · `ret_lag1` · `vol` · `rel_strength` · `beta` ·
`beta_residual`, plus a book-wide `xa_breadth_up`. Two of those are exactly what
the literature survey (§ 1.5.5) says a crypto peer feature needs:

- **`beta` / `beta_residual`** carry an **estimated sign**. Cross-chain
  spillovers are frequently *negative* — a surge on one chain coinciding with
  declines on others — so a peer feature that assumes a positive relationship is
  wrong in exactly the conditions an exit cares about.
- **`rel_strength`** is the *"has the peer already turned?"* term, which is the
  operator's own motivating observation (a short XRP held while a long ETH was
  opened, at ρ 0.88).

There is a kill switch (`CROSS_ASSET_LIVE_DISABLED`), the peers ride the regime
scorer's existing fetch cadence so no new fetch rate is introduced, and the whole
path is observe-only. **E1's falsifier is therefore satisfiable by configuration
rather than new live code** — which is the cheapest possible way to clear it.

## 2. The blocker: the peer map covers one symbol

`config/cross_asset.yaml` declares peers for **`ETHUSDT` only** (`BTCUSDT`,
`SOLUSDT`), because it was written for one shadow head,
`eth-regime-1h-lgbm-xasset-v1`.

Measured 2026-08-20 against `config/strategies.yaml`, restricted to legs that are
`enabled` and `execution: live`:

> **22 of 23 live-traded symbols have no peers declared.**

| symbols with no peers | legs |
|---|---|
| `SOLUSDT` | 6 |
| `BTCUSDT` · `XRPUSDT` | 4 each |
| `ADAUSDT` · `AVAXUSDT` · `GLD` · `MGC` · `QQQ` · `SLV` · `SPY` · `TLT` | 2 each |
| `GDX` · `IAUM` · `IEF` · `IWM` · `MES` · `MHG` · `QLD` · `SCHA` · `SPLG` · `TQQQ` · `USO` | 1 each |

**This compounds the zero-fill collapse.** `_finite_or_zero` maps an absent peer
slot to `0.0` across all six columns, so for those 22 symbols the block is
*structurally* all-zero — byte-identical to "peers measured, all flat at zero
vol and zero beta". A panel built on it today would carry 22 symbols' worth of
confident zeros. (`BL-20260820-XA-BREADTH-COLLAPSES-NO-PEERS-INTO-ALL-DOWN`.)

**Widening the map is inert for live scoring**, which is what makes it safe to do
first: `cross_asset_live.group_needs_cross_asset` computes the block only for a
predictor that *trained* on `xa_*` columns, and only the one ETH head has. Adding
`BTCUSDT` peers changes no live score until a BTC head trains on them.

## 3. The static correlation matrix is a peer-CHOOSER, not a feature

`comms/research/crypto_correlation_2026-08-18.json` (note: **-18**, not -19)
carries Pearson r on daily log returns over 90d and 365d windows, 5 crypto
symbols, n=90 / n=364, pulled from Bybit v5 klines via trainer-diag #9966.

⚠️ **It cannot ship as an in-trade feature.** It is one constant per pair, so it
has **zero within-trade variance** and cannot inform an exit decision — feeding
it to E2 would produce a feature that is perfectly collinear with the symbol
identity. Its correct roles are (a) choosing which peers go in the map and (b)
stating which pairs are unmeasured.

What *would* be a feature is a **rolling correlation z-score** on the trade's own
bar grid — current pairwise correlation against its own history, which is how
§ 1.5.5's regime-dependence finding (≈0.30 normal → >0.70 in selloffs) becomes
something an exit can read. That needs building; the live-accessor pattern to
copy is `cross_asset_live`.

Measured pairs, 90d (all five crypto names):

| pair | ρ 90d |
|---|--:|
| BTC–ETH | 0.883 |
| ETH–XRP | 0.876 |
| ETH–SOL | 0.852 |
| SOL–XRP | 0.845 |
| BTC–XRP | 0.843 |
| BTC–SOL | 0.836 |
| ADA–XRP | 0.759 |
| ADA–ETH | 0.724 |
| ADA–BTC | 0.703 |
| ADA–SOL | 0.702 |

## 4. The coverage hole this exposes

The artifact's own `coverage_note` says it: **CRYPTO ONLY**. The open book also
holds `GLD`, `IEF`, `IWM`, `MES`, `MGC`, `QLD`, `QQQ`, `SCHA`, `SLV`, `SPY`,
`TLT`, `USO` — **twelve symbols for which every pair is UNMEASURED, which is not
the same as uncorrelated.**

So for those twelve, peers cannot even be *chosen* from measurement. That is the
same equity/ETF corpus gap that already blocks sweeping `mgc`/`qqq`/`tlt`/`spy`,
now with a second consequence: it blocks the exogenous panel for the non-crypto
half of the book as well. One artifact would unblock both.

## 5. Ordered E1 plan, each step with its falsifier

| # | step | falsifier |
|---|---|---|
| 1 | **Fix the zero-fill collapse first** — emit `xa_breadth_present` and a per-slot presence flag | a bar with zero readable peers and a bar with all peers negative must produce DIFFERENT rows; today they do not |
| 2 | **Widen the peer map** to the crypto roster from the measured matrix | a peer chosen without a measured ρ is a guess; record which pairs are chosen on measurement and which are not |
| 3 | **Join the block to the in-trade panel** (`build_intrabar_exit_panel.py`), as-of on the target's bar grid, carrying the coverage column | a feature with no live decision-time accessor is lookahead and does not ship |
| 4 | **Rolling correlation z-score** as a new feature, offline + live accessor together | ships with its accessor or not at all |
| 5 | **Regime label as an in-trade series**, then session, then portfolio state | same rule each time |
| 6 | **Extend the correlation measurement to the 12 non-crypto symbols** | until then, those legs' peer features are absent — declared absent, never zero-filled |

Steps 1–2 are cheap and inert for live scoring. Step 3 is where E2 becomes
runnable — and E2 is the step that has never been run.

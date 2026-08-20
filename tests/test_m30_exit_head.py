"""M30 × M20 — tests for the per-bar in-trade EXIT head toolchain.

Covers the three pure modules (intrabar_features, triple_barrier, meta_label) and
a builder integration through a synthetic backtest adapter — the leakage contract,
the label geometry, and the de-Prado primitives. Offline, deterministic, no
network / DB.
"""
from __future__ import annotations

import pytest

from src.research import meta_label as ml
from src.research.intrabar_features import (
    INTRABAR_FEATURE_NAMES,
    entry_atr_from_prewindow,
    intrabar_features,
)
from src.research.triple_barrier import hold_meta_label, triple_barrier_forward


def _bar(h, low, c, v=100.0, taker=None):
    d = {"high": h, "low": low, "close": c, "open": c, "volume": v}
    if taker is not None:
        d["taker_buy_base"] = taker
    return d


# ---------------------------------------------------------------------------
# intrabar_features
# ---------------------------------------------------------------------------


def test_intrabar_running_excursions_long():
    # entry 100, stop 90 (risk 10). Path rises to 115 then back to 108.
    path = [_bar(105, 99, 104), _bar(115, 103, 110), _bar(112, 106, 108)]
    f = intrabar_features(
        path, entry_price=100, stop_loss=90, side="long",
        entry_atr=5.0, expected_hold_bars=12,
    )
    assert f["running_mfe_r"] == pytest.approx(1.5)   # (115-100)/10
    assert f["running_mae_r"] == pytest.approx(0.1)   # (100-99)/10
    assert f["upnl_r"] == pytest.approx(0.8)          # (108-100)/10
    assert f["mfe_giveback_r"] == pytest.approx(0.7)  # 1.5 - 0.8
    assert f["bars_in_trade"] == 3.0
    assert f["bars_in_trade_frac"] == pytest.approx(0.25)
    assert f["dist_to_stop_atr"] == pytest.approx((108 - 90) / 5.0)


def test_intrabar_short_mirrors():
    # short entry 100, stop 110 (risk 10). Path falls to 92.
    path = [_bar(101, 96, 98), _bar(99, 92, 94)]
    f = intrabar_features(path, entry_price=100, stop_loss=110, side="short",
                          entry_atr=4.0, expected_hold_bars=8)
    assert f["running_mfe_r"] == pytest.approx(0.8)   # (100-92)/10
    assert f["upnl_r"] == pytest.approx(0.6)          # (100-94)/10


def test_intrabar_features_all_present_and_tolerant():
    f = intrabar_features([_bar(101, 99, 100)], entry_price=100, stop_loss=95,
                          side="long", entry_atr=2.0, expected_hold_bars=10)
    for name in INTRABAR_FEATURE_NAMES:
        assert name in f
    # unresolved side → honest None-filled (never a raise)
    bad = intrabar_features([_bar(1, 1, 1)], entry_price=None, stop_loss=None,
                            side="?", entry_atr=1.0, expected_hold_bars=1)
    assert bad["running_mfe_r"] is None


def test_taker_imbalance_present_and_absent():
    path = [_bar(101, 99, 100, v=100, taker=75), _bar(102, 100, 101, v=100, taker=25)]
    f = intrabar_features(path, entry_price=100, stop_loss=95, side="long",
                          entry_atr=2.0, expected_hold_bars=10)
    assert f["taker_imbalance"] == pytest.approx(2 * 0.25 - 1)          # last bar
    assert f["taker_imbalance_intrade"] == pytest.approx(((0.5) + (-0.5)) / 2)
    # no taker volume in the feed → honest None
    f2 = intrabar_features([_bar(101, 99, 100)], entry_price=100, stop_loss=95,
                           side="long", entry_atr=2.0, expected_hold_bars=10)
    assert f2["taker_imbalance"] is None


def test_dmae_dt_grows_with_adverse():
    # MAE grows steadily as price falls → dMAE/dt positive.
    path = [_bar(101, 100, 100), _bar(100, 98, 99), _bar(99, 96, 97),
            _bar(98, 94, 95), _bar(97, 92, 93)]
    f = intrabar_features(path, entry_price=100, stop_loss=80, side="long",
                          entry_atr=3.0, expected_hold_bars=12, dmae_window=2)
    assert f["dmae_dt"] is not None and f["dmae_dt"] > 0


def test_entry_atr_prewindow():
    candles = [_bar(10 + i, 8 + i, 9 + i) for i in range(20)]
    atr = entry_atr_from_prewindow(candles, 15, period=5)
    assert atr is not None and atr > 0
    assert entry_atr_from_prewindow(candles, 0, period=5) is None  # too little history


# ---------------------------------------------------------------------------
# triple_barrier + meta-label
# ---------------------------------------------------------------------------


def test_triple_barrier_tp_touch():
    fwd = [_bar(103, 99, 102), _bar(121, 118, 120)]  # 2R above entry (100,stop90) = 120
    tb = triple_barrier_forward(fwd, entry_price=100, stop_loss=90, side="long",
                                tp_r=2.0, time_stop_bars=12)
    assert tb["touch"] == "tp" and tb["forward_r"] == pytest.approx(2.0)
    assert tb["touch_offset"] == 2


def test_triple_barrier_sl_touch():
    fwd = [_bar(101, 89, 92)]  # low 89 <= stop 90
    tb = triple_barrier_forward(fwd, entry_price=100, stop_loss=90, side="long",
                                tp_r=2.0, time_stop_bars=12)
    assert tb["touch"] == "sl" and tb["forward_r"] == pytest.approx(-1.0)


def test_triple_barrier_time_stop_marks_to_market():
    fwd = [_bar(105, 99, 104), _bar(106, 101, 105)]  # neither barrier; close 105 = +0.5R
    tb = triple_barrier_forward(fwd, entry_price=100, stop_loss=90, side="long",
                                tp_r=2.0, time_stop_bars=2)
    assert tb["touch"] == "time" and tb["forward_r"] == pytest.approx(0.5)


def test_triple_barrier_tolerant():
    assert triple_barrier_forward([], entry_price=100, stop_loss=90, side="long")["forward_r"] is None
    assert triple_barrier_forward([_bar(1, 1, 1)], entry_price=100, stop_loss=100,
                                  side="long")["forward_r"] is None  # degenerate risk


def test_hold_meta_label():
    # holding to +2R vs currently at +0.5R → hold (advantage 1.5)
    m = hold_meta_label(2.0, 0.5)
    assert m["label_hold"] == 1 and m["advantage_r"] == pytest.approx(1.5) and m["size"] == pytest.approx(1.5)
    # holding gives back to -0.2R vs currently at +0.8R → exit now
    m2 = hold_meta_label(-0.2, 0.8)
    assert m2["label_hold"] == 0
    # cost buffer flips a razor-thin advantage
    assert hold_meta_label(0.55, 0.5, cost_r=0.1)["label_hold"] == 0
    assert hold_meta_label(None, 0.5)["label_hold"] is None


# ---------------------------------------------------------------------------
# meta_label — uniqueness, bootstrap, DSR, PBO
# ---------------------------------------------------------------------------


def test_average_uniqueness_known():
    # two disjoint spans → uniqueness 1.0 each; two identical overlapping → 0.5 each
    assert ml.average_uniqueness([(0, 2), (5, 7)]) == [1.0, 1.0]
    u = ml.average_uniqueness([(0, 3), (0, 3)])
    assert u == pytest.approx([0.5, 0.5])
    # partial overlap
    u2 = ml.average_uniqueness([(0, 3), (2, 5)])
    assert all(0.5 < x <= 1.0 for x in u2)


def test_sequential_bootstrap_valid_indices():
    spans = [(0, 2), (1, 3), (10, 12), (11, 13)]
    draws = ml.sequential_bootstrap(spans, 8, seed=1)
    assert len(draws) == 8 and all(0 <= i < len(spans) for i in draws)


def test_norm_cdf_ppf_roundtrip():
    for p in (0.05, 0.25, 0.5, 0.9, 0.975):
        assert ml._norm_cdf(ml._norm_ppf(p)) == pytest.approx(p, abs=1e-4)


def test_psr_dsr_monotone():
    good = [0.3, 0.4, 0.2, 0.5, 0.35, 0.45, 0.25, 0.4] * 5   # positive mean, low vol
    noise = [0.3, -0.4, 0.2, -0.5, 0.1, -0.2, 0.4, -0.3] * 5  # ~zero mean
    assert ml.probabilistic_sharpe_ratio(good) > ml.probabilistic_sharpe_ratio(noise)
    dsr = ml.deflated_sharpe_ratio(good, n_trials=10, variance_of_trial_sr=1.0)
    assert 0.0 <= dsr <= 1.0


def test_pbo_cscv_overfit_vs_robust():
    # config 0 is robust (consistent), config 1 is IS-lucky / OOS-bad by half.
    import random
    rng = random.Random(0)
    robust = [0.2 + rng.uniform(-0.01, 0.01) for _ in range(64)]
    lucky = [(1.0 if i < 32 else -1.0) for i in range(64)]
    matrix = list(zip(robust, lucky))
    res = ml.pbo_cscv(matrix, n_blocks=8)
    assert res["computed"] and 0.0 <= res["pbo"] <= 1.0


# ---------------------------------------------------------------------------
# builder integration (synthetic adapter)
# ---------------------------------------------------------------------------


def test_build_intrabar_exit_panel_synthetic():
    pd = pytest.importorskip("pandas")
    import scripts.research.build_backtest_panel as BP
    import scripts.research.build_intrabar_exit_panel as BX

    # A rising feed; one long trade entered at bar 5, "actual" exit at bar 12.
    rows = []
    for i in range(60):
        px = 100 + i * 0.5
        rows.append({"timestamp": f"2026-01-01T00:{i:02d}:00Z", "open": px,
                     "high": px + 1, "low": px - 1, "close": px,
                     "volume": 100.0, "taker_buy_base": 60.0})
    df = pd.DataFrame(rows)
    st = BP.SimTrade(strategy="synth", symbol="BTCUSDT", side="long",
                     entry_price=102.5, stop_loss=99.5, exit_price=106.0,
                     r_multiple=1.1, entry_index=5, exit_index=12,
                     exit_time="2026-01-01T00:12:00Z", meta={"regime": "trend"})

    def _synth_adapter(**_kw):
        return df, [st], {"harness": "synthtest"}

    BX.ADAPTERS["synthtest"] = _synth_adapter
    try:
        panel, manifest = BX.build_intrabar_exit_panel(
            harness="synthtest", adapter_opts={}, time_stop_bars=6, tp_r=2.0,
        )
    finally:
        BX.ADAPTERS.pop("synthtest", None)

    assert manifest["row_count"] > 0
    assert manifest["trades_used"] == 1
    # dense: every listed dense feature is present + non-null on every row
    for r in panel:
        for c in manifest["dense_feature_cols"]:
            assert r.get(c) is not None
        assert r["label_hold"] in (0, 1)
        assert r["label_t0"] <= r["label_t1"]
        assert "trade_realized_r" in r
        # leakage: the decision bar's label window is strictly future
        # entry_index=5, decision bar t = 5 + feat_bars_in_trade, label_t0 = t+1
        assert r["label_t0"] == 5 + int(r["feat_bars_in_trade"]) + 1
    # taker imbalance survived into the dense set (feed carried taker volume)
    assert "feat_taker_imbalance" in manifest["dense_feature_cols"]
    assert manifest["leakage_contract"]


from ml.datasets.cross_asset_features import CROSS_ASSET_FEATURE_COLUMNS  # noqa: E402


class TestCrossAssetJoin:
    """E1 step 3 — the exogenous half of the in-trade panel.

    Targets ETHUSDT deliberately: it is the one symbol configured in
    `config/cross_asset.yaml` on every branch, so these do not depend on the
    peer-map widening landing first.
    """

    @staticmethod
    def _frame(n=60, start=100.0, step=1.0):
        return [
            {"timestamp": f"2026-01-01T{h:02d}:00:00+00:00", "close": start + step * h}
            for h in range(n)
        ]

    def test_no_peers_configured_is_distinct_from_no_peer_series(self):
        """The two absences are different facts and must not collapse.

        `no_peers_configured` = we never had peers for this symbol (true of 18 of
        23 traded symbols). `no_peer_series` = peers ARE declared and we were
        handed no data for any of them. Reporting one as the other would hide a
        broken feed behind a legitimate gap.
        """
        from scripts.research.build_intrabar_exit_panel import cross_asset_index

        candles = self._frame()
        idx_a, meta_a = cross_asset_index(candles, "NOSUCHSYMBOL", {"ETHUSDT": []})
        assert meta_a["state"] == "no_peers_configured"
        assert idx_a == {}

        idx_b, meta_b = cross_asset_index(candles, "ETHUSDT", None)
        assert meta_b["state"] == "no_peer_series"
        assert meta_b["peers_configured"], "precondition: ETHUSDT must have peers"
        assert idx_b == {}

        assert meta_a["state"] != meta_b["state"]

    def test_join_populates_and_the_timestamp_forms_match(self):
        """THE POSITIVE CONTROL for the whole feature.

        `_aligned_return_series` matches peer bars to target bars by EXACT string
        equality, so a normalisation mismatch produces an empty join that is
        indistinguishable from "this symbol has no peers". Assert the join
        actually lands, and that the index keys are the same strings the build
        loop looks up with `_bar_ts`.
        """
        from scripts.research.build_intrabar_exit_panel import _bar_ts, cross_asset_index

        candles = self._frame()
        peer = [{"ts": _bar_ts(c), "close": 50.0 + 0.5 * i} for i, c in enumerate(candles)]
        idx, meta = cross_asset_index(candles, "ETHUSDT", {"BTCUSDT": peer})

        assert meta["state"] == "joined"
        assert meta["peers_joined"] == ["BTCUSDT"]
        assert meta["bars_indexed"] == len(candles)
        assert meta["bar_coverage"] == 1.0

        # the exact lookup the build loop performs
        key = _bar_ts(candles[30])
        assert key in idx, "ts normalisation diverged between index and lookup"
        row = idx[key]
        assert row["xa_peer1_present"] == 1.0
        assert row["xa_breadth_present"] == 1.0
        # slot 2 has no series -> declared absent, not flat
        assert row["xa_peer2_present"] == 0.0

    def test_a_misaligned_peer_reads_as_absent_not_as_flat(self):
        """Negative control with teeth.

        A peer whose bars are on a different grid must come back UNMEASURED
        (`present == 0`), not as a peer that returned zero. Before the coverage
        columns landed these two were byte-identical rows.
        """
        from scripts.research.build_intrabar_exit_panel import _bar_ts, cross_asset_index

        candles = self._frame()
        # same shape, timestamps shifted to a grid that shares no bar
        peer = [
            {"ts": f"2027-06-0{(i % 9) + 1}T00:00:00+00:00", "close": 50.0 + i}
            for i in range(len(candles))
        ]
        idx, meta = cross_asset_index(candles, "ETHUSDT", {"BTCUSDT": peer})
        assert meta["state"] == "joined", "it still ran; the question is what it says"
        row = idx[_bar_ts(candles[30])]
        assert row["xa_peer1_present"] == 0.0
        assert row["xa_breadth_present"] == 0.0

    def test_the_index_is_keyed_past_only_and_covers_every_bar(self):
        """Sanity on the leakage contract's precondition.

        One row per target bar, keyed at that bar — so the per-bar merge cannot
        pick up a row from a later bar.
        """
        from scripts.research.build_intrabar_exit_panel import _bar_ts, cross_asset_index

        candles = self._frame(n=25)
        peer = [{"ts": _bar_ts(c), "close": 10.0 + i} for i, c in enumerate(candles)]
        idx, _ = cross_asset_index(candles, "ETHUSDT", {"BTCUSDT": peer})
        assert sorted(idx) == sorted(_bar_ts(c) for c in candles)

    def test_an_unsupplied_slot_emits_no_columns_at_all(self):
        """An unjoined peer slot must not reach the panel as constant zeros.

        The feature block zero-fills an absent slot by design — correct for a
        head that trained on those columns, wrong for a research panel, because
        the builder's dense filter drops all-NULL columns and not all-CONSTANT
        ones. Measured before this scoping: a 142-row panel with one of two peers
        supplied carried SIX perfectly collinear zero columns for slot 2. The
        manifest's peers_configured-vs-peers_joined already says more than those
        columns could.
        """
        from scripts.research.build_intrabar_exit_panel import _bar_ts, cross_asset_index

        candles = self._frame()
        peer = [{"ts": _bar_ts(c), "close": 50.0 + 0.5 * i} for i, c in enumerate(candles)]
        _, meta = cross_asset_index(candles, "ETHUSDT", {"BTCUSDT": peer})

        # slot 1 supplied, slot 2 not
        assert meta["joined_slots"] == [1]
        assert len(meta["peers_configured"]) == 2

        # the emit filter the build loop applies, derived the same way
        slots = set(meta["joined_slots"])
        emitted = [
            c for c in CROSS_ASSET_FEATURE_COLUMNS
            if not c.startswith("xa_peer")
            or any(c.startswith(f"xa_peer{n}_") for n in slots)
        ]
        assert not [c for c in emitted if c.startswith("xa_peer2_")], (
            "slot 2 was never supplied; emitting its columns would put constant "
            "zeros in front of E2"
        )
        assert "xa_peer1_ret" in emitted and "xa_breadth_up" in emitted
        assert "xa_breadth_present" in emitted, (
            "the book-level coverage column must survive the slot filter — it is "
            "what makes a thin join readable"
        )

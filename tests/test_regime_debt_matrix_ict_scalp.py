"""ict_scalp harness wiring in the regime debt matrix (checklist row E28).

This is E25's fvg_range wiring (`test_regime_debt_matrix_fvg_range.py`) one
family along and at eight times the size — `classify()` had four branches and
none matched an ict_scalp config, so `backtest_ict_scalp.py` existed with
nothing routing to it and all eight legs carried `coverage_state: "no_harness"`.

⚠️ **It is NOT the same shape, and the differences are what this file protects.**

1. **The harness reads `config/strategies.yaml` ITSELF**, where the other four
   are handed every parameter on the command line. It declares no flag for any
   of the fourteen keys `src.units.strategies.ict_scalp._DEFAULTS` consumes, so
   the "`--ignore-yaml` and forward everything" option does not exist and the
   YAML read is load-bearing. `_SCALP_PLAIN` records which keys it carries, and
   `test_scalp_plain_covers_every_unit_default` fails if the unit gains one the
   set does not.

2. **That read was HARDCODED to the `ict_scalp_5m` block for every leg** until
   this row (MI-321, `docs/research/ict-scalp-seam-2026-09-18.md` § 3, filed and
   deliberately not fixed there). Routing eight legs at the old signature would
   have measured one leg's config eight times under eight names and written
   eight `measured` records — worse than the `no_harness` they replace, because
   a wrong number reads as evidence and a missing one does not. So
   `--strategy-name` must select the block, and an unknown name must RAISE
   rather than fall back.

3. **`off_cells` is `not_expressible` and `vol_spec` is an `asymmetric_gate`**
   (MI-321 § 3, by joining two independent measurements rather than one
   predicate). `ict_scalp_xrp_5m` carries both, so it is the one leg that must
   grade `approximate` and name them. Forwarding `--vol-spec-json` to make the
   row look faithful would LABEL a run with a spec that reaches no skip — the
   trap, not the fix.

4. **The break-even ratchet is live behaviour that needs a flag.** The live
   monitor arms it from `be_offset_bps` (`ict_scalp.py::monitor` ->
   `_base.monitor_breakeven_sl`); the harness models it only under
   `--sim-breakeven`, a store_true that cannot live in a config-key -> value
   map. Omitting it would measure all eight legs with no ratchet at all.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "research"))

rdm = pytest.importorskip("regime_debt_matrix")

HARNESS = os.path.join(REPO, "scripts", "backtest_ict_scalp.py")
STRATEGIES_YAML = os.path.join(REPO, "config", "strategies.yaml")

# The live `ict_scalp_xrp_5m` config as of 2026-09-22, copied verbatim so a
# config change shows up as a diff here as well as in the anchored tests below.
# XRP is the copied leg on purpose: it is the ONLY one of the eight that carries
# a key this harness cannot express.
LIVE_XRP_5M = {
    "model": None,
    "signal_prefixes": ["ict_scalp"],
    "enabled": True,
    "execution": "live",
    "timeframe": "5m",
    "symbols": ["XRPUSDT"],
    "sweep_lookback_bars": 12,
    "swing_lookback_bars": 20,
    "atr_period": 14,
    "sweep_buffer_bps": 5.0,
    "displacement_atr_mult": 1.3,
    "min_displacement_body_to_range": 0.55,
    "min_fvg_size_bps": 2.0,
    "mitigation_mode": "wick_rejection",
    "htf_trend_filter_enabled": True,
    "htf_filter_timeframe": "1h",
    "htf_filter_ema_period": 20,
    "atr_sl_buffer_mult": 0.2,
    "tp_at_r": 1.5,
    "be_offset_bps": 15,
    "session_filter_enabled": False,
    "session_start_hour": 7,
    "session_end_hour": 17,
    "shadow_model_ids": [],
    "off_cells": [["trending", "volatile"], ["chop", "volatile"]],
    "vol_spec": {
        "model_id": "m27-derived-xrpusdt-5m-frozen",
        "vol_bucket_labels": ["low", "mid", "high"],
        "vol_bucket_edges": [0.0010762314415101184, 0.0016488076787838295],
        "vol_window_n": 20,
    },
}


def _live_legs() -> dict:
    with open(STRATEGIES_YAML, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["strategies"]


def _cmd(cfg, name="ict_scalp_xrp_5m", resample="5m"):
    return rdm.build_harness_cmd(name, cfg, rdm.classify(cfg), "/tmp/d.csv",
                                 resample, "/tmp/e.jsonl", "/tmp/j.json")


def _harness_flags() -> set:
    """Every flag string `backtest_ict_scalp.py`'s parser declares, read from the
    AST. Field beats comment: this is what argparse will accept, not what the
    help text or a docstring says it accepts."""
    tree = ast.parse(open(HARNESS, encoding="utf-8").read())
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    flags.add(arg.value)
    return flags


# --------------------------------------------------------------------------
# 1. Routing — the branch exists, and it captures exactly the intended legs
# --------------------------------------------------------------------------

def test_classify_routes_the_scalp_family():
    assert rdm.classify(LIVE_XRP_5M) == "ict_scalp"


def test_classify_keys_are_carried_by_the_scalp_family_and_nothing_else():
    """The conjunction the branch keys on must not be able to capture a leg an
    earlier branch should have taken. Measured over the whole live roster rather
    than asserted, because that is the property that decays when a leg is added.
    """
    legs = _live_legs()
    matched = {n for n, c in legs.items()
               if isinstance(c, dict)
               and "sweep_lookback_bars" in c and "mitigation_mode" in c}
    assert matched == {n for n in legs if n.startswith("ict_scalp_")}
    assert len(matched) == 8
    # and none of them carries a key an earlier branch keys on
    earlier = {"donchian", "trend_lookback", "pullback_frac", "kc_mult",
               "bb_period", "range_lookback", "third_frac"}
    for n in matched:
        assert not (earlier & set(legs[n])), n


def test_scalp_branch_is_checked_last():
    """A leg carrying BOTH an earlier branch's shape and this one keeps the
    earlier harness — no silent re-routing of an already-measured leg."""
    hybrid = dict(LIVE_XRP_5M, donchian=20)
    assert rdm.classify(hybrid) == "trend"
    hybrid = dict(LIVE_XRP_5M, range_lookback=48, third_frac=0.34)
    assert rdm.classify(hybrid) == "fvg_range"


def test_every_enabled_leg_now_routes_except_turtle_soup():
    """The population, not one leg. E25 left 9 of 52 enabled legs unrouted and
    8 of those 9 were this family; closing it must leave exactly one."""
    legs = {n: c for n, c in _live_legs().items()
            if isinstance(c, dict) and c.get("enabled")}
    unrouted = sorted(n for n, c in legs.items() if rdm.classify(c) is None)
    assert len(legs) == 52
    assert unrouted == ["turtle_soup"]


# --------------------------------------------------------------------------
# 2. Fidelity — honest about what the harness cannot express
# --------------------------------------------------------------------------

def test_scalp_plain_covers_every_unit_default():
    """`_SCALP_PLAIN` claims the harness's YAML read carries these keys to the
    unit. Assert it against the unit rather than transcribing it: if
    `ict_scalp._DEFAULTS` gains a parameter, this fails instead of the new
    parameter silently grading as an omitted lever on all eight legs."""
    ict_scalp = pytest.importorskip("src.units.strategies.ict_scalp")
    assert set(ict_scalp._DEFAULTS) <= rdm._SCALP_PLAIN


def test_xrp_5m_is_approximate_and_names_both_unexpressible_keys():
    argv, faithful, omitted = _cmd(LIVE_XRP_5M)
    assert faithful is False
    assert omitted == ["off_cells", "vol_spec"]


def test_vol_spec_is_never_forwarded():
    """`--vol-spec-json` exists and reaches no skip — it guards the regime
    STAMPING routine only. Passing it would label a run with a spec while it
    behaves as if unset, which is a worse state than naming the omission."""
    argv, _, _ = _cmd(LIVE_XRP_5M)
    assert "--vol-spec-json" not in argv
    assert "--stamp-regime" not in argv


def test_the_other_seven_legs_are_faithful():
    legs = _live_legs()
    grades = {}
    for n, c in legs.items():
        if not n.startswith("ict_scalp_"):
            continue
        _, faithful, omitted = _cmd(c, name=n, resample=c["timeframe"])
        grades[n] = (faithful, tuple(omitted))
    assert grades["ict_scalp_xrp_5m"] == (False, ("off_cells", "vol_spec"))
    assert sorted(n for n, (f, _) in grades.items() if f) == sorted(
        n for n in grades if n != "ict_scalp_xrp_5m")


def test_every_mapped_flag_is_one_the_harness_actually_declares():
    """The `_FVG_LEVER_FLAG` lesson: read the parser, not the docstring. A flag
    the harness does not declare aborts the subprocess with "unrecognized
    arguments", which `run_one` reports as `harness failed` — a missing flag
    misread as a broken harness."""
    declared = _harness_flags()
    for key, flag in rdm._SCALP_LEVER_FLAG.items():
        assert flag in declared, f"{key} -> {flag} is not a declared flag"
    assert rdm._SCALP_BE_FLAG in declared


def test_no_unsupported_shared_flag_leaks_into_the_argv():
    """This harness accepts neither `--resample` nor the `common` list's
    `--atr-period` / `--atr-stop-mult` / `--trail-mult`. The fvg_range branch
    had to stop reusing `common` for the same reason."""
    declared = _harness_flags()
    for cfg in (LIVE_XRP_5M, dict(LIVE_XRP_5M, adx_min=25.0)):
        argv, _, _ = _cmd(cfg)
        for tok in argv:
            if tok.startswith("--"):
                assert tok in declared, f"{tok} is not accepted by the harness"
    assert "--resample" not in declared     # the negative control for the above


# --------------------------------------------------------------------------
# 3. The flags that carry live behaviour
# --------------------------------------------------------------------------

def test_the_leg_name_is_forwarded_so_the_harness_reads_the_right_block():
    argv, _, _ = _cmd(LIVE_XRP_5M, name="ict_scalp_xrp_5m")
    assert argv[argv.index("--strategy-name") + 1] == "ict_scalp_xrp_5m"


def test_breakeven_is_armed_from_be_offset_bps():
    argv, _, _ = _cmd(LIVE_XRP_5M)
    assert rdm._SCALP_BE_FLAG in argv
    # and NOT armed when the leg does not declare the offset — the negative
    # control, without which this test passes on a hardcoded flag.
    argv, _, _ = _cmd({k: v for k, v in LIVE_XRP_5M.items()
                       if k != "be_offset_bps"})
    assert rdm._SCALP_BE_FLAG not in argv


def test_htf_geometry_is_forwarded_rather_than_left_to_a_coincidence():
    """The harness defaults (`1h`/20) happen to equal today's config. Relying on
    that is how a lever stops being applied the day a leg moves, so assert the
    forwarding on a config whose values DIFFER from the defaults."""
    cfg = dict(LIVE_XRP_5M, htf_filter_timeframe="4h", htf_filter_ema_period=50)
    argv, faithful, _ = _cmd(cfg)
    assert argv[argv.index("--htf-rule") + 1] == "4h"
    assert argv[argv.index("--htf-ema-period") + 1] == "50"


def test_eth_15m_stale_exit_levers_reach_the_harness():
    """`stale_exit_bars` is a `run_backtest` KEYWORD, not a cfg key, so the YAML
    read cannot carry it — it is faithful only because it is forwarded."""
    cfg = _live_legs()["ict_scalp_eth_15m"]
    argv, faithful, omitted = _cmd(cfg, name="ict_scalp_eth_15m", resample="15m")
    assert argv[argv.index("--stale-exit-bars") + 1] == "12"
    assert "--stale-exit-below-r" in argv
    assert faithful is True and omitted == []


# --------------------------------------------------------------------------
# 4. The harness's own config read — the precondition of all of the above
# --------------------------------------------------------------------------

def test_harness_reads_the_block_the_leg_name_selects():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    harness = pytest.importorskip("backtest_ict_scalp")
    eth = harness._load_yaml_params("ict_scalp_eth_15m")
    assert eth["timeframe"] == "15m"
    assert eth["stale_exit_bars"] == 12
    # the default is still the historical literal, so a caller that passes
    # nothing — mi321_ict_scalp_seam.py does — is byte-identical
    assert harness._load_yaml_params()["timeframe"] == "5m"
    assert harness.DEFAULT_CFG_KEY == "ict_scalp_5m"


def test_an_unknown_leg_name_raises_rather_than_serving_the_default_block():
    """The silent fallback IS the defect: "the leg is not in the config" and
    "the leg's config is what ict_scalp_5m declares" are opposite statements."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    harness = pytest.importorskip("backtest_ict_scalp")
    with pytest.raises(KeyError):
        harness._load_yaml_params("ict_scalp_no_such_leg")


def test_emitted_rows_carry_the_corpus_field_names():
    """`exit_reason` and a top-level `mfe_r` are what the other four harnesses
    emit and what the committed `runs/*/*__trades.jsonl` corpus is read with.
    This harness spells them `outcome` and `meta.mfe_r`; the aliases are what
    stop a corpus-wide consumer raising KeyError on exactly these eight legs."""
    src = open(HARNESS, encoding="utf-8").read()
    assert '"exit_reason": t.outcome,' in src
    assert '"mfe_r": t.meta.get("mfe_r"),' in src
    assert '"outcome": t.outcome,' in src      # the original stays


# --------------------------------------------------------------------------
# 5. The feed-empty diagnosis — a fourth state this family made reachable
# --------------------------------------------------------------------------
#
# `ict_scalp_mgc_15m` is the one non-USDT leg of the eight, so routing the
# family is what first asked Yahoo for 15m bars over 365 days. It came back
# empty and `_yahoo_empty_reason` blamed the TICKER — "delisting, a short
# listing history, or a wrong symbol map" — because its SPY control probes
# `period="5d", interval="1d"` and therefore varies along NEITHER axis under
# test. Measured 2026-09-22: `GC=F` at 15m returns 4,338 rows over 5d and 0
# over 365d, and Yahoo's own error names the cause. The symbol map is correct.
#
# A control that cannot vary along the axis under test can only return the
# answer it already had, and a wrong named cause is worse than an unexplained
# failure because it closes off the investigation.


class _StubDownload:
    """Minimal `yf` stand-in: returns rows for the (ticker, interval) pairs it
    is told are served, and an empty frame otherwise. Lets both branches be
    exercised without a network call, which is what the original control could
    never be tested for."""

    def __init__(self, served):
        self.served = served
        self.calls = []

    def download(self, ticker, period=None, interval=None, **kw):
        self.calls.append((ticker, period, interval))
        return ["row"] * 3 if (ticker, interval) in self.served else []


def test_a_too_wide_intraday_window_is_not_reported_as_a_ticker_problem():
    # SPY/1d serves (the venue is up) and GC=F/15m serves over a short window —
    # so the only thing wrong is the span the caller asked for.
    yf = _StubDownload({("SPY", "1d"), ("GC=F", "15m")})
    reason = rdm._yahoo_empty_reason("GC=F", yf, "15m")
    assert "window_exceeds_intraday_limit" in reason
    assert "NOT a symbol-map change" in reason
    assert "delisting" not in reason
    # it must have re-asked at the REQUESTED interval, not at the control's
    assert ("GC=F", rdm._YF_SELF_PROBE_PERIOD, "15m") in yf.calls


def test_a_genuinely_absent_intraday_ticker_still_reads_as_a_ticker_problem():
    """The negative control, without which the test above passes on a constant.
    Here the short-window self-probe is ALSO empty, so the ticker really is the
    problem and the pre-existing answer must survive."""
    yf = _StubDownload({("SPY", "1d")})
    reason = rdm._yahoo_empty_reason("NOSUCH=F", yf, "15m")
    assert "window_exceeds_intraday_limit" not in reason
    assert "venue_state=serving" in reason
    assert "delisting" in reason


def test_a_refusing_venue_still_outranks_the_interval_question():
    """When the control is empty too, nothing about this ticker has been
    established — and the self-probe must not be allowed to overwrite that with
    an interval verdict."""
    yf = _StubDownload(set())
    reason = rdm._yahoo_empty_reason("GC=F", yf, "15m")
    assert "venue_state=refusing_us" in reason
    assert "window_exceeds_intraday_limit" not in reason


def test_a_daily_request_never_runs_the_intraday_self_probe():
    yf = _StubDownload({("SPY", "1d")})
    rdm._yahoo_empty_reason("NOSUCH", yf, "1d")
    assert [c for c in yf.calls if c[0] == "NOSUCH"] == []


def test_the_requested_interval_reaches_the_diagnosis():
    """`_fetch_csv` must pass the feed's own interval through, or the whole
    branch above is unreachable in production — the inert-code failure mode."""
    src = open(os.path.join(REPO, "scripts", "research",
                            "regime_debt_matrix.py"), encoding="utf-8").read()
    assert '_yahoo_empty_reason(feed["ticker"], yf, feed["interval"])' in src

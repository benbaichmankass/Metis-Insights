"""The runner must write the filename the SWEEP actually reads.

`BL-20260826-E35-RUNNER-WRITES-A-FILENAME-THE-SWEEP-NEVER-READS`.

`e35-bracket-sweep.yml` derived its output stem inline as `{symbol}_{tf}`,
while `m20_fleet_exit_sweep.resolve_data` applies `PROXY_DATA`
(`MES->ES_F`, `MGC`/`XAUUSD->GC_F`, `MHG->HG_F`) unconditionally and with **no
native fallback**. Two definitions of where a leg's candles live, and they
disagreed for every `PROXY_DATA` symbol.

⚠️ **THE FAILURE WAS GREEN.** Measured 2026-08-26 by invoking the sweep exactly
as the workflow does, with `MES_1d.csv` on disk::

    plan: 0 legs runnable, 1 skipped
      SKIP mes_trend_long_1d: data_missing:MES
    EXIT CODE = 0     report.json -> legs= 0

The leg paid the fetch, the job passed, and the artifact held a report with an
empty ``legs`` list — which the rollup then counted as a leg that had returned
a report.

`test_every_scheduled_leg_resolves_after_its_own_fetch` is the assertion that
would have caught it, and
`test_the_naive_stem_is_what_used_to_break` is its POSITIVE CONTROL: it pins
that the old derivation genuinely fails, so the first test cannot quietly
become vacuous if `PROXY_DATA` is ever emptied.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "e35-bracket-sweep.yml"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import e35_shard_plan as plan  # noqa: E402
import m20_fleet_exit_sweep as fleet  # noqa: E402

_OHLCV = "timestamp,open,high,low,close,volume\n2024-01-01,1,2,0.5,1.5,10\n"


def _scheduled() -> list[dict]:
    runnable, _ = plan.sweep.plan_legs(
        REPO / "data", None, fleet.LIVE_TP_CAP_PCT, ignore_missing_data=True)
    include, _refused = plan.build_matrix(runnable)
    assert include, "the planner scheduled nothing — this test would be vacuous"
    return include


def _written(stems, tmp: Path) -> Path:
    d = tmp / "data"
    d.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (d / f"{stem}.csv").write_text(_OHLCV)
    return d


def test_every_scheduled_leg_resolves_after_its_own_fetch(tmp_path):
    """Write what the runner writes; the sweep must then FIND every leg."""
    include = _scheduled()
    data = _written([j["data_basename"] for j in include], tmp_path)

    missing = [j["leg"] for j in include
               if fleet.resolve_data(j["symbol"], j["tf"], data)[0] is None]
    assert not missing, (
        f"{len(missing)} of {len(include)} scheduled legs would fetch candles "
        f"and then read data_missing: {missing}")


def test_the_naive_stem_is_what_used_to_break(tmp_path):
    """POSITIVE CONTROL — the old `{symbol}_{tf}` derivation really does fail.

    Without this, `test_every_scheduled_leg_resolves_after_its_own_fetch` would
    still pass if `PROXY_DATA` were emptied, and would then be asserting
    nothing. This pins that there IS a difference to get right.
    """
    include = _scheduled()
    data = _written([f"{j['symbol']}_{j['tf']}" for j in include], tmp_path)

    missing = [j["leg"] for j in include
               if fleet.resolve_data(j["symbol"], j["tf"], data)[0] is None]
    assert missing, (
        "the naive stem resolved every leg, so the fix under test is a no-op "
        "and the sibling assertion has no teeth")
    # Exactly the PROXY_DATA legs, named rather than merely counted.
    assert {j["leg"] for j in include if j["symbol"] in fleet.PROXY_DATA} == set(missing)


def test_data_basename_agrees_with_resolve_data_on_casing(tmp_path):
    """The stem helper must not be MORE PERMISSIVE than the map it mirrors.

    A first draft normalised the lookup key (`PROXY_DATA.get(symbol.upper())`),
    which reads as strictly more helpful and is the divergence again in
    miniature: `resolve_data` does `PROXY_DATA.get(symbol)` with no `.upper()`,
    so for a lowercase `mes` the runner would write `ES_F_1d.csv` while the
    sweep looked for `mes_1d` — the green-job-measures-nothing failure this
    module exists to prevent, reintroduced by a "safe" normalisation.

    Inert in production (all 24 configured symbols are uppercase), which is
    exactly why it needs a test rather than a comment.
    """
    for sym in ("mes", "MES", "mgc", "MGC", "spy", "SPY"):
        stem = plan.data_basename(sym, "1d")
        data = _written([stem], tmp_path / sym)
        resolved, _proxy, _rs = fleet.resolve_data(sym, "1d", data)
        assert resolved is not None, (
            f"data_basename({sym!r}) produced {stem!r}, which resolve_data "
            f"does not find — the two have drifted apart again")


def test_yfinance_interval_set_matches_the_fetchers_own_map():
    """Anti-drift, mirroring the Dukascopy assertion this file's sibling makes.

    A planner that schedules a leg the fetcher then REFUSES is the two-copies
    drift `e35_shard_plan` already avoids for leg scope; asserting the set
    rather than trusting the comment is what keeps it that way.
    """
    import fetch_backtest_candles as fetcher

    assert plan._YF_SERVABLE_INTERVALS == set(fetcher._BYBIT_TO_YF_TIMEFRAME)


def test_proxy_symbols_route_to_yfinance_and_others_do_not():
    for sym in sorted(fleet.PROXY_DATA):
        assert plan.resolve_feed_source(sym, "D") == "yfinance", sym
    assert plan.resolve_feed_source("GLD", "D") == "dukascopy"
    assert plan.resolve_feed_source("BTCUSDT", "60") == "binance_vision"


def test_leveraged_etfs_are_never_served_by_a_proxy():
    """`QLD`/`TQQQ` must never be served from a QQQ series — and ARE served
    from their own.

    ⚠️ **THIS TEST PREVIOUSLY ASSERTED THAT BOTH LEGS RAISE, AND THE ASSERTION
    WAS A NON-SEQUITUR ON A TRUE PREMISE** (changed MI-195, 2026-09-08). Its
    reasoning was *"a daily leverage reset means the path is not N x the
    underlying, so no proxy is honest"* — correct, and still asserted below —
    followed by *"therefore refused"*, which does not follow: needing no proxy
    is precisely why they are absent from `PROXY_DATA`, not a reason to refuse
    a DIRECT series. `yf_symbols` has mapped `QLD -> QLD` and `TQQQ -> TQQQ` as
    pass-through the whole time.

    The original intent — no leveraged ETF is ever backed by its underlying's
    series — is what this now pins, and it pins MORE than the old assertion
    did: the proxy map, the untouched Dukascopy adjudication, the ticker being
    the symbol itself, and the states staying apart.
    """
    for sym in ("QLD", "TQQQ"):
        # 1. THE PREMISE, UNCHANGED: no proxy entry exists, so nothing can
        #    silently serve these from ES/GC/HG or from QQQ.
        assert sym not in fleet.PROXY_DATA
        # 2. THE ADJUDICATION IS UNTOUCHED. The rung ADDS a feed; it does not
        #    overturn Dukascopy's refusal, which remains correct about proxying.
        assert plan._dk_state_for(sym) == "refused"
        # 3. THE TICKER IS THE SYMBOL ITSELF — a direct series, never a proxy.
        #    This is the gate the rung is keyed on, so a proxy row could not
        #    ride it even if the PROXY_DATA branch above were removed.
        assert plan.yf_serves_directly(sym) is True
        # 4. ...and it therefore routes.
        assert plan.resolve_feed_source(sym, "D") == "yfinance"


def test_a_proxy_row_is_not_a_direct_series():
    """The rung's gate must reject PROXY rows, or it re-opens the trap
    `resolve_feed_source` refuses for: a file whose NAME asserts a provenance
    its CONTENT does not have."""
    for sym in ("MES", "MGC", "MHG", "XAUUSD"):
        assert sym in fleet.PROXY_DATA
        assert plan.yf_serves_directly(sym) is False


def test_a_symbol_no_feed_can_serve_is_still_refused():
    """`unknown` stays refused, so `mapped` / `refused` / `unknown` stay three
    states. NVDA is unadjudicated by Dukascopy AND absent from the yfinance
    map — the rung must not schedule it on a guess."""
    assert plan._dk_state_for("NVDA") == "unknown"
    assert plan.yf_serves_directly("NVDA") is False
    with pytest.raises(plan.NoFeedSource):
        plan.resolve_feed_source("NVDA", "D")


def test_the_rung_refuses_an_interval_it_cannot_serve():
    """Refused, never coerced — the same discipline the other two branches
    apply. yfinance has no 4h mapping, so a 4h leg must not be scheduled."""
    with pytest.raises(plan.NoFeedSource):
        plan.resolve_feed_source("QLD", "240")


def test_the_rung_only_ever_adds_legs():
    """THE LOAD-BEARING ONE. Every symbol Dukascopy already maps must keep the
    feed it had, or the committed corpus stops being comparable and recorded
    verdicts are silently re-based onto a new source."""
    for sym in ("SPY", "GLD", "QQQ", "SPLG", "GDX", "IWM",
                "SLV", "TLT", "IEF", "IAUM", "SCHA", "USO"):
        assert plan.resolve_feed_source(sym, "D") == "dukascopy"


def test_workflow_reads_the_stem_from_the_matrix():
    """Extracted from the shipping YAML, never a copy — a copy would drift."""
    text = WORKFLOW.read_text()
    assert 'OUT="data/${{ matrix.data_basename }}.csv"' in text
    assert 'data/${{ matrix.symbol }}_${{ matrix.tf }}.csv' not in text, (
        "the workflow is deriving the stem itself again — that second opinion "
        "is the defect this test exists for")


# --------------------------------------------------------------- the rollup
def _aggregate_source() -> str:
    """Lift the rollup's inline python OUT of the workflow, never a copy.

    Same discipline as `tests/test_merge_slot_guard.py`, which extracts the
    shipping command from `settings.json`: a pasted duplicate would pass while
    the thing that actually runs in CI drifted.
    """
    text = WORKFLOW.read_text()
    block = re.search(r"python - <<'PY' >> \"\$GITHUB_STEP_SUMMARY\"\n(.*?)\n\s*PY\n",
                      text, re.S)
    assert block, "could not find the rollup heredoc in the workflow"
    body = "\n".join(line[10:] if line.startswith(" " * 10) else line
                     for line in block.group(1).split("\n"))
    # The only templated value in the block is the planned count.
    return body.replace('int("${{ needs.plan.outputs.count }}" or 0)', "PLANNED")


def _run_rollup(planned: int, reports: list[dict], tmp_path: Path) -> str:
    for i, rep in enumerate(reports):
        d = tmp_path / "collected" / f"e35-bracket-{i}"
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(rep))
    src = f"PLANNED = {planned}\n" + _aggregate_source()
    proc = subprocess.run([sys.executable, "-c", src], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _leg(name):
    return {"leg": name, "surface": {"base_net_r": 1.0, "net_r_min": 0.0,
                                     "net_r_max": 2.0, "net_r_spread": 2.0,
                                     "best_cell": "tp1"}}


def test_rollup_counts_legs_measured_not_reports_returned(tmp_path):
    """An EMPTY report must not read as a measured leg.

    This is the exact shape the four broken legs produced: `report.json`
    exists, `legs` is empty, `skipped` names the reason. The old rollup counted
    the FILE, so 2-of-2 reports read as complete coverage of 2 planned legs.
    """
    out = _run_rollup(
        planned=2,
        reports=[{"legs": [_leg("good_leg")], "skipped": []},
                 {"legs": [], "skipped": [{"leg": "mes_trend_long_1d",
                                           "reason": "data_missing:MES"}]}],
        tmp_path=tmp_path)

    assert "1 of 2 leg(s) MEASURED" in out
    assert "1 leg(s) produced no measurement" in out
    # The reason is NAMED, not just counted — a count cannot distinguish a dead
    # runner from a leg the sweep refused for missing data.
    assert "mes_trend_long_1d" in out and "data_missing:MES" in out
    assert "good_leg" in out


def test_rollup_is_silent_when_every_leg_measured(tmp_path):
    out = _run_rollup(
        planned=2,
        reports=[{"legs": [_leg("a")], "skipped": []},
                 {"legs": [_leg("b")], "skipped": []}],
        tmp_path=tmp_path)
    assert "2 of 2 leg(s) MEASURED" in out
    assert "produced no measurement" not in out


def test_rollup_still_reports_a_lost_runner(tmp_path):
    """A leg whose job died uploads NO report at all. That must stay visible
    and stay distinguishable from a leg skipped inside a report."""
    out = _run_rollup(planned=3,
                      reports=[{"legs": [_leg("a")], "skipped": []}],
                      tmp_path=tmp_path)
    assert "1 of 3 leg(s) MEASURED" in out
    assert "2 leg(s) produced no measurement" in out
    assert "1 report file(s) returned" in out

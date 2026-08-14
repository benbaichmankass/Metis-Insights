"""`market_raw_to_csv` must refuse to write a native-named CSV that is the proxy.

BL-20260814-EQUITY-DAILY-LABELS-PROXY-DATA-AS-THE-NATIVE-SYMBOL.

`build_trainer_datasets.sh` builds some shards under the MICRO symbol from the
FULL-SIZE contract (`build_equity_daily MGC "GC=F"`), so `market_raw/MGC/1d`
holds GC=F bars. Converting that to `data/MGC_1d.csv` produced a file whose NAME
asserted a provenance its CONTENT lacked — and every downstream signal looked
clean (real rows, real prices, no blanks). With
`resolve_data(prefer_native=True)` it would have reported `proxy=False` and
`m20_exit_head_round`'s "native history required for head training" refusal
would have PASSED on proxy data.

The refusal must fail OPEN in every ambiguous case — it may block a write only
on positive evidence that the two series are the same.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "market_raw_to_csv", REPO / "scripts" / "research" / "market_raw_to_csv.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mrc = _mod()

_HDR = "timestamp,open,high,low,close,volume\n"


def _shard(root: Path, sym: str, interval: str, closes: dict[str, float]) -> None:
    d = root / "market_raw" / sym / interval / "v002"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "data.jsonl").open("w") as f:
        for day, c in sorted(closes.items()):
            f.write(json.dumps({"ts": f"{day}T00:00:00Z", "open": c, "high": c + 1,
                                "low": c - 1, "close": c, "volume": 10}) + "\n")


def _csv(path: Path, closes: dict[str, float]) -> None:
    path.write_text(_HDR + "".join(
        f"{d}T00:00:00Z,{c},{c + 1},{c - 1},{c},10\n" for d, c in sorted(closes.items())))


def _series(n: int, start: float = 1000.0) -> dict[str, float]:
    return {f"2020-01-{i + 1:02d}": start + i for i in range(n)}


def test_the_real_case_is_refused(tmp_path):
    """The measured 2026-08-14 shape: MGC shard identical to GC_F_1d.csv."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    s = _series(20)
    _shard(root, "MGC", "1d", s)
    _csv(data / "GC_F_1d.csv", s)
    rc = mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d"])
    assert rc == 2, "identical-to-proxy shard must be refused"
    assert not (data / "MGC_1d.csv").exists(), "refused write must leave no file"


def test_genuinely_different_native_data_is_written(tmp_path):
    """The can-fail control. If this ever fails the guard is refusing real
    native data, which is worse than the bug it prevents."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    _shard(root, "MGC", "1d", {d: c + 500 for d, c in _series(20).items()})
    _csv(data / "GC_F_1d.csv", _series(20))
    rc = mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d"])
    assert rc == 0
    assert (data / "MGC_1d.csv").exists()


def test_one_stale_final_bar_does_not_rescue_it(tmp_path):
    """The measured case was 2,511 of 2,512 — the proxy's last bar was stale.
    A single differing bar must NOT read as 'a different series'."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    s = _series(20)
    _shard(root, "MGC", "1d", s)
    proxy = dict(s)
    proxy["2020-01-20"] = proxy["2020-01-20"] + 9.6   # stale/unsettled print
    _csv(data / "GC_F_1d.csv", proxy)
    assert mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d"]) == 2


def test_allow_proxy_alias_overrides(tmp_path):
    """Deliberately materialising a proxy under its own name stays possible."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    s = _series(20)
    _shard(root, "MGC", "1d", s)
    _csv(data / "GC_F_1d.csv", s)
    rc = mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d",
                   "--allow-proxy-alias"])
    assert rc == 0 and (data / "MGC_1d.csv").exists()


@pytest.mark.parametrize("sym", ["BTCUSDT", "SPY"])
def test_a_symbol_with_no_declared_proxy_is_never_checked(tmp_path, sym):
    """Fail-open: no PROXY_DATA entry means no check, so the guard cannot
    block the overwhelming majority of conversions."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    _shard(root, sym, "1d", _series(10))
    assert mrc.main([sym, str(root), str(data / f"{sym}_1d.csv"), "1d"]) == 0


def test_no_proxy_csv_on_disk_fails_open(tmp_path):
    """Absence of the comparison file is absence of EVIDENCE, not evidence the
    series differ — but it must not block the write either."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    _shard(root, "MGC", "1d", _series(10))
    assert mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d"]) == 0


def test_no_overlapping_dates_fails_open(tmp_path):
    """Two series that never overlap cannot be compared; do not guess."""
    root, data = tmp_path / "datasets-out", tmp_path / "data"
    data.mkdir()
    _shard(root, "MGC", "1d", _series(10))
    _csv(data / "GC_F_1d.csv", {f"2021-02-{i + 1:02d}": 900.0 + i for i in range(10)})
    assert mrc.main(["MGC", str(root), str(data / "MGC_1d.csv"), "1d"]) == 0


def test_the_proxy_map_matches_the_sweeps(tmp_path):
    """This module keeps its own copy (stdlib-only, no yaml import). Drift
    fails OPEN, but a silent divergence still deserves to be caught here."""
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep", REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    assert mrc._PROXY_OF == sweep.PROXY_DATA, (
        "market_raw_to_csv._PROXY_OF has drifted from m20_fleet_exit_sweep.PROXY_DATA")

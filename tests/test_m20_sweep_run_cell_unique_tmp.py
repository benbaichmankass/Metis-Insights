"""Two concurrent `run_cell` calls must each read back THEIR OWN result.

FIX-CA-29 / `CA-B03-exit-sweep-shared-tmp-race` (+ the folded-in
`CA-B03-trail-resweep-shared-tmp-race`), code audit 2026-09-27.
`m20_exit_sweep.run_cell` wrote every cell to the fixed `/tmp/m20_cell.json`
and `m20_trail_resweep.run_cell` to `/tmp/m20_trail_cell.json`, then read the
file back. Two sweeps on one box therefore served each other's results — the
BL-20260820-RUN-CELL-SHARES-A-FIXED-TEMP-PATH class, measured on the sibling
`m20_fleet_exit_sweep.py` (three legs read back the same net_R=-9.6113) and
fixed there with `tempfile.mkstemp`, but not in these two files.

The fake harness makes the race deterministic: cell A writes its JSON FIRST
and exits LAST; cell B writes in between. With a shared path A reads B's
result.
"""
from __future__ import annotations

import sys
import textwrap
import threading
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import m20_exit_sweep  # noqa: E402
import m20_trail_resweep  # noqa: E402

FAKE = textwrap.dedent('''
    import json, sys, time
    argv = sys.argv[1:]
    tag = argv[argv.index("--tag") + 1]
    out = argv[argv.index("--json") + 1]
    if tag == "A":
        with open(out, "w") as fh:
            json.dump({"tag": tag}, fh)
        time.sleep(0.8)          # exit LAST
    else:
        time.sleep(0.3)          # write in between
        with open(out, "w") as fh:
            json.dump({"tag": tag}, fh)
''')


def _race(call_a, call_b) -> dict:
    got: dict = {}
    ta = threading.Thread(target=lambda: got.__setitem__("A", call_a()))
    tb = threading.Thread(target=lambda: got.__setitem__("B", call_b()))
    ta.start(); tb.start(); ta.join(); tb.join()
    return got


def test_exit_sweep_run_cell_reads_its_own_result(tmp_path, monkeypatch):
    (tmp_path / "fake_harness.py").write_text(FAKE)
    monkeypatch.setattr(m20_exit_sweep, "REPO", tmp_path)
    got = _race(lambda: m20_exit_sweep.run_cell("fake_harness.py", ["--tag", "A"]),
                lambda: m20_exit_sweep.run_cell("fake_harness.py", ["--tag", "B"]))
    assert got["A"] == {"tag": "A"}, got
    assert got["B"] == {"tag": "B"}, got


def test_trail_resweep_run_cell_reads_its_own_result(tmp_path, monkeypatch):
    (tmp_path / "fake_harness.py").write_text(FAKE)
    monkeypatch.setattr(m20_trail_resweep, "HARNESS", str(tmp_path / "fake_harness.py"))
    monkeypatch.setattr(m20_trail_resweep, "base_args", lambda a: [])
    a = types.SimpleNamespace(timeout=30)
    got = _race(lambda: m20_trail_resweep.run_cell(a, ["--tag", "A"], None, None),
                lambda: m20_trail_resweep.run_cell(a, ["--tag", "B"], None, None))
    assert got["A"] == {"tag": "A"}, got
    assert got["B"] == {"tag": "B"}, got


def test_no_m20_run_cell_uses_a_fixed_tmp_json_path():
    """Detector for the class: no m20_*.py may hardcode a /tmp/*.json literal
    (the shape that is both write-target and read-source in a run_cell)."""
    import re
    offenders = []
    for p in sorted((REPO / "scripts" / "research").glob("m20_*.py")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r'=\s*["\']/tmp/[^"\']*\.json["\']', line):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    assert not offenders, offenders

"""Two sweeps write `verdicts.json`. Only one of them is this extractor's.

`m20_fleet_exit_sweep` writes per-cell measurements (`cells`, `d_net_r_IS`,
`wf_folds`, …). `m20_flip_replay_sweep` writes per-leg flip results
(`flip_pct`, `walkforward`, `actual_net_r`, `flip_net_r`). The filename is
identical, `find_verdicts` is an `rglob("verdicts.json")`, and both sweeps write
under `runtime_logs/` — so `--in runtime_logs/` reaches both.

MEASURED 2026-08-16 on a real flip-sweep payload, before this guard existed: the
extractor ACCEPTED the foreign file and emitted a row asserting
`leg_status: "no_levers"` — *"this leg has no levers to sweep"* — about a leg
whose own source file records a lever that fired on 43.9 % of trades and
returned `fail`. A confident row stating the opposite of its input, keyed into
the durable corpus that `matrix-corpus-agreement` and the coverage roll-up both
read.

The discriminator is the FLEET shape's marker, deliberately not the flip
shape's: "does it look like a flip file?" would wave through any third schema
that shows up later.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "scripts/research/m20_corpus_extract.py"


def _load():
    spec = importlib.util.spec_from_file_location("m20_corpus_extract", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_corpus_extract"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


E = _load()

# Verbatim from relay #9534.
FLIP_DOC = {
    "generated_at": "2026-08-16T04:38:29.374368+00:00",
    "tp_cap_pct": 0.099, "tp_geometry": "live_parity_capped",
    "families": ["donchian"],
    "verdicts": {"trend_donchian_eth_4h": {
        "tp_geometry": "live_parity_capped", "tp_cap_pct": 0.099,
        "proxy": False, "trades": 196, "flip_pct": 43.9,
        "walkforward": "2/6", "verdict": "fail",
        "actual_net_r": 26.73, "flip_net_r": 16.3223}},
}


def test_a_flip_sweep_file_is_refused() -> None:
    try:
        E.rows_from_verdicts(FLIP_DOC, "run")
    except E.ForeignVerdictsSchema as exc:
        msg = str(exc)
        assert "flip_pct" in msg, "the message does not say what it actually saw"
        assert "m20_flip_replay_sweep" in msg, "it does not name the real owner"
        return
    raise AssertionError("the foreign schema was accepted")


def test_the_refusal_does_not_invent_a_leg_status() -> None:
    """The specific wrong answer this replaced."""
    try:
        rows = E.rows_from_verdicts(FLIP_DOC, "run")
    except E.ForeignVerdictsSchema:
        return
    assert not any(r.get("leg_status") == "no_levers" for r in rows), (
        "still manufacturing `no_levers` from a file that records a fired lever")


def test_a_fleet_shaped_file_still_extracts() -> None:
    """The positive control, in the shape the sweep ACTUALLY writes.

    An earlier version of this test used `{"cells": {}}` — a shape I invented,
    which happened to satisfy the equally-invented discriminator it was paired
    with. Both were wrong together, so the pair proved nothing, and the guard
    rejected every genuine fleet document. `test_m20_regime_book_provenance`
    caught it. The shape below is lifted from that suite.
    """
    doc = {"generated_at": "x",
           "verdicts": {"x": {"base_book": {}, "levers": {"s": [{"cell": "c"}]}}}}
    assert E.rows_from_verdicts(doc, "r")


def test_a_status_only_entry_is_still_fleet() -> None:
    """A leg the fleet sweep could not run: `status`, no `levers`. Legitimate."""
    doc = {"generated_at": "x",
           "verdicts": {"leg_a": {"status": "data_missing"}}}
    rows = E.rows_from_verdicts(doc, "r")
    assert rows and rows[0]["leg_status"] == "data_missing"


def test_the_markers_are_the_ones_the_reader_branches_on() -> None:
    """The guard must not drift from the code it is guarding.

    `rows_from_verdicts` branches on `"levers" not in v`, then reads
    `v.get("status")`; the cell path reads `v.get("base_book")`. If the reader
    starts keying on something else, this guard silently starts rejecting real
    input again — which is exactly what the first version did.
    """
    src = SRC.read_text()
    assert 'if "levers" not in v' in src
    for marker in ('"levers" in b', '"base_book" in b', '"status" in b'):
        assert marker in src, f"the guard no longer accepts {marker}"


def test_degenerate_docs_do_not_raise() -> None:
    """An empty run is a real state, not a foreign schema."""
    for doc in ({}, {"verdicts": {}}, {"verdicts": {"a": None}}):
        E.rows_from_verdicts(doc, "r")  # must not raise


def test_the_cli_fails_the_run_and_leaves_the_corpus_untouched(
        tmp_path: Path) -> None:
    """Skipping the file would report success over a population missing one."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "verdicts.json").write_text(json.dumps(FLIP_DOC))
    corpus = tmp_path / "corpus.jsonl"
    original = '{"kind":"cell","leg":"x","lever":"stale_stop"}\n'
    corpus.write_text(original)

    p = subprocess.run(
        [sys.executable, str(SRC), "--in", str(tmp_path),
         "--corpus", str(corpus)],
        capture_output=True, text=True, timeout=300, cwd=REPO)

    assert p.returncode == 1, f"expected a failed run, got {p.returncode}"
    assert "m20_flip_replay_sweep" in p.stderr
    assert corpus.read_text() == original, "the corpus was written anyway"

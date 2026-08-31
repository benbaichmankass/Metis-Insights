"""The e35 corpus supersedes measurements in place; the sidecar keeps the old ones.

`e35_corpus_extract.merge` is keyed on `measurement_key` --
`(leg, cell, tp_cap_pct, split_mode, split_target_oos)` -- so re-running the
SAME cell REPLACES its prior row rather than appending. That is correct for the
corpus (one row per measurement, not one per attempt) and it silently destroys
the earlier measurement.

Measured on the 2026-08-31 live sweep, against the real diff: before the run the
corpus held 1,035 rows / 995 distinct (leg, cell); after it, identical counts,
with all 995 touched cells having had a prior row and only 40 surviving --
roughly 955 measurements gone with no record that they had ever been taken
(`BL-20260831-E35-CORPUS-SUPERSEDES-THE-BASELINE-ITS-MONTHLY-JOB-ASKS-ABOUT`).

⚠️ THE LOAD-BEARING TEST IS `test_a_sidecar_failure_refuses_to_rewrite_the_corpus`.
The corpus write is what destroys the displaced rows, so archiving AFTER it would
mean a sidecar failure loses them permanently with the corpus already rewritten.
Order is the entire safety property; a test that only checks "the rows appear in
the sidecar" would pass with the two writes in either order.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTRACT = REPO / "scripts" / "research" / "e35_corpus_extract.py"


def _report(path: Path, *, stamp: str, net_r: float) -> None:
    """One e35 report.json holding a single leg with a single stop cell."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": stamp,
        "tp_cap_pct": 0.099,
        "fee_bps_roundtrip": 7.5,
        "legs": [{
            "leg": "leg_a", "symbol": "S", "tf": "1h", "family": "donchian",
            "execution": "live",
            "base": {"net_total_r": 1.0, "max_drawdown_r": 2.0},
            "cells": [{"cell": "sm1.5", "axis": "stop", "stop_mult": 1.5,
                       "net_total_r": net_r, "d_net_r": net_r - 1.0,
                       "state": "ok"}],
            "gate": [],
        }],
    }))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(EXTRACT), *args],
                          capture_output=True, text=True, cwd=REPO)


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_a_superseded_row_is_archived_with_its_ordering_stamps(tmp_path):
    """Run twice on the same cell: the OLD measurement survives in the sidecar."""
    corpus = tmp_path / "corpus.jsonl"
    history = tmp_path / "corpus-history.jsonl"

    first = tmp_path / "run1" / "report.json"
    _report(first, stamp="2026-01-01T00:00:00+00:00", net_r=5.0)
    r1 = _run([str(first), "--corpus", str(corpus), "--history", str(history)])
    assert r1.returncode == 0, r1.stderr
    assert not history.exists(), "a first run displaces nothing and must not write"

    second = tmp_path / "run2" / "report.json"
    _report(second, stamp="2026-02-02T00:00:00+00:00", net_r=9.0)
    r2 = _run([str(second), "--corpus", str(corpus), "--history", str(history)])
    assert r2.returncode == 0, r2.stderr

    # The corpus keeps ONE row per measurement -- the newest.
    rows = _lines(corpus)
    assert len(rows) == 1
    assert rows[0]["net_total_r"] == 9.0

    # The sidecar holds the measurement the corpus no longer does.
    archived = _lines(history)
    assert len(archived) == 1
    old = archived[0]
    assert old["net_total_r"] == 5.0, "the ARCHIVED row must be the OLD one"
    # Without both stamps the archive has no ordering: you could not say which
    # measurement replaced which.
    assert old["sweep_generated_at"] == "2026-01-01T00:00:00+00:00"
    assert old["superseded_by_sweep"] == "2026-02-02T00:00:00+00:00"
    assert old["superseded_at"], "superseded_at must be stamped"


def test_a_sidecar_failure_refuses_to_rewrite_the_corpus(tmp_path):
    """THE ordering property: no archive => no corpus write, and a non-zero exit.

    Induced by pointing --history at a path whose PARENT is a regular file, so
    `mkdir(parents=True)` raises NotADirectoryError (an OSError). If the corpus
    write ran first this test would still see the sidecar missing -- what it
    actually pins is that the corpus is UNCHANGED and the run FAILED.
    """
    corpus = tmp_path / "corpus.jsonl"
    first = tmp_path / "run1" / "report.json"
    _report(first, stamp="2026-01-01T00:00:00+00:00", net_r=5.0)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a regular file\n")
    history = blocker / "sub" / "history.jsonl"

    assert _run([str(first), "--corpus", str(corpus),
                 "--history", str(history)]).returncode == 0
    before = corpus.read_text()

    second = tmp_path / "run2" / "report.json"
    _report(second, stamp="2026-02-02T00:00:00+00:00", net_r=9.0)
    r2 = _run([str(second), "--corpus", str(corpus), "--history", str(history)])

    assert r2.returncode != 0, "an unarchivable supersede must not report success"
    assert "REFUSING to rewrite the corpus" in r2.stderr
    assert corpus.read_text() == before, (
        "the corpus was rewritten despite the archive failing -- the displaced "
        "measurement is gone. The sidecar write must PRECEDE the corpus write.")


def test_history_dash_destroys_loudly_rather_than_silently(tmp_path):
    """The escape hatch behaves as declared: it warns, and it really does destroy."""
    corpus = tmp_path / "corpus.jsonl"
    first = tmp_path / "run1" / "report.json"
    _report(first, stamp="2026-01-01T00:00:00+00:00", net_r=5.0)
    assert _run([str(first), "--corpus", str(corpus), "--history", "-"]).returncode == 0

    second = tmp_path / "run2" / "report.json"
    _report(second, stamp="2026-02-02T00:00:00+00:00", net_r=9.0)
    r2 = _run([str(second), "--corpus", str(corpus), "--history", "-"])
    assert r2.returncode == 0
    assert "being DESTROYED, not archived" in (r2.stdout + r2.stderr)
    assert not list(tmp_path.glob("*history*")), "no sidecar may be written"
    assert _lines(corpus)[0]["net_total_r"] == 9.0


def test_the_default_history_path_sits_beside_the_corpus(tmp_path):
    """An omitted --history archives, rather than silently taking the dash path."""
    corpus = tmp_path / "e35-bracket-corpus.jsonl"
    first = tmp_path / "run1" / "report.json"
    _report(first, stamp="2026-01-01T00:00:00+00:00", net_r=5.0)
    assert _run([str(first), "--corpus", str(corpus)]).returncode == 0
    second = tmp_path / "run2" / "report.json"
    _report(second, stamp="2026-02-02T00:00:00+00:00", net_r=9.0)
    assert _run([str(second), "--corpus", str(corpus)]).returncode == 0

    default = tmp_path / "e35-bracket-corpus-history.jsonl"
    assert default.exists(), "the default sidecar must be derived from the corpus stem"
    assert _lines(default)[0]["net_total_r"] == 5.0


def test_a_dry_run_writes_neither_file(tmp_path):
    """--dry-run must not archive either -- it destroys nothing, so it saves nothing."""
    corpus = tmp_path / "corpus.jsonl"
    history = tmp_path / "corpus-history.jsonl"
    first = tmp_path / "run1" / "report.json"
    _report(first, stamp="2026-01-01T00:00:00+00:00", net_r=5.0)
    assert _run([str(first), "--corpus", str(corpus), "--history", str(history)]).returncode == 0
    before = corpus.read_text()

    second = tmp_path / "run2" / "report.json"
    _report(second, stamp="2026-02-02T00:00:00+00:00", net_r=9.0)
    r2 = _run([str(second), "--corpus", str(corpus), "--history", str(history),
               "--dry-run"])
    assert r2.returncode == 0
    assert not history.exists()
    assert corpus.read_text() == before


def test_the_workflow_commits_both_files(tmp_path):
    """A sidecar the sweep never commits is a sidecar that does not exist.

    `commit-to-main`'s `paths` input is word-split (`git add -- ${PATHS}`), so
    the two paths ride one space-separated value.
    """
    wf = (REPO / ".github" / "workflows" / "e35-bracket-sweep.yml").read_text()
    assert "docs/research/e35-bracket-corpus.jsonl" in wf
    assert "docs/research/e35-bracket-corpus-history.jsonl" in wf, (
        "the sweep archives superseded rows into a sidecar it never commits")

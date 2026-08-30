"""The e35 landing assertion must be able to FAIL.

Until 2026-08-30 `e35-bracket-sweep.yml` asserted
`--field cell --contains sm --min-rows 1` against
`docs/research/e35-bracket-corpus.jsonl`. That store is CUMULATIVE, so the
predicate was satisfied by HISTORY and `assert_rows_landed`'s `pending_merge`
branch was unreachable: the check could not fail.

Measured on run 33306805155 -- it reported
`landed - 6624 rows with cell containing 'sm' (need >= 1) of 8289 total`
while EVERY one of that run's rows sat on an unmerged side branch. All 6,624
matching rows predate the run.

⚠️ THE LOAD-BEARING TEST IS `test_the_old_predicate_is_what_used_to_pass_vacuously`.
It pins the WRONG-BUT-ACTUAL old behaviour on purpose, so that anyone reverting
the workflow to the cumulative predicate sees a test fail and reads why, rather
than the assertion silently going green forever again. Without it the run-scoped
assertion below could become vacuous in some future refactor with nothing to say
so -- the same shape as `test_the_naive_stem_is_what_used_to_break` in the e35
shard-plan tests.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSERT = REPO / "scripts" / "ci" / "assert_rows_landed.py"
STORE = "docs/research/e35-bracket-corpus.jsonl"

OLD_STAMP = "2026-08-29T22:02:40.702003+00:00"
RUN_STAMP = "2026-08-30T10:49:01.123456+00:00"


def _row(stamp: str, cell: str, leg: str = "trend_donchian") -> str:
    return json.dumps({"leg": leg, "cell": cell, "sweep_generated_at": stamp})


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'origin' with main carrying HISTORY and a side branch carrying THIS RUN."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)],
                   check=True, capture_output=True)
    _git(work, "config", "user.email", "t@t"); _git(work, "config", "user.name", "t")
    store = work / STORE
    store.parent.mkdir(parents=True, exist_ok=True)

    # main: history only. Note it ALREADY satisfies `cell contains sm`.
    store.write_text("\n".join(_row(OLD_STAMP, f"sm{i}") for i in range(20)) + "\n")
    _git(work, "add", STORE); _git(work, "commit", "-m", "history")
    _git(work, "push", "origin", "main")

    # side branch: history + THIS RUN's rows (the retarget case).
    _git(work, "checkout", "-b", "side")
    store.write_text(store.read_text()
                     + "\n".join(_row(RUN_STAMP, f"sm{i}") for i in range(5)) + "\n")
    _git(work, "add", STORE); _git(work, "commit", "-m", "this run")
    _git(work, "push", "origin", "side")
    _git(work, "checkout", "main")
    return work, origin


def _run(work: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ASSERT), "--store", STORE,
         "--ref", "origin/main", "--pushed-ref", "origin/side",
         "--min-rows", "1", *extra],
        cwd=work, capture_output=True, text=True)


def test_the_old_predicate_is_what_used_to_pass_vacuously(tmp_path):
    """CONTROL: the cumulative predicate reports `landed` on rows that did NOT land."""
    work, _ = _fixture(tmp_path)
    r = _run(work, "--field", "cell", "--contains", "sm")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "landed" in r.stdout
    # ...and it is satisfied purely by history: 20 old rows, 0 of this run's.
    assert "20 rows" in r.stdout or "of 20" in r.stdout, r.stdout


def test_run_scoped_predicate_reports_pending_merge(tmp_path):
    """THE FIX: scoped to this run's stamp, the same state is non-zero."""
    work, _ = _fixture(tmp_path)
    r = _run(work, "--field", "sweep_generated_at", "--contains", RUN_STAMP)
    assert r.returncode != 0, "a run whose rows are not on main must NOT pass"
    assert "pending_merge" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_run_scoped_predicate_passes_once_the_rows_are_on_main(tmp_path):
    """...and it is not merely always-failing: merging the branch turns it green."""
    work, _ = _fixture(tmp_path)
    _git(work, "merge", "--no-edit", "origin/side")
    _git(work, "push", "origin", "main")
    _git(work, "fetch", "origin")
    r = _run(work, "--field", "sweep_generated_at", "--contains", RUN_STAMP)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "landed" in r.stdout


def test_extractor_emits_a_run_stamp_line(tmp_path):
    """The workflow parses `run-stamp:` off the extractor's stdout."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "corpus": "e35-bracket", "generated_at": RUN_STAMP,
        "tp_cap_pct": 0.099, "fee_bps_roundtrip": 7.5,
        "legs": [{"leg": "trend_donchian", "symbol": "BTCUSDT", "tf": "1h",
                  "family": "donchian", "execution": "live", "base": {},
                  "gate": [], "cells": [{"cell": "sm2", "axis": "stop_mult"}]}],
    }))
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/e35_corpus_extract.py"),
         str(report), "--corpus", str(tmp_path / "c.jsonl"), "--dry-run"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"run-stamp: {RUN_STAMP}" in r.stdout, r.stdout


def test_a_report_missing_generated_at_is_refused_by_the_PRE_EXISTING_guard(tmp_path):
    """Not my guard — `generated_at` is in `_REQUIRED_TOP`, so `_assert_e35`
    rejects it first. Pinned so nobody 'helpfully' relaxes that schema check and
    silently makes stamps optional, which would put the run-scoped assertion
    back on an unscopeable footing."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "corpus": "e35-bracket", "tp_cap_pct": 0.099, "fee_bps_roundtrip": 7.5,
        "legs": [{"leg": "trend_donchian", "symbol": "BTCUSDT", "tf": "1h",
                  "family": "donchian", "execution": "live", "base": {},
                  "gate": [], "cells": [{"cell": "sm2", "axis": "stop_mult"}]}],
    }))
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/e35_corpus_extract.py"),
         str(report), "--corpus", str(tmp_path / "c.jsonl"), "--dry-run"],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "generated_at" in r.stderr


def test_extractor_refuses_a_stamped_report_that_yields_zero_rows(tmp_path):
    """The REACHABLE case for the new guard: `generated_at` present, but no cell
    produced a row, so there is no stamp to scope an assertion by. It must refuse
    rather than exit 0 having written nothing — otherwise the workflow's own
    `[ -z "$STAMP" ]` check is the only thing standing between a zero-row run and
    a fallback to the cumulative predicate."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "corpus": "e35-bracket", "generated_at": RUN_STAMP,
        "tp_cap_pct": 0.099, "fee_bps_roundtrip": 7.5,
        "legs": [{"leg": "trend_donchian", "symbol": "BTCUSDT", "tf": "1h",
                  "family": "donchian", "execution": "live", "base": {},
                  "gate": [], "cells": []}],
    }))
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/e35_corpus_extract.py"),
         str(report), "--corpus", str(tmp_path / "c.jsonl"), "--dry-run"],
        capture_output=True, text=True)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "cannot be scoped" in r.stderr

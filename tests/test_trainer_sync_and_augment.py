"""Regression tests for the two 2026-08-07 trainer-side data defects.

Both live as Python heredocs inside ops shell scripts, so they are extracted from
the script source and executed directly. That is deliberate: it pins the code
that ACTUALLY RUNS on the trainer rather than a copy that can drift from it.

1. ``build_trainer_datasets.sh`` § PYAUG — the pooled augmentation merge copied
   ``trades.id`` across two independently-numbered databases, so it was
   guaranteed to fail once the id ranges overlapped. It did, on 2026-08-07:
   ``sqlite3.IntegrityError: UNIQUE constraint failed: trades.id``, after which
   the whole cycle silently fell back to a journal-only (un-augmented) build
   while every family still reported ok.
   ``BL-20260807-POOLED-AUGMENT-MERGE-SILENT-FALLBACK``.

2. ``sync_trainer_data.sh`` § PYVERIFY — the journal pull rsynced a HOT WAL-mode
   SQLite DB straight over the mirror, so a torn copy replaced a good one and
   nothing checked integrity (the pull asserted a SIZE match).
   ``BL-20260807-TRAINER-JOURNAL-PULL-TORN-RSYNC``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_OPS = os.path.join(os.path.dirname(_HERE), "scripts", "ops")

_TRADES_SCHEMA = (
    "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
    "is_backtest INTEGER DEFAULT 0)"
)


def _extract(script: str, marker: str) -> str:
    """Pull one `<<'MARKER' ... MARKER` heredoc body out of a shell script."""
    src = open(os.path.join(_OPS, script), encoding="utf-8").read()
    assert f"<<'{marker}'" in src, f"{marker} heredoc missing from {script}"
    return src.split(f"<<'{marker}'")[1].split(marker)[0]


def _run(body: str, tmp_path, name: str, *args):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return subprocess.run([sys.executable, str(p), *args],
                          capture_output=True, text=True)


# --------------------------------------------------------------------------
# 1. pooled augmentation merge
# --------------------------------------------------------------------------

def _make_pair(tmp_path, journal_n: int, bt_n: int):
    """A journal and a backtest db that BOTH number trades.id from 1."""
    j = tmp_path / "journal.db"
    con = sqlite3.connect(j)
    con.execute(_TRADES_SCHEMA)
    con.executemany(
        "INSERT INTO trades (id,symbol,pnl,is_backtest) VALUES (?,?,?,0)",
        [(i, "BTCUSDT", 1.0) for i in range(1, journal_n + 1)])
    con.commit()
    con.close()
    b = tmp_path / "bt.db"
    con = sqlite3.connect(b)
    con.execute(_TRADES_SCHEMA)
    con.executemany(
        "INSERT INTO trades (id,symbol,pnl,is_backtest) VALUES (?,?,?,1)",
        [(i, "BTCUSDT", 2.0) for i in range(1, bt_n + 1)])
    con.commit()
    con.close()
    return j, b


def test_augment_merge_survives_overlapping_ids(tmp_path):
    """THE REGRESSION. Overlapping id ranges are the normal state of two
    independently-numbered databases, not an edge case."""
    journal, bt = _make_pair(tmp_path, journal_n=50, bt_n=30)
    merged = tmp_path / "merged.db"
    merged.write_bytes(journal.read_bytes())

    r = _run(_extract("build_trainer_datasets.sh", "PYAUG"), tmp_path,
             "merge.py", str(merged), str(bt))
    assert r.returncode == 0, f"merge failed: {r.stderr}"

    con = sqlite3.connect(merged)
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 80
    assert con.execute(
        "SELECT COUNT(*) FROM trades WHERE is_backtest=1").fetchone()[0] == 30
    assert con.execute(
        "SELECT COUNT(*) FROM trades WHERE is_backtest=0").fetchone()[0] == 50
    # every row still uniquely addressable -> the pk was reassigned, not copied
    assert con.execute("SELECT COUNT(DISTINCT id) FROM trades").fetchone()[0] == 80


def test_augment_merge_refuses_a_vacuous_merge(tmp_path):
    """A merge that inserts zero rows must FAIL rather than report success — a
    build that proceeds 'augmented' over an un-augmented population is the
    green-while-measuring-nothing class."""
    journal, bt = _make_pair(tmp_path, journal_n=10, bt_n=0)
    merged = tmp_path / "merged.db"
    merged.write_bytes(journal.read_bytes())
    r = _run(_extract("build_trainer_datasets.sh", "PYAUG"), tmp_path,
             "merge.py", str(merged), str(bt))
    assert r.returncode != 0
    assert "vacuous" in (r.stderr + r.stdout).lower()


def test_augment_merge_reports_the_destination_count(tmp_path):
    """The success line must state what landed IN THE MERGED DB, not just what
    the source offered."""
    journal, bt = _make_pair(tmp_path, journal_n=5, bt_n=4)
    merged = tmp_path / "merged.db"
    merged.write_bytes(journal.read_bytes())
    r = _run(_extract("build_trainer_datasets.sh", "PYAUG"), tmp_path,
             "merge.py", str(merged), str(bt))
    assert r.returncode == 0
    assert "backtest_rows_added=4" in r.stdout
    assert "is_backtest_rows_in_merged=4" in r.stdout
    assert "pk_dropped=['id']" in r.stdout


# --------------------------------------------------------------------------
# 2. journal-pull integrity verifier
# --------------------------------------------------------------------------

def _good_journal(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, pnl REAL)")
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, logged_at_utc TEXT)")
    con.execute("CREATE INDEX ix_signals_ts ON signals(logged_at_utc)")
    con.executemany("INSERT INTO signals (logged_at_utc) VALUES (?)",
                    [(f"2026-08-{(i % 28) + 1:02d}",) for i in range(400)])
    con.executemany("INSERT INTO trades (pnl) VALUES (?)", [(1.0,)] * 60)
    con.commit()
    con.close()
    return path


@pytest.mark.parametrize("kind,expect_ok", [("good", True), ("torn", False),
                                            ("garbage", False), ("missing", False)])
def test_journal_verify_classifies(tmp_path, kind, expect_ok):
    verifier = _extract("sync_trainer_data.sh", "PYVERIFY")
    target = tmp_path / "cand.db"
    if kind == "good":
        _good_journal(target)
    elif kind == "torn":
        src = _good_journal(tmp_path / "src.db")
        raw = src.read_bytes()
        target.write_bytes(raw[: int(len(raw) * 0.6)])
    elif kind == "garbage":
        target.write_bytes(b"not a database" * 500)
    # "missing" -> never created

    r = _run(verifier, tmp_path, "verify.py", str(target))
    assert (r.returncode == 0) is expect_ok, (
        f"{kind}: rc={r.returncode} stderr={r.stderr[:200]}")


def test_journal_verify_rejects_a_structurally_valid_but_wrong_db(tmp_path):
    """A file can be a perfectly healthy SQLite database and still not be the
    journal. Verifying only `quick_check` would pass it — the verifier also has
    to answer a query a real consumer makes."""
    target = tmp_path / "other.db"
    con = sqlite3.connect(target)
    con.execute("CREATE TABLE unrelated (x INTEGER)")
    con.commit()
    con.close()
    assert con is not None
    r = _run(_extract("sync_trainer_data.sh", "PYVERIFY"), tmp_path,
             "verify.py", str(target))
    assert r.returncode != 0


# --------------------------------------------------------------------------
# 3. the pull's temp-file hygiene (found by verifying the fix in production)
# --------------------------------------------------------------------------

def _stub_pull_env(tmp_path, delivered_rows: int, ssh_exit: int = 1):
    """Run the real sync script with stubbed ssh/rsync.

    ssh_exit=1 forces the direct-rsync fallback (what the live VM actually does
    today — it has no sqlite3 CLI, and python3 backup is exercised separately).
    """
    for d in ("bin", "data", "repo/.git", "logs", "src"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for path, n in ((tmp_path / "data" / "trade_journal.db", 5),
                    (tmp_path / "src" / "fresh.db", delivered_rows)):
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, pnl REAL)")
        con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, logged_at_utc TEXT)")
        con.executemany("INSERT INTO signals (logged_at_utc) VALUES (?)",
                        [("2026-08-07",)] * n)
        con.executemany("INSERT INTO trades (pnl) VALUES (?)", [(1.0,)] * n)
        con.commit()
        con.close()
    (tmp_path / "key").write_text("")
    (tmp_path / "bin" / "ssh").write_text(f"#!/bin/bash\nexit {ssh_exit}\n")
    (tmp_path / "bin" / "rsync").write_text(
        f'#!/bin/bash\ndest="${{@: -1}}"\ncp {tmp_path}/src/fresh.db "$dest"\nexit 0\n')
    for f in ("ssh", "rsync"):
        os.chmod(tmp_path / "bin" / f, 0o755)
    env = dict(os.environ)
    env.update(PATH=f"{tmp_path}/bin:{env['PATH']}", VM_SSH_KEY=str(tmp_path / "key"),
               DATA_DIR=str(tmp_path / "data"), REPO_ROOT=str(tmp_path / "repo"),
               PULL_LOG_PATH=str(tmp_path / "logs" / "pull.jsonl"))
    subprocess.run(["bash", os.path.join(_OPS, "sync_trainer_data.sh")],
                   env=env, capture_output=True, text=True)
    return tmp_path / "data"


def test_pull_leaves_no_incoming_sidecars(tmp_path):
    """Opening a WAL-mode DB to VERIFY it creates `-wal`/`-shm` beside the temp.
    Moving only the body left `.trade_journal.db.incoming-wal`/`-shm` orphaned —
    observed on the trainer 2026-08-07, the first run after the fix landed.

    Not cosmetic: a stale `.incoming-shm` beside a FUTURE `.incoming` body is the
    mismatched-sidecar hazard the promote-on-verify logic exists to prevent."""
    data_dir = _stub_pull_env(tmp_path, delivered_rows=42)
    leftovers = [p.name for p in data_dir.iterdir() if "incoming" in p.name]
    assert leftovers == [], f"orphaned temp sidecars: {leftovers}"
    con = sqlite3.connect(data_dir / "trade_journal.db")
    assert con.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 42


def test_pull_reports_the_method_it_actually_used(tmp_path):
    """The snapshot path was shipped INERT — the live VM has no sqlite3 CLI, so
    the probe failed every time and the pull silently used the fallback. The
    method must be stated in the log so an inert path is visible rather than
    assumed working."""
    data_dir = _stub_pull_env(tmp_path, delivered_rows=7)
    log = (data_dir.parent / "logs" / "pull.jsonl").read_text()
    ok = [json.loads(x) for x in log.splitlines()
          if x.strip() and json.loads(x).get("artifact") == "trade_journal.db"
          and json.loads(x).get("status") == "ok"]
    assert ok, "no ok row for trade_journal.db"
    assert ok[-1]["method"] == "direct_rsync_fallback"
    assert ok[-1]["integrity_verified"] is True

"""WC-6 — unit tests for ``scripts/check_writer_conformance.py``.

The guard is a regex-over-added-lines scan, so the test surface is
(a) every offending pattern, (b) the legitimate counter-patterns that
must NOT fire (signal ``side=buy``, allowlisted files), and (c) the
override / scope-exclusion paths. Each fixture is a synthetic unified
diff string; no on-disk repo state is required (the scan style mirrors
``tests/test_check_silent_empty_in_diff.py``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_writer_conformance as guard


def _diff(path: str, hunk: str, *, start_line: int = 10) -> str:
    """Compose a minimal valid unified diff with one hunk.

    *hunk* is the body lines (one per line; prefix ``+`` for added,
    ``-`` for removed, `` `` for context).
    """
    body = "\n".join(hunk.splitlines())
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{start_line},2 +{start_line},6 @@\n"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Rule 1 — raw writers outside the canonical module: MUST flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, label_frag",
    [
        ('cur.execute("INSERT INTO trades (id) VALUES (?)", (1,))', "INSERT INTO trades"),
        ('cur.execute("UPDATE trades SET status=? WHERE id=?", (s, i))', "UPDATE trades SET"),
        ('conn.execute("INSERT INTO order_packages (id) VALUES (?)", (p,))', "INSERT INTO order_packages"),
        ('conn.execute("UPDATE order_packages SET status=? WHERE id=?", (s, p))', "UPDATE order_packages SET"),
    ],
)
def test_flags_raw_write_outside_canonical(sql: str, label_frag: str) -> None:
    diff = _diff("src/runtime/order_monitor.py", f"+    {sql}\n")
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert label_frag in findings[0]
    assert "src/runtime/order_monitor.py" in findings[0]


def test_flags_fstring_raw_write() -> None:
    """The f-string shape the canonical writer itself uses, copied
    elsewhere, is caught."""
    diff = _diff(
        "src/core/coordinator.py",
        '+        query = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"\n',
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "INSERT INTO trades" in findings[0]


# ---------------------------------------------------------------------------
# Rule 1 — allowlisted files / dirs: MUST NOT flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/units/db/database.py",       # the canonical writer module
        "src/units/ui/processor.py",      # WC-1 /closeall raw path
        "scripts/ops/backfill_pnl_nulls.py",   # operator tooling
        "notebooks/operator/cleanup_ghost_trades.ipynb",  # one-shot notebook
        "tests/test_some_writer.py",      # tests seed raw rows
    ],
)
def test_does_not_flag_raw_write_in_allowlisted(path: str) -> None:
    diff = _diff(path, '+    cur.execute("UPDATE trades SET status=? WHERE id=?", (s, i))\n')
    assert guard.scan_diff(diff) == []


def test_does_not_flag_raw_write_with_allow_marker() -> None:
    diff = _diff(
        "src/runtime/order_monitor.py",
        '+    cur.execute("UPDATE trades SET x=?", (x,))  # writer-conformance: allow bulk repair\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_self_referencing_script() -> None:
    """The guard's own source contains the patterns it scans for."""
    diff = _diff(
        "scripts/check_writer_conformance.py",
        '+    re.compile(r"\\bINSERT\\s+INTO\\s+trades\\b")\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_docs() -> None:
    diff = _diff(
        "docs/audits/writer-conformance.md",
        "+    cur.execute(\"INSERT INTO trades ...\")  # the bad pattern\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_select_only() -> None:
    """A read (SELECT) against the tables is fine — only INSERT/UPDATE
    are guarded."""
    diff = _diff(
        "src/runtime/order_monitor.py",
        '+    cur.execute("SELECT * FROM trades WHERE id=?", (i,))\n',
    )
    assert guard.scan_diff(diff) == []


@pytest.mark.parametrize(
    "comment_line",
    [
        # pure-comment lines naming the bad idioms (the database.py:203 TODO
        # that tripped the guard on its own PR #3827 was exactly this shape)
        "+    # historical rows: legacy direction='buy' on is_backtest=1 rows",
        '+    # do NOT hand-roll an UPDATE trades SET ... outside this module',
        "+    # e.g. INSERT INTO order_packages by hand is forbidden",
        # trailing comment on an innocuous code line
        '+    x = compute()  # was direction = "buy" in the old code',
    ],
)
def test_does_not_flag_patterns_inside_comments(comment_line: str) -> None:
    """A ``#`` comment that merely NAMES a bad idiom is prose, not a
    violation — only the code portion of an added line is matched."""
    diff = _diff("src/runtime/order_monitor.py", comment_line + "\n")
    assert guard.scan_diff(diff) == []


# ---------------------------------------------------------------------------
# Rule 2 — non-canonical direction value: MUST flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        '+    direction = "buy"\n',
        "+    direction = 'sell'\n",
        '+    trade["direction"] = "buy"\n',
        '+    row = {"direction": "sell", "symbol": sym}\n',
        '+    self.insert_trade(direction="buy")\n',
    ],
)
def test_flags_non_canonical_direction(line: str) -> None:
    diff = _diff("src/core/coordinator.py", line)
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "direction" in findings[0]


def test_flags_direction_even_in_canonical_module() -> None:
    """Rule 2 applies even to the canonical writer — it must never
    hard-code a buy/sell direction either."""
    diff = _diff("src/units/db/database.py", '+    direction = "buy"\n')
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "direction" in findings[0]


# ---------------------------------------------------------------------------
# Rule 2 — legitimate side=buy/sell: MUST NOT flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        '+    side = "buy"\n',
        "+    signal = {\"side\": \"sell\"}\n",
        '+    order["side"] = "buy"\n',
        '+    place_order(side="sell")\n',
    ],
)
def test_does_not_flag_side_buy_sell(line: str) -> None:
    diff = _diff("src/core/coordinator.py", line)
    assert guard.scan_diff(diff) == []


@pytest.mark.parametrize(
    "line",
    [
        '+    if trade["direction"] == "buy":\n',
        '+    if direction == "sell":\n',
    ],
)
def test_does_not_flag_direction_equality_check(line: str) -> None:
    """A read/comparison (`==`) against buy/sell is not an assignment and
    must not be flagged."""
    diff = _diff("src/core/coordinator.py", line)
    assert guard.scan_diff(diff) == []


def test_does_not_flag_canonical_direction() -> None:
    diff = _diff(
        "src/core/coordinator.py",
        '+    direction = "long"\n+    other = "short"\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_direction_with_allow_marker() -> None:
    diff = _diff(
        "src/core/coordinator.py",
        '+    direction = "buy"  # writer-conformance: allow legacy alias mapping\n',
    )
    assert guard.scan_diff(diff) == []


# ---------------------------------------------------------------------------
# Diff mechanics
# ---------------------------------------------------------------------------


def test_does_not_flag_context_lines() -> None:
    """Pre-existing (context) lines are grandfathered — only + lines
    are inspected."""
    diff = _diff(
        "src/runtime/order_monitor.py",
        ' def fn():\n'
        '     cur.execute("UPDATE trades SET status=? WHERE id=?", (s, i))\n'
        '     direction = "buy"\n'
        '+    pass\n',
    )
    assert guard.scan_diff(diff) == []


def test_clean_diff() -> None:
    diff = _diff(
        "src/runtime/order_monitor.py",
        "+    self.db.update_trade(trade_id, {\"direction\": \"long\"})\n",
    )
    assert guard.scan_diff(diff) == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class _StringIOLike:
    """Tiny stand-in for ``sys.stdin`` (monkeypatching the real one
    breaks pytest capture on some platforms)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


def test_main_returns_0_when_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff("src/runtime/order_monitor.py", " pass\n+# benign added line\n")
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_writer_conformance.py"])
    assert rc == 0
    assert "clean" in capsys.readouterr().out


def test_main_returns_1_on_raw_write_hit(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff(
        "src/runtime/order_monitor.py",
        '+    cur.execute("UPDATE trades SET status=? WHERE id=?", (s, i))\n',
    )
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_writer_conformance.py"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "WRITER_CONFORMANCE_GUARD\t" in captured.out
    assert "WRITER-CONFORMANCE GUARD" in captured.err


def test_main_returns_1_on_direction_hit(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff("src/core/coordinator.py", '+    direction = "buy"\n')
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_writer_conformance.py"])
    assert rc == 1
    assert "WRITER_CONFORMANCE_GUARD\t" in capsys.readouterr().out


def test_main_reads_diff_from_argv_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = _diff(
        "src/runtime/order_monitor.py",
        '+    cur.execute("INSERT INTO trades (id) VALUES (?)", (1,))\n',
    )
    path = tmp_path / "pr.diff"
    path.write_text(diff, encoding="utf-8")
    rc = guard.main(["check_writer_conformance.py", str(path)])
    assert rc == 1
    assert "WRITER_CONFORMANCE_GUARD\t" in capsys.readouterr().out

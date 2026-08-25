"""The target-naked CRITICAL cooldown must survive a trader restart.

MEASURED DEFECT (2026-08-23). `_emit_target_naked_alert` declares "one page
per 6h per (account, symbol)" -- at most 4/day/symbol -- but latched in a
module global keyed on `time.monotonic()`. Both are per-PROCESS, and the
trader restarts on every merge to `main`, so the cooldown reset on every
restart.

Live evidence, `/api/bot/logs?level=error` (the ERROR+/CRITICAL feed):
  - 202 of 376 rows -- 53.7% of every ERROR+ row over ~6.5 days -- were
    `ib_target_naked`, for TWO paper positions in an already-filed state.
  - Per day, MES and MGC alert counts pair almost exactly (16/16, 31/31,
    11/11, 24/24), the signature of one alert per symbol per process.
  - 2026-08-23: `exit_interval_soak` reports 9 distinct trader process
    starts; `ib_target_naked` fired for MES exactly 9 times. (n=1 day --
    the soak page truncates to 1000 lines, which covered only 08-23.)

CRITICAL reaches Telegram, so this trained the operator to scroll past the
one channel reserved for "a position can only stop out or run" -- the
desensitized-alarm P1 the cooldown's own comment cites as the reason it
exists. The comment was right; the implementation could not deliver it.
"""
from __future__ import annotations

import json

import pytest

import src.runtime.order_monitor as om


@pytest.fixture()
def latched(tmp_path, monkeypatch):
    """Point the latch at a temp dir and stub the outbound page.

    THE SEAM MOVED 2026-08-25 and this fixture moved with it. The cooldown was
    generalised from `target_naked` to any alert KIND so a second safety page
    (`stop_over_cover`) could not reintroduce the per-process latch by being a
    copy. `_alert_state_path(kind)` is the single owner now;
    `_target_naked_state_path` survives only as an alias, so patching the alias
    would leave the real path live and the test would assert against a wiring
    production does not have.

    Every behavioural assertion below is UNCHANGED — only the injection point.
    """
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    return tmp_path / "target_naked_alert_state.json"


def test_the_live_latch_filename_is_unchanged():
    """Renaming the file would orphan the latch the trader is holding NOW.

    A rename does not fail loudly: the new path simply does not exist, reads as
    "never fired", and silently re-arms a cooldown that is currently
    suppressing — the 2026-08-23 defect returning through the refactor that was
    meant to prevent it.
    """
    assert om._alert_state_path("target_naked").name == om._TARGET_NAKED_STATE_FILENAME


def _emit():
    return om._emit_target_naked_alert(
        account_id="ib_paper", symbol="MGC", size=95.0, target_qty=0.0,
        stop_qty=95.0, declared_tp=4393.02, trade_id=4773,
    )


def test_cooldown_survives_a_simulated_restart(latched, monkeypatch):
    """THE REGRESSION. A fresh process must still be suppressed."""
    assert _emit() is True, "first page must go out"
    assert _emit() is False, "same process, inside 6h -> suppressed"

    # Simulate a restart: every module global is rebuilt. Under the old
    # monotonic/in-memory latch this alone re-armed the alert.
    for name in dir(om):
        if "TARGET_NAKED" in name and isinstance(getattr(om, name), dict):
            getattr(om, name).clear()

    assert _emit() is False, (
        "a restart must NOT re-arm the page -- this is the defect that put "
        "202 CRITICALs on the operator's channel in 6.5 days"
    )


def test_alerts_again_once_the_cooldown_genuinely_elapses(latched, monkeypatch):
    assert _emit() is True
    real_time = om.time.time()
    monkeypatch.setattr(om.time, "time",
                        lambda: real_time + om._TARGET_NAKED_ALERT_COOLDOWN_S + 1)
    assert _emit() is True, "past 6h the condition must page again"


def test_unreadable_latch_alerts_rather_than_suppressing(latched, monkeypatch):
    """'We could not look' must never be read as 'already paged'."""
    assert _emit() is True
    latched.write_text("{ this is not json", encoding="utf-8")
    assert _emit() is True, (
        "an unreadable latch must fail LOUD -- suppressing a CRITICAL "
        "safety page on a file-read failure is the wrong direction"
    )


def test_future_dated_entry_does_not_suppress_forever(latched):
    """Clock skew must not mute the page indefinitely."""
    latched.parent.mkdir(parents=True, exist_ok=True)
    latched.write_text(json.dumps({"ib_paper|MGC": om.time.time() + 10 * 86400}),
                       encoding="utf-8")
    assert _emit() is True


def test_latch_does_not_use_monotonic(latched):
    """monotonic is meaningless across processes -- pin the wall clock."""
    import inspect
    # Strip comments first: the fix's own explanatory comment says the words
    # "time.monotonic()", and an annotation must never count as evidence for
    # the claim it annotates (the collapsed-state-guard override discipline).
    def _stripped(fn):
        """Executable source only — no comments, NO DOCSTRING.

        Line-stripping `#` alone is not enough: the generalised gate's own
        docstring says the words "time.monotonic()" while explaining why it
        does not use it, so a naive strip makes the explanation fail the
        assertion it explains. Round-trip through the AST with docstrings
        removed instead — prose can then neither satisfy nor break the claim.
        """
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:] or [ast.Pass()]
        return ast.unparse(ast.fix_missing_locations(tree))

    # The wall clock now lives in the shared gate, so assert against the
    # function that OWNS the latch — asserting `time.time() in` the emit path
    # after the move would have passed vacuously or failed for the wrong
    # reason. Both are checked: neither may reach for monotonic.
    gate = _stripped(om._cooldown_admits)
    assert "time.monotonic()" not in gate
    assert "time.time()" in gate
    assert "time.monotonic()" not in _stripped(om._emit_target_naked_alert)

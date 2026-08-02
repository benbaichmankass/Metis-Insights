"""`python -m ml <heavy> --help` must NOT acquire the trainer heavy-job flock.

BL-20260730-ML-CLI-HELP-TAKES-TRAINER-HEAVY-LOCK: main() used to take the shared
~5 GB flock for any heavy command (build-dataset / train) before it noticed the
invocation was a `--help`/`--version` documentation lookup, so on the 1-core
trainer a help print queued behind (and blocked) a running training cycle, and on
a clean queue-timeout the doc lookup exited SystemExit(75). These pin the fix:
a help/version invocation short-circuits the lock; a real run still takes it.
"""
from __future__ import annotations

import ml.cli as cli


def _record_lock(monkeypatch):
    """Patch the lock to a recorder + stub the sub-dispatch so no real work runs.
    Returns the list that receives one entry per lock acquisition."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "_acquire_heavy_lock", lambda label: calls.append(label) or object())
    monkeypatch.setattr(cli, "_HEAVY_COMMANDS", frozenset({"build-dataset", "train"}))
    # build-dataset dispatches to datasets_main; stub it so --help doesn't hit argparse's
    # own SystemExit and a real build never runs.
    monkeypatch.setattr(cli, "datasets_main", lambda argv: 0)
    return calls


def test_help_on_heavy_command_does_not_acquire_lock(monkeypatch):
    calls = _record_lock(monkeypatch)
    assert cli.main(["build-dataset", "--help"]) == 0
    assert calls == []  # a documentation lookup never queues behind a training run


def test_short_help_flag_skips_lock(monkeypatch):
    calls = _record_lock(monkeypatch)
    assert cli.main(["build-dataset", "-h"]) == 0
    assert calls == []


def test_version_flag_skips_lock(monkeypatch):
    calls = _record_lock(monkeypatch)
    assert cli.main(["build-dataset", "--version"]) == 0
    assert calls == []


def test_help_anywhere_in_argv_skips_lock(monkeypatch):
    # The flag need not be immediately after the subcommand.
    calls = _record_lock(monkeypatch)
    assert cli.main(["build-dataset", "--family", "x", "--help"]) == 0
    assert calls == []


def test_real_heavy_command_still_acquires_lock(monkeypatch):
    """The fix must NOT weaken the queue for a genuine run — no --help ⇒ lock taken."""
    calls = _record_lock(monkeypatch)
    assert cli.main(["build-dataset", "--family", "x"]) == 0
    assert calls == ["ml:build-dataset"]


def test_non_heavy_command_with_help_also_takes_no_lock(monkeypatch):
    # A non-heavy command never took the lock anyway; assert the short-circuit
    # didn't accidentally invert that.
    calls = _record_lock(monkeypatch)
    monkeypatch.setattr(cli, "_HEAVY_COMMANDS", frozenset({"train"}))  # build-dataset now non-heavy
    assert cli.main(["build-dataset", "--help"]) == 0
    assert calls == []

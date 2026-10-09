"""Tests for the single-manifest OOM streak tracker (src/utils/trainer_manifest_health).

BL-20260717-TRAINER-SINGLE-MANIFEST-OOM introduced a 7-day QUARANTINE after 3
consecutive OOM/timeouts. The operator's NO-HALT directive (2026-10-09, "There is
no halting.") retired it: a failing manifest is retried EVERY cycle, its OOM
diagnosis is recorded, ONE red flag is raised on the 3rd consecutive failure,
recovery is announced once, and a stale quarantine left by the old code is
ignored + removed on load.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from src.utils import trainer_manifest_health as H

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "manifest_oom_state.json"
    monkeypatch.setenv(H._ENV_STATE_FILE, str(p))
    monkeypatch.delenv(H._ENV_FLAG_AFTER, raising=False)
    return p


def _write_old_code_quarantine(path, key, days_ago=1, streak=3):
    """The exact row shape the pre-2026-10-09 code persisted for a quarantine."""
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    json.dump({"manifests": {key: {
        "consecutive_oom": streak, "last_reason": "137", "last_utc": stamp,
        "quarantined_at": stamp, "quarantine_count": 1}}}, open(path, "w"))
    return stamp


def test_never_skips_after_three_ooms_still_attempted(state_path):
    """(a) no standing block: after 3+ OOMs the next cycle still runs it."""
    for i in range(1, 7):
        r = H.record_oom_failure("ml/configs/big.yaml", "137")
        assert r["consecutive_oom"] == i
        d = H.retry_decision("ml/configs/big.yaml")
        assert d["skip"] is False
        assert d["reason"] == "retry_after_oom"
        assert d["consecutive_oom"] == i


def test_exactly_one_red_flag_on_third_failure(state_path):
    """(b) repeated failure → exactly ONE red flag, on the 3rd, then quiet."""
    flags = [H.record_oom_failure("big.yaml", "137")["red_flag"] for _ in range(8)]
    assert flags == [False, False, True, False, False, False, False, False]


def test_recovery_announced_once_and_new_episode_can_flag_again(state_path):
    for _ in range(3):
        H.record_oom_failure("big.yaml", "137")
    first = H.record_success("big.yaml")
    assert first == {"cleared": True, "recovered": True, "prior_streak": 3}
    # A second success is NOT another recovery announcement.
    assert H.record_success("big.yaml")["recovered"] is False
    # A fresh failure episode flags once more (bounded: once per episode).
    flags = [H.record_oom_failure("big.yaml", "137")["red_flag"] for _ in range(4)]
    assert flags == [False, False, True, False]


def test_success_below_threshold_is_not_a_recovery(state_path):
    H.record_oom_failure("big.yaml", "137")
    r = H.record_success("big.yaml")
    assert r["cleared"] is True and r["recovered"] is False
    assert H.retry_decision("big.yaml")["reason"] == "ok"


def test_diagnosis_attached_to_record(state_path):
    diag = H.build_diagnosis("137", timeout_s="1800",
                             stderr_tail="x" * 900 + "MemoryError")
    r = H.record_oom_failure("big.yaml", "137", diagnosis=diag)
    assert r["diagnosis"]["exit_code"] == 137
    assert "SIGKILL" in r["diagnosis"]["meaning"]
    assert r["diagnosis"]["timeout_seconds"] == 1800
    assert r["diagnosis"]["stderr_tail"].endswith("MemoryError")
    assert len(r["diagnosis"]["stderr_tail"]) == 500
    row = json.load(open(state_path))["manifests"]["big.yaml"]
    assert row["last_diagnosis"]["exit_code"] == 137
    assert "quarantined_at" not in row
    # timeout(1) SIGTERM meaning for 124
    assert "wall-clock" in H.build_diagnosis("124")["meaning"]


def test_stale_old_code_quarantine_is_ignored_and_removed(state_path, capsys):
    """(c) a stale quarantine record is ignored + removed on load, with a log line."""
    stamp = _write_old_code_quarantine(state_path, "big.yaml", days_ago=1)
    d = H.retry_decision("big.yaml")
    assert d["skip"] is False
    assert d["stale_quarantine_cleared"] == ["big.yaml"]
    row = json.load(open(state_path))["manifests"]["big.yaml"]
    assert "quarantined_at" not in row and "quarantine_count" not in row
    # The old code already escalated it, so it counts as flagged — no duplicate flag.
    assert row["flagged_at"] == stamp
    assert "stale quarantine" in capsys.readouterr().err
    # Cleared once; a second load has nothing left to clear.
    assert H.retry_decision("big.yaml")["stale_quarantine_cleared"] == []
    # Another OOM does not re-flag the already-escalated episode; success recovers it.
    assert H.record_oom_failure("big.yaml", "137")["red_flag"] is False
    assert H.record_success("big.yaml")["recovered"] is True


def test_quarantine_clear_env_not_needed(state_path, monkeypatch):
    """The old manual-clear knob is gone because nothing needs clearing."""
    monkeypatch.delenv("TRAINER_MANIFEST_QUARANTINE_CLEAR", raising=False)
    _write_old_code_quarantine(state_path, "big.yaml", days_ago=0)  # FRESH quarantine
    assert H.retry_decision("big.yaml")["skip"] is False


def test_flag_after_cannot_disable_the_flag(state_path, monkeypatch):
    monkeypatch.setenv(H._ENV_FLAG_AFTER, "0")
    flags = [H.record_oom_failure("big.yaml", "137")["red_flag"] for _ in range(4)]
    assert flags == [False, False, True, False]
    monkeypatch.setenv(H._ENV_FLAG_AFTER, "2")
    H.record_success("big.yaml")
    flags = [H.record_oom_failure("big.yaml", "137")["red_flag"] for _ in range(3)]
    assert flags == [False, True, False]


def test_manifest_key_normalizes_path_vs_basename(state_path):
    H.record_oom_failure("ml/configs/big.yaml", "137")
    r = H.record_oom_failure("big.yaml", "137")   # same row via basename
    assert r["consecutive_oom"] == 2


def test_fail_open_on_bad_state(tmp_path, monkeypatch):
    monkeypatch.setenv(H._ENV_STATE_FILE, str(tmp_path / "afile"))
    (tmp_path / "afile").write_text("not json")
    assert H.retry_decision("big.yaml")["skip"] is False


def test_back_compat_alias(state_path):
    assert H.quarantine_decision is H.retry_decision


def test_cli_exit_codes(state_path, tmp_path):
    env = dict(os.environ)
    err = tmp_path / "train.err"
    err.write_text("Traceback ...\nMemoryError: cannot allocate\n")

    def run(*args):
        return subprocess.run([sys.executable, "-m", "src.utils.trainer_manifest_health", *args],
                              cwd=_REPO, env=env, capture_output=True, text=True)
    assert run("record-oom", "big.yaml", "137", "1800", str(err)).returncode == 0
    assert run("record-oom", "big.yaml", "137", "1800", str(err)).returncode == 0
    third = run("record-oom", "big.yaml", "137", "1800", str(err))
    assert third.returncode == 20                       # the ONE red flag
    payload = json.loads(third.stdout)
    assert "MemoryError" in payload["diagnosis"]["stderr_tail"]
    assert run("record-oom", "big.yaml", "137").returncode == 0   # no repeat flag
    # decide NEVER returns a skip code
    dec = run("decide", "big.yaml")
    assert dec.returncode == 0 and json.loads(dec.stdout)["skip"] is False
    assert run("record-success", "big.yaml").returncode == 30    # recovered, once
    assert run("record-success", "big.yaml").returncode == 0
    assert run("decide", "big.yaml").returncode == 0

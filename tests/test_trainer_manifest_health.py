"""Tests for the single-manifest OOM quarantine (src/utils/trainer_manifest_health).

BL-20260717-TRAINER-SINGLE-MANIFEST-OOM: a manifest that OOMs ALONE (can't fit
the 5 GB cgroup even without contention) must, after N consecutive OOM/timeout
failures, be quarantined (skipped) + loudly escalated instead of retried every
cycle forever — with a self-healing recheck so a landed fix auto-clears it.
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
    for k in (H._ENV_QUARANTINE_AFTER, H._ENV_RECHECK_DAYS, H._ENV_CLEAR):
        monkeypatch.delenv(k, raising=False)
    return p


def _backdate_quarantine(path, key, days):
    state = json.load(open(path))
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    state["manifests"][key]["quarantined_at"] = old
    json.dump(state, open(path, "w"))


def test_below_threshold_not_quarantined(state_path):
    r1 = H.record_oom_failure("ml/configs/big.yaml", "137")
    assert r1["consecutive_oom"] == 1 and not r1["quarantined"]
    r2 = H.record_oom_failure("ml/configs/big.yaml", "137")
    assert r2["consecutive_oom"] == 2 and not r2["quarantined"]
    # not skipped while under threshold
    assert H.quarantine_decision("ml/configs/big.yaml")["skip"] is False


def test_threshold_trips_quarantine_and_skips(state_path):
    for _ in range(3):
        r = H.record_oom_failure("big.yaml", "137")
    assert r["quarantined"] is True
    assert r["just_tripped"] is True          # crossed on the 3rd
    d = H.quarantine_decision("big.yaml")
    assert d["skip"] is True and d["reason"] == "quarantined_oom"
    assert H.quarantined_manifests() and H.quarantined_manifests()[0]["manifest"] == "big.yaml"


def test_success_clears_streak_and_quarantine(state_path):
    for _ in range(3):
        H.record_oom_failure("big.yaml", "137")
    assert H.quarantine_decision("big.yaml")["skip"] is True
    res = H.record_success("big.yaml")
    assert res["cleared"] is True
    assert H.quarantine_decision("big.yaml")["skip"] is False
    assert H.quarantined_manifests() == []


def test_recheck_due_lets_one_through_then_requarantines(state_path, monkeypatch):
    monkeypatch.setenv(H._ENV_RECHECK_DAYS, "7")
    for _ in range(3):
        H.record_oom_failure("big.yaml", "137")
    # fresh quarantine → skip
    assert H.quarantine_decision("big.yaml")["skip"] is True
    # age it past the recheck window → decision lets it run
    _backdate_quarantine(state_path, "big.yaml", days=8)
    d = H.quarantine_decision("big.yaml")
    assert d["skip"] is False and d["recheck_due"] is True
    # the recheck OOMs again → re-quarantine, loudly (just_tripped again)
    r = H.record_oom_failure("big.yaml", "137")
    assert r["quarantined"] is True and r["just_tripped"] is True


def test_disabled_never_quarantines(state_path, monkeypatch):
    monkeypatch.setenv(H._ENV_QUARANTINE_AFTER, "0")
    for _ in range(6):
        r = H.record_oom_failure("big.yaml", "137")
    assert r["quarantined"] is False
    assert H.quarantine_decision("big.yaml")["skip"] is False


def test_env_clear_releases_quarantine(state_path, monkeypatch):
    for _ in range(3):
        H.record_oom_failure("big.yaml", "137")
    assert H.quarantine_decision("big.yaml")["skip"] is True
    monkeypatch.setenv(H._ENV_CLEAR, "big.yaml")
    # decision honours the one-shot clear
    assert H.quarantine_decision("big.yaml")["skip"] is False


def test_manifest_key_normalizes_path_vs_basename(state_path):
    H.record_oom_failure("ml/configs/big.yaml", "137")
    r = H.record_oom_failure("big.yaml", "137")   # same row via basename
    assert r["consecutive_oom"] == 2


def test_fail_open_on_bad_state_dir(tmp_path, monkeypatch):
    # Point the state file at a path whose parent can't be a dir → save() swallows,
    # decision fails open to "run it".
    monkeypatch.setenv(H._ENV_STATE_FILE, str(tmp_path / "afile"))
    (tmp_path / "afile").write_text("not json")
    d = H.quarantine_decision("big.yaml")
    assert d["skip"] is False


def test_cli_decide_and_record_exit_codes(state_path):
    env = dict(os.environ)
    def run(*args):
        return subprocess.run([sys.executable, "-m", "src.utils.trainer_manifest_health", *args],
                              cwd=_REPO, env=env, capture_output=True, text=True)
    # two OOMs: exit 0 (not tripped)
    assert run("record-oom", "big.yaml", "137").returncode == 0
    assert run("record-oom", "big.yaml", "137").returncode == 0
    # third OOM trips quarantine: exit 20
    assert run("record-oom", "big.yaml", "137").returncode == 20
    # decide now says skip: exit 10
    assert run("decide", "big.yaml").returncode == 10
    # success clears: decide exit 0
    assert run("record-success", "big.yaml").returncode == 0
    assert run("decide", "big.yaml").returncode == 0

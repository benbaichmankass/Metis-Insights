"""The VM-side hourly digest carrier — MI-83, half (2).

The digest's declared cadence is hourly and its ACTUAL cadence was 5 firings a
day at :19, :10, :33 and :47, because `.github/workflows/work-digest.yml` rides
a GitHub Actions cron. This moves it onto `ict-work-digest.timer`.

What these assert is the part reading the code does not settle: that the new
carrier cannot be capped by the workflow's DAY latch, that it stamps a receipt
on every outcome including the failures, and that an unresolvable window is
never reported as a quiet hour.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "_wdn_under_test", ROOT / "scripts" / "ops" / "work_digest_now.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "RECEIPT", tmp_path / "work_digest_receipt.json")
    return mod


# --- the latch that would have silently capped this at one a day -------------

def test_the_runner_does_not_share_work_digests_day_granular_latch(monkeypatch, tmp_path):
    """`work_digest --write` latches on `lastDigestDay`. That latch is INERT in
    CI (runtime_logs/ is gitignored, so a runner never has the file) and would
    be LIVE on the VM — capping an hourly carrier at one digest per day, with no
    error and no sign anything was suppressed."""
    mod = _load(monkeypatch, tmp_path)
    from scripts.ops import work_digest as wd

    # Asserted BEHAVIOURALLY, not by grepping the source: plant a day latch
    # saying today's digest already went, and require the runner to send anyway.
    # A source grep would pass on a module that imported the latch and used it.
    day_latch = tmp_path / "work_digest_state.json"
    day_latch.write_text(json.dumps(
        {"lastDigestDay": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%d")}))
    monkeypatch.setattr(wd, "STATE", day_latch)

    sent = []
    sys.modules["send_ping"] = type(sys)("send_ping")
    sys.modules["send_ping"].enqueue = (
        lambda body, **k: sent.append(body) or Path("queued")
    )
    assert mod.run() == 0
    assert len(sent) == 1, (
        "the workflow's DAY latch must not cap the hourly carrier — that would "
        "silently reduce 24 digests a day to 1, with no error"
    )
    assert mod.RECEIPT != day_latch


def test_the_hour_latch_suppresses_a_second_send_in_the_same_hour(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    sent = []
    mod.RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    mod.RECEIPT.write_text(json.dumps({"lastSentHour": hour}))
    monkeypatch.setitem(sys.modules, "send_ping",
                        type(sys)("send_ping"))
    sys.modules["send_ping"].enqueue = lambda *a, **k: sent.append(a) or Path("x")
    assert mod.run() == 0
    assert sent == [], "a second send in the same hour is a duplicate"
    assert json.loads(mod.RECEIPT.read_text())["outcome"] == "skipped_hour_latch"


def test_force_overrides_the_hour_latch(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    mod.RECEIPT.write_text(json.dumps({"lastSentHour": hour}))
    sent = []
    sys.modules["send_ping"] = type(sys)("send_ping")
    sys.modules["send_ping"].enqueue = (
        lambda body, **k: sent.append(body) or Path("queued")
    )
    assert mod.run(force=True) == 0
    assert len(sent) == 1


# --- three states, not two --------------------------------------------------

def test_an_unresolvable_window_is_not_a_quiet_hour(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_resolve_base", lambda *a, **k: (None, "unresolved"))
    sent = []
    sys.modules["send_ping"] = type(sys)("send_ping")
    sys.modules["send_ping"].enqueue = lambda *a, **k: sent.append(a) or Path("x")
    rc = mod.run()
    assert rc == 1, "systemd must mark the unit failed, not report success"
    assert sent == [], "we did not look — send nothing rather than a clean bill"
    assert json.loads(mod.RECEIPT.read_text())["outcome"] == "window_unresolved"


def test_resolve_base_distinguishes_its_three_bases():
    spec = importlib.util.spec_from_file_location(
        "_wdn_bases", ROOT / "scripts" / "ops" / "work_digest_now.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    base, basis = mod._resolve_base()
    assert basis in ("window", "root_fallback") and base
    assert mod._resolve_base("definitely-not-a-ref-000") == (None, "unresolved")


# --- the receipt ------------------------------------------------------------

def test_a_failed_enqueue_still_stamps_a_receipt(monkeypatch, tmp_path):
    """A receipt written only on success cannot tell a DEAD timer from a
    FAILING run — which is the exact question this carrier exists to answer."""
    mod = _load(monkeypatch, tmp_path)
    sys.modules["send_ping"] = type(sys)("send_ping")

    def _boom(*a, **k):
        raise OSError("inbox unwritable")

    sys.modules["send_ping"].enqueue = _boom
    assert mod.run() == 1
    got = json.loads(mod.RECEIPT.read_text())
    assert got["outcome"] == "enqueue_failed"
    assert "lastSentHour" in got and got["lastSentHour"] is None


def test_the_hour_only_advances_after_a_successful_enqueue(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    sys.modules["send_ping"] = type(sys)("send_ping")
    sys.modules["send_ping"].enqueue = lambda body, **k: Path("queued")
    assert mod.run() == 0
    got = json.loads(mod.RECEIPT.read_text())
    assert got["outcome"] == "sent" and got["lastSentHour"] == got["hour"]


def test_an_unreadable_receipt_sends_rather_than_suppresses(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    mod.RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    mod.RECEIPT.write_text("{not json")
    sent = []
    sys.modules["send_ping"] = type(sys)("send_ping")
    sys.modules["send_ping"].enqueue = (
        lambda body, **k: sent.append(body) or Path("queued")
    )
    assert mod.run() == 0
    assert len(sent) == 1, "a broken latch must announce itself as a duplicate, not silence"


# --- the units and their read surface ---------------------------------------

def test_both_units_exist_so_the_installer_glob_picks_them_up():
    # scripts/install_systemd_units.sh globs deploy/*.timer; no edit is needed
    # there, and none was made.
    assert (DEPLOY / "ict-work-digest.service").is_file()
    assert (DEPLOY / "ict-work-digest.timer").is_file()
    timer = (DEPLOY / "ict-work-digest.timer").read_text()
    assert "OnCalendar=hourly" in timer
    assert "Unit=ict-work-digest.service" in timer


def test_the_service_carries_no_data_dir_dropin_reference():
    """The receipt reader is repo_root()-anchored; a data-dir drop-in would move
    the writer and leave the reader on an eternally-absent path."""
    svc = (DEPLOY / "ict-work-digest.service").read_text()
    directives = [ln for ln in svc.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("data-dir" in ln for ln in directives), (
        "a data-dir drop-in would move the writer off the path diag reads"
    )
    assert "DELIBERATELY NO data-dir" in svc, "and the omission must say why"


def test_the_cadence_is_readable_not_merely_declared():
    """A timer nobody can check is how the erratic Actions cron went unnoticed
    for a day. Both units must be reportable and the receipt must be fetchable."""
    diag = (ROOT / "src" / "web" / "api" / "routers" / "diag.py").read_text()
    assert '"ict-work-digest.service"' in diag
    assert '"ict-work-digest.timer"' in diag
    assert '"work_digest_receipt": _WORK_DIGEST_RECEIPT' in diag
    assert 'Path(repo_root()) / "runtime_logs" / "work_digest_receipt.json"' in diag


def test_self_test_passes():
    spec = importlib.util.spec_from_file_location(
        "_wdn_selftest", ROOT / "scripts" / "ops" / "work_digest_now.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert mod._self_test() == 0

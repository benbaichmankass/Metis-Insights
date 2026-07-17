"""Smoke tests for `scripts/ops/run_training_cycle.sh` (S-AI-WS9 FU).

The script's primary contract is its exit codes + JSONL event
stream. Full end-to-end testing requires a real repo + venv + the
ml.cli surface, which we exercise via the unit tests on those
modules — this file covers the bash-level env-error guardrail.
The happy path is exercised against a real VM during operator
acceptance.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "run_training_cycle.sh"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the script with a controlled env."""
    full_env = {**os.environ, **env}
    return subprocess.run(
        ["/bin/bash", str(_SCRIPT)],
        env=full_env, capture_output=True, text=True, timeout=30,
    )


def _last_event(stdout: str) -> dict:
    """Pluck the last well-formed JSON event from stdout."""
    last = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            last = json.loads(line)
        except Exception:
            continue
    return last or {}


class TestEnvErrors:
    def test_missing_repo_exits_2(self, tmp_path: Path):
        # Point REPO_ROOT at a non-git directory.
        env = {
            "REPO_ROOT": str(tmp_path),
            "TRAINING_LOG_PATH": str(tmp_path / "log.jsonl"),
        }
        r = _run(env)
        assert r.returncode == 2
        ev = _last_event(r.stdout)
        assert ev["status"] == "env_error"
        assert "not a git repo" in ev["detail"]

    def test_jsonl_written_to_log_path(self, tmp_path: Path):
        log = tmp_path / "log.jsonl"
        env = {
            "REPO_ROOT": str(tmp_path),  # also triggers env_error
            "TRAINING_LOG_PATH": str(log),
        }
        _run(env)
        # The env_error event is also persisted to disk.
        assert log.is_file()
        lines = [line for line in log.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["status"] == "env_error"


def test_bash_syntax_valid():
    """`bash -n` should pass on the script."""
    r = subprocess.run(
        ["/bin/bash", "-n", str(_SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# Checkpoint / resume (2026-07-02, BL-20260702-TRAINER-OOM)
#
# A minimal but real fixture: a real git repo (with a local bare "origin" so
# `git fetch origin main` succeeds), the best-effort helper scripts stubbed
# to no-ops, and a PATH-level `python` shim that fakes `python -m ml train`
# (real python3 for everything else) — the same shape the module docstring
# above says would be needed for true end-to-end coverage, scoped just to
# the checkpoint/resume contract rather than the full ml.cli surface.
# ---------------------------------------------------------------------------


def _init_fixture_repo(root: Path, manifests: list[str]) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "ml" / "configs").mkdir(parents=True)
    (root / "scripts" / "ops").mkdir(parents=True)
    (root / "runtime_logs").mkdir()
    for m in manifests:
        (root / m).write_text(f"name: {Path(m).stem}\n", encoding="utf-8")
    for helper in ("sync_trainer_data.sh", "build_trainer_datasets.sh",
                    "publish_trainer_mirror.sh", "fit_calibrators.sh"):
        p = root / "scripts" / "ops" / helper
        p.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    script_dst = root / "scripts" / "ops" / "run_training_cycle.sh"
    script_dst.write_text(_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script_dst.chmod(0o755)
    # run_training_cycle.sh sources the shared heavy-job queue helper (the
    # trainer resource protocol, 2026-07-17) — copy the REAL file into the
    # scaffold so the `. _trainer_heavy_lock.sh` + take_trainer_heavy_lock()
    # resolve. It's uncontended in these single-invocation tests, so the lock
    # acquires immediately; the cycle self-lock (checked first) still owns the
    # concurrency contract these tests exercise.
    lock_src = _REPO_ROOT / "scripts" / "ops" / "_trainer_heavy_lock.sh"
    lock_dst = root / "scripts" / "ops" / "_trainer_heavy_lock.sh"
    lock_dst.write_text(lock_src.read_text(encoding="utf-8"), encoding="utf-8")
    lock_dst.chmod(0o755)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "activate").write_text("", encoding="utf-8")
    subprocess.run(["git", "checkout", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    bare = Path(tempfile.mkdtemp(prefix="origin-")) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=root, check=True)


_FAKE_PYTHON_SHIM = """#!/usr/bin/env bash
if [ "$1" = "-m" ] && [ "$2" = "ml" ] && [ "$3" = "train" ]; then
  manifest="$4"
  if [ "$manifest" = "${KILL_TARGET:-}" ]; then
    kill -9 $$
  fi
  echo '{"model_id": "'"$(basename "$manifest")"'-model"}'
  exit 0
fi
exec /usr/bin/env python3 "$@"
"""


def _fixture_env(root: Path, manifests: list[str], extra: dict[str, str] | None = None) -> dict:
    fakebin = Path(tempfile.mkdtemp(prefix="fakebin-"))
    shim = fakebin / "python"
    shim.write_text(_FAKE_PYTHON_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ.get('PATH', '')}",
        "REPO_ROOT": str(root),
        "TRAINING_MANIFESTS": " ".join(manifests),
    }
    env.update(extra or {})
    return env


def _progress_file(root: Path) -> dict:
    files = list((root / "runtime_logs" / "trainer").glob("cycle_progress_*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


class TestCheckpointResume:
    MANIFESTS = ["ml/configs/manifest-a.yaml", "ml/configs/manifest-b.yaml",
                 "ml/configs/manifest-c.yaml"]

    def test_second_same_day_run_is_a_near_noop(self, tmp_path: Path):
        _init_fixture_repo(tmp_path, self.MANIFESTS)
        env = _fixture_env(tmp_path, self.MANIFESTS)

        r1 = subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert r1.returncode == 0, r1.stdout + r1.stderr
        progress = _progress_file(tmp_path)
        assert progress["status"] == "complete"
        assert all(v["status"] == "done" for v in progress["manifests"].values())

        r2 = subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert '"status":"cycle_already_complete"' in r2.stdout
        assert '"to_run":0' in r2.stdout

    def test_killed_subprocess_is_retried_on_resume_others_are_not(self, tmp_path: Path):
        _init_fixture_repo(tmp_path, self.MANIFESTS)
        env = _fixture_env(tmp_path, self.MANIFESTS,
                            {"KILL_TARGET": "ml/configs/manifest-b.yaml"})

        r1 = subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        # b's subprocess was SIGKILL'd (rc=137) — the cycle still completes
        # (existing behaviour: one failed manifest doesn't abort the loop)
        # but overall_rc reflects the failure.
        assert r1.returncode == 1, r1.stdout + r1.stderr
        progress = _progress_file(tmp_path)
        assert progress["manifests"]["ml/configs/manifest-a.yaml"]["status"] == "done"
        assert progress["manifests"]["ml/configs/manifest-b.yaml"]["status"] == "failed"
        assert progress["manifests"]["ml/configs/manifest-c.yaml"]["status"] == "done"

        env2 = _fixture_env(tmp_path, self.MANIFESTS)  # no KILL_TARGET this time
        r2 = subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                             cwd=tmp_path, env=env2, capture_output=True, text=True, timeout=30)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert '"to_run":1' in r2.stdout
        assert "manifest-b.yaml" in r2.stdout
        assert "manifest-a.yaml" not in r2.stdout  # a was already done, not re-run
        progress2 = _progress_file(tmp_path)
        assert all(v["status"] == "done" for v in progress2["manifests"].values())

    def test_force_restart_ignores_prior_progress(self, tmp_path: Path):
        _init_fixture_repo(tmp_path, self.MANIFESTS)
        env = _fixture_env(tmp_path, self.MANIFESTS)
        subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert all(v["status"] == "done" for v in _progress_file(tmp_path)["manifests"].values())

        env_force = _fixture_env(tmp_path, self.MANIFESTS,
                                  {"TRAINING_CYCLE_FORCE_RESTART": "1"})
        r2 = subprocess.run(["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                             cwd=tmp_path, env=env_force, capture_output=True, text=True, timeout=30)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert "cycle_resumed" not in r2.stdout  # fresh start, nothing to resume
        for m in self.MANIFESTS:
            assert m in r2.stdout  # every manifest actually re-ran

    def test_concurrent_invocations_second_one_locks_out(self, tmp_path: Path):
        _init_fixture_repo(tmp_path, self.MANIFESTS[:1])
        # Make the pre-training helper scripts slow enough that the two
        # invocations are guaranteed to overlap.
        for helper in ("sync_trainer_data.sh", "build_trainer_datasets.sh"):
            p = tmp_path / "scripts" / "ops" / helper
            p.write_text("#!/usr/bin/env bash\nsleep 1\nexit 0\n", encoding="utf-8")
            p.chmod(0o755)
        env = _fixture_env(tmp_path, self.MANIFESTS[:1])

        import threading
        results: dict[str, subprocess.CompletedProcess] = {}

        def _run(key: str) -> None:
            results[key] = subprocess.run(
                ["/bin/bash", "scripts/ops/run_training_cycle.sh"],
                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
            )

        t1 = threading.Thread(target=_run, args=("first",))
        t2 = threading.Thread(target=_run, args=("second",))
        t1.start()
        import time
        time.sleep(0.2)
        t2.start()
        t1.join()
        t2.join()

        outputs = [results["first"].stdout, results["second"].stdout]
        locked = [o for o in outputs if "cycle_locked" in o]
        completed = [o for o in outputs if "cycle_end" in o]
        assert len(locked) == 1, outputs
        assert len(completed) == 1, outputs

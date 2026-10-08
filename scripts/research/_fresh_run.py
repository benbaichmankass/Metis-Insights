"""Run a harness subprocess and return its output file ONLY if this run wrote it.

CA-B04 (2026-09-27): validate_corr / validate_robustness / sweep_wave1_families /
sweep_wave2_momentum ran a harness with ``subprocess.run(..., capture_output=True)``
and then read ``--json`` / ``--emit-trades`` output from a FIXED path, ignoring the
return code. A harness that crashed, timed out or was killed left the PREVIOUS
run's file in place, and the sweep recorded those numbers as ``ok: True`` for the
new parameters. ``run_fresh`` deletes the target first and refuses a non-zero exit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class HarnessRunError(RuntimeError):
    pass


def run_fresh(cmd: Sequence[str], out_path, *, cwd=None, timeout: int = 240) -> Path:
    """Run ``cmd``; return ``out_path`` iff the run exited 0 and wrote it anew.

    Raises HarnessRunError (including on timeout) otherwise. Any file already at
    ``out_path`` is removed BEFORE the run, so it can never be mistaken for output.
    """
    out = Path(out_path)
    out.unlink(missing_ok=True)
    try:
        cp = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HarnessRunError(f"timeout after {timeout}s") from exc
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip().splitlines()[-1:] or [""]
        raise HarnessRunError(f"exit {cp.returncode}: {tail[0][:100]}")
    if not out.exists():
        raise HarnessRunError("exit 0 but no output file written")
    return out

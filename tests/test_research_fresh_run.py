"""Regression for CA-B04 stale-JSON reuse in the validate_* / sweep_wave* scripts."""
import os
import re
import sys

import pytest

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "..", "scripts", "research")
sys.path.insert(0, RES)

from _fresh_run import HarnessRunError, run_fresh  # noqa: E402


def test_stale_output_is_not_returned_when_harness_fails(tmp_path):
    out = tmp_path / "r.json"
    out.write_text('{"net_total_r": 99.0}')            # a PREVIOUS run's result
    with pytest.raises(HarnessRunError):
        run_fresh([sys.executable, "-c", "import sys; sys.exit(3)"], out)
    assert not out.exists()


def test_zero_exit_without_output_is_an_error(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("{}")
    with pytest.raises(HarnessRunError):
        run_fresh([sys.executable, "-c", "pass"], out)


def test_timeout_is_an_error_and_clears_stale(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("{}")
    with pytest.raises(HarnessRunError):
        run_fresh([sys.executable, "-c", "import time; time.sleep(5)"], out, timeout=1)
    assert not out.exists()


def test_success_returns_fresh_output(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("stale")
    code = f"open({str(out)!r}, 'w').write('fresh')"
    assert run_fresh([sys.executable, "-c", code], out).read_text() == "fresh"


@pytest.mark.parametrize("name", ["validate_corr", "validate_robustness",
                                  "sweep_wave1_families", "sweep_wave2_momentum"])
def test_scripts_use_run_fresh_not_bare_subprocess(name):
    src = open(os.path.join(RES, name + ".py")).read()
    assert "run_fresh(" in src
    assert not re.search(r"subprocess\.run\(", src)

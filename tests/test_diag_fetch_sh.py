"""Candidate-ordering tests for `scripts/ops/diag_fetch.sh`.

WHY THIS EXISTS (2026-08-20). The script already carried a "stale-env
self-heal" that rewrote the retired micro `158.178.210.252` to the raw live IP
`141.145.193.91` — and the sandbox proxy allowlists by SCHEME+HOSTNAME, so a
plain-http call to a raw IP is DROPPED at the default `Trusted` level. The heal
therefore turned a dead host into an unreachable one, timed out, exited 3, and
sent every session down the 30-60s issue relay while logging that it had healed
the setting. Measured in one session, seconds apart:

    http://141.145.193.91:8001/api/health   -> curl 28 timeout
    https://ict-bot.duckdns.org/api/health  -> 200 {"ok":true}

Nothing tested it, because testing it needs the network. These tests shim
`curl` on PATH so the ORDER of attempted URLs is asserted directly — no egress,
no live VM, deterministic.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "diag_fetch.sh"
_CANONICAL = "https://ict-bot.duckdns.org"
_RETIRED = "http://158.178.210.252:8001"
_RAW_LIVE = "http://141.145.193.91:8001"


def _run(tmp_path: Path, *, base: str | None, token: str | None = "tok",
         succeed_on: str | None = None, http_status: str | None = None,
         path: str = "version"):
    """Run the script with a shimmed `curl`; return (proc, attempted_urls).

    `succeed_on` is a substring — the shim exits 0 for the first URL containing
    it and non-zero otherwise, so a candidate list can be walked deterministically.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "urls.txt"
    shim = bindir / "curl"
    # The shim must HONOUR -o and -w, because the script reads the body from
    # the -o file and the HTTP status from -w's stdout. A shim that echoes the
    # body to stdout instead would let the script's own output path go
    # untested — the "world that does not exist" failure this repo keeps
    # paying for. `http_status` lets a test drive the answered-vs-unreachable
    # split: a status means the host ANSWERED, 000 means it never did.
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "url=\"${@: -1}\"\n"
        "out=\"\"\n"
        "prev=\"\"\n"
        "for a in \"$@\"; do\n"
        "  if [ \"$prev\" = \"-o\" ]; then out=\"$a\"; fi\n"
        "  prev=\"$a\"\n"
        "done\n"
        f"echo \"$url\" >> {log}\n"
        + (f'if [[ "$url" == *"{succeed_on}"* ]]; then\n'
           '  [ -n "$out" ] && printf \'{"ok":true}\' > "$out"\n'
           '  echo -n 200\n'
           '  exit 0\n'
           'fi\n'
           if succeed_on else "")
        + (f'echo -n {http_status}\nexit 22\n' if http_status else "echo -n 000\nexit 7\n")
    )
    shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.pop("DIAG_BASE_URL", None)
    env.pop("DIAG_READ_TOKEN", None)
    if base is not None:
        env["DIAG_BASE_URL"] = base
    if token is not None:
        env["DIAG_READ_TOKEN"] = token

    proc = subprocess.run([str(_SCRIPT), path], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT)
    attempted = log.read_text().splitlines() if log.exists() else []
    return proc, attempted


@pytest.mark.parametrize("base", [_RETIRED, _RAW_LIVE, "http://example.invalid:8001"])
def test_canonical_https_is_tried_first_for_any_unreachable_base(tmp_path, base):
    """THE REGRESSION. A plain-http / known-VM-IP base must not be tried first.

    This is the exact defect: the old code rewrote to the raw IP and tried ONLY
    that. If someone restores a single-candidate rewrite, this fails.
    """
    _proc, attempted = _run(tmp_path, base=base)
    assert attempted, "curl was never invoked"
    assert attempted[0].startswith(_CANONICAL), (
        f"first attempt was {attempted[0]!r}, expected the canonical HTTPS base"
    )


def test_the_configured_base_is_still_tried_as_a_fallback(tmp_path):
    """Canonical-first must not mean canonical-only — a Full-network session
    with a deliberately-set base should still reach it."""
    _proc, attempted = _run(tmp_path, base=_RAW_LIVE)
    assert any(u.startswith(_RAW_LIVE) for u in attempted), attempted


def test_a_deliberate_https_base_keeps_priority(tmp_path):
    """Someone who set their own https host meant it; do not override them."""
    mine = "https://diag.example.test"
    _proc, attempted = _run(tmp_path, base=mine)
    assert attempted[0].startswith(mine), attempted
    assert any(u.startswith(_CANONICAL) for u in attempted), "canonical lost as fallback"


def test_unset_base_url_still_attempts_the_canonical_host(tmp_path):
    """Previously the gate demanded BOTH vars, so a session holding a good
    bearer but no base URL took the relay for no reason."""
    proc, attempted = _run(tmp_path, base=None)
    assert attempted, f"no attempt made; stderr={proc.stderr!r}"
    assert attempted[0].startswith(_CANONICAL)


def test_missing_bearer_exits_3_without_calling_curl(tmp_path):
    """The token IS genuinely required — no point dialling without it."""
    proc, attempted = _run(tmp_path, base=_CANONICAL, token=None)
    assert proc.returncode == 3
    assert attempted == []


def test_first_failure_falls_through_to_a_working_candidate(tmp_path):
    proc, attempted = _run(tmp_path, base=_RAW_LIVE, succeed_on=_RAW_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert len(attempted) >= 2, attempted          # canonical tried, then the base
    assert "served by" in proc.stderr              # provenance, on stderr not stdout


def test_all_candidates_failing_exits_3_and_names_what_it_tried(tmp_path):
    proc, attempted = _run(tmp_path, base=_RETIRED)
    assert proc.returncode == 3
    assert _CANONICAL in proc.stderr
    # It must name the per-candidate STAGE, not a menu of guessed causes.
    assert "unreachable" in proc.stderr, proc.stderr


def test_a_candidate_is_never_dialled_twice(tmp_path):
    """De-dup: configured base == canonical must not produce two attempts."""
    _proc, attempted = _run(tmp_path, base=_CANONICAL)
    assert len(attempted) == len(set(attempted)), attempted


def test_the_json_goes_to_stdout_and_the_provenance_does_not(tmp_path):
    """A consumer pipes stdout into a JSON parser; a 'served by' line there
    would corrupt it."""
    proc, _ = _run(tmp_path, base=_CANONICAL, succeed_on=_CANONICAL)
    assert proc.returncode == 0
    assert proc.stdout.strip() == '{"ok":true}'
    assert "served by" not in proc.stdout


# --------------------------------------------------------------------------
# A FAILURE MUST NAME THE STAGE IT REACHED, NOT A MENU OF GUESSES.
#
# Added 2026-08-20 after the script reported "web-api down, bearer wrong, or
# egress blocked" for a request that got a clean HTTP 404 from a host that was
# serving perfectly. None of the three named causes was true, and the real one
# — the caller's own path form — was in hand as curl's exit status. That is
# UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A (CLAUDE.md § "Diagnostic
# provenance"): a failure message naming a cause no code path tested. It cost a
# diagnostic round-trip and would have sent the next reader to the issue relay
# to work around a VM that was fine.
# --------------------------------------------------------------------------

def test_an_http_404_is_reported_as_ANSWERED_not_as_an_outage(tmp_path):
    """The single most misleading case: the host is UP and the request is wrong.

    Reporting this as 'web-api down' points the reader at the wrong system.
    """
    proc, _ = _run(tmp_path, base=_CANONICAL, http_status="404")
    assert proc.returncode == 3
    assert "ANSWERED" in proc.stderr, proc.stderr
    assert "answered_404" in proc.stderr, proc.stderr
    # And it must NOT assert an outage it never observed.
    assert "web-api down" not in proc.stderr
    # Assert on the CLASSIFICATION, not the bare word: the summary's guidance
    # sentence legitimately contains "unreachable" while classifying nothing
    # as such. Checking the word alone failed a correct message.
    assert "-> unreachable" not in proc.stderr, proc.stderr


def test_a_401_names_the_bearer_and_says_it_is_not_an_outage(tmp_path):
    proc, _ = _run(tmp_path, base=_CANONICAL, http_status="401")
    assert proc.returncode == 3
    assert "answered_401" in proc.stderr, proc.stderr
    assert "bearer" in proc.stderr.lower(), proc.stderr
    assert "NOT an outage" in proc.stderr, proc.stderr


def test_only_a_no_status_failure_is_called_unreachable(tmp_path):
    """`unreachable` is the ONLY verdict that implicates the network.

    The shim returns 000 here (never reached a host), which is the one case
    where the issue relay is the right next move.
    """
    proc, _ = _run(tmp_path, base=_CANONICAL)
    assert proc.returncode == 3
    assert "unreachable" in proc.stderr, proc.stderr
    assert "ANSWERED" not in proc.stderr, proc.stderr


def test_the_summary_reports_a_stage_per_candidate(tmp_path):
    """Two candidates failing DIFFERENTLY must not collapse to one verdict."""
    proc, _ = _run(tmp_path, base=_RETIRED, http_status="404")
    assert proc.returncode == 3
    for base in (_CANONICAL, _RETIRED):
        assert base in proc.stderr, proc.stderr
    assert proc.stderr.count("answered_404") >= 2, proc.stderr


# --------------------------------------------------------------------------
# THE PATH FORM IS NOT A TRAP TO REMEMBER.
#
# The base already carries `/api/diag/`, so passing the FULL route built
# `.../api/diag//api/diag/version` -> 404. Measured 2026-08-20: a session did
# exactly that and was told the web-api was down. No real diag path begins with
# `api/diag/`, so there is no ambiguity to preserve — accept both.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("given", [
    "version",
    "/version",
    "api/diag/version",
    "/api/diag/version",
])
def test_every_path_form_resolves_to_one_url(tmp_path, given):
    _proc, attempted = _run(tmp_path, base=_CANONICAL, succeed_on=_CANONICAL,
                            path=given)
    assert attempted, "curl was never called"
    assert attempted[0] == f"{_CANONICAL}/api/diag/version", attempted


def test_the_doubled_path_can_no_longer_be_built(tmp_path):
    """The specific regression: `/api/diag//api/diag/...` must be unreachable."""
    _proc, attempted = _run(tmp_path, base=_CANONICAL, succeed_on=_CANONICAL,
                            path="/api/diag/version")
    assert not any("api/diag//api/diag" in u for u in attempted), attempted


def test_a_query_string_survives_path_normalisation(tmp_path):
    """Normalising the prefix must not damage the rest of the path."""
    _proc, attempted = _run(tmp_path, base=_CANONICAL, succeed_on=_CANONICAL,
                            path="/api/diag/journal?table=trades&limit=100")
    assert attempted[0] == f"{_CANONICAL}/api/diag/journal?table=trades&limit=100", attempted

"""egress-landing-probe (PROP-DXTRADE-FIRMS): the wrapper must survive an absent
header and must never print JSON values (an error body can echo the client IP).

A stub ``curl`` on PATH stands in for the network: no request leaves the test.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "egress_landing_probe_action.sh"

# Stub: records its argv, writes headers/body per URL, prints the status code
# the way `-w '%{http_code}'` would.
STUB = r"""#!/usr/bin/env bash
ALL="$*"; hdr=""; out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -D) hdr="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    -w|-m|--max-redirs|-A|--proto|--proto-redir) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "${ALL}" >> "${STUB_LOG}"
case "$url" in
  */specs)
    # No content-type header unless STUB_SPECS_JSON=1 (the reviewed bug).
    if [ "${STUB_SPECS_JSON:-0}" = "1" ]; then
      printf 'HTTP/2 409\r\nserver: cloudflare\r\ncontent-type: application/json\r\n\r\n' > "$hdr"
    else
      printf 'HTTP/2 409\r\nserver: cloudflare\r\n\r\n' > "$hdr"
    fi
    printf '{"error":"SERVICE_ERROR","message":"client 203.0.113.9 refused"}' > "$out"
    printf '409' ;;
  *)
    printf 'HTTP/2 200\r\nserver: cloudflare\r\ncontent-type: text/html\r\n\r\n' > "$hdr"
    printf '<html>loginForm-main</html>' > "$out"
    printf '200' ;;
esac
"""


def _run(tmp_path: Path, specs_json: bool = False) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(STUB)
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "curl.log"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STUB_LOG=str(log), STUB_SPECS_JSON="1" if specs_json else "0",
               HOME=str(tmp_path))
    proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                          timeout=60, cwd=REPO)
    proc.curl_log = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
    return proc


def test_missing_content_type_does_not_abort_and_last_url_is_probed(tmp_path):
    p = _run(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    # /specs had no content-type; the third URL must still have been probed.
    assert "--- https://tradeify247.co/" in p.stdout
    assert "http_status=409" in p.stdout


def test_json_values_are_never_printed(tmp_path):
    p = _run(tmp_path)
    assert "203.0.113.9" not in p.stdout + p.stderr
    assert "refused" not in p.stdout
    # Keys are allowed, but only when the content-type says JSON; with no
    # content-type header nothing from the body is printed at all.
    assert "small_json" not in p.stdout


def test_json_body_prints_keys_only_not_values(tmp_path):
    p = _run(tmp_path, specs_json=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "small_json_keys=error message" in p.stdout
    assert "203.0.113.9" not in p.stdout and "refused" not in p.stdout


def test_curl_is_https_only_and_ignores_curlrc(tmp_path):
    p = _run(tmp_path)
    lines = [ln for ln in p.curl_log.splitlines() if ln.strip()]
    # one curl per allowlisted URL: 3 Tradeify (2026-09-30) + 3 Velotrade (2026-10-04)
    assert len(lines) == 6
    for ln in lines:
        assert ln.startswith("-q "), ln
        assert "--proto =https" in ln and "--proto-redir =https" in ln, ln

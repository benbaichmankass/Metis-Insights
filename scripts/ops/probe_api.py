#!/usr/bin/env python3
# wiring: docs/claude/OPEN-ITEMS.json `probe.cmd`; run by scripts/ops/run_probes.py
"""Probe the UNAUTHENTICATED `/api/bot/*` read surface — work-plan item 3.

⚠️ NOT "W3". The 2026-08-31 operations plan's W-sequence already uses W3 for
the MERGE SERIALIZER, which was refuted by measurement and deliberately not
built — `.github/workflows/scope-overlap-audit.yml` carries that record so no
session re-proposes it. This work is item 3 of the artifact's five-item work
plan (probe coverage), a continuation of W2. The two enumerations are
different sequences and a third (`full-system-audit W2`) exists in ROADMAP.md,
so a bare W-number is ambiguous here — say which plan.

WHY THIS EXISTS, AND WHY IT IS NOT A DUPLICATE OF probe_soak.py
---------------------------------------------------------------
Two `probe_absent_reason` texts on `monitoring` rows blamed the bearer:

    "needs /api/bot/logs?level=error, which is not under /api/diag/* and so is
     not served by the diag bearer this runner holds"
    "Half (a) reads /api/bot/config, not /api/diag/*, so the diag bearer does
     not serve it."

**Both are wrong about the cause, and wrong in the direction that stops work.**
`/api/bot/*` is the Tier-1 read surface and is UNAUTHENTICATED — `CLAUDE.md`
§ "Dashboard REST API" says so, and it was measured from the sandbox on
2026-08-31: `GET https://ict-bot.duckdns.org/api/bot/logs?level=error&limit=5`
answered **HTTP 200 with no Authorization header at all**, as did
`/api/bot/config` and `/api/bot/trades/closed`. There is no bearer to lack.

The REAL blocker was narrower and entirely ours: `scripts/ops/diag_fetch.sh`
hardcodes the `/api/diag/` prefix into the URL it builds, so the only fetcher
the probe family had could not address any other route. That is a missing
fetcher, not a missing credential — a *buildable* gap wearing the label of a
permanent one, which is why those two reasons had to be corrected rather than
merely re-worded.

This carries NO credential, deliberately. A probe that sends no token cannot be
blinded by a token problem, and its own success is the standing evidence that
the surface needs none.

SHAPES
------
`/api/bot/trades/closed` serves a bare LIST; `/api/bot/config` serves an
OBJECT. Both are accepted and the note says which was seen, so a silently
changed shape shows up in the output rather than as a mysterious zero. An
object is treated as ONE row — which is right for `config` (predicates address
`strategies.<leg>.tp_r`) and is stated in the denominator so a reader cannot
mistake "1 row" for a population.

ANY non-200 IS `could_not_look`
-------------------------------
Not hypothetical, and found the hard way while writing this: `?limit=500` on
`/api/bot/trades/closed` returns **HTTP 422** with a validation body, not a row
list. A reader that shrugged that off as "no rows" would report a confident
negative about a request the server refused.

Exit codes: 0 pass · 1 read-and-nothing-matched · 2 we could not look.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_lib  # noqa: E402

# The Caddy HTTPS front — the same host the Svelte SPA calls, and the one the
# sandbox proxy allowlists. A raw `http://<ip>:8001` is DROPPED at the default
# `Trusted` network level (CLAUDE.md § "PM-side session capabilities"), so the
# hostname is the default rather than the IP.
CANONICAL_BASE = "https://ict-bot.duckdns.org"
TIMEOUT_S = 30


def _bases() -> list[str]:
    """Canonical HTTPS first; an override only ever ADDS a candidate."""
    out = [CANONICAL_BASE]
    override = (os.environ.get("BOT_API_URL") or "").strip().rstrip("/")
    # A plain-http or raw-IP override is kept but never tried FIRST — that is
    # the exact "self-heal into an unreachable host" defect diag_fetch.sh
    # documents, where a rewrite fired, produced a firewalled host, and reported
    # that it had healed the setting.
    if override and override not in out:
        out.append(override)
    return out


def fetch_rows(path: str) -> tuple[list[dict] | None, str]:
    """Return (rows, note). `None` rows means we could not look — never []."""
    if not path.startswith("/"):
        path = "/" + path
    stages = []
    for base in _bases():
        url = f"{base}{path}"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:  # noqa: S310
                code = resp.getcode()
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # The host ANSWERED and refused. Emphatically not an empty result.
            stages.append(f"{base} -> answered HTTP {exc.code}")
            continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            stages.append(f"{base} -> never reached a host ({exc})")
            continue

        if code != 200:
            stages.append(f"{base} -> answered HTTP {code}")
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            stages.append(f"{base} -> answered 200 but not JSON ({exc})")
            continue

        if isinstance(payload, list):
            rows = probe_lib.normalise_rows(payload)
            return rows, f"read {len(rows)} row(s) from {path} [shape=list, {base}]"
        if isinstance(payload, dict):
            for key in ("records", "rows", "lines", "reports", "banners", "results"):
                if isinstance(payload.get(key), list):
                    rows = probe_lib.normalise_rows(payload[key])
                    return rows, (f"read {len(rows)} row(s) from {path} "
                                  f"[shape=envelope.{key}, {base}]")
            # A whole document as ONE row. Said out loud, because a denominator
            # of 1 must never be mistaken for a population of 1.
            return [payload], (f"read the WHOLE DOCUMENT as 1 row from {path} "
                               f"[shape=object, keys={sorted(payload)[:8]}, {base}]")
        stages.append(f"{base} -> answered 200 with a {type(payload).__name__}, not a list/object")

    return None, ("no candidate served it — " + "; ".join(stages))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--path", help="e.g. /api/bot/trades/closed?limit=200")
    ap.add_argument("--require", action="append", default=[],
                    help="condition `path=value`, `path~a,b` or `path>value`; "
                         "ALL must hold on ONE row. Repeatable.")
    ap.add_argument("--positive-control",
                    help="a condition that DOES hold today. If it does not fire, the "
                         "verdict is could_not_look — a reader proven blind must not "
                         "emit a confident negative.")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.path or not args.require:
        ap.error("--path and at least one --require are mandatory")

    try:
        conds = [probe_lib.parse_condition(c) for c in args.require]
        control = (probe_lib.parse_condition(args.positive_control)
                   if args.positive_control else None)
    except ValueError as exc:
        return probe_lib.die_unlooked(str(exc))

    rows, note = fetch_rows(args.path)
    if rows is None:
        return probe_lib.die_unlooked(note)
    return probe_lib.report(rows, conds, args.require, note,
                            control, args.positive_control or "")


def _self_test() -> int:
    import http.server
    import threading
    probe_lib.self_test()
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    routes = {
        "/list": (200, '[{"a": 1}, {"a": 2}]'),
        "/object": (200, '{"strategies": {"leg": {"tp_r": 3.0}}}'),
        "/envelope": (200, '{"records": [{"b": 1}], "count": 1}'),
        "/refused": (422, '{"detail": "limit too large"}'),
        "/notjson": (200, "<html>nope</html>"),
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            code, body = routes.get(self.path.split("?")[0], (404, "{}"))
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    saved_base = os.environ.get("BOT_API_URL")
    saved_canon = globals()["CANONICAL_BASE"]
    globals()["CANONICAL_BASE"] = base
    try:
        rows, note = fetch_rows("/list")
        ok(rows is not None and len(rows) == 2 and "shape=list" in note,
           "a bare LIST payload is read as rows and says so")

        rows, note = fetch_rows("/object")
        ok(rows is not None and len(rows) == 1 and "WHOLE DOCUMENT" in note,
           "an OBJECT is one row, and the note says so — a denominator of 1 must "
           "never be mistaken for a population of 1")
        ok(probe_lib.parse_condition("strategies.leg.tp_r=3.0")(rows[0]),
           "and a dotted predicate addresses inside it, which is what /api/bot/config needs")

        rows, note = fetch_rows("/envelope")
        ok(rows is not None and len(rows) == 1 and "envelope.records" in note,
           "an envelope's row list is found and named")

        rows, note = fetch_rows("/refused")
        ok(rows is None and "422" in note,
           "HTTP 422 is could-not-look, NEVER an empty result — measured live: "
           "?limit=500 on /trades/closed really does 422")

        rows, note = fetch_rows("/notjson")
        ok(rows is None and "not JSON" in note, "a 200 that is not JSON is an unread")

        rows, note = fetch_rows("/nope")
        ok(rows is None and "404" in note, "a 404 is an unread, not an empty page")

        ok(main(["--path", "/list", "--require", "a=1"]) == 0, "end-to-end pass")
        ok(main(["--path", "/list", "--require", "a=99"]) == 1, "end-to-end real negative")
        ok(main(["--path", "/refused", "--require", "a=1"]) == 2,
           "end-to-end could_not_look on a refused request — and it is NOT 1")
        ok(main(["--path", "/list", "--require", "a=99",
                 "--positive-control", "a=1"]) == 1,
           "a firing control leaves a genuine negative as a negative")
        ok(main(["--path", "/list", "--require", "a=99",
                 "--positive-control", "zz=1"]) == 2,
           "a control that cannot fire turns the negative into a declared unread")

        globals()["CANONICAL_BASE"] = "https://never.invalid.example"
        os.environ["BOT_API_URL"] = base
        rows, _ = fetch_rows("/list")
        ok(rows is not None, "BOT_API_URL is tried as an ADDITIONAL candidate")
        ok(_bases()[0] == "https://never.invalid.example",
           "but never FIRST — the canonical HTTPS base keeps priority, so an "
           "override cannot 'self-heal' the reader onto a host the proxy drops")
    finally:
        globals()["CANONICAL_BASE"] = saved_canon
        if saved_base is None:
            os.environ.pop("BOT_API_URL", None)
        else:
            os.environ["BOT_API_URL"] = saved_base
        srv.shutdown()

    # The no-credential property, as a control rather than a promise: this file
    # must never read a bearer. If a future edit added one, a token outage would
    # silently start blinding a probe whose whole point is that it cannot be.
    src = Path(__file__).read_text(encoding="utf-8")
    # ⚠️ THE EVIDENCE EXCLUDES THE ANNOTATION, and it has to: the first version
    # of this control searched the whole file for the credential names and
    # FAILED on its own assertion line, which contains both. That is the
    # `new-table-wiring-guard` lesson in miniature (a check must not be
    # satisfiable — or refutable — by its own text). So the window is the
    # module docstring's end to the start of this self-test: the operative code
    # and nothing else.
    body = src[src.index("from __future__"):src.index("def _self_test")]
    forbidden = ("DIAG_" + "READ_TOKEN", "Author" + "ization")
    ok(all(f not in body for f in forbidden),
       "this probe sends NO credential — its own success is the standing "
       "evidence that the /api/bot/* surface needs none, so a token outage "
       "can never silently blind it")
    ok(any(f in src for f in forbidden),
       "and the control has a DENOMINATOR: the names it hunts really do occur "
       "in this file (in the docstring), so a passing check is not a typo "
       "matching nothing")

    print(f"probe-api: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())

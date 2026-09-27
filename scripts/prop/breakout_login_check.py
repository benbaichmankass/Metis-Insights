#!/usr/bin/env python3
"""Read-only login check for a prop account's web terminal (probe step 1).

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 4, step 1.
Run on the live VM by the ``breakout-login-check`` system-action
(``scripts/ops/breakout_login_check_action.sh``).

What it does, in order:
1. Resolves the account's platform adapter from ``config/prop_platforms.yaml``.
2. Launches Playwright Chromium, **headless, default configuration** — no
   stealth plugin, no user-agent or fingerprint change, no fake jitter.
3. Logs in with the username/password env vars named in that config.
4. Reads balance/equity, open positions and working orders.
5. Prints them. Never prints the username, the password, or a cookie.
   When part of the read does not parse, also prints the adapter's REDACTED
   structure dump (``DXtradeAdapter.structure``: label elements, header rows,
   ancestor classes, visible text with e-mails/tokens/credentials stripped) so
   the selectors can be fixed from the public run log. Balances are printed;
   the operator allows them (the journal already records them).
6. Only with ``--emit-status``: posts ONE ``account_status`` to the local
   ``POST /api/bot/prop/report`` (the existing ingest chokepoint). Default OFF.

What it can NOT do: click any order control. The adapter's order methods
raise ``NotImplementedError`` and this script never calls them.

Exit codes:
  0  login ok and every read parsed
  3  login ok, but part of the read path did not parse (selector work needed)
  4  feasibility stop: challenge / CAPTCHA / 2FA / login rejected / timeout /
     no credentials. Printed as ``feasibility: <reason>``.
  5  environment: Playwright or Chromium is not usable on this host
  1  anything else
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prop.platform import FeasibilityError, adapter_for_platform, load_platform_config  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNPARSED, EXIT_FEASIBILITY, EXIT_ENV = 0, 1, 3, 4, 5


def build_status_report(account_id: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``account_status`` body for ``ingest_report``; None if balance AND
    equity are both unread (never post a snapshot of nothing)."""
    if snapshot.get("balance") is None and snapshot.get("equity") is None:
        return None
    return {
        "kind": "account_status",
        "account_id": account_id,
        "balance": snapshot.get("balance"),
        "equity": snapshot.get("equity"),
        "unrealized": snapshot.get("unrealized"),
        "realized_today": snapshot.get("realized_today"),
        "source": "breakout_login_check",
    }


def post_status(report: Dict[str, Any], api_base: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        api_base.rstrip("/") + "/api/bot/prop/report",
        data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (localhost)
        return json.loads(resp.read().decode() or "{}")


def _redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default="breakout_1")
    ap.add_argument("--emit-status", action="store_true",
                    help="post ONE account_status through POST /api/bot/prop/report (default: off)")
    ap.add_argument("--api-base", default="http://127.0.0.1:8001")
    ap.add_argument("--timeout-s", type=int, default=45)
    ap.add_argument("--dump-dir", default="",
                    help="write the post-login page text + extracted tables here (on the VM, "
                         "never to stdout) so selectors can be fixed from the first live run")
    args = ap.parse_args(argv)

    cfg = load_platform_config(args.account)
    platform = cfg["platform"]
    username = os.environ.get(cfg.get("username_env", ""), "")
    password = os.environ.get(cfg.get("password_env", ""), "")
    print(f"account: {args.account}")
    print(f"platform: {platform}")
    print(f"login_url: {cfg['login_url']}")
    print(f"credentials: username {'set' if username else 'MISSING'}, "
          f"password {'set' if password else 'MISSING'} (values never printed)")
    if not username or not password:
        print("feasibility: no_credentials (sync BREAKOUT_DX_* to the VM .env first)")
        return EXIT_FEASIBILITY

    adapter = adapter_for_platform(platform)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"environment: playwright is not importable ({exc})")
        return EXIT_ENV

    rc = EXIT_OK
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # missing browser build or system libraries
            print(f"environment: chromium failed to launch ({type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:300]})")
            return EXIT_ENV
        try:
            context = browser.new_context()
            page = context.new_page()
            if hasattr(adapter, "timeout_ms"):
                adapter.timeout_ms = args.timeout_s * 1000
            try:
                adapter.login(page, cfg["login_url"], username, password)
            except FeasibilityError as fe:
                print(_redact(f"feasibility: {fe.reason}" + (f" ({fe.detail})" if fe.detail else ""),
                              username, password))
                return EXIT_FEASIBILITY
            except Exception as exc:
                # Playwright errors can echo call logs; redact before printing.
                print(_redact(f"login: ERROR ({type(exc).__name__}: {str(exc)[:500]})",
                              username, password))
                return EXIT_ERROR
            print("login: ok")
            try:
                print(f"landed: {_redact(page.url.split('?')[0].split('#')[0], username, password)}")
            except Exception:
                pass
            page.wait_for_timeout(5_000)  # let the terminal populate its panels
            if hasattr(adapter, "wait_ready"):
                ready = adapter.wait_ready(page, timeout_ms=20_000)
                print(f"ready: {'balance/equity visible' if ready else 'NOT within 25s'}")

            snap = adapter.read_account(page).as_dict()
            print("account: " + json.dumps({k: v for k, v in snap.items() if k != "unparsed"}))
            if snap.get("unparsed"):
                print(f"account_unparsed: {', '.join(snap['unparsed'])}")
            if snap.get("balance") is None or snap.get("equity") is None:
                rc = EXIT_UNPARSED

            for label, reader in (("positions", adapter.read_positions),
                                  ("orders", adapter.read_orders)):
                try:
                    items = [i.as_dict() for i in reader(page)]
                    print(f"{label}: {len(items)}")
                    for it in items:
                        it.pop("raw", None)
                        print(f"  {json.dumps(it)}")
                except LookupError as le:
                    print(f"{label}: UNPARSED ({le})")
                    rc = EXIT_UNPARSED

            if rc == EXIT_UNPARSED and hasattr(adapter, "structure"):
                try:
                    for line in adapter.structure(page, (username, password)):
                        print(line)
                except Exception as exc:
                    print(f"structure: FAILED ({type(exc).__name__}: {str(exc)[:200]})")

            if args.dump_dir:
                d = Path(args.dump_dir)
                d.mkdir(parents=True, exist_ok=True)
                body = _redact(page.inner_text("body"), username, password)
                (d / "page_text.txt").write_text(body)
                from src.prop.platform.dxtrade import EXTRACT_TABLES_JS
                (d / "tables.json").write_text(_redact(
                    json.dumps(page.evaluate(EXTRACT_TABLES_JS), indent=1), username, password))
                print(f"dump: written to {d} (on the VM only)")

            if args.emit_status:
                report = build_status_report(args.account, snap)
                if report is None:
                    print("emit_status: SKIPPED (balance and equity both unread)")
                else:
                    try:
                        res = post_status(report, args.api_base)
                        print(f"emit_status: ok id={res.get('id')}")
                    except Exception as exc:
                        print(f"emit_status: FAILED ({type(exc).__name__}: {exc})")
                        rc = rc or EXIT_ERROR
            else:
                print("emit_status: off (default)")
        except Exception as exc:
            print(_redact(f"read: ERROR ({type(exc).__name__}: {str(exc)[:500]})", username, password))
            return EXIT_ERROR
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())

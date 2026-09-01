#!/usr/bin/env python3
# wiring: manual-only - a DIAGNOSTIC a human or session runs ON THE VM to prove a
# Telegram route delivers. It must NOT be wired to a workflow or timer: every run
# sends a real message to the operator, so a scheduled one would be recurring noise
# -- the desensitized-alarm failure this repo has a standing rule against, caused by
# the very tool built to verify the channel that noise is being moved off.
"""Send ONE test ping down each resolved Telegram route, and report what happened.

Runs ON THE VM, where the tokens live. That is the point: the token is read from
the process environment and never printed, never logged, and never leaves the
box — so a session can prove delivery without the credential passing through it.

⚠️ THIS PROVES THE TOKEN, NOT THE PIPELINE. A ping sent here reaching Telegram
shows the (token, chat) pair is good. It does NOT show that the live drain in
``cloud_notifier`` routes Claude pings to the new bot — that is a separate change
and a separate observation. Conflating them is how "deployed" gets reported as
"working", which this repo has been burned by repeatedly (the 2026-06-14 cutover
dropped a token and Claude pings went undelivered for weeks while everything
looked healthy).

Exit codes: 0 every route attempted was delivered · 1 at least one FAILED ·
2 nothing was deliverable (no token/chat resolved at all).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.bot.telegram_routes import claude_route, prop_route  # noqa: E402

TIMEOUT_S = 15


def _send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """POST one message. Returns (ok, detail). Never returns the token."""
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = json.loads(r.read().decode())
        if body.get("ok"):
            msg = body.get("result", {}).get("message_id")
            return True, f"delivered (message_id={msg})"
        return False, f"telegram refused: {body.get('description', '(no description)')}"
    except urllib.error.HTTPError as e:
        # ⚠️ Read the BODY. Telegram returns a 4xx with a specific description
        # ("chat not found", "Unauthorized"), and those are different faults:
        # the first means the chat id is wrong, the second means the token is.
        try:
            desc = json.loads(e.read().decode()).get("description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        return False, f"HTTP {e.code}{': ' + desc if desc else ''}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--route", choices=["claude", "prop", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and REPORT, send nothing")
    a = ap.parse_args(argv)

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    routes = {"claude": claude_route, "prop": prop_route}
    picked = list(routes) if a.route == "both" else [a.route]

    any_deliverable = False
    failed = False
    for name in picked:
        r = routes[name]()
        print(f"\n=== {name} ===")
        print(f"  {r.describe()}")
        if not r.deliverable:
            print(f"  SKIPPED — not deliverable. token={r.token_state} "
                  f"chat={r.chat_state}. Nothing was sent; this is NOT a pass.")
            continue
        any_deliverable = True
        if r.isolated:
            print("  route is ISOLATED — its own bot, so its own conversation.")
        else:
            print("  ⚠️ route is a FALLBACK — it will deliver, to the SHARED bot. "
                  "That is the noise the dedicated bot exists to remove.")
        if a.dry_run:
            print("  dry-run: nothing sent.")
            continue
        text = (f"🧪 route test — {name}\n"
                f"{stamp}\n"
                f"token_from={r.token_from} ({r.token_state})\n"
                f"chat_from={r.chat_from} ({r.chat_state})\n"
                f"If you are reading this in a NEW conversation, the "
                f"{name} route is separated. This ping proves the TOKEN "
                f"works — not that live pings route here yet.")
        ok, detail = _send(r.token, r.chat_id, text)
        print(f"  {'OK' if ok else 'FAIL'} — {detail}")
        failed |= not ok

    if not any_deliverable:
        print("\nNo route was deliverable — nothing was sent. "
              "This is exit 2, deliberately distinct from 'sent and failed': "
              "'we did not try' must not read as 'it did not work'.")
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

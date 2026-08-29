#!/usr/bin/env python3
"""Grade the T+1 cash-settlement soak for `alpaca-settlement-soak-watch`.

THE POINT OF THIS SCRIPT IS THE DENOMINATOR, not the soak read.

An empty soak has two completely different causes and only one is a bug:

  * the writer has had no chance to fire (no alpaca dispatch since the
    settlement code deployed) — *we did not look*; and
  * the writer had a chance and did not fire — a consumer wired in code that
    never writes, the ``unwired-artifact-guard`` shape.

Reporting the first as a failure is a false alarm; reporting the second as
"nothing to see" hides a real defect. They are indistinguishable from the soak
alone, so the newest alpaca dispatch is established FIRST and the soak is
graded against it.

States, never collapsed:

  ``measured``            rows exist. Report them.
  ``never_wrote``         no rows, but an alpaca package was dispatched after
                          the writer deployed. THE FINDING.
  ``not_yet_exercised``   no rows and no post-deploy dispatch. Not a failure.
  ``unreadable``          a diag read failed. *We could not look* — which is
                          evidence about the relay, not about the soak.

Usage:  grade_settlement_soak.py <dir-holding-soak.json-trades.json-version.json>
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# The earliest instant the soak writer could possibly have fired: PR #10408
# ("T+1 cash-settlement basis for alpaca_live — the go-live blocker, at
# annotate") merged to main and reached the live VM. A dispatch BEFORE this
# proves nothing about the writer, so it must not be counted as evidence the
# writer had its chance. Override for a later redeploy rather than editing, so
# the provenance of the default stays readable.
DEPLOYED_AT = os.environ.get("SOAK_DEPLOYED_AT", "2026-08-29T15:05:00+00:00")


def _load(path: str) -> Optional[Any]:
    """Parse a diag document, or None when it is missing or not JSON.

    None means *we could not read it* and is deliberately distinct from an
    empty document, which is a real answer.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - any unreadable file is "could not look"
        return None


def _parse_ts(value: object) -> Optional[datetime]:
    """Parse an ISO-8601 stamp to an aware UTC datetime, else None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def newest_alpaca_dispatch(trades: Any) -> Optional[datetime]:
    """Newest `created_at` across alpaca rows — the denominator.

    Every alpaca row counts, including a ``rejected`` one: the settlement
    observation is recorded on the ORDER PATH, before the risk gate decides,
    so a refusal still proves the writer was reached.
    """
    if not isinstance(trades, list):
        return None
    newest: Optional[datetime] = None
    for row in trades:
        if not isinstance(row, dict):
            continue
        if not str(row.get("account_id") or "").startswith("alpaca"):
            continue
        stamp = _parse_ts(row.get("created_at"))
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


def soak_rows(soak: Any) -> list[dict]:
    """Decode the JSONL lines the diag log_file surface returns."""
    if not isinstance(soak, dict):
        return []
    rows = []
    for line in soak.get("lines") or []:
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def grade(soak: Any, trades: Any) -> tuple[str, Optional[datetime], list[dict]]:
    """Return (state, newest_alpaca_dispatch, soak_rows)."""
    if soak is None or trades is None:
        return "unreadable", None, []
    rows = soak_rows(soak)
    if rows:
        return "measured", newest_alpaca_dispatch(trades), rows
    newest = newest_alpaca_dispatch(trades)
    deployed = _parse_ts(DEPLOYED_AT)
    if newest is not None and deployed is not None and newest > deployed:
        return "never_wrote", newest, []
    return "not_yet_exercised", newest, []


def _fmt(value: object) -> str:
    return "—" if value is None else str(value)


def render(state: str, newest: Optional[datetime], rows: list[dict], version: Any) -> str:
    sha = version.get("git_sha") if isinstance(version, dict) else None
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = [f"### T+1 settlement soak — `{state}`", ""]
    out.append(f"Checked {stamp} · VM `{_fmt(sha)}`")
    out.append(
        f"Newest alpaca dispatch: `{newest.isoformat() if newest else 'none in window'}` "
        f"· writer deployed `{DEPLOYED_AT}`"
    )
    out.append("")

    if state == "unreadable":
        out.append(
            "A diag read failed, so **we could not look**. That is evidence about "
            "the diag path, not about the soak — do not read it either way."
        )
    elif state == "not_yet_exercised":
        out.append(
            "No soak rows, and **no alpaca dispatch since the writer deployed**. "
            "Expected, not a failure: the writer fires on the order path, so it "
            "cannot run while nothing is routed to an alpaca account."
        )
    elif state == "never_wrote":
        out.append(
            "⚠️ **FINDING.** An alpaca package was dispatched AFTER the writer "
            "deployed and the soak is still empty — a consumer wired in code "
            "that never writes. Check the settlement call site in "
            "`src/core/coordinator.py` and "
            "`src/runtime/cash_settlement.py::record_observation`."
        )
    else:
        out.append(f"**{len(rows)} row(s).** `would_have_reduced_usd` is the review figure.")
        out.append("")
        out.append("| ts | account | state | basis_usd | unsettled_usd | would_have_reduced_usd | applied |")
        out.append("|---|---|---|---|---|---|---|")
        for row in rows[-12:]:
            out.append(
                "| {ts} | {acct} | {st} | {basis} | {uns} | {red} | {app} |".format(
                    ts=_fmt(row.get("ts"))[:19],
                    acct=_fmt(row.get("account_id")),
                    st=_fmt(row.get("state")),
                    basis=_fmt(row.get("basis_usd")),
                    uns=_fmt(row.get("unsettled_usd")),
                    red=_fmt(row.get("would_have_reduced_usd")),
                    app=_fmt(row.get("applied")),
                )
            )
        out.append("")
        out.append(
            "⚠️ Read `state` beside the money: `measured` and `journal_unreadable` "
            "can carry the SAME `basis_usd` while meaning opposite things. And "
            "`would_have_reduced_usd: 0.0` on **alpaca_live** is CORRECT, not a "
            "failure — T+1 binds only after a sale and that account has no closed "
            "trades in the 10-day window. The mechanism evidence is on the paper "
            "accounts, which do sell."
        )
        out.append("")
        out.append(
            "Once the operator has acted on this, disable this workflow — a watch "
            "that keeps reporting a result nobody is waiting for becomes noise."
        )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: grade_settlement_soak.py <dir>", file=sys.stderr)
        return 2
    base = argv[1]
    soak = _load(os.path.join(base, "soak.json"))
    trades = _load(os.path.join(base, "trades.json"))
    version = _load(os.path.join(base, "version.json")) or {}

    state, newest, rows = grade(soak, trades)
    report = render(state, newest, rows, version)

    # `not_yet_exercised` is deliberately SILENT. It is the ordinary weekday
    # state before the first dispatch, and commenting it daily would train the
    # reader to skip this issue — the desensitised-alarm failure this repo
    # keeps paying for.
    should_report = state != "not_yet_exercised"

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"state={state}\n")
            fh.write(f"should_report={'true' if should_report else 'false'}\n")
            fh.write("report<<SOAK_EOF\n")
            fh.write(report + "\n")
            fh.write("SOAK_EOF\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""What is a STANDARD account actually worth, for grading purposes?

WHY THIS EXISTS
---------------
``account_rulesets._standard_ruleset`` graded every standard account against a
synthetic **$10,000** — ``_DEFAULT_STANDARD_SIZE``, reached because
``risk_block.get("account_size_usd")`` is ``None`` for **0 of 11** accounts in
``config/accounts.yaml`` (measured 2026-08-27; the key is declared nowhere).

That placeholder is not "roughly right". Measured the same day against
``/api/bot/accounts/balances`` (``source: db``, as_of ``2026-08-27T09:00:30Z``):

===================== ================ ==============================
account               balance          vs the $10,000 placeholder
===================== ================ ==============================
``alpaca_live``       $200.10          **50x too large**
``bybit_2``           $302.68          **33x too large**
``bybit_portfolio``   $91,219.23       9x too small
``alpaca_portfolio``  $98,347.82       10x too small
``bybit_1``           $184,719.62      18x too small
``ib_paper``          $1,341,065.16    **134x too small**
===================== ================ ==============================

A **6,700x** spread collapsed onto one number, and wrong in BOTH directions by
one to two orders of magnitude. Risk per trade is ``risk_pct x size`` and the
drawdown floor is a fraction of the same size, so this does not nudge a
survival figure — it decides it.

⚠️ **THE FIX IS A REFUSAL, NOT A BETTER DEFAULT.** *"We could not establish this
account's size"* and *"this account is worth $10,000"* are different statements,
and only one of them is true. A default is what made the original defect
invisible for months: every row carried a confident number, so nothing in the
output could distinguish a graded account from an ungraded one. This module
returns ``size_usd=None`` and a state saying why, and the caller refuses to
grade. Operator decision, 2026-08-27: grade off the live balance snapshot,
stamped with its ``as_of``; an unreadable or stale balance refuses.

FOUR STATES, NEVER COLLAPSED
----------------------------
(``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states".)

  ``declared``    — the account's ``risk`` block declares ``account_size_usd``.
                    A human wrote it down, so it WINS over the measurement: an
                    explicit declaration is a decision, and silently overriding
                    it with a snapshot would make the declaration inert.
  ``measured``    — a ``balance_snapshots`` row, ``api_ok``, inside the age
                    bound. ``size_as_of`` says when.
  ``stale``       — a snapshot exists but is older than the bound. **REFUSES.**
                    A size we cannot show to be current is not a size, the same
                    reading ``prop_balance``/``/api/bot/prop/status`` already
                    take of a stale cushion.
  ``unreadable``  — no row, ``api_ok`` false, a non-numeric balance, a
                    non-positive balance, or the read itself failed. **REFUSES.**
                    Emphatically NOT "the account is small".

``stale`` and ``unreadable`` are kept apart because they call for different
operator actions: *the snapshot writer stopped* versus *this account has no
readable balance at all* (``breakout_1`` has no API by design; ``ib_live`` is
dry/shelved — 2 of 11 accounts read ``api_ok: false`` today).

⚠️ **THE STAMP TRAVELS WITH THE VERDICT, ALWAYS.** A grade computed off a
$302 balance and a grade computed off a $1.3 M one are not comparable, and the
balance moves. Every consumer gets ``size_state`` / ``size_as_of`` /
``size_source`` beside the number, so a verdict can always say what it was
graded against and when — the ``pnlCoverage``-beside-the-sum discipline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

#: Grade states. ``DECLARED``/``MEASURED`` carry a size; the other two never do.
DECLARED = "declared"
MEASURED = "measured"
STALE = "stale"
UNREADABLE = "unreadable"

#: How old a balance snapshot may be and still size a grade.
#:
#: CHOSEN, not tuned, and deliberately generous: the hourly-report writer
#: refreshes ``balance_snapshots`` about once an hour, so a bound under ~2h
#: would refuse on ordinary jitter. 24h is the same threshold
#: ``PROP_STATUS_REQUEST_MAX_AGE_HOURS`` defaults to for the prop cushion, and
#: reusing it means "too old to trust" has one definition in this repo rather
#: than two that can drift. There is no distribution of snapshot ages behind
#: this number — do not read it as measured.
_DEFAULT_MAX_AGE_HOURS = 24.0
_ENV_MAX_AGE = "STANDARD_ACCOUNT_SIZE_MAX_AGE_HOURS"


def max_age_hours() -> float:
    """Age bound for a balance snapshot, in hours.

    An unparseable or non-positive value falls back to the DEFAULT rather than
    to zero or to unbounded: a typo must not silently switch the freshness
    check off (which would grade off any snapshot however old), nor switch it
    to refusing everything. Same discipline as ``CANDLE_CACHE_TTL_FRACTION``.
    """
    raw = os.environ.get(_ENV_MAX_AGE)
    if raw is None:
        return _DEFAULT_MAX_AGE_HOURS
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_AGE_HOURS
    return v if v > 0 else _DEFAULT_MAX_AGE_HOURS


@dataclass(frozen=True)
class StandardAccountSize:
    """A standard account's grading size, with the provenance of the number."""

    account_id: str
    size_usd: Optional[float]
    size_state: str
    size_source: str
    size_as_of: Optional[str] = None
    size_age_hours: Optional[float] = None
    reason: str = ""

    @property
    def gradeable(self) -> bool:
        """True only when a size was actually established.

        Read THIS, never ``size_usd is not None`` scattered at call sites — one
        predicate so a future state cannot be mis-handled by half the callers.
        """
        return self.size_usd is not None and self.size_state in (DECLARED, MEASURED)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN is not a size. `f != f` is the stdlib-only NaN test.
    if f != f:
        return None
    return f


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def resolve_standard_account_size(
    account_id: str,
    risk_block: Optional[Mapping[str, Any]] = None,
    *,
    snapshots: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now: Optional[datetime] = None,
    override_usd: Optional[float] = None,
) -> StandardAccountSize:
    """Resolve one standard account's grading size. Pure — no I/O.

    ``snapshots`` is the mapping ``Database.get_latest_balance_snapshots()``
    returns (``{account_id: {balance, api_ok, ts, ...}}``). Pass ``None`` to
    mean *the caller could not read the store at all*, which is
    ``unreadable`` — distinct from passing ``{}``, which means *the store was
    read and holds nothing for anyone*. Those are different facts and the
    reason string says which.

    Precedence: ``override_usd`` (an explicit caller/CLI value) > ``declared``
    > ``measured``. A refusal is never overridden by a default, because there
    is no default.
    """
    now = now or datetime.now(timezone.utc)

    ov = _as_float(override_usd)
    if ov is not None and ov > 0:
        return StandardAccountSize(
            account_id=account_id, size_usd=ov, size_state=DECLARED,
            size_source="override", reason="size supplied explicitly by the caller",
        )

    declared = _as_float((risk_block or {}).get("account_size_usd"))
    if declared is not None and declared > 0:
        return StandardAccountSize(
            account_id=account_id, size_usd=declared, size_state=DECLARED,
            size_source="accounts.yaml:risk.account_size_usd",
            reason="declared in the account's risk block",
        )

    if snapshots is None:
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=UNREADABLE,
            size_source="balance_snapshots",
            reason="the balance-snapshot store could not be read at all "
                   "(we did not look — this is NOT evidence the account is empty)",
        )

    row = snapshots.get(account_id)
    if not row:
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=UNREADABLE,
            size_source="balance_snapshots",
            reason=f"no balance_snapshots row for {account_id!r} "
                   f"(the store was read and holds {len(snapshots)} account(s), none of them this one)",
        )

    if not row.get("api_ok"):
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=UNREADABLE,
            size_source="balance_snapshots", size_as_of=str(row.get("ts") or "") or None,
            reason="the snapshot records api_ok=false — the balance was not "
                   "successfully read from the venue, so its value is not a measurement",
        )

    bal = _as_float(row.get("balance"))
    if bal is None:
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=UNREADABLE,
            size_source="balance_snapshots", size_as_of=str(row.get("ts") or "") or None,
            reason=f"the snapshot's balance is not a number ({row.get('balance')!r})",
        )
    if bal <= 0:
        # A zero/negative balance is REFUSED rather than graded. Sizing off it
        # would make risk-per-trade zero and every survival figure vacuously
        # perfect — a confident wrong answer, which is worse than abstaining.
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=UNREADABLE,
            size_source="balance_snapshots", size_as_of=str(row.get("ts") or "") or None,
            reason=f"the snapshot balance is non-positive ({bal}); grading off it would "
                   f"make risk-per-trade zero and every survival figure vacuously perfect",
        )

    ts = _parse_ts(row.get("ts"))
    if ts is None:
        # Undateable is STALE, not measured: a snapshot that cannot be dated
        # cannot be shown to be current, and the fail-safe reading of a
        # grading input is the conservative one.
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=STALE,
            size_source="balance_snapshots",
            reason=f"the snapshot timestamp is unparseable ({row.get('ts')!r}), so the "
                   f"balance cannot be shown to be current",
        )

    age_h = (now - ts).total_seconds() / 3600.0
    bound = max_age_hours()
    if age_h > bound:
        return StandardAccountSize(
            account_id=account_id, size_usd=None, size_state=STALE,
            size_source="balance_snapshots", size_as_of=ts.isoformat(),
            size_age_hours=round(age_h, 4),
            reason=f"the balance snapshot is {age_h:.1f}h old against a {bound:.0f}h bound",
        )

    return StandardAccountSize(
        account_id=account_id, size_usd=bal, size_state=MEASURED,
        size_source="balance_snapshots", size_as_of=ts.isoformat(),
        size_age_hours=round(age_h, 4),
        reason="live balance snapshot",
    )


def load_balance_snapshots() -> Optional[Dict[str, Dict[str, Any]]]:
    """Read the latest balance snapshot per account, or ``None`` on failure.

    ``None`` (could not look) is deliberately distinct from ``{}`` (looked, the
    table is empty) — ``resolve_standard_account_size`` reports different
    reasons for the two, and a caller that collapsed them would tell an
    operator to fix the wrong thing.
    """
    try:
        from src.units.db.database import Database  # local: keep this module import-light
        return Database().get_latest_balance_snapshots() or {}
    except Exception:  # noqa: BLE001  # allow-silent: an unreadable store IS a state we return
        return None


def _self_test() -> int:
    """Planted controls — every state, and the traps that motivated them."""
    checks = []

    def ck(name: str, ok: bool) -> None:
        checks.append(bool(ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")

    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now.replace(hour=11)).isoformat()
    old = (now.replace(day=20)).isoformat()

    r = resolve_standard_account_size("bybit_2", {}, snapshots={
        "bybit_2": {"balance": 302.68, "api_ok": True, "ts": fresh}}, now=now)
    ck("a fresh api_ok snapshot is MEASURED and carries the real balance",
       r.size_state == MEASURED and r.size_usd == 302.68 and r.gradeable)
    ck("the measured row stamps as_of and age", r.size_as_of is not None and r.size_age_hours == 1.0)

    r = resolve_standard_account_size("bybit_2", {"account_size_usd": 500.0}, snapshots={
        "bybit_2": {"balance": 302.68, "api_ok": True, "ts": fresh}}, now=now)
    ck("an explicit declaration WINS over the snapshot (a decision is not overridden)",
       r.size_state == DECLARED and r.size_usd == 500.0)

    r = resolve_standard_account_size("bybit_2", {}, snapshots={
        "bybit_2": {"balance": 302.68, "api_ok": True, "ts": old}}, now=now)
    ck("a snapshot past the age bound is STALE and REFUSES",
       r.size_state == STALE and r.size_usd is None and not r.gradeable)

    r = resolve_standard_account_size("ib_live", {}, snapshots={
        "ib_live": {"balance": None, "api_ok": False, "ts": fresh}}, now=now)
    ck("api_ok=false is UNREADABLE, never a size",
       r.size_state == UNREADABLE and r.size_usd is None)

    r = resolve_standard_account_size("nobody", {}, snapshots={}, now=now)
    ck("a read that found no row for this account is UNREADABLE",
       r.size_state == UNREADABLE and r.size_usd is None)
    ck("...and its reason states the denominator it DID see", "holds 0 account(s)" in r.reason)

    r_none = resolve_standard_account_size("nobody", {}, snapshots=None, now=now)
    ck("snapshots=None (could not look) is distinguishable from {} (looked, empty)",
       r_none.size_state == UNREADABLE and r_none.reason != r.reason)

    r = resolve_standard_account_size("x", {}, snapshots={
        "x": {"balance": 0.0, "api_ok": True, "ts": fresh}}, now=now)
    ck("a ZERO balance REFUSES (grading off it makes survival vacuously perfect)",
       r.size_state == UNREADABLE and r.size_usd is None)

    r = resolve_standard_account_size("x", {}, snapshots={
        "x": {"balance": 100.0, "api_ok": True, "ts": "not-a-timestamp"}}, now=now)
    ck("an UNDATEABLE snapshot is STALE, not measured", r.size_state == STALE and r.size_usd is None)

    r = resolve_standard_account_size("x", {}, snapshots=None, override_usd=25_000.0, now=now)
    ck("an explicit override wins even when nothing is readable",
       r.size_state == DECLARED and r.size_usd == 25_000.0)

    # THE CONTROL THAT MATTERS MOST: the old behaviour must be gone.
    r = resolve_standard_account_size("bybit_2", {}, snapshots=None, now=now)
    ck("NO PATH RETURNS THE OLD $10,000 DEFAULT (the whole point)",
       r.size_usd != 10_000.0 and r.size_usd is None)

    prev = os.environ.get(_ENV_MAX_AGE)
    try:
        os.environ[_ENV_MAX_AGE] = "not-a-number"
        ck("an unparseable age bound falls back to the DEFAULT, not to 0 or infinity",
           max_age_hours() == _DEFAULT_MAX_AGE_HOURS)
        os.environ[_ENV_MAX_AGE] = "0"
        ck("a non-positive age bound falls back to the DEFAULT (cannot disable the check)",
           max_age_hours() == _DEFAULT_MAX_AGE_HOURS)
    finally:
        if prev is None:
            os.environ.pop(_ENV_MAX_AGE, None)
        else:
            os.environ[_ENV_MAX_AGE] = prev

    ok = all(checks)
    print(f"standard-account-size self-test {'PASS' if ok else 'FAIL'} "
          f"— {sum(checks)}/{len(checks)} planted controls fire")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(_self_test())

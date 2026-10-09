"""Account-level daily-drawdown switch — the ONE sanctioned automatic off.

Operator directive, verbatim, 2026-10-09 (recorded in
docs/CLAUDE-RULES-CANONICAL.md § Prime Directive rule 7 as the single
exception to "there is no halting"):

    "I overreacted regarding the halting. There is one thing that I want to
    take back, and that is the daily drawdown on at the account level. That's
    the only place I want there to be like a automatic off switch, basically,
    where if we lose more than a defined amount from the account in a day, then
    the account automatically turns off until it resets the next day. But that
    should be a default off. We'll arm all of them, but I, the default for when
    it's built should be off so that like we know that it's something that has
    to actively be done and turned on. And the default setting should be, for
    the prop accounts, obviously it should be like the prop accounts daily
    drawdown with a buffer. And for the other accounts, it should be 3%."

Popup answers (same session): on trip, "Keep open, block new"; prop buffer
"20% of the firm's limit" ($150 firm daily limit trips at $120 lost).

Semantics
---------
* **Config** — ``config/accounts.yaml::<account>.risk.daily_dd_switch``::

      daily_dd_switch: {armed: false, limit_pct: 0.03}   # non-prop
      daily_dd_switch: {armed: false}                    # prop: firm-derived

  Absent block or absent ``armed`` = **DISARMED** (the operator wants arming
  to be an explicit act). Non-prop default limit is 3% of day-start equity.
  A prop account's limit is ``(1 - 0.20) x`` the firm's daily-loss amount,
  read from the account's ``backtest_ruleset`` ``limits:`` block, on the
  firm's own amount basis (``account_size`` or day-start balance).
* **Day** — UTC midnight for exchange accounts; the firm's
  ``limits.daily_loss_reset_utc`` for prop accounts.
* **Loss** — ``day_start_equity - current_equity`` (EQUITY drawdown from the
  day start, i.e. realized + unrealized), the same shape as the firms'
  ``equity_vs_daystart_balance`` rule. Day-start equity is the last equity
  read within 2h before the boundary, else the first read after it.
* **Trip** — armed and ``loss >= limit``: NEW entries on the account are
  refused (``DAILY_DD_SWITCH``, the RiskManager per-trade refusal path).
  Open positions and their brackets are untouched; reduce-only legs pass.
  The trip latches until the next day boundary, then clears by itself — no
  manual clear. ONE alert on trip, ONE on reset.
* **Could not look** — unreadable equity never trips the switch (and never
  clears a trip already latched today). After ``UNREADABLE_FLAG_AFTER``
  consecutive unreadable checks on an armed account, ONE red flag is sent;
  checks keep running (NO-HALT rule 7).

* **Observed every trader tick** — ``observe_armed_accounts`` (called once
  per tick from ``src/main.py``) folds one on-disk equity reading per armed
  account, observe-only, so day-start equity, the trip alert and the reset
  alert never wait for a signal. ``RiskManager.evaluate`` still observes (with
  the dispatch's live total equity where it has one) and is the only refusal.

State lives in ``runtime_logs/daily_dd_switch_state.json`` (accounts are
rebuilt every dispatch tick, so the file is the cross-tick memory — the same
reason ``daily_cap_alert`` used one). Best-effort: a state-file failure never
blocks dispatch.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

REASON = "DAILY_DD_SWITCH"
DEFAULT_LIMIT_PCT = 0.03          # operator: "for the other accounts, it should be 3%"
PROP_FIRM_BUFFER = 0.20           # operator popup: "20% of the firm's limit"
UNREADABLE_FLAG_AFTER = 3         # consecutive unreadable checks before the red flag
CARRY_DAY_START_WINDOW = timedelta(hours=2)
_STATE_FILENAME = "daily_dd_switch_state.json"

BASIS_PCT = "pct_of_day_start_equity"
BASIS_PROP = "prop_firm_daily_limit_minus_buffer"

READ_OK = "ok"
READ_UNREADABLE = "could_not_look"
READ_NOT_OBSERVED = "not_observed"


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _parse_hhmm(raw: Any) -> timedelta:
    try:
        hh, mm = str(raw).strip().split(":")
        return timedelta(hours=int(hh), minutes=int(mm))
    except (ValueError, AttributeError):
        return timedelta(0)


@dataclass(frozen=True)
class SwitchConfig:
    armed: bool
    basis: str
    limit_pct: Optional[float]               # non-prop: fraction of day-start equity
    reset_utc: str = "00:00"
    firm_daily_loss_pct: Optional[float] = None
    firm_amount_basis: Optional[str] = None  # "account_size" | day-start balance
    account_size_usd: Optional[float] = None
    buffer: float = PROP_FIRM_BUFFER
    config_error: Optional[str] = None

    def limit_usd(self, day_start_equity: Optional[float]) -> Optional[float]:
        """The $ loss from day start that trips the switch (None = unknown)."""
        if self.basis == BASIS_PROP:
            if self.firm_daily_loss_pct is None:
                return None
            if str(self.firm_amount_basis or "").lower() == "account_size":
                base = self.account_size_usd
            else:
                base = day_start_equity
            if not base or base <= 0:
                return None
            return (1.0 - self.buffer) * self.firm_daily_loss_pct * base
        if self.limit_pct is None or not day_start_equity or day_start_equity <= 0:
            return None
        return self.limit_pct * day_start_equity

    def describe(self) -> str:
        if self.basis == BASIS_PROP:
            pct = self.firm_daily_loss_pct
            on = ("account size" if str(self.firm_amount_basis or "").lower() == "account_size"
                  else "day-start equity")
            return (f"{1.0 - self.buffer:.0%} x firm daily limit "
                    f"({pct:.2%} of {on})" if pct is not None else "firm limit unreadable")
        return f"{(self.limit_pct or 0):.2%} of day-start equity"


def prop_firm_terms(account_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """The firm's daily-loss terms from the account's ``backtest_ruleset``.

    Returns ``{}`` when the ruleset cannot be read (the switch then has no
    limit and cannot trip — surfaced as ``config_error``)."""
    rel = account_cfg.get("backtest_ruleset")
    if not rel or str(rel).strip().lower() == "standard":
        return {}
    path = Path(str(rel))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / "config" / path
    try:
        import yaml
        raw = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001 — unreadable ruleset = no limit, surfaced
        logger.warning("daily_dd_switch: ruleset %s unreadable: %s", path, exc)
        return {}
    limits = raw.get("limits") or {}
    return {
        "daily_loss_pct": _f(limits.get("daily_loss_pct")),
        "daily_loss_reset_utc": str(limits.get("daily_loss_reset_utc") or "00:00"),
        "daily_loss_amount_basis": limits.get("daily_loss_amount_basis"),
        "account_size_usd": _f(account_cfg.get("account_size_usd")) or _f(raw.get("account_size_usd")),
    }


def parse_config(block: Any, *, prop_terms: Optional[Mapping[str, Any]] = None) -> SwitchConfig:
    """Build the switch config. ``prop_terms`` non-None marks a prop account."""
    blk = block if isinstance(block, Mapping) else {}
    armed = blk.get("armed") is True       # anything but an explicit true = disarmed
    if prop_terms is not None:
        pct = _f(prop_terms.get("daily_loss_pct"))
        buf = _f(blk.get("firm_buffer"))
        return SwitchConfig(
            armed=armed, basis=BASIS_PROP, limit_pct=None,
            reset_utc=str(prop_terms.get("daily_loss_reset_utc") or "00:00"),
            firm_daily_loss_pct=pct,
            firm_amount_basis=prop_terms.get("daily_loss_amount_basis"),
            account_size_usd=_f(prop_terms.get("account_size_usd")),
            buffer=PROP_FIRM_BUFFER if buf is None else buf,
            config_error=None if pct is not None else "prop ruleset daily_loss_pct unreadable",
        )
    pct = _f(blk.get("limit_pct"))
    return SwitchConfig(armed=armed, basis=BASIS_PCT,
                        limit_pct=DEFAULT_LIMIT_PCT if pct is None else pct)


def trading_day(now: datetime, reset_utc: str) -> date:
    """The account's trading day containing ``now`` (UTC)."""
    return (now.astimezone(timezone.utc) - _parse_hhmm(reset_utc)).date()


def day_boundary(day: date, reset_utc: str) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + _parse_hhmm(reset_utc)


# Test seam: tests/conftest.py points this at a per-test tmp file so no test
# reads another's day state or writes the real runtime_logs/.
_PATH_OVERRIDE: Optional[Path] = None


def _state_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / _STATE_FILENAME


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        p = path or _state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_dd_switch: state load failed: %s", exc)
        return {}


def _save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    try:
        p = path or _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_dd_switch: state save failed: %s", exc)


def _default_alert(kind: str, account_id: str, row: Dict[str, Any], cfg: SwitchConfig) -> None:
    from src.runtime.execution_diagnostics import enqueue_daily_dd_switch_alert
    enqueue_daily_dd_switch_alert(account=account_id, kind=kind, row=row,
                                  limit_desc=cfg.describe())


class DailyDDSwitch:
    """Per-account switch. Cheap to construct; all memory is in the state file."""

    def __init__(self, account_id: str, cfg: SwitchConfig, *,
                 state_path: Optional[Path] = None,
                 alert: Optional[Callable[[str, str, Dict[str, Any], SwitchConfig], None]] = None,
                 ) -> None:
        self.account_id = account_id
        self.cfg = cfg
        self._state_path = state_path
        self._alert = alert or _default_alert

    # -- state ---------------------------------------------------------------
    def _row(self) -> Dict[str, Any]:
        return dict(load_state(self._state_path).get(self.account_id) or {})

    def _put(self, row: Dict[str, Any]) -> None:
        state = load_state(self._state_path)
        state[self.account_id] = row
        _save_state(state, self._state_path)

    def _send(self, kind: str, row: Dict[str, Any]) -> None:
        try:
            self._alert(kind, self.account_id, row, self.cfg)
        except Exception as exc:  # noqa: BLE001 — an alert failure never blocks dispatch
            logger.warning("daily_dd_switch: %s alert failed for %s: %s", kind, self.account_id, exc)

    # -- the check -----------------------------------------------------------
    def observe(self, equity: Optional[float], now: Optional[datetime] = None, *,
                reading_ts: Optional[datetime] = None) -> Dict[str, Any]:
        """Fold one equity reading into today's state; alert on transitions.

        ``reading_ts`` is when the equity was READ (a balance snapshot can be
        up to 2h old); it defaults to ``now`` (a live read). A reading older
        than the one already folded never regresses the row, and a reading
        from before today's carry window is not today's equity — both leave
        the row's verdict as it was / "could not look" respectively.

        Returns the account's row (see :meth:`status`)."""
        if not self.account_id:
            return {}
        now = now or datetime.now(timezone.utc)
        reading_ts = reading_ts or now
        if reading_ts.tzinfo is None:
            reading_ts = reading_ts.replace(tzinfo=timezone.utc)
        day = trading_day(now, self.cfg.reset_utc)
        before = self._row()
        row = dict(before)
        if row.get("day") != day.isoformat():
            was_tripped = bool(row.get("tripped"))
            carry = None
            last_eq, last_ts = _f(row.get("last_equity")), row.get("last_equity_ts")
            if last_eq and last_ts:
                try:
                    ts = datetime.fromisoformat(str(last_ts))
                    if day_boundary(day, self.cfg.reset_utc) - ts <= CARRY_DAY_START_WINDOW:
                        carry = last_eq
                except ValueError:
                    carry = None
            row = {"day": day.isoformat(), "day_start_equity": carry,
                   "last_equity": last_eq, "last_equity_ts": last_ts,
                   "tripped": False, "tripped_at": None,
                   "unreadable_streak": 0, "red_flagged": False}
            if was_tripped:
                self._send("reset", row)

        eq = _f(equity)
        if eq is not None and eq > 0:
            last_ts = _parse_ts(row.get("last_equity_ts"))
            if last_ts is not None and reading_ts < last_ts:
                # Older than what is already folded (e.g. the hourly snapshot
                # after a dispatch's live read): no new information.
                self._put_if_changed(before, row)
                return row
            if reading_ts < day_boundary(day, self.cfg.reset_utc) - CARRY_DAY_START_WINDOW:
                eq = None      # yesterday's equity is not a reading of today

        if eq is None or eq <= 0:
            row["unreadable_streak"] = int(row.get("unreadable_streak") or 0) + 1
            row["read_state"] = READ_UNREADABLE
            if (self.cfg.armed and row["unreadable_streak"] >= UNREADABLE_FLAG_AFTER
                    and not row.get("red_flagged")):
                row["red_flagged"] = True
                self._send("unreadable", row)
            self._put(row)
            return row

        row["unreadable_streak"] = 0
        row["red_flagged"] = False
        row["read_state"] = READ_OK
        row["last_equity"] = eq
        row["last_equity_ts"] = reading_ts.isoformat()
        if not row.get("day_start_equity"):
            row["day_start_equity"] = eq
        dse = _f(row.get("day_start_equity"))
        limit = self.cfg.limit_usd(dse)
        loss = (dse - eq) if dse is not None else None
        row["loss_usd"] = round(loss, 2) if loss is not None else None
        row["limit_usd"] = round(limit, 2) if limit is not None else None
        if (self.cfg.armed and not row.get("tripped") and limit is not None
                and loss is not None and loss >= limit):
            row["tripped"] = True
            row["tripped_at"] = now.isoformat()
            self._send("trip", row)
        self._put_if_changed(before, row)
        return row

    def _put_if_changed(self, before: Dict[str, Any], row: Dict[str, Any]) -> None:
        # Observed every trader tick: an unchanged row costs no write.
        if row != before:
            self._put(row)

    def blocks_new_entries(self, row: Optional[Dict[str, Any]] = None,
                           now: Optional[datetime] = None) -> bool:
        """Armed AND tripped for the CURRENT trading day."""
        if not self.cfg.armed:
            return False
        row = row if row is not None else self._row()
        day = trading_day(now or datetime.now(timezone.utc), self.cfg.reset_utc)
        return bool(row.get("tripped")) and row.get("day") == day.isoformat()

    def status(self, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The operator-facing view (``/api/bot/config``, ``report()``)."""
        row = row if row is not None else self._row()
        dse = _f(row.get("day_start_equity"))
        limit = self.cfg.limit_usd(dse)
        return {
            "armed": self.cfg.armed,
            "limit_basis": self.cfg.basis,
            "limit_desc": self.cfg.describe(),
            "limit_pct": self.cfg.limit_pct,
            "firm_daily_loss_pct": self.cfg.firm_daily_loss_pct,
            "buffer": self.cfg.buffer if self.cfg.basis == BASIS_PROP else None,
            "reset_utc": self.cfg.reset_utc,
            "config_error": self.cfg.config_error,
            "day": row.get("day"),
            "day_start_equity": dse,
            "limit_usd": round(limit, 2) if limit is not None else None,
            "loss_usd": row.get("loss_usd"),
            "tripped": self.blocks_new_entries(row),
            "tripped_at": row.get("tripped_at"),
            "read_state": row.get("read_state") or READ_NOT_OBSERVED,
            "unreadable_streak": int(row.get("unreadable_streak") or 0),
        }


def switch_for_account(account_id: str, account_cfg: Mapping[str, Any], **kw: Any) -> DailyDDSwitch:
    """Build the switch from a FULL account config dict (prop-aware)."""
    from src.prop.prop_identity import is_prop_account
    risk = account_cfg.get("risk") if isinstance(account_cfg.get("risk"), Mapping) else {}
    block = risk.get("daily_dd_switch")
    terms = prop_firm_terms(account_cfg) if is_prop_account(account_cfg) else None
    return DailyDDSwitch(account_id, parse_config(block, prop_terms=terms), **kw)


# -- equity readings (shared by RiskManager.evaluate and the per-tick observe) --

Reading = Tuple[Optional[float], Optional[datetime]]
SNAPSHOT_MAX_AGE_HOURS = 2.0      # the hourly snapshot writer's cadence plus slack


def snapshot_equity_reading(account_id: str, *, max_age_hours: float = SNAPSHOT_MAX_AGE_HOURS,
                            now: Optional[datetime] = None) -> Reading:
    """``(equity, read_at)`` from ``runtime_logs/balance_snapshots.json``.

    ``(None, None)`` when absent, unreadable or older than ``max_age_hours`` —
    a stale reading is "could not look", never a figure. Connection-free."""
    if not account_id:
        return None, None
    try:
        from src.utils.paths import runtime_logs_dir
        p = runtime_logs_dir() / "balance_snapshots.json"
        if not p.exists():
            return None, None
        entry = (json.loads(p.read_text(encoding="utf-8")) or {}).get(account_id)
        if not isinstance(entry, dict) or entry.get("balance") is None:
            return None, None
        ts = _parse_ts(entry.get("ts"))
        if ts is None:
            return None, None
        if ((now or datetime.now(timezone.utc)) - ts).total_seconds() > max_age_hours * 3600:
            return None, None
        return float(entry["balance"]), ts
    except Exception:  # noqa: BLE001 — unreadable = could not look
        return None, None


def prop_equity_reading(account_id: str, *, now: Optional[datetime] = None) -> Reading:
    """``(equity, read_at)`` from the latest operator/executor-reported prop
    status row (``prop_sizing_balance``: equity preferred over balance), only
    when that row is fresh — a stale or absent row is "could not look"."""
    if not account_id:
        return None, None
    try:
        from src.prop.prop_balance import prop_sizing_balance
        state, val, meta = prop_sizing_balance(account_id)
    except Exception:  # noqa: BLE001 — unreadable = could not look
        return None, None
    if state != "ok" or val is None:
        return None, None
    age = _f((meta or {}).get("age_hours"))
    now = now or datetime.now(timezone.utc)
    return val, (now - timedelta(hours=age)) if age is not None else now


def observe_armed_accounts(accounts_path: Optional[Path] = None,
                           now: Optional[datetime] = None) -> Dict[str, str]:
    """Fold one equity reading per ARMED account — the per-tick observe.

    OBSERVE-ONLY: it never refuses anything (the refusal stays in
    ``RiskManager.evaluate``). It exists so day-start equity, the trip alert
    and the reset alert do not wait for a signal: before it, ``observe`` ran
    only inside ``evaluate``, so a quiet account was never observed and its
    day start was the equity at the day's FIRST signal (PI-20261009-VQIPJE2H-0001).

    Reads only what is already on disk — the hourly balance snapshot, or the
    prop status row for a prop account — so it adds no broker call; a
    dispatch's live total equity is folded by ``evaluate`` itself, and a
    snapshot older than that live read never regresses the row. Returns
    ``{account: read_state}``. Never raises.
    """
    out: Dict[str, str] = {}
    try:
        from src.config.accounts_loader import load_accounts_dict
        from src.prop.prop_identity import is_prop_account, is_retired_account
        errors: list = []
        accounts = load_accounts_dict(accounts_path, errors=errors)
    except Exception as exc:  # noqa: BLE001
        errors, accounts = [{"error": str(exc)}], {}
    if not accounts:
        logger.warning("daily_dd_switch: tick observe read no accounts: %s", errors or "empty")
        return out
    for name, cfg in accounts.items():
        try:
            if not isinstance(cfg, Mapping) or cfg.get("enabled") is False or is_retired_account(cfg):
                continue
            sw = switch_for_account(str(name), cfg)
            if not sw.cfg.armed:
                continue
            reading = (prop_equity_reading(str(name), now=now) if is_prop_account(cfg)
                       else snapshot_equity_reading(str(name), now=now))
            row = sw.observe(reading[0], now, reading_ts=reading[1])
            out[str(name)] = str(row.get("read_state") or READ_NOT_OBSERVED)
        except Exception as exc:  # noqa: BLE001 — one account never stops the rest
            logger.warning("daily_dd_switch: tick observe failed for %s: %s", name, exc)
    return out


__all__ = [
    "REASON", "DEFAULT_LIMIT_PCT", "PROP_FIRM_BUFFER", "UNREADABLE_FLAG_AFTER",
    "BASIS_PCT", "BASIS_PROP", "SwitchConfig", "DailyDDSwitch", "parse_config",
    "prop_firm_terms", "trading_day", "switch_for_account", "load_state",
    "snapshot_equity_reading", "prop_equity_reading", "observe_armed_accounts",
]

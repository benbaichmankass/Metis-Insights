"""Prop executor: the platform-neutral layer above the terminal adapter (step 3).

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2 (boundary),
§ 3.2 (intake + reconcile), § 3.3 (rule guards), § 3.4 (write-back), § 3.5
(kill switch + failure containment). Operator decision 2026-09-28 ~13:35Z
("Build now"): built held off by default; the first real click is the watched
step-3 test.

What one cycle does (:func:`run_cycle`):

1. **Kill switch.** ``PROP_EXECUTOR_MODE=off|read_only|live``, default
   ``read_only``; an unparseable value is ``read_only`` (a typo can never arm
   ``live``). ``off`` returns before touching the page.
2. **Read** balance/equity, positions and working orders off the terminal, in
   THIS cycle. A read that does not parse halts new entries (selector drift).
3. **Reconcile the intent ledger**: every ticket we clicked for is confirmed
   by RE-READ (never by click success), contained per § 3.5, or after
   ``unconfirmed_reads`` cycles reported ``skipped: unconfirmed_submit``.
4. **Reconcile the journal** (open ``prop_fills``) against the terminal by
   ``prop_position_identity`` (account + bot symbol + direction): closed on the
   terminal on two consecutive reads → ``closed``; a terminal position nobody
   placed → orphan: alert, HALT, never touch it; SL/TP differs → ``amend``.
5. **Intake**: ``GET /api/bot/prop/tickets?status=emitted``. The ticket id is
   the idempotency key; a ticket already in the ledger is never acted on again.
6. **Guards** (:func:`evaluate_guards`), local and fail-closed, from the
   balance/equity read in step 2. Anything but "fits" — including "could not
   look" — means no click.
7. **Act on at most one ticket**: ledger ``intended`` is written and fsynced
   BEFORE the click; then ``place_bracket(arm=True)`` (``live`` only); then an
   immediate re-read.

``read_only`` runs 1–6 and logs what it WOULD do: **no click and no write to
the API** (the manual bridge and the feed own those while it is off).

Write-back is only ever ``POST /api/bot/prop/report`` (→ ``ingest_report``).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.prop.platform.base import (
    AccountSnapshot,
    BracketSpec,
    PlaceAttempt,
    Position,
    WorkingOrder,
)

MODE_ENV = "PROP_EXECUTOR_MODE"
MODES = ("off", "read_only", "live")
DEFAULT_MODE = "read_only"

_REPO_ROOT = Path(__file__).resolve().parents[2]
RULESET_PATH = _REPO_ROOT / "config" / "prop_rulesets" / "breakout.yaml"
ROUTING_PATH = _REPO_ROOT / "config" / "prop_rulesets" / "breakout_routing.yaml"
PLATFORMS_PATH = _REPO_ROOT / "config" / "prop_platforms.yaml"


def executor_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """``off`` / ``read_only`` / ``live``. Unset or unparseable → ``read_only``:
    falling back to ``live`` would let a typo arm real clicks, and falling back
    to ``off`` would hide a misconfiguration behind silence."""
    raw = ((env if env is not None else os.environ).get(MODE_ENV) or "").strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


# ── config ────────────────────────────────────────────────────────────────


@dataclass
class ExecutorConfig:
    account_id: str = "breakout_1"
    account_size_usd: float = 5000.0
    daily_loss_pct: float = 0.03
    max_dd_pct: float = 0.06
    daily_reset_utc: str = "00:30"
    safety_margin_usd: float = 5.0
    risk_cap_usd: Optional[float] = 75.0      # breakout.yaml sizing.flat.max_risk_usd
    unconfirmed_reads: int = 3
    confirm_rel_tol: float = 0.02
    # bot symbol → {venue, cvpp, lot_units, min_lots, lot_step}
    symbols: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    watched_click_max_lots: Dict[str, float] = field(default_factory=dict)
    # accounts.yaml risk.breach_guards (enforce | report), the SAME key the
    # RiskManager and the ticket caveat read. ``report`` = the static-DD /
    # daily-loss / cushion-exceeded verdicts are alerts, not refusals.
    breach_guards: str = "enforce"
    # Venue symbols the executor may act on (manager 2026-09-28: SOL-only
    # go-live until symbol switching lands). A ticket for any other venue is
    # SKIPPED (`symbol_not_enabled`) before the form is touched: never a
    # refusal, never a latch count. None = no filter (programmatic use);
    # load_config reads the YAML and a missing key enables NOTHING.
    enabled_venue_symbols: Optional[List[str]] = None


SYMBOLS_ENV = "PROP_EXECUTOR_SYMBOLS"


def enabled_venues(ex: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> List[str]:
    """``PROP_EXECUTOR_SYMBOLS`` (comma-separated) when set, else
    ``executor.enabled_venue_symbols``; missing = [] (fail closed)."""
    raw = (env if env is not None else os.environ).get(SYMBOLS_ENV)
    vals = raw.split(",") if raw is not None and raw.strip() else (ex.get("enabled_venue_symbols") or [])
    return sorted({str(v).strip().upper() for v in vals if str(v).strip()})


def load_config(account_id: str = "breakout_1") -> ExecutorConfig:
    """Read the numbers from the YAML that already owns them; nothing here is
    a second copy of a rule."""
    import yaml

    rules = yaml.safe_load(RULESET_PATH.read_text()) or {}
    routing = yaml.safe_load(ROUTING_PATH.read_text()) or {}
    plat = (yaml.safe_load(PLATFORMS_PATH.read_text()) or {}).get("accounts", {}).get(account_id) or {}
    ex = plat.get("executor") or {}
    lim = rules.get("limits") or {}
    sizing = rules.get("sizing") or {}
    cap = None
    if sizing.get("mode") == "flat":
        cap = (sizing.get("flat") or {}).get("max_risk_usd")
    lots = ex.get("lots") or {}
    syms: Dict[str, Dict[str, Any]] = {}
    # The venue symbol is per terminal: ``<platform>_symbol`` in the routing
    # map (dxtrade_symbol today). A platform with no such keys maps nothing,
    # so every ticket fails the structure guard — closed, never guessed.
    sym_key = f"{plat.get('platform') or 'dxtrade'}_symbol"
    for bot_sym, blk in (routing.get("symbols") or {}).items():
        if not isinstance(blk, dict) or not blk.get(sym_key):
            continue
        venue = str(blk[sym_key])
        lot = lots.get(venue) or {}
        syms[str(bot_sym).upper()] = {
            "venue": venue,
            "cvpp": blk.get("contract_value_usd_per_point"),
            "lot_units": lot.get("lot_units"),
            "min_lots": lot.get("min_lots"),
            "lot_step": lot.get("lot_step"),
            "price_step": lot.get("price_step"),
        }
    return ExecutorConfig(
        account_id=account_id,
        account_size_usd=float(rules.get("account_size_usd") or 0) or 5000.0,
        daily_loss_pct=float(lim.get("daily_loss_pct")),
        max_dd_pct=float(lim.get("max_drawdown_pct")),
        daily_reset_utc=str(lim.get("daily_loss_reset_utc") or "00:30"),
        safety_margin_usd=float(ex.get("safety_margin_usd", 5.0)),
        risk_cap_usd=float(cap) if cap is not None else None,
        unconfirmed_reads=int(ex.get("unconfirmed_reads", 3)),
        confirm_rel_tol=float(ex.get("confirm_rel_tol", 0.02)),
        symbols=syms,
        watched_click_max_lots={str(k): float(v) for k, v in (ex.get("watched_click_max_lots") or {}).items()
                                if v is not None},
        breach_guards=_breach_guards_for(account_id),
        enabled_venue_symbols=enabled_venues(ex),
    )


def _breach_guards_for(account_id: str) -> str:
    from src.prop.prop_risk_gate import breach_guards_for
    return breach_guards_for(account_id)


# ── pure helpers ──────────────────────────────────────────────────────────


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def trading_day(now: datetime, reset_utc: str = "00:30") -> str:
    """The prop day a moment belongs to: days roll at ``reset_utc`` (UTC)."""
    hh, mm = (int(p) for p in reset_utc.split(":"))
    return (now.astimezone(timezone.utc) - timedelta(hours=hh, minutes=mm)).date().isoformat()


def _close(a: Optional[float], b: Optional[float], rel: float) -> bool:
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-9)


def _dir(side: Optional[str]) -> Optional[str]:
    s = (side or "").strip().lower()
    return {"buy": "long", "long": "long", "sell": "short", "short": "short"}.get(s)


def round_to_step(x: Optional[float], step: Optional[float]) -> Optional[float]:
    """A price rounded to the venue's declared increment (nearest), or
    unchanged when no increment is declared. Typing a price the venue cannot
    represent leaves the terminal to round it (dry run #13965: 119.2002 read
    back as 119.2), so the executor rounds first and the read-back compares
    what it actually meant."""
    v, st = _f(x), _f(step)
    if v is None or not st or st <= 0:
        return v
    return round(round(v / st) * st, 10)


def size_lots(qty_units: Optional[float], sym: Mapping[str, Any]) -> Tuple[Optional[float], str]:
    """Ticket units → venue lots, rounded DOWN to the lot step. ``(None, why)``
    when the lot size is not declared (unmeasured) or the size rounds below the
    venue minimum — never rounded UP, which would add risk."""
    q = _f(qty_units)
    lot_units, step, mn = _f(sym.get("lot_units")), _f(sym.get("lot_step")), _f(sym.get("min_lots"))
    if q is None or q <= 0:
        return None, "ticket carries no positive qty"
    if not lot_units or not step:
        return None, (f"lot size for {sym.get('venue')} is not declared "
                      f"(config/prop_platforms.yaml executor.lots; unmeasured)")
    lots = math.floor((q / lot_units) / step + 1e-9) * step
    lots = round(lots, 10)
    if lots <= 0:
        return None, "size rounds to zero lots"
    if mn and lots < mn:
        return None, f"size {lots} lots is below the venue minimum {mn}"
    return lots, ""


def bracket_from_ticket(ticket: Mapping[str, Any], cfg: ExecutorConfig,
                        max_lots: Optional[float] = None) -> Tuple[Optional[BracketSpec], Dict[str, Any], str]:
    """(spec, facts, refusal). ``facts`` carries the sizing arithmetic the
    guards grade (``ticket_risk_usd`` is recomputed from the LOTS actually
    typed, never taken from the ticket's own claim)."""
    bot = str(ticket.get("symbol") or "").upper()
    sym = cfg.symbols.get(bot)
    if not sym:
        return None, {}, f"symbol {bot or '?'} is not in breakout_routing.yaml"
    side = _dir(ticket.get("direction"))
    entry, sl, tp = _f(ticket.get("entry")), _f(ticket.get("sl")), _f(ticket.get("tp"))
    if side is None:
        return None, {}, "ticket has no long/short direction"
    if sl is None or tp is None:
        return None, {}, "ticket lacks SL or TP (never placed without both)"
    if entry is None:
        return None, {}, "ticket has no entry price"
    lots, why = size_lots(ticket.get("qty"), sym)
    if lots is None:
        return None, {}, why
    if max_lots is not None and max_lots < lots:
        # Re-apply the venue step/minimum to the capped size: a cap is never
        # a way to type a size the venue would reject or round.
        lots, why = size_lots(max_lots * float(sym["lot_units"]), sym)
        if lots is None:
            return None, {}, f"watched-click size: {why}"
    cvpp = _f(sym.get("cvpp"))
    if cvpp is None:
        return None, {}, f"contract value per point for {bot} is not declared"
    units = lots * float(sym["lot_units"])
    # Prices are typed at the venue's increment when it is declared; the risk
    # the guards grade is computed from the values actually typed.
    ps = _f(sym.get("price_step"))
    entry, sl, tp = round_to_step(entry, ps), round_to_step(sl, ps), round_to_step(tp, ps)
    risk = units * abs(entry - sl) * cvpp
    spec = BracketSpec(ticket_id=str(ticket.get("ticket_id") or ""), venue_symbol=sym["venue"],
                       side=side, quantity=lots, stop_loss=sl, take_profit=tp,
                       order_type="limit", limit_price=entry, price_step=ps)
    return spec, {"lots": lots, "units": units, "ticket_risk_usd": round(risk, 2),
                  "ticket_claimed_risk_usd": _f(ticket.get("risk_usd")), "cvpp": cvpp}, ""


def open_risk(positions: Sequence[Position], orders: Sequence[WorkingOrder],
              cfg: ExecutorConfig) -> Tuple[Optional[float], str]:
    """$ the account can still lose to the stops of what is on the terminal:
    open positions (unrealized minus P&L-at-stop) plus resting entry orders
    (their full entry→stop risk). ``(None, state)`` when any leg's stop, size,
    unrealized P&L or contract value cannot be read — an unknown stop can lose
    any amount. States mirror ``compute_rule_distance``'s
    ``after_open_risk_state``."""
    by_venue = {v["venue"].upper(): v for v in cfg.symbols.values()}
    total = 0.0
    for p in positions:
        sym = by_venue.get(p.symbol.upper())
        cv, lu = (_f(sym.get("cvpp")), _f(sym.get("lot_units"))) if sym else (None, None)
        if p.stop_loss is None:
            return None, "stop_unknown"
        if p.unrealized_pnl is None:
            return None, "unrealized_unreported"
        if None in (cv, lu, p.quantity, p.entry_price) or p.side not in ("long", "short"):
            return None, "unreadable"
        units = p.quantity * lu
        pnl_at_stop = units * cv * ((p.stop_loss - p.entry_price) if p.side == "long"
                                    else (p.entry_price - p.stop_loss))
        total += max(0.0, p.unrealized_pnl - pnl_at_stop)
    for o in orders:
        sym = by_venue.get(o.symbol.upper())
        cv, lu = (_f(sym.get("cvpp")), _f(sym.get("lot_units"))) if sym else (None, None)
        if o.stop_loss is None:
            return None, "stop_unknown"
        if None in (cv, lu, o.quantity, o.price):
            return None, "unreadable"
        total += o.quantity * lu * cv * abs(o.price - o.stop_loss)
    if not positions and not orders:
        return 0.0, "no_open_positions"
    return round(total, 2), "measured"


@dataclass
class GuardVerdict:
    fits: bool
    reasons: List[str]
    checks: Dict[str, Any]
    # Breach verdicts that did NOT refuse because the account is
    # ``breach_guards: report``: raised as alerts, never silently dropped.
    breach_reports: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"fits": self.fits, "reasons": self.reasons, "checks": self.checks,
                "breach_reports": self.breach_reports}


def evaluate_guards(*, ticket: Mapping[str, Any], spec: Optional[BracketSpec], facts: Mapping[str, Any],
                    refusal: str, account: AccountSnapshot, day_start_balance: Optional[float],
                    open_risk_usd: Optional[float], open_risk_state: str, cfg: ExecutorConfig,
                    now: datetime, halted: Optional[str] = None) -> GuardVerdict:
    """§ 3.3, pure. Every check runs (so the report names every reason), and
    the verdict fits only when NONE refused. "Could not look" refuses.

    Two kinds of "no". HARD, in every mode: halt, structure, expiry, balance/
    equity not read, open risk not readable, and the risk gate's
    ``cushion_unknown`` (could not look), and a risk above the flat $-cap
    (the sizing decision). BREACH, whose outcome depends on
    ``cfg.breach_guards``: static DD, daily loss and the gate's
    ``exceeds_cushion``. ``enforce`` refuses on them;
    ``report`` (breakout_1, operator 2026-09-28) moves them to
    ``breach_reports`` and places anyway."""
    from src.prop import prop_risk_gate
    from src.prop.platform.dxtrade import check_bracket_spec

    reasons: List[str] = []
    breach: List[str] = []
    checks: Dict[str, Any] = {"breach_guards": cfg.breach_guards}
    if halted:
        reasons.append(f"executor halted: {halted}")
    # structure
    if refusal:
        reasons.append(f"structure: {refusal}")
    elif spec is not None:
        for b in check_bracket_spec(spec):
            reasons.append(f"structure: {b}")
    # validity
    vu = _parse_ts(ticket.get("valid_until"))
    if vu is None:
        reasons.append("expiry: ticket has no readable valid_until")
    elif vu <= now:
        reasons.append(f"expiry: expired at {vu.isoformat()}")
    # account read, this cycle
    eq, bal = account.equity, account.balance
    if eq is None or bal is None:
        reasons.append("account: balance/equity not read this cycle (could not look)")
    if open_risk_usd is None:
        reasons.append(f"open risk: {open_risk_state} (could not look)")
    risk = _f(facts.get("ticket_risk_usd"))
    floor = cfg.account_size_usd * (1.0 - cfg.max_dd_pct)
    daily_floor = day_start_balance * (1.0 - cfg.daily_loss_pct) if day_start_balance else None
    checks.update(equity=eq, balance=bal, open_risk_usd=open_risk_usd, open_risk_state=open_risk_state,
                  ticket_risk_usd=risk, dd_floor=floor, daily_floor=daily_floor,
                  day_start_balance=day_start_balance, margin=cfg.safety_margin_usd)
    if daily_floor is None:
        # Feeds ONLY the daily-loss breach guard, so it is a breach item.
        breach.append("daily loss: day-start balance unknown for this prop day (could not look)")
    if None not in (eq, open_risk_usd, risk):
        after = eq - open_risk_usd - risk
        checks["equity_after_all_stops"] = round(after, 2)
        if after <= floor + cfg.safety_margin_usd:
            breach.append(f"static DD: equity after all stops ${after:,.2f} <= floor ${floor:,.2f} "
                          f"+ margin ${cfg.safety_margin_usd:,.2f}")
        if daily_floor is not None and after <= daily_floor + cfg.safety_margin_usd:
            breach.append(f"daily loss: equity after all stops ${after:,.2f} <= day floor "
                          f"${daily_floor:,.2f} + margin ${cfg.safety_margin_usd:,.2f}")
    # prop_risk_gate in ENFORCE for the executor, whatever the global default
    if eq is not None and open_risk_usd is not None:
        grade = prop_risk_gate.grade_ticket_risk(
            risk_usd=risk,
            distance_to_dd_floor_usd=eq - open_risk_usd - floor,
            distance_to_daily_loss_usd=(eq - open_risk_usd - daily_floor) if daily_floor is not None else None,
            status_freshness="ok",
            open_risk_state=open_risk_state,
            open_loss_to_stop_usd=open_risk_usd)
    else:
        grade = prop_risk_gate.grade_ticket_risk(risk_usd=risk, status_freshness="unreadable")
    checks["risk_gate"] = {"state": grade.get("state"), "reason": grade.get("reason")}
    if grade.get("state") == prop_risk_gate.EXCEEDS:
        breach.append(f"risk gate: {grade.get('state')} ({grade.get('reason')})")
    elif grade.get("state") != prop_risk_gate.WITHIN:
        # cushion_unknown / no_risk_declared: could not look → HARD in every mode
        reasons.append(f"risk gate: {grade.get('state')} ({grade.get('reason')})")
    if risk is not None:
        cap = prop_risk_gate.enforce_ticket_cap(risk_usd=risk, cap_usd=cfg.risk_cap_usd,
                                                gate_mode="enforce", sizing_mode="flat")
        checks["risk_cap"] = {"action": cap.get("action"), "cap_usd": cfg.risk_cap_usd}
        # The $-cap is the SIZING decision (flat $75, operator), not a breach
        # guard: above it is HARD in every mode.
        if cap.get("action") != "unchanged":
            reasons.append(f"risk cap: {cap.get('action')} ({cap.get('cause')})")
    if cfg.breach_guards == "report":
        return GuardVerdict(fits=not reasons, reasons=reasons, checks=checks, breach_reports=breach)
    reasons.extend(breach)
    return GuardVerdict(fits=not reasons, reasons=reasons, checks=checks)


def match_terminal(spec: Mapping[str, Any], positions: Sequence[Position],
                   orders: Sequence[WorkingOrder], rel: float = 0.02) -> Dict[str, Any]:
    """Re-read confirmation of one submitted bracket, by (symbol, side, qty,
    price). Returns ``{"orders": [...], "positions": [...]}`` of the matches;
    the caller decides placed / open / partial / duplicate."""
    venue = str(spec.get("venue_symbol") or "").upper()
    side = spec.get("side")
    qty = _f(spec.get("quantity"))
    px = _f(spec.get("limit_price"))
    om = [o for o in orders if o.symbol.upper() == venue and _dir(o.side) == side
          and _close(o.quantity, qty, rel) and (px is None or _close(o.price, px, rel))]
    pm = [p for p in positions if p.symbol.upper() == venue and p.side == side
          and _close(p.quantity, qty, rel)]
    return {"orders": om, "positions": pm}


def classify_confirmation(spec: Mapping[str, Any], found: Mapping[str, Any], rel: float = 0.02) -> str:
    """``placed`` / ``open`` / ``partial_no_sl_tp`` / ``duplicate`` / ``not_found``. Pure."""
    om, pm = list(found.get("orders") or []), list(found.get("positions") or [])
    if len(om) + len(pm) > 1:
        return "duplicate"
    sl, tp = _f(spec.get("stop_loss")), _f(spec.get("take_profit"))
    for leg in om + pm:
        if not (_close(leg.stop_loss, sl, rel) and _close(leg.take_profit, tp, rel)):
            return "partial_no_sl_tp"
    if pm:
        return "open"
    if om:
        return "placed"
    return "not_found"


def open_from_fills(fills: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Open positions from ``GET /api/bot/prop/fills`` rows: the NEWEST row per
    position identity (account + symbol + direction), kept when its status is
    ``open``/``filled`` — the same derivation ``find_open_prop_positions`` uses
    in-process (``prop_monitor_pulse``), done here over the API because the
    executor never opens the DB."""
    newest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in fills:
        key = (str(r.get("account_id") or ""), str(r.get("symbol") or "").upper(), _dir(r.get("direction")) or "")
        cur = newest.get(key)
        stamp = (str(r.get("updated_at") or r.get("created_at") or ""), int(r.get("id") or 0))
        if cur is None or stamp > cur["_stamp"]:
            newest[key] = {**r, "_stamp": stamp}
    return [{k: v for k, v in r.items() if k != "_stamp"} for r in newest.values()
            if str(r.get("status") or "").lower() in ("open", "filled")]


class LocalApi:
    """The executor's only line to the system: the local FastAPI (§ 3.1).
    ``transport(method, path, body) -> dict`` is injectable for tests."""

    def __init__(self, base: str = "http://127.0.0.1:8001", token: str = "",
                 transport: Optional[Callable[[str, str, Optional[Dict[str, Any]]], Dict[str, Any]]] = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self._transport = transport or self._http

    def _http(self, method: str, path: str, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        import urllib.request
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (localhost)
            return json.loads(resp.read().decode() or "{}")

    def tickets(self, account_id: str) -> List[Dict[str, Any]]:
        got = self._transport("GET", f"/api/bot/prop/tickets?account_id={account_id}&status=emitted&limit=50", None)
        if not got.get("present", True) and not got.get("tickets"):
            raise RuntimeError("ticket store not readable (present:false)")
        return list(got.get("tickets") or [])

    def open_fills(self, account_id: str) -> List[Dict[str, Any]]:
        got = self._transport("GET", f"/api/bot/prop/fills?account_id={account_id}&limit=500", None)
        if not got.get("present", True):
            raise RuntimeError("fills store not readable (present:false)")
        return open_from_fills(got.get("fills") or [])

    def post_report(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._transport("POST", "/api/bot/prop/report", body)


# ── the intent ledger (idempotency key = ticket id) ───────────────────────


class IntentLedger:
    """Append-only JSONL, one line per state change, fsynced before return.

    States: ``intended`` (written BEFORE the click) → ``submitted`` →
    ``placed`` / ``open`` (confirmed by re-read) | ``unconfirmed`` →
    ``skipped`` / ``contained``. ``refused`` / ``expired`` record tickets the
    executor decided not to place, so it never re-decides them.

    ``close_unconfirmed`` is a watched close that did not confirm flat
    (round trip or close-out): the position may still be open under its
    bracket, and the JOURNAL row for it is what the reconcile consults. It
    is NOT an unresolved submit — go-live 2026-09-29 18:44Z: two such rows,
    written ``unconfirmed`` by the day's test round trips whose positions
    the venue had long since closed, were read as unconfirmed SUBMITS, hit
    three misses and latched AUTO-REVERT on nothing (PI-20260929-AQRK6CL1-0009)."""

    UNRESOLVED = ("intended", "submitted", "unconfirmed")
    #: A close that did not confirm: linkable by a later close-out, never
    #: reconciled as a submit.
    CLOSE_UNRESOLVED = ("close_unconfirmed",)
    #: The purposes under which the pre-fix code wrote such a close as
    #: "unconfirmed" (ledgers on disk keep those rows forever): read as a
    #: close, not a submit, so a legacy row can never trip the reconcile again.
    CLOSE_PURPOSES = ("round_trip_close", "close_position")

    @classmethod
    def is_close_row(cls, row: Mapping[str, Any]) -> bool:
        return row.get("state") in cls.CLOSE_UNRESOLVED or (
            row.get("state") == "unconfirmed" and row.get("purpose") in cls.CLOSE_PURPOSES)

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _rows(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue  # a torn last line is skipped, never fatal
        return out

    def latest(self) -> Dict[str, Dict[str, Any]]:
        cur: Dict[str, Dict[str, Any]] = {}
        for r in self._rows():
            tid = r.get("ticket_id")
            if tid:
                cur[tid] = {**cur.get(tid, {}), **r}
        return cur

    def state(self, ticket_id: str) -> Optional[str]:
        return (self.latest().get(ticket_id) or {}).get("state")

    def unresolved(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in self.latest().items()
                if v.get("state") in self.UNRESOLVED and not self.is_close_row(v)}

    def watched(self) -> Dict[str, Dict[str, Any]]:
        """Unresolved submits plus resting ``placed`` orders (waiting to fill).
        A close that did not confirm (new state, or a legacy "unconfirmed"
        row with a close purpose) is never a submit and is never watched."""
        return {k: v for k, v in self.latest().items()
                if v.get("state") in self.UNRESOLVED + ("placed",) and not self.is_close_row(v)}

    def close_unresolved(self) -> Dict[str, Dict[str, Any]]:
        """Closes that did not confirm flat. Each HOLDS new entries (the
        position may still be live under its bracket) until a clean terminal
        read shows no position for its symbol — ``run_cycle`` step 3b."""
        return {k: v for k, v in self.latest().items() if self.is_close_row(v)}

    def record(self, ticket_id: str, state: str, **extra: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(timezone.utc).isoformat(), "ticket_id": ticket_id, "state": state, **extra}
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(row, default=str) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)


# ── small persisted state (day-start balance, halt, absence counters) ─────


class ExecutorState:
    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir)
        self.file = self.dir / "executor_state.json"
        self.halt_file = self.dir / "halted"

    def load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.file.read_text())
        except Exception:
            return {}

    def save(self, st: Mapping[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(".executor_state.json.tmp")
        tmp.write_text(json.dumps(st, default=str))
        os.replace(tmp, self.file)

    def halted(self) -> Optional[str]:
        if self.halt_file.exists():
            try:
                return self.halt_file.read_text().strip() or "halted (no reason recorded)"
            except Exception:
                return "halted (marker unreadable)"
        return None

    def halt(self, reason: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.halt_file.exists():
            self.halt_file.write_text(f"{datetime.now(timezone.utc).isoformat()} {reason}\n")


# ── auto-revert: permanent trip conditions (operator go-live criteria,
# 2026-09-28, checklist row PROP-EXEC) ─────────────────────────────────────
# Each one writes the ``halted`` latch, which refuses every new entry (HARD
# in every mode) until a person clears it, while reconcile and containment
# keep running, and raises an alert the tick pings. The latch is the
# in-process revert; the env / timer revert is the system-action run on the
# alert.
TRIP_CONSECUTIVE_ERRORS = 2
TRIP_READBACK_REFUSALS = 2


def _trip(res: "CycleResult", state: "ExecutorState", live: bool, why: str) -> str:
    """Latch ``why`` (live only: a read_only cycle never writes state that a
    live one would obey) and alert. Returns ``why`` for the caller's halt."""
    reason = f"AUTO-REVERT: {why}"
    if live:
        state.halt(reason)
    res.alerts.append(reason + " — new entries halted until cleared")
    return reason


def record_tick_error(state_dir: Path, live: bool, why: str) -> Optional[str]:
    """A tick that raised before its cycle finished counts as an executor
    error; ``TRIP_CONSECUTIVE_ERRORS`` in a row trips the latch. Returns the
    trip reason, or None."""
    state = ExecutorState(Path(state_dir))
    st = state.load()
    n = int(st.get("consecutive_errors") or 0) + 1
    st["consecutive_errors"] = n
    state.save(st)
    if n >= TRIP_CONSECUTIVE_ERRORS and live:
        reason = f"AUTO-REVERT: executor errors on {n} consecutive ticks (last: {why})"
        state.halt(reason)
        return reason
    return None


def day_start_balance(st: Dict[str, Any], account: AccountSnapshot, now: datetime,
                      reset_utc: str) -> Optional[float]:
    """The prop day's opening balance, the conservative (HIGHER) of: the
    balance captured at this executor's first read of the day, and
    ``balance − realized_today`` when the terminal shows today's realized P&L.
    A higher day-start is a higher daily floor, i.e. fewer tickets fit.
    ``None`` (→ the daily guard refuses) when neither is available."""
    day = trading_day(now, reset_utc)
    if st.get("day") != day and account.balance is not None:
        st["day"], st["day_start_captured"] = day, account.balance
    cands = []
    if st.get("day") == day and _f(st.get("day_start_captured")) is not None:
        cands.append(float(st["day_start_captured"]))
    if account.balance is not None and account.realized_today is not None:
        cands.append(account.balance - account.realized_today)
    return max(cands) if cands else None


# ── the cycle ─────────────────────────────────────────────────────────────


@dataclass
class CycleResult:
    mode: str
    reads: Dict[str, Any] = field(default_factory=dict)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    reports: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)
    halted: Optional[str] = None

    def log(self, what: str, **kw: Any) -> None:
        self.actions.append({"what": what, **kw})


def _close_row_key(tid: str, row: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(venue symbol, side) of a close row: from its spec when the row carries
    one, else the venue spelled in a ``roundtrip-<venue>-`` / ``closeout-<venue>-``
    id (a legacy close-out row recorded no spec). ``(None, None)`` when neither
    says — the caller then keeps holding rather than resolving blindly."""
    spec = row.get("spec") or {}
    venue = str(spec.get("venue_symbol") or "").upper() or None
    side = spec.get("side")
    if venue is None:
        parts = str(tid or "").split("-")
        if len(parts) >= 3 and parts[0] in ("roundtrip", "closeout") and parts[1]:
            venue = parts[1].upper()
    return venue, side


def _report(res: CycleResult, post: Optional[Callable[[Dict[str, Any]], Any]], body: Dict[str, Any]) -> None:
    """Every write-back goes through POST /api/bot/prop/report. In read_only
    (``post is None``) it is recorded as would-post and not sent."""
    entry = {"body": body, "sent": False}
    if post is not None:
        try:
            entry["response"] = post(body)
            entry["sent"] = True
        except Exception as exc:  # the cycle keeps going; the alert says so
            entry["error"] = f"{type(exc).__name__}: {exc}"
            res.alerts.append(f"write-back failed for {body.get('ticket_id') or body.get('kind')}: {entry['error']}")
    res.reports.append(entry)


def run_cycle(*, adapter: Any, page: Any, api: Any, cfg: ExecutorConfig, mode: str,
              ledger: IntentLedger, state: ExecutorState, now: Optional[datetime] = None,
              walk_form: bool = False, max_lots: Any = None,
              only_ticket_id: Optional[str] = None,
              sleep: Callable[[float], None] = lambda s: None) -> CycleResult:
    """One executor cycle. See the module docstring for the order of steps.

    ``walk_form`` (dry run only): when a ticket FITS, also open the order form,
    type it and read it back (``place_bracket(arm=False)``), then close it.
    ``max_lots`` / ``only_ticket_id``: the watched step-3 test (minimum size,
    one named or newest ticket)."""
    now = now or datetime.now(timezone.utc)
    res = CycleResult(mode=mode)
    if mode == "off":
        res.log("off", why=f"{MODE_ENV}=off: nothing read, nothing clicked")
        return res
    live = mode == "live"
    post = api.post_report if live else None
    st = state.load()

    # 2. read this cycle
    try:
        acct = adapter.read_account(page)
        positions = adapter.read_positions(page)
        orders = adapter.read_orders(page)
    except Exception as exc:
        why = f"terminal read failed ({type(exc).__name__}: {exc}) — selector drift or expired session"
        res.alerts.append(why)
        res.halted = why
        n = int(st.get("consecutive_errors") or 0) + 1
        st["consecutive_errors"] = n
        state.save(st)
        if n >= TRIP_CONSECUTIVE_ERRORS:
            res.halted = _trip(res, state, live, f"executor errors on {n} consecutive ticks ({why})")
        res.log("halt_no_entries", why=res.halted)
        return res
    st["consecutive_errors"] = 0
    res.reads = {"account": acct.as_dict(), "positions": len(positions), "orders": len(orders)}
    if acct.balance is None or acct.equity is None:
        res.alerts.append("balance/equity did not parse this cycle")

    halted = state.halted()
    try:
        journal_open = api.open_fills(cfg.account_id)
    except Exception as exc:
        journal_open = None
        halted = halted or f"journal read failed ({type(exc).__name__})"

    # 3. reconcile the ledger (confirm by re-read, contain per § 3.5)
    claimed_keys = set()
    for tid, row in ledger.watched().items():
        spec = row.get("spec") or {}
        found = match_terminal(spec, positions, orders, cfg.confirm_rel_tol)
        verdict = classify_confirmation(spec, found, cfg.confirm_rel_tol)
        claimed_keys.add((str(spec.get("venue_symbol") or "").upper(), spec.get("side")))
        trip = _contain(res, adapter, page, live, post, ledger, cfg, tid, row, spec, found, verdict)
        if trip:
            halted = halted or _trip(res, state, live, trip)

    # 3b. resolve the closes that did not confirm (review of #14388): the
    #     position may still be live under its bracket, so the row HOLDS new
    #     entries (step 5) but never trips the unconfirmed_submit latch. It
    #     resolves only on a CLEAN read that shows no position for its symbol
    #     (a failed read returned above and is never "flat"); while the
    #     position is still found, one line per tick names the ticket.
    for tid, row in ledger.close_unresolved().items():
        venue, side = _close_row_key(tid, row)
        if not venue:
            res.log("close_pending", ticket_id=tid, why="symbol unknown for this close; holding new entries")
            continue
        # A legacy close-out row recorded no side: the venue is claimed both
        # ways so the journal reconcile does not read the still-open position
        # as an orphan while this close holds it.
        for s in ((side,) if side else ("long", "short")):
            claimed_keys.add((venue, s))
        still = [p for p in positions if p.symbol.upper() == venue and (side is None or p.side == side)]
        if still:
            res.log("close_pending", ticket_id=tid, venue=venue, side=side,
                    why="position still on the terminal after an unconfirmed close; new entries held")
            continue
        res.log("close_resolved", ticket_id=tid, venue=venue, side=side, reason="flat_on_terminal")
        if live:
            ledger.record(tid, "close_confirmed", reason="flat_on_terminal")

    # 4. reconcile the journal against the terminal
    if journal_open is not None:
        halted = _reconcile_journal(res, cfg, st, positions, journal_open, ledger, claimed_keys, post) or halted
    if halted and not state.halted() and live:
        state.halt(halted)
    res.halted = halted

    ds = day_start_balance(st, acct, now, cfg.daily_reset_utc)
    state.save(st)
    if live and (acct.balance is not None or acct.equity is not None):
        _report(res, post, {"kind": "account_status", "account_id": cfg.account_id,
                            "balance": acct.balance, "equity": acct.equity,
                            "unrealized": acct.unrealized, "realized_today": acct.realized_today,
                            "day_start_balance": ds, "source": "prop_executor"})

    # 5. intake
    try:
        tickets = api.tickets(cfg.account_id)
    except Exception as exc:
        res.alerts.append(f"ticket intake failed ({type(exc).__name__}); no entries this cycle")
        return res
    seen = ledger.latest()
    fresh = [t for t in tickets if t.get("ticket_id") and t["ticket_id"] not in seen]
    if only_ticket_id:
        fresh = [t for t in fresh if t["ticket_id"] == only_ticket_id]
    fresh.sort(key=lambda t: str(t.get("created_at") or ""))
    candidate = None
    for t in fresh:
        vu = _parse_ts(t.get("valid_until"))
        if vu is not None and vu <= now:
            res.log("expired", ticket_id=t["ticket_id"])
            if live:
                ledger.record(t["ticket_id"], "expired")
                _report(res, post, _skip_body(cfg, t, "expired"))
            continue
        venue = (cfg.symbols.get(str(t.get("symbol") or "").upper()) or {}).get("venue")
        if cfg.enabled_venue_symbols is not None and str(venue or "").upper() not in cfg.enabled_venue_symbols:
            # Skipped BEFORE the form: not a refusal, no latch count (an ETH
            # ticket stays manual via Telegram until symbol switching lands).
            res.log("skipped", ticket_id=t["ticket_id"], reason="symbol_not_enabled", venue=venue)
            if live:
                ledger.record(t["ticket_id"], "skipped", reason="symbol_not_enabled")
                _report(res, post, _skip_body(cfg, t, "symbol_not_enabled"))
            continue
        if candidate is None:
            candidate = t
    if candidate is None:
        res.log("no_ticket")
        return res
    if ledger.unresolved():
        res.log("hold", ticket_id=candidate["ticket_id"], why="an earlier submit is still unresolved")
        return res
    pending_close = ledger.close_unresolved()
    if pending_close:
        res.log("hold", ticket_id=candidate["ticket_id"],
                why=f"an earlier close did not confirm and its position may still be live: {sorted(pending_close)}")
        return res

    # 6. guards
    orisk, ostate = open_risk(positions, orders, cfg)
    ml: Optional[float] = None
    ml_refusal = ""
    if isinstance(max_lots, Mapping):  # the watched click: per-venue minimum size
        venue = (cfg.symbols.get(str(candidate.get("symbol") or "").upper()) or {}).get("venue")
        ml = _f(max_lots.get(venue)) if venue else None
        if ml is None:
            ml_refusal = f"watched click: no watched_click_max_lots entry for {venue or '?'}"
    elif max_lots is not None:
        ml = float(max_lots)
    spec, facts, refusal = bracket_from_ticket(candidate, cfg, max_lots=ml)
    if ml_refusal:
        spec, refusal = None, ml_refusal
    # HARD: the symbol already carries a position or working order. On a
    # netting account a new bracket would merge into it, the re-read could not
    # match the ticket (reported unconfirmed_submit) and exposure would double
    # while the journal never learns (manager review of #13647).
    if spec is not None and not refusal:
        busy = [p for p in positions if p.symbol.upper() == spec.venue_symbol.upper()] + \
               [o for o in orders if o.symbol.upper() == spec.venue_symbol.upper()]
        if busy:
            refusal = (f"{spec.venue_symbol} already has {len(busy)} position(s)/working order(s) "
                       f"on the terminal; one bracket per symbol")
    v = evaluate_guards(ticket=candidate, spec=spec, facts=facts, refusal=refusal, account=acct,
                        day_start_balance=ds, open_risk_usd=orisk, open_risk_state=ostate,
                        cfg=cfg, now=now, halted=halted)
    res.log("guards", ticket_id=candidate["ticket_id"], **v.as_dict(), facts=dict(facts))
    for b in v.breach_reports:
        res.alerts.append(f"{candidate['ticket_id']}: breach_guards=report, placing anyway — {b}")
    if not v.fits:
        if live:
            ledger.record(candidate["ticket_id"], "refused", reasons=v.reasons)
            _report(res, post, _skip_body(cfg, candidate, "; ".join(v.reasons)))
        return res

    # 7. act on this one ticket
    assert spec is not None
    if not live:
        if walk_form:
            att = adapter.place_bracket(page, spec, arm=False)
            res.log("would_click", ticket_id=spec.ticket_id, spec=spec.as_dict(), walk=_attempt_public(att))
        else:
            res.log("would_click", ticket_id=spec.ticket_id, spec=spec.as_dict())
        return res
    ledger.record(spec.ticket_id, "intended", spec=spec.as_dict(), facts=dict(facts))
    att: PlaceAttempt = adapter.place_bracket(page, spec, arm=True)
    res.log("place_bracket", ticket_id=spec.ticket_id, attempt=_attempt_public(att))
    st = state.load()
    if not att.submitted:
        ledger.record(spec.ticket_id, "refused", reasons=[att.detail])
        _report(res, post, _skip_body(cfg, candidate, f"not submitted: {att.detail}"))
        if "read-back" in str(att.detail or ""):
            n = int(st.get("readback_refusals") or 0) + 1
            st["readback_refusals"] = n
            state.save(st)
            if n >= TRIP_READBACK_REFUSALS:
                res.halted = _trip(res, state, live, f"{n} consecutive read-back refusals (last: {att.detail})")
        return res
    st["readback_refusals"] = 0
    state.save(st)
    ledger.record(spec.ticket_id, "submitted", detail=att.detail)
    # immediate re-read; a miss here stays `submitted` and later cycles decide
    sleep(3.0)
    try:
        positions = adapter.read_positions(page)
        orders = adapter.read_orders(page)
    except Exception as exc:
        res.alerts.append(f"re-read after submit failed ({type(exc).__name__}); left UNCONFIRMED")
        return res
    row = ledger.latest()[spec.ticket_id]
    found = match_terminal(spec.as_dict(), positions, orders, cfg.confirm_rel_tol)
    verdict = classify_confirmation(spec.as_dict(), found, cfg.confirm_rel_tol)
    trip = _contain(res, adapter, page, live, post, ledger, cfg, spec.ticket_id, row, spec.as_dict(), found, verdict)
    if trip:
        res.halted = _trip(res, state, live, trip)
    return res


def run_round_trip(*, adapter: Any, page: Any, api: Any, cfg: ExecutorConfig, ledger: IntentLedger,
                   venue_symbol: str, side: str = "long", lots: Optional[float] = None,
                   bracket_pct: float = 0.01, arm: bool = False, reads: int = 20,
                   sleep: Callable[[float], None] = lambda s: None,
                   now: Optional[datetime] = None) -> CycleResult:
    """The end-to-end test (operator 2026-09-28 ~13:40Z, relayed by the
    manager, approved by the operator in this lane's popup): place ONE
    minimum-size MARKET bracket with SL and TP attached → confirm the position
    AND both legs by re-read → report ``open`` → CLOSE it at market → confirm
    flat by re-read → report ``closed``. Both reports go through
    ``POST /api/bot/prop/report``.

    ``lots`` defaults to ``watched_click_max_lots[venue]`` and may never exceed
    it; the symbol's lot size must be declared. It refuses when the symbol
    already has a position or working order, so it can never net against or
    close something it did not open. With ``arm=False`` it walks the form to
    ``form_verified`` and the close to the row control, clicking neither.
    A breach verdict does not stop it (``breach_guards: report`` is the only
    account this runs against); "could not look" does.
    """
    now = now or datetime.now(timezone.utc)
    res = CycleResult(mode="round_trip_live" if arm else "round_trip_dry")
    post = api.post_report if arm else None
    venue = venue_symbol.upper()
    sym = next((v for v in cfg.symbols.values() if str(v["venue"]).upper() == venue), None)
    cap = cfg.watched_click_max_lots.get(venue_symbol, cfg.watched_click_max_lots.get(venue))

    def stop(why: str) -> CycleResult:
        res.halted = why
        res.log("refused", why=why)
        return res

    if sym is None:
        return stop(f"{venue} is not in breakout_routing.yaml")
    if cfg.enabled_venue_symbols is not None and venue not in cfg.enabled_venue_symbols:
        return stop(f"{venue} is not in executor.enabled_venue_symbols {cfg.enabled_venue_symbols}")
    if not _f(sym.get("lot_units")) or not _f(sym.get("lot_step")):
        return stop(f"lot size for {venue} is not declared (executor.lots; unmeasured)")
    if cap is None:
        return stop(f"no executor.watched_click_max_lots entry for {venue}")
    lots = cap if lots is None else lots
    if not (lots > 0) or lots > cap:
        return stop(f"lots {lots} must be > 0 and <= watched_click_max_lots {cap}")
    stepped, why = size_lots(lots * float(sym["lot_units"]), sym)
    if stepped is None or abs(stepped - lots) > 1e-9:
        return stop(f"lots {lots} is not a valid venue size ({why or f'nearest step is {stepped}'})")
    if side not in ("long", "short"):
        return stop(f"side {side!r} is not long/short")
    try:
        acct = adapter.read_account(page)
        positions = adapter.read_positions(page)
        orders = adapter.read_orders(page)
        quote = adapter.read_quote(page, venue)
    except Exception as exc:
        return stop(f"terminal read failed ({type(exc).__name__}: {exc})")
    res.reads = {"account": acct.as_dict(), "positions": len(positions), "orders": len(orders), "quote": quote}
    if acct.balance is None or acct.equity is None:
        return stop("balance/equity did not parse (could not look)")
    if any(p.symbol.upper() == venue for p in positions) or any(o.symbol.upper() == venue for o in orders):
        return stop(f"{venue} already has a position or working order; the test only closes what it opened")
    if not quote:
        diag = getattr(adapter, "quote_diagnostics", None)
        if diag is not None:
            res.log("quote_diagnostics", venue=venue, watchlist=diag(page, venue))
        return stop(f"no bid/ask for {venue} in the watchlist (could not look)")
    ref = quote["ask"] if side == "long" else quote["bid"]
    sl = ref * (1 - bracket_pct) if side == "long" else ref * (1 + bracket_pct)
    tp = ref * (1 + bracket_pct) if side == "long" else ref * (1 - bracket_pct)
    # Typed at the venue's price increment when declared (dry run #13965: the
    # terminal rounded a typed 119.2002 to 119.2 and the exact read-back
    # refused; the criterion is "within one tick").
    ps = _f(sym.get("price_step"))
    sl, tp = round_to_step(round(sl, 6), ps), round_to_step(round(tp, 6), ps)
    tid = f"roundtrip-{venue.lower()}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    spec = BracketSpec(ticket_id=tid, venue_symbol=venue, side=side, quantity=float(lots),
                       stop_loss=sl, take_profit=tp, order_type="market", price_step=ps)
    risk = float(lots) * float(sym["lot_units"]) * float(_f(sym.get("cvpp")) or 1.0) * abs(ref - sl)
    res.log("round_trip_spec", spec=spec.as_dict(), ref_price=ref, risk_at_stop_usd=round(risk, 2))

    if arm:
        ledger.record(tid, "intended", spec=spec.as_dict(), purpose="round_trip_test")
    att = adapter.place_bracket(page, spec, arm=arm)
    res.log("place_bracket", ticket_id=tid, attempt=_attempt_public(att))
    if not arm:
        res.log("would_close", ticket_id=tid, result=adapter.flatten(page, venue, arm=False))
        return res
    if not att.submitted:
        ledger.record(tid, "refused", reasons=[att.detail])
        return stop(f"not submitted: {att.detail}")
    ledger.record(tid, "submitted", detail=att.detail)

    # 1. confirm entry + both legs by re-read (a market fill's price is not ours: match symbol/side/qty).
    #    ``reads`` x 3 s: 20 reads = the 60 s window criterion L2 registers
    #    (live test #13987 stopped after 5 reads = 15 s).
    confirm_spec = {**spec.as_dict(), "limit_price": None}
    verdict, found = "not_found", {"orders": [], "positions": []}
    for _ in range(max(1, reads)):
        sleep(3.0)
        try:
            positions, orders = adapter.read_positions(page), adapter.read_orders(page)
        except Exception as exc:
            res.alerts.append(f"{tid}: re-read failed ({type(exc).__name__})")
            continue
        found = match_terminal(confirm_spec, positions, orders, cfg.confirm_rel_tol)
        verdict = classify_confirmation(confirm_spec, found, cfg.confirm_rel_tol)
        if verdict != "not_found":
            break
    res.log("confirm_entry", ticket_id=tid, verdict=verdict)
    if verdict != "open":
        _contain(res, adapter, page, True, post, ledger, cfg, tid, ledger.latest()[tid], confirm_spec, found, verdict)
        res.alerts.append(f"{tid}: entry not confirmed as an open bracketed position ({verdict}); "
                          f"not closing blind — the broker bracket protects any fill")
        res.halted = f"round trip stopped at entry ({verdict})"
        return res
    pos = found["positions"][0]
    ledger.record(tid, "open")
    _report(res, post, {**_fill_body(cfg, {"ticket_id": tid}, spec.as_dict(), "open", entry=pos.entry_price),
                        "reason": "round_trip_test"})

    # 2. close through the terminal's own flow (row -> Close Position modal,
    #    every step read back against THIS position), then confirm flat
    last_unreal = pos.unrealized_pnl
    closed, last_unreal, why = _close_and_confirm(res, adapter, page, venue, side, pos, reads, sleep, tid)
    if not closed:
        res.alerts.append(f"{tid}: {why}")
        res.halted = "round trip: close not confirmed"
        ledger.record(tid, "close_unconfirmed", purpose="round_trip_close")
        return res
    ledger.record(tid, "closed")
    _report(res, post, {**_fill_body(cfg, {"ticket_id": tid}, spec.as_dict(), "closed", entry=pos.entry_price),
                        "pnl": last_unreal, "closed_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "round_trip_test: closed at market by the executor; pnl is the last "
                                  "unrealized P&L read before the close (ESTIMATED, not the broker fill)"})
    res.log("round_trip_done", ticket_id=tid)
    return res


def _close_and_confirm(res: CycleResult, adapter: Any, page: Any, venue: str, side: str, pos: Position,
                       reads: int, sleep: Callable[[float], None], tid: str
                       ) -> Tuple[bool, Optional[float], str]:
    """The watched close of ONE position and its confirmation (criterion L5):
    ``adapter.flatten`` with the row facts we mean to close (side, size,
    fill), then up to ``reads`` x 3 s until the row is gone, then no working
    order left for the symbol (an orphan SL / TP), then the account read
    after. Returns (confirmed, last unrealized P&L read, why-not)."""
    r = adapter.flatten(page, venue, arm=True, side=side, quantity=pos.quantity, entry_price=pos.entry_price)
    res.log("close", ticket_id=tid, result=r)
    if not r.get("clicked") or not r.get("ok"):
        return False, pos.unrealized_pnl, f"close not confirmed on the terminal: {r.get('why')}"
    last_unreal, flat = pos.unrealized_pnl, False
    for _ in range(max(1, reads)):
        sleep(3.0)
        try:
            positions = adapter.read_positions(page)
        except Exception as exc:
            res.alerts.append(f"{tid}: re-read after close failed ({type(exc).__name__})")
            continue
        still = [p for p in positions if p.symbol.upper() == venue and p.side == side]
        if still:
            last_unreal = still[0].unrealized_pnl
        else:
            flat = True
            break
    if not flat:
        return False, last_unreal, (f"close NOT confirmed flat after {reads} re-reads; the position stays "
                                    f"protected by its bracket; close it by hand")
    try:
        orphans = [o for o in adapter.read_orders(page) if o.symbol.upper() == venue]
    except Exception as exc:
        return False, last_unreal, f"flat, but the orders re-read failed ({type(exc).__name__}): orphan legs unknown"
    if orphans:
        res.log("orphan_orders", ticket_id=tid, orders=[{"order_id": o.order_id, "side": o.side, "price": o.price,
                                                            "type": getattr(o, "order_type", None)} for o in orphans])
        return False, last_unreal, f"flat, but {len(orphans)} working order(s) for {venue} remain (orphan SL/TP)"
    try:
        after = adapter.read_account(page).as_dict()
        res.log("after_close", ticket_id=tid, account={k: after.get(k) for k in ("balance", "equity", "margin_used", "available")})
    except Exception as exc:
        res.alerts.append(f"{tid}: account read after close failed ({type(exc).__name__})")
    return True, last_unreal, ""


def run_close_position(*, adapter: Any, page: Any, api: Any, cfg: ExecutorConfig, ledger: IntentLedger,
                       venue_symbol: str, arm: bool = False, reads: int = 20,
                       sleep: Callable[[float], None] = lambda s: None,
                       now: Optional[datetime] = None) -> CycleResult:
    """Close ONE existing position on the terminal (manager 2026-09-29: the
    live test's 0.01 SOLUSD fill that our blind reader never confirmed),
    through the same watched flow the round trip uses, and JOURNAL it: an
    ``open`` fill report from the row (its entry, SL, TP, size) under the
    ledger's own unresolved round-trip ticket for the symbol when there is
    one, else a fresh ``closeout-`` id; then ``closed`` once flat is
    confirmed, no orphan order remains, and the account was re-read.
    Refuses unless exactly ONE position for the symbol exists. Disarmed, it
    locates the row and its close control and reports; clicks nothing."""
    now = now or datetime.now(timezone.utc)
    res = CycleResult(mode="close_position_live" if arm else "close_position_dry")
    post = api.post_report if arm else None
    venue = venue_symbol.upper()

    def stop(why: str) -> CycleResult:
        res.halted = why
        res.log("refused", why=why)
        return res

    if cfg.enabled_venue_symbols is not None and venue not in cfg.enabled_venue_symbols:
        return stop(f"{venue} is not in executor.enabled_venue_symbols {cfg.enabled_venue_symbols}")
    try:
        acct = adapter.read_account(page)
        positions = adapter.read_positions(page)
        orders = adapter.read_orders(page)
    except Exception as exc:
        return stop(f"terminal read failed ({type(exc).__name__}: {exc})")
    mine = [p for p in positions if p.symbol.upper() == venue]
    res.reads = {"account": acct.as_dict(), "positions": len(positions), "orders": len(orders),
                 "symbol_positions": [{"side": p.side, "quantity": p.quantity, "entry_price": p.entry_price,
                                       "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                                       "unrealized_pnl": p.unrealized_pnl} for p in mine]}
    if len(mine) != 1:
        return stop(f"need exactly 1 {venue} position to close (found {len(mine)})")
    pos = mine[0]
    if pos.side not in ("long", "short") or not pos.quantity:
        return stop(f"the {venue} row's side / size did not parse (side={pos.side!r}, size={pos.quantity!r})")
    # The ledger's own unresolved round-trip ticket for this symbol keeps the
    # journal linked to the click that placed it; else a fresh close-out id.
    tid = next((k for k, v in sorted(ledger.latest().items(), key=lambda kv: str(kv[1].get("ts") or ""))
                if k.startswith(f"roundtrip-{venue.lower()}-")
                and v.get("state") in IntentLedger.UNRESOLVED + IntentLedger.CLOSE_UNRESOLVED), None)
    if tid is None:
        tid = f"closeout-{venue.lower()}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    spec = {"ticket_id": tid, "venue_symbol": venue, "side": pos.side, "quantity": pos.quantity,
            "stop_loss": pos.stop_loss, "take_profit": pos.take_profit, "order_type": "market", "limit_price": None}
    res.log("close_position_spec", ticket_id=tid, spec=spec, entry_price=pos.entry_price)
    if not arm:
        r = adapter.flatten(page, venue, arm=False, side=pos.side, quantity=pos.quantity, entry_price=pos.entry_price)
        res.log("would_close", ticket_id=tid, result=r)
        if not r.get("ok"):
            res.halted = f"close control not located: {r.get('why')}"
        return res
    ledger.record(tid, "open", purpose="close_position", entry=pos.entry_price)
    _report(res, post, {**_fill_body(cfg, {"ticket_id": tid}, spec, "open", entry=pos.entry_price),
                        "reason": "close_position: journaled from the terminal row before the watched close"})
    closed, last_unreal, why = _close_and_confirm(res, adapter, page, venue, pos.side, pos, reads, sleep, tid)
    if not closed:
        res.alerts.append(f"{tid}: {why}")
        res.halted = f"close_position: {why}"
        ledger.record(tid, "close_unconfirmed", purpose="close_position", spec=spec)
        return res
    ledger.record(tid, "closed", purpose="close_position")
    _report(res, post, {**_fill_body(cfg, {"ticket_id": tid}, spec, "closed", entry=pos.entry_price),
                        "pnl": last_unreal, "closed_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "close_position: closed through the terminal's Close Position flow by the executor; "
                                  "pnl is the last unrealized P&L read before the close (ESTIMATED, not the broker fill)"})
    res.log("close_position_done", ticket_id=tid)
    return res


def _attempt_public(att: PlaceAttempt) -> Dict[str, Any]:
    """A PlaceAttempt for logs/journal WITHOUT the raw form (its text can
    carry the account number); field labels + button names only."""
    form = att.form or {}
    oc = form.get("one_click") or {}
    return {"stage": att.stage, "submitted": att.submitted, "detail": att.detail,
            "form_fields": sorted((form.get("fields") or {}).keys()),
            "form_buttons": sorted((form.get("buttons") or {}).keys()),
            # The one-click toggle's reading: a DIAGNOSTIC the adapter records
            # and gates nothing on (operator 2026-09-28). state + why only.
            "one_click": {"state": oc.get("state"), "why": oc.get("why")} if oc else None,
            # Where the submit search looked and why each candidate failed
            # (buttons matching the submit pattern only: "Buy 0.01 SOLUSD at
            # 117.14"-shaped labels, never account data). Absent when the
            # submit was inside the form.
            "submit_search": form.get("submit_search"),
            # Each typed field per fill pass: what the form showed before and
            # after (our own numbers, never account data), so a run log says
            # which field the terminal reset and whether the re-fill stuck.
            "fill_trace": form.get("fill_trace"),
            # After a DISARMED walk: what the dismiss did and what the form
            # still shows (criterion D7, read back rather than assumed).
            "ticket_after": form.get("ticket_after"),
            # After an ARMED submit click: what appeared (new controls, the
            # overlay's redacted text) and whether one confirmation was pressed.
            "after_submit": form.get("after_submit"),
            # Where the ticket panel and its tagged controls sit (boxes only).
            "panel": {k: form.get(k) for k in ("panel_box", "fields_box", "button_boxes") if form.get(k)} or None,
            "ambiguous": form.get("ambiguous") or None}


def _skip_body(cfg: ExecutorConfig, t: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    return {"kind": "fill", "status": "skipped", "account_id": cfg.account_id,
            "ticket_id": t.get("ticket_id"), "symbol": t.get("symbol"),
            "direction": t.get("direction"), "reason": reason[:500], "source": "prop_executor"}


def _fill_body(cfg: ExecutorConfig, row: Mapping[str, Any], spec: Mapping[str, Any], status: str,
               entry: Optional[float] = None) -> Dict[str, Any]:
    from src.prop.symbol_map import to_bot_symbol
    return {"kind": "fill", "status": status, "account_id": cfg.account_id,
            "ticket_id": row.get("ticket_id") or spec.get("ticket_id"),
            "symbol": to_bot_symbol(spec.get("venue_symbol")) or spec.get("venue_symbol"),
            "direction": spec.get("side"), "qty": spec.get("quantity"),
            "entry_price": entry if entry is not None else spec.get("limit_price"),
            "sl": spec.get("stop_loss"), "tp": spec.get("take_profit"), "source": "prop_executor"}


def _contain(res: CycleResult, adapter: Any, page: Any, live: bool, post: Any, ledger: IntentLedger,
             cfg: ExecutorConfig, tid: str, row: Mapping[str, Any], spec: Mapping[str, Any],
             found: Mapping[str, Any], verdict: str) -> Optional[str]:
    """§ 3.5 for one submitted ticket. Never resubmits. Returns an
    auto-revert trip reason (a bracket leg missing, a duplicate, or an
    unconfirmed placement), or None."""
    prev = row.get("state")
    res.log("confirm", ticket_id=tid, verdict=verdict, prev=prev)
    if verdict in ("partial_no_sl_tp", "duplicate"):
        trip: Optional[str] = f"{tid}: {verdict}"
    else:
        trip = None
    if verdict == prev:
        return trip  # unchanged since last cycle: nothing to report twice
    if prev == "placed" and verdict == "not_found":
        # A resting order that vanished without a position: cancelled or
        # expired on the terminal. Two reads, then report it skipped.
        n = int(row.get("misses") or 0) + 1
        if live:
            if n >= 2:
                ledger.record(tid, "skipped", reason="working order gone from the terminal without a fill")
                _report(res, post, {**_fill_body(cfg, row, spec, "skipped"),
                                    "reason": "working order gone from the terminal without a fill"})
            else:
                ledger.record(tid, "placed", misses=n)
        return trip
    if verdict == "placed":
        if live:
            ledger.record(tid, "placed")
            _report(res, post, _fill_body(cfg, row, spec, "placed"))
        return trip
    if verdict == "open":
        if live:
            p = found["positions"][0]
            ledger.record(tid, "open")
            _report(res, post, _fill_body(cfg, row, spec, "open", entry=p.entry_price))
        return trip
    if verdict == "not_found":
        n = int(row.get("misses") or 0) + 1
        if n >= cfg.unconfirmed_reads:
            res.alerts.append(f"{tid}: submitted but not found after {n} re-reads — skipped: unconfirmed_submit")
            if live:
                ledger.record(tid, "skipped", reason="unconfirmed_submit", misses=n)
                _report(res, post, {**_fill_body(cfg, row, spec, "skipped"), "reason": "unconfirmed_submit"})
            return f"{tid}: unconfirmed placement (not found after {n} re-reads)"
        elif live:
            ledger.record(tid, "unconfirmed", misses=n)
        return None
    if verdict == "duplicate":
        res.alerts.append(f"{tid}: {len(found['orders'])} orders + {len(found['positions'])} positions "
                          f"match one ticket — duplicate submit")
        # Cancel every duplicate WORKING order beyond the first (all of them
        # when a position already filled). Two FILLED positions are never
        # auto-closed: a flatten cannot pick one of two rows, so the excess is
        # left to the operator and new entries halt.
        extra_orders = found["orders"][1:] if not found["positions"] else found["orders"]
        for o in extra_orders:
            r = adapter.cancel_order(page, o, arm=live)
            res.log("cancel_duplicate", ticket_id=tid, order_id=o.order_id, result=r)
        if len(found["positions"]) > 1:
            res.alerts.append(f"{tid}: {len(found['positions'])} positions filled for one ticket — "
                              f"operator must close the excess; executor halted")
        if live:
            ledger.record(tid, "contained", verdict=verdict)
        return trip
    if verdict == "partial_no_sl_tp":
        leg = (found["positions"] or found["orders"])[0]
        if not row.get("leg_fix_tried"):
            if isinstance(leg, Position):
                r = adapter.modify_bracket(page, leg, spec.get("stop_loss"), spec.get("take_profit"), arm=live)
            else:
                # A resting entry without its bracket holds no position yet:
                # cancelling it is the smaller action than editing it.
                r = adapter.cancel_order(page, leg, arm=live)
            res.log("place_missing_leg", ticket_id=tid, result=r)
            if live:
                ledger.record(tid, "unconfirmed", leg_fix_tried=True)
            res.alerts.append(f"{tid}: bracket leg missing — one repair attempted")
            return trip
        # still missing after the one repair: close at market and alert
        if isinstance(leg, Position):
            r = adapter.flatten(page, leg.symbol, arm=live)
        else:
            r = adapter.cancel_order(page, leg, arm=live)
        res.log("close_naked", ticket_id=tid, result=r)
        res.alerts.append(f"{tid}: bracket leg still missing after one repair — closed/cancelled and alerted")
        if live:
            ledger.record(tid, "contained", verdict=verdict)
    return trip


def _reconcile_journal(res: CycleResult, cfg: ExecutorConfig, st: Dict[str, Any],
                       positions: Sequence[Position], journal_open: Sequence[Mapping[str, Any]],
                       ledger: IntentLedger, claimed_keys: set, post: Any) -> Optional[str]:
    """§ 3.2 step 4. Returns a halt reason, or None."""
    from src.prop.symbol_map import to_bot_symbol

    term: Dict[Tuple[str, str], Position] = {}
    halt = None
    for p in positions:
        key = (str(to_bot_symbol(p.symbol) or p.symbol).upper(), p.side or "")
        if key in term:
            halt = f"two terminal positions on {key}: cannot map to one journal row"
        term[key] = p
    jr = {(str(j.get("symbol") or "").upper(), _dir(j.get("direction")) or ""): j for j in journal_open}
    absent = st.setdefault("absent_counts", {})
    for key, j in jr.items():
        p = term.get(key)
        k = f"{key[0]}|{key[1]}"
        if p is None:
            absent[k] = int(absent.get(k, 0)) + 1
            if absent[k] >= 2:
                res.log("closed_on_terminal", key=k)
                _report(res, post, {"kind": "fill", "status": "closed", "account_id": cfg.account_id,
                                    "ticket_id": j.get("ticket_id"), "symbol": key[0], "direction": key[1],
                                    "qty": j.get("qty"), "entry_price": j.get("entry_price"),
                                    "reason": "closed_on_terminal (executor reconcile; exit read from the "
                                              "terminal history is not built)", "source": "prop_executor"})
                absent.pop(k, None)
            continue
        absent.pop(k, None)
        jsl, jtp = _f(j.get("sl")), _f(j.get("tp"))
        if (p.stop_loss is not None and not _close(p.stop_loss, jsl, 1e-6)) or \
           (p.take_profit is not None and not _close(p.take_profit, jtp, 1e-6)):
            res.log("amend", key=k, sl=p.stop_loss, tp=p.take_profit)
            _report(res, post, {"kind": "amend", "account_id": cfg.account_id, "symbol": key[0],
                                "direction": key[1], "sl": p.stop_loss, "tp": p.take_profit,
                                "reason": "terminal SL/TP differs from the journal (executor reconcile)",
                                "source": "prop_executor"})
    for key, p in term.items():
        venue_key = (p.symbol.upper(), p.side)
        if key not in jr and venue_key not in claimed_keys:
            why = f"orphan: terminal position {key[0]} {key[1]} has no journal row and no ledger intent"
            res.alerts.append(why + " — not touched; new entries halted")
            halt = halt or why
    return halt


__all__ = [
    "MODE_ENV", "MODES", "DEFAULT_MODE", "executor_mode", "ExecutorConfig", "load_config",
    "size_lots", "bracket_from_ticket", "open_risk", "evaluate_guards", "GuardVerdict",
    "match_terminal", "classify_confirmation", "IntentLedger", "ExecutorState",
    "day_start_balance", "trading_day", "run_cycle", "run_round_trip", "run_close_position", "round_to_step", "CycleResult",
]

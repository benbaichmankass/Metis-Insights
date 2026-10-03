#!/usr/bin/env python3
# wiring: .github/workflows/live-replay-parity.yml (daily + issue label
# `live-replay-parity`); scripts/ops/render_daily_brief.py reads
# comms/live_parity/latest.json for its one §4 line.
"""LIVE-vs-REPLAY PARITY — is it really "no signal", or a bug that looks like one?

Operator directive, 2026-10-03 (paraphrased): *"I want a more mechanical
verification that everything is actually working and that it's really just that
there are no signals, not some other bug stopping the signals so that it only
looks like silence."*

Lane LIVE-SILENCE (PR #15805, ``docs/research/evidence/live-silence-2026-10-03/``)
answered that once, by hand, for the four bybit_2 legs. This module is that
replay turned into a standing check over EVERY execution:live leg on a
real-money roster, plus the cheap paper legs that share a supported unit.

WHAT IT COMPARES, PER LEG, PER EVALUATED DECISION
-------------------------------------------------
The canonical decision record is ``trade_journal.db::signals`` — the
``signal_audit.jsonl`` dual-write — read through ``/api/diag/audit_query``. Every
builder logs one ``<leg>_eval`` row per evaluation (side + reason + confidence +
``adx_14`` computed on the exact frame it decided on). No new table: this
PROJECTS over that store (db-wiring rule).

For each live eval at time ``t`` the replay rebuilds the frame live saw from the
bot's own candles (``/api/bot/candles``, the same ``fetch_candles`` path) and runs
the LIVE ``order_package()`` of the leg's unit with ``load_strategy_config()``
params, applying the builder's own wrapper gates (side_filter, closed-bar
framing) — detected from the builder source, never hand-listed.

* ``decision_bar: closed`` legs: the frame is the 199 closed bars ending at the
  bar that just closed. Deterministic, so the comparison is EXACT, including the
  ``adx_14`` fingerprint.
* ``decision_bar: forming`` legs: live decided on a still-forming bar at ``t``.
  The bar as of ``t`` is bracketed from the 15m candles: completed 15m candles
  give the bar so far, and the price at ``t`` must lie inside the 15m candle that
  contains ``t``. The replay is run over a set of closes spanning that candle's
  range (and, for donchian legs, the exact close live printed in its reason).
  **A divergence is declared only when NO price inside that range reproduces
  live's decision** — so bar-timing noise cannot raise it, and a real bug (a
  different code path, different params, different candles) still does.

THE DIVERGENCE CLASSES (any one is a finding)
---------------------------------------------
``missed_signal``       replay=signal for every plausible price, live said none.
``live_only_signal``    live signalled, replay finds no price that signals.
``side_mismatch``       both signalled, opposite sides.
``candle_mismatch``     live's fingerprint (adx_14 / printed close / channel)
                        cannot come from the candles the replay sees.
``lost_bar``            a bar the market traded in that live never evaluated.
``unevaluated_signal``  a 15m window live did not evaluate in which the replay
                        signals for every plausible price.
``dropped_no_package``  live signalled; no order package and no named downstream
                        gate (open_package_blocked, bar_debounce_blocked, …).
``dropped_no_order``    a package exists, but a live account on the leg's roster
                        has no order and no named reason.

COLLAPSED STATES
----------------
A leg the run could not look at is ``could_not_check`` with the reason, and its
divergence count is ``null`` — never ``0``. The headline names how many legs
could not be checked before it names a divergence count.

POSITIVE CONTROLS (part of done)
--------------------------------
1. ``planted_defect`` — on the real data of this run, a replay-signal window has
   its live records replaced by fabricated no-signal records; the check MUST
   flag it. Also plants a lost bar and an unexplained live signal.
2. ``historical_positive`` — every live order package of a covered leg inside
   the candle reach must classify as a MATCHED signal bar; named anchors
   (``ANCHORS``) are reported individually.
3. ``liveness`` — within the window at least one leg on some account went
   signal → order package → placed order. If not, said so.

Usage::

    # fetch over an SSH tunnel to the live VM's web-api, then analyse
    python3 scripts/ops/live_replay_parity.py --base-url http://127.0.0.1:18001 \
        --token-env DIAG_READ_TOKEN --days 7 --out comms/live_parity
    # re-analyse a saved bundle (no network)
    python3 scripts/ops/live_replay_parity.py --bundle bundle.json --out /tmp/p
    python3 scripts/ops/live_replay_parity.py --summary-line comms/live_parity/latest.json
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = 1

#: Real-money rosters (CLAUDE.md § promotion ladder, Stage 2 + the prop book).
REAL_MONEY_ACCOUNTS = ("bybit_2", "bybit_portfolio", "alpaca_live",
                       "alpaca_portfolio", "breakout_1")
#: Paper rosters covered "where cheap": legs whose unit is already supported.
PAPER_ACCOUNTS = ("bybit_1", "alpaca_paper")

#: unit name (builder.monitor_unit) -> module exposing order_package(). Adding a
#: unit here is the whole cost of covering its legs.
SUPPORTED_UNITS = {
    "trend_donchian": "src.units.strategies.trend_donchian",
    "htf_pullback_trend_2h": "src.units.strategies.htf_pullback_trend_2h",
}

#: The live builders fetch limit=200 candles; the frame is 199 closed + 1.
LIVE_FETCH_LIMIT = 200
FINE_INTERVAL = "15m"
FINE_S = 900
CANDLE_LIMIT = 1000  # /api/bot/candles MAX_LIMIT

#: Audit events that NAME why a live signal produced no package/order.
DOWNSTREAM_GATE_EVENTS = (
    "open_package_blocked", "open_package_read_failed", "open_package_scoped",
    "open_package_pair_coupled", "bar_debounce_blocked", "cooldown_blocked",
    "empty_sizing_refused", "regime_hard_gate",
)

DIVERGENCE_CLASSES = (
    "missed_signal", "live_only_signal", "side_mismatch", "candle_mismatch",
    "lost_bar", "unevaluated_signal", "dropped_no_package", "dropped_no_order",
)

#: Known live entries the historical positive must report MATCHED while they
#: are inside the candle reach. Source: LIVE-SILENCE replay output
#: (docs/research/evidence/live-silence-2026-10-03/output.txt, PR #15805).
ANCHORS = (
    {"leg": "trend_donchian_eth_4h", "t": "2026-09-28T03:30:00+00:00",
     "side": "short", "note": "forming-bar entry, order package 09-28T03:35Z"},
    {"leg": "ada_pullback_2h", "t": "2026-09-26T06:00:00+00:00",
     "side": "long", "note": "09-26 entry (replay SIG 06:00 bar)"},
)

_ISO_FMT = "%Y-%m-%dT%H:%M:%S+00:00"


# ── small helpers ────────────────────────────────────────────────────────────

def to_epoch(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime(_ISO_FMT)


def norm_side(s: Any) -> str:
    s = str(s or "").lower()
    return {"buy": "long", "long": "long", "sell": "short", "short": "short"}.get(s, "none")


_CLOSE_RE = re.compile(r"close=([0-9.eE+-]+)")
_CHAN_RE = re.compile(r"channel \[([0-9.eE+-]+), ([0-9.eE+-]+)\]")


def parse_close(reason: str | None) -> float | None:
    m = _CLOSE_RE.search(reason or "")
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def parse_channel(reason: str | None) -> tuple[float, float] | None:
    m = _CHAN_RE.search(reason or "")
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


# ── leg resolution ───────────────────────────────────────────────────────────

@dataclass
class Leg:
    name: str
    symbol: str | None
    timeframe: str
    unit: str | None
    decision_bar: str
    execution: str
    accounts: list[str]
    real_money: bool
    features: dict = field(default_factory=dict)
    cfg: dict = field(default_factory=dict)
    skip_reason: str | None = None


def _builder_features(builder: Callable) -> dict:
    """Which wrapper gates does the live builder apply? Read from its source
    (and the shared ``_*_builder`` it delegates to), never from a hand list."""
    import src.runtime.strategy_signal_builders as ssb
    srcs = [inspect.getsource(builder)]
    for m in re.finditer(r"\b(_[a-z0-9_]+_builder)\(", srcs[0]):
        fn = getattr(ssb, m.group(1), None)
        if callable(fn):
            srcs.append(inspect.getsource(fn))
    text = "\n".join(srcs)
    return {
        "side_filter": "_side_filter_suppresses(" in text,
        "decision_frame": "_decision_frame(" in text,
        "us_session_gate": 'is_market_open("us_equity")' in text,
        "long_only": "short_suppressed_long_only" in text,
    }


def resolve_legs(accounts_doc: dict, strategies_doc: dict, *,
                 include_paper: bool = True) -> list[Leg]:
    import src.runtime.strategy_signal_builders as ssb
    accts = accounts_doc.get("accounts", accounts_doc)
    strats = strategies_doc.get("strategies", strategies_doc)
    wanted = list(REAL_MONEY_ACCOUNTS) + (list(PAPER_ACCOUNTS) if include_paper else [])
    by_leg: dict[str, Leg] = {}
    for acct in wanted:
        a = accts.get(acct) or {}
        for name in a.get("strategies") or []:
            cfg = strats.get(name) or {}
            if not cfg.get("enabled", False):
                continue
            execution = str(cfg.get("execution") or "live")
            real = acct in REAL_MONEY_ACCOUNTS
            if not real and execution != "live":
                continue  # paper shadow legs are out of scope
            leg = by_leg.get(name)
            if leg is None:
                syms = cfg.get("symbols") or []
                builder = getattr(ssb, f"{name}_signal_builder", None)
                unit = getattr(builder, "monitor_unit", None) if builder else None
                leg = Leg(name=name, symbol=str(syms[0]) if syms else None,
                          timeframe=str(cfg.get("timeframe") or ""),
                          unit=unit, decision_bar=str(cfg.get("decision_bar") or "forming").lower(),
                          execution=execution, accounts=[], real_money=False, cfg=dict(cfg))
                if builder is None:
                    leg.skip_reason = f"no builder {name}_signal_builder in strategy_signal_builders"
                elif unit not in SUPPORTED_UNITS:
                    leg.skip_reason = f"unit {unit!r} not supported by the replay"
                elif not leg.symbol:
                    leg.skip_reason = "no symbols: in strategies.yaml"
                else:
                    leg.features = _builder_features(builder)
                by_leg[name] = leg
            leg.accounts.append(acct)
            leg.real_money = leg.real_money or real
    legs = [lg for lg in by_leg.values() if lg.real_money or lg.skip_reason is None]
    return sorted(legs, key=lambda lg: (not lg.real_money, lg.name))


# ── data: the bundle ─────────────────────────────────────────────────────────

class Fetcher:
    """Reads the live VM's web-api (normally through an SSH tunnel)."""

    def __init__(self, base_url: str, token: str, timeout: float = 90.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.requests = 0

    def get(self, path: str, params: dict) -> Any:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(f"{self.base}{path}?{q}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        self.requests += 1
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed host
            return json.loads(r.read().decode("utf-8"))

    def candles(self, symbol: str, interval: str) -> dict:
        d = self.get("/api/bot/candles", {"symbol": symbol, "interval": interval,
                                          "limit": CANDLE_LIMIT})
        return {"candles": d.get("candles") or [], "error": d.get("error")}

    def audit(self, strategy: str, since: str, until: str, page: int = 1000) -> dict:
        rows: list = []
        offset = 0
        while True:
            d = self.get("/api/diag/audit_query", {"strategy": strategy, "since": since,
                                                   "until": until, "limit": page,
                                                   "offset": offset})
            if d.get("error") or not d.get("dual_write_present", True):
                return {"rows": rows, "error": d.get("error") or "dual_write_absent"}
            got = d.get("rows") or []
            rows.extend(got)
            if len(got) < page:
                return {"rows": rows, "error": None}
            offset += page

    def journal(self, table: str, stop_before: float, time_keys: tuple[str, ...],
                page: int = 500, max_pages: int = 40) -> dict:
        rows: list = []
        for i in range(max_pages):
            d = self.get("/api/diag/journal", {"table": table, "limit": page,
                                               "offset": i * page, "envelope": "true"})
            got = d.get("rows") or []
            rows.extend(got)
            ts = [to_epoch(r.get(k)) for r in got for k in time_keys]
            ts = [t for t in ts if t is not None]
            if len(got) < page or (ts and max(ts) < stop_before):
                return {"rows": rows, "error": None, "complete": True}
        return {"rows": rows, "error": None, "complete": False,
                "note": f"stopped after {max_pages} pages"}


def fetch_bundle(f: Fetcher, legs: list[Leg], since: float, until: float) -> dict:
    """Everything the analysis needs, in one JSON-able dict."""
    b: dict = {"fetched_at": iso(datetime.now(timezone.utc).timestamp()),
               "since": iso(since), "until": iso(until),
               "candles": {}, "signals": {}, "order_packages": None, "trades": None,
               "errors": []}
    need = set()
    for lg in legs:
        if lg.skip_reason:
            continue
        need.add((lg.symbol, lg.timeframe))
        need.add((lg.symbol, FINE_INTERVAL))
    for sym, tf in sorted(need):
        try:
            b["candles"][f"{sym}|{tf}"] = f.candles(sym, tf)
        except Exception as exc:  # noqa: BLE001 — recorded as could-not-check, never as empty
            b["candles"][f"{sym}|{tf}"] = {"candles": [], "error": f"fetch_failed: {exc}"}
    # signals: pull a margin before `since` so the first bar's earlier ticks exist
    s_since = iso(since - 86400 * 2)
    for lg in legs:
        if lg.skip_reason:
            continue
        try:
            b["signals"][lg.name] = f.audit(lg.name, s_since, iso(until))
        except Exception as exc:  # noqa: BLE001
            b["signals"][lg.name] = {"rows": [], "error": f"fetch_failed: {exc}"}
    for table, keys in (("order_packages", ("updated_at", "created_at")),
                        ("trades", ("timestamp", "created_at"))):
        try:
            b[table] = f.journal(table, since - 86400 * 2, keys)
        except Exception as exc:  # noqa: BLE001
            b[table] = {"rows": [], "error": f"fetch_failed: {exc}"}
    b["requests"] = f.requests
    return b


# ── replay ───────────────────────────────────────────────────────────────────

class Replayer:
    def __init__(self, leg: Leg):
        import importlib
        from src.runtime.regime import detect_regime
        from src.runtime.strategy_signal_builders import (
            _resolve_side_filter, _side_filter_suppresses)
        self.leg = leg
        self.fn = importlib.import_module(SUPPORTED_UNITS[leg.unit]).order_package
        self.detect_regime = detect_regime
        self.cfg = {"symbol": leg.symbol, "timeframe": leg.timeframe, **leg.cfg,
                    "strategy_label": leg.name}
        self.side_filter = _resolve_side_filter(leg.cfg) if leg.features.get("side_filter") else "both"
        self._suppresses = _side_filter_suppresses
        self.long_only = bool(leg.features.get("long_only"))
        self.calls = 0

    def adx(self, frame) -> float | None:
        return self.detect_regime(frame).get("adx")

    def decide(self, frame, *, with_adx: bool = False) -> dict:
        """Run the LIVE unit on ``frame`` exactly as the builder would."""
        self.calls += 1
        try:
            pkg = self.fn(dict(self.cfg), candles_df=frame)
        except ValueError as exc:
            out = {"side": "none", "reason": str(exc)}
        else:
            d = norm_side(pkg.get("direction"))
            if self.long_only and d != "long":
                out = {"side": "none", "reason": "short_suppressed_long_only", "raw_side": d}
            elif self.side_filter != "both" and self._suppresses(pkg["direction"], self.side_filter):
                out = {"side": "none", "reason": f"side_filter={self.side_filter}",
                       "raw_side": d}
            else:
                out = {"side": d, "reason": None, "confidence": pkg.get("confidence")}
        if with_adx:
            out["adx"] = self.adx(frame)
        return out


def _frame(rows: list[dict]):
    import pandas as pd
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


@dataclass
class EvalRow:
    t: float
    side: str
    reason: str | None
    adx: float | None
    confidence: float | None
    close: float | None
    channel: tuple | None
    #: explicit frame fingerprint, written by builders once the held Tier-2
    #: writer change (``_stamp_regime`` → ``bar_open_ts`` / ``bar_close``) ships
    bar_open: float | None = None
    bar_close: float | None = None


def _evals_and_events(rows: list[dict], leg: str) -> tuple[list[EvalRow], list[dict]]:
    evals, events = [], []
    for r in rows:
        t = to_epoch(r.get("logged_at_utc"))
        if t is None:
            continue
        ev = r.get("event")
        if ev == f"{leg}_eval":
            reason = r.get("reason")
            evals.append(EvalRow(t=t, side=norm_side(r.get("side")), reason=reason,
                                 adx=r.get("adx_14"), confidence=r.get("confidence"),
                                 close=parse_close(reason), channel=parse_channel(reason),
                                 bar_open=to_epoch(r.get("bar_open_ts")),
                                 bar_close=r.get("bar_close")))
        elif ev in DOWNSTREAM_GATE_EVENTS:
            events.append({"t": t, "event": ev, "row": r})
    evals.sort(key=lambda e: e.t)
    events.sort(key=lambda e: e["t"])
    return evals, events


def _variant_closes(c: dict, extra: Iterable[float] = ()) -> list[float]:
    lo, hi = float(c["low"]), float(c["high"])
    pts = {float(c["open"]), float(c["close"]), lo, hi}
    for k in range(1, 4):
        pts.add(lo + (hi - lo) * k / 4.0)
    for x in extra:
        pts.add(float(x))
    return sorted(pts)


# ── the analysis of one leg ──────────────────────────────────────────────────

def analyse_leg(leg: Leg, bundle: dict, since: float, until: float, *,
                replayer: Replayer | None = None,
                evals_override: list[EvalRow] | None = None,
                packages_override: list[dict] | None = None) -> dict:
    out: dict = {"leg": leg.name, "symbol": leg.symbol, "timeframe": leg.timeframe,
                 "unit": leg.unit, "decision_bar": leg.decision_bar,
                 "execution": leg.execution, "accounts": leg.accounts,
                 "real_money": leg.real_money, "features": leg.features,
                 "state": "could_not_check", "reason": None,
                 "bars_checked": None, "evals_checked": None, "divergences": None,
                 "by_class": None, "bars": None}
    if leg.skip_reason:
        out["reason"] = leg.skip_reason
        return out
    from src.runtime.closed_bars import TF_SECONDS
    tf_s = TF_SECONDS.get(leg.timeframe)
    if not tf_s:
        out["reason"] = f"timeframe {leg.timeframe!r} unknown to closed_bars"
        return out
    tfc = bundle["candles"].get(f"{leg.symbol}|{leg.timeframe}") or {}
    fnc = bundle["candles"].get(f"{leg.symbol}|{FINE_INTERVAL}") or {}
    sig = bundle["signals"].get(leg.name) or {}
    for label, blob in (("tf candles", tfc), ("15m candles", fnc), ("signals", sig)):
        if blob.get("error"):
            out["reason"] = f"{label}: {blob['error']}"
            return out
    tf_rows = sorted(tfc.get("candles") or [], key=lambda c: c["time"])
    fine = sorted(fnc.get("candles") or [], key=lambda c: c["time"])
    if len(tf_rows) < LIVE_FETCH_LIMIT or not fine:
        out["reason"] = f"too few candles (tf={len(tf_rows)}, 15m={len(fine)})"
        return out
    evals, events = _evals_and_events(sig.get("rows") or [], leg.name)
    if evals_override is not None:
        evals = evals_override
    rep = replayer or Replayer(leg)

    fine_by_t = {int(c["time"]): c for c in fine}
    fine_reach = fine[0]["time"]
    tf_times = [int(c["time"]) for c in tf_rows]
    closed_mode = leg.decision_bar == "closed"
    session = leg.features.get("us_session_gate")

    def in_session(t: float) -> bool:
        if not session:
            return True
        from src.runtime.market_hours import is_market_open
        return is_market_open("us_equity", datetime.fromtimestamp(t, tz=timezone.utc))

    # bars fully inside the window, inside the 15m reach, with 199 bars of history
    bars = []
    for i, b0 in enumerate(tf_times):
        b_end = b0 + tf_s
        if b0 < since or b_end > until or i < LIVE_FETCH_LIMIT - 1:
            continue
        bars.append((i, b0))
    import bisect
    eval_ts = [e.t for e in evals]

    def evals_between(a: float, b: float) -> list[EvalRow]:
        lo, hi = bisect.bisect_left(eval_ts, a), bisect.bisect_left(eval_ts, b)
        return evals[lo:hi]

    def events_between(a: float, b: float) -> list[dict]:
        return [e for e in events if a <= e["t"] < b]

    variant_cache: dict = {}

    def _bar_state(i: int, b0: int, fstart: int):
        hist = tf_rows[i - (LIVE_FETCH_LIMIT - 1):i]
        done = [fine_by_t[t] for t in range(b0, fstart, FINE_S) if t in fine_by_t]
        c = fine_by_t[fstart]
        o = float(tf_rows[i]["open"])
        ph = max([o] + [float(x["high"]) for x in done])
        pl = min([o] + [float(x["low"]) for x in done])
        vol = sum(float(x.get("volume") or 0) for x in done) + float(c.get("volume") or 0) / 2
        return hist, c, o, ph, pl, vol

    base_frames: dict = {}

    def _replay_closes(i: int, b0: int, fstart: int, closes: Iterable[float]) -> list[dict]:
        hist, c, o, ph, pl, vol = _bar_state(i, b0, fstart)
        if i not in base_frames:
            base_frames[i] = _frame(hist + [{"time": b0, "open": o, "high": o, "low": o,
                                              "close": o, "volume": 0.0}])
        base = base_frames[i]
        res, seen = [], {}
        for x in closes:
            for wide in (False, True):
                h = max(ph, float(c["open"]), x, float(c["high"]) if wide else x)
                lo_ = min(pl, float(c["open"]), x, float(c["low"]) if wide else x)
                k = (h, lo_, x)
                if k not in seen:
                    f = base.copy()
                    f.iloc[-1, 1:6] = [o, h, lo_, x, vol]
                    seen[k] = dict(rep.decide(f), close=x)
                res.append(seen[k])
        return res

    def forming_variants(i: int, b0: int, fstart: int, extra: tuple = ()) -> list[dict]:
        """Replay decisions for every plausible state of bar b0 during the 15m
        candle starting at ``fstart`` (plus the exact close live printed)."""
        key = (b0, fstart)
        if key not in variant_cache:
            variant_cache[key] = _replay_closes(i, b0, fstart, _variant_closes(fine_by_t[fstart]))
        res = list(variant_cache[key])
        for x in extra:
            k2 = ("x", b0, fstart, x)
            if k2 not in variant_cache:
                variant_cache[k2] = _replay_closes(i, b0, fstart, [x])
            res += variant_cache[k2]
        return res

    def adx_range(i: int, b0: int, fstart: int) -> tuple[float, float] | None:
        """ADX-14 hull over the corners of the bar's plausible (high, low) at a
        tick inside this 15m candle. ADX reads the forming bar's high/low (not
        its close), so the corners bound what live could have printed."""
        key = ("adx", b0, fstart)
        if key not in variant_cache:
            hist, c, o, ph, pl, vol = _bar_state(i, b0, fstart)
            hs = {max(ph, float(c["open"])), max(ph, float(c["high"]))}
            ls = {min(pl, float(c["open"])), min(pl, float(c["low"]))}
            vals = []
            for h in hs:
                for lo_ in ls:
                    f = base_frames[i].copy()
                    f.iloc[-1, 1:6] = [o, h, lo_, float(c["close"]), vol]
                    a = rep.adx(f)
                    if a is not None:
                        vals.append(a)
            variant_cache[key] = (min(vals), max(vals)) if vals else None
        return variant_cache[key]

    def closed_decision(i: int) -> dict:
        key = ("closed", i)
        if key not in variant_cache:
            variant_cache[key] = rep.decide(_frame(tf_rows[i - (LIVE_FETCH_LIMIT - 2):i + 1]),
                                            with_adx=True)
        return variant_cache[key]

    divs: list[dict] = []
    bar_rows: list[dict] = []
    evals_checked = 0
    not_comparable = 0
    adx_gap_max = 0.0
    for i, b0 in bars:
        b_end = b0 + tf_s
        status = None
        kinds: list[str] = []
        signal_evals: list[EvalRow] = []
        if closed_mode:
            fresh = float(leg.cfg.get("decision_bar_fresh_seconds") or 360)
            evs = evals_between(b_end, b_end + fresh + 120)
            if i + 1 >= len(tf_rows):
                continue
            if not evs:
                kinds.append("lost_bar")
                divs.append({"class": "lost_bar", "bar": iso(b0),
                             "detail": f"no {leg.name}_eval in the {fresh:.0f}s window after close"})
            d = closed_decision(i)
            for e in evs:
                evals_checked += 1
                cls = None
                if e.bar_open is not None and int(e.bar_open) != int(b0):
                    cls = "candle_mismatch"
                elif e.bar_close is not None and not _close(float(e.bar_close),
                                                            float(tf_rows[i]["close"])):
                    cls = "candle_mismatch"
                elif e.side != d["side"]:
                    cls = ("missed_signal" if e.side == "none" else
                           "live_only_signal" if d["side"] == "none" else "side_mismatch")
                elif e.adx is not None and d.get("adx") is not None and abs(e.adx - d["adx"]) > 1e-3:
                    cls = "candle_mismatch"
                elif e.channel and d.get("reason") and parse_channel(d["reason"]) and not all(
                        _close(a, b) for a, b in zip(e.channel, parse_channel(d["reason"]))):
                    cls = "candle_mismatch"
                if e.adx is not None and d.get("adx") is not None:
                    adx_gap_max = max(adx_gap_max, abs(e.adx - d["adx"]))
                if cls:
                    kinds.append(cls)
                    divs.append({"class": cls, "bar": iso(b0), "t": iso(e.t),
                                 "live": {"side": e.side, "adx": e.adx, "reason": e.reason},
                                 "replay": {"side": d["side"], "adx": d.get("adx"),
                                            "reason": d.get("reason")}})
                if e.side != "none":
                    signal_evals.append(e)
        else:
            evs = evals_between(b0, b_end)
            traded = [t for t in range(b0, b_end, FINE_S) if t in fine_by_t and in_session(t + 60)]
            if b0 < fine_reach:
                not_comparable += 1
                bar_rows.append({"bar": iso(b0), "status": "not_comparable",
                                 "why": "before the 15m candle reach"})
                continue
            if traded and not evs:
                kinds.append("lost_bar")
                divs.append({"class": "lost_bar", "bar": iso(b0),
                             "detail": f"{len(traded)} traded 15m candles, 0 live evals"})
            evaluated_fines = set()
            for e in evs:
                fstart = int(e.t // FINE_S * FINE_S)
                if fstart not in fine_by_t:
                    not_comparable += 1
                    continue
                evaluated_fines.add(fstart)
                evals_checked += 1
                if e.bar_close is not None and e.close is None:
                    e.close = float(e.bar_close)
                extra = (e.close,) if e.close is not None else ()
                vs = forming_variants(i, b0, fstart, extra)
                pool = [v for v in vs if e.close is not None and _close(v["close"], e.close)] or vs
                sides = {v["side"] for v in pool}
                cls = None
                c = fine_by_t[fstart]
                if e.bar_open is not None and int(e.bar_open) != int(b0):
                    cls = "candle_mismatch"
                if cls is None and e.close is not None:
                    tol = 1e-6 * max(1.0, e.close)
                    if not (float(c["low"]) - tol <= e.close <= float(c["high"]) + tol):
                        cls = "candle_mismatch"
                if cls is None and e.side not in sides:
                    cls = ("missed_signal" if e.side == "none" else
                           "live_only_signal" if sides == {"none"} else "side_mismatch")
                ar = adx_range(i, b0, fstart) if e.adx is not None else None
                adxs = list(ar) if ar else []
                if cls is None and e.adx is not None and adxs:
                    gap = max(0.0, min(adxs) - e.adx, e.adx - max(adxs))
                    adx_gap_max = max(adx_gap_max, gap)
                    if gap > 0.05:
                        cls = "candle_mismatch"
                if cls is None and e.channel:
                    rch = [parse_channel(v.get("reason")) for v in vs]
                    rch = [x for x in rch if x]
                    if rch and not all(_close(a, b) for a, b in zip(e.channel, rch[0])):
                        cls = "candle_mismatch"
                if cls:
                    kinds.append(cls)
                    divs.append({"class": cls, "bar": iso(b0), "t": iso(e.t),
                                 "live": {"side": e.side, "adx": e.adx, "close": e.close,
                                          "reason": e.reason},
                                 "replay": {"sides": sorted(sides),
                                            "adx_range": [min(adxs), max(adxs)] if adxs else None,
                                            "fine_candle": {k: c[k] for k in ("time", "low", "high")}}})
                if e.side != "none":
                    signal_evals.append(e)
            # windows live never evaluated: robust replay signal there is a loss
            for fstart in traded:
                if fstart in evaluated_fines:
                    continue
                vs = forming_variants(i, b0, fstart)
                sides = {v["side"] for v in vs}
                if "none" not in sides:
                    kinds.append("unevaluated_signal")
                    divs.append({"class": "unevaluated_signal", "bar": iso(b0),
                                 "t": iso(fstart), "replay": {"sides": sorted(sides)}})
        # downstream accounting for bars where live signalled consistently
        down = None
        if signal_evals and not set(kinds) & {"live_only_signal", "side_mismatch", "candle_mismatch"}:
            down = _downstream(leg, bundle, signal_evals, b_end, events_between,
                               packages_override=packages_override)
            if down["class"]:
                kinds.append(down["class"])
                divs.append({"class": down["class"], "bar": iso(b0),
                             "t": iso(signal_evals[0].t), "detail": down})
        if kinds:
            status = "divergent"
        elif signal_evals:
            status = "matched_signal"
        else:
            status = "matched_no_signal"
        bar_rows.append({"bar": iso(b0), "status": status, "evals": len(evs),
                         "signal_evals": len(signal_evals),
                         **({"classes": sorted(set(kinds))} if kinds else {}),
                         **({"downstream": down} if down else {})})
    by_class = {k: sum(1 for d in divs if d["class"] == k) for k in DIVERGENCE_CLASSES}
    checked = [b for b in bar_rows if b["status"] != "not_comparable"]
    out.update({
        "state": "checked" if checked else "could_not_check",
        "reason": None if checked else "no bar in the window was comparable",
        "bars_checked": len(checked) if checked else None,
        "bars_not_comparable": not_comparable,
        "evals_checked": evals_checked if checked else None,
        "divergences": len(divs) if checked else None,
        "by_class": by_class if checked else None,
        "divergence_detail": divs[:50],
        "bars": bar_rows,
        "replay_calls": rep.calls,
        "adx_gap_max": round(adx_gap_max, 6),
        "live_evals_in_window": len([e for e in evals if since <= e.t < until]),
        "matched_signal_bars": [b["bar"] for b in bar_rows if b["status"] == "matched_signal"],
        "replay_signal_windows": _signal_windows(variant_cache),
        "replay_none_windows": _none_windows(variant_cache)[:50],
    })
    return out


def _signal_windows(cache: dict) -> list[str]:
    """15m windows where every plausible replay state signals (robust)."""
    outl = []
    for key, vs in cache.items():
        if key[0] in ("closed", "x", "adx") or not isinstance(vs, list):
            continue
        sides = {v["side"] for v in vs}
        if "none" not in sides:
            outl.append(f"{iso(key[1])}|{sorted(sides)[0]}")
    return sorted(set(outl))


def _none_windows(cache: dict) -> list[str]:
    """15m windows where every plausible replay state is no-signal (robust)."""
    return sorted(iso(k[1]) for k, vs in cache.items()
                  if k[0] not in ("closed", "x", "adx") and isinstance(vs, list)
                  and {v["side"] for v in vs} == {"none"})


def _downstream(leg: Leg, bundle: dict, signal_evals: list[EvalRow], b_end: float,
                events_between, *, packages_override: list[dict] | None = None) -> dict:
    t0 = signal_evals[0].t
    t1 = max(b_end, signal_evals[-1].t) + 300
    pk_rows = packages_override if packages_override is not None else \
        ((bundle.get("order_packages") or {}).get("rows") or [])
    pkgs = [p for p in pk_rows if p.get("strategy_name") == leg.name
            and (to_epoch(p.get("created_at")) or 0) >= t0 - 120
            and (to_epoch(p.get("created_at")) or 0) <= t1]
    gates = events_between(t0 - 5, t1)
    res: dict = {"class": None, "packages": [p.get("order_package_id") for p in pkgs],
                 "gates": sorted({g["event"] for g in gates})}
    if not pkgs:
        if not gates:
            res["class"] = "dropped_no_package"
        return res
    if leg.execution != "live":
        return res
    trades = (bundle.get("trades") or {}).get("rows") or []
    ids = set(res["packages"])
    by_acct: dict[str, list] = {}
    for t in trades:
        if t.get("order_package_id") in ids:
            by_acct.setdefault(str(t.get("account_id")), []).append(t)
    scoped_out = set()
    for g in gates:
        r = g["row"]
        for k in ("held_by_account",):
            v = r.get(k)
            if isinstance(v, dict):
                scoped_out |= set(v)
        if r.get("account"):
            scoped_out.add(str(r["account"]))
    missing = []
    for a in leg.accounts:
        if a in by_acct or a in scoped_out:
            continue
        missing.append(a)
    res["orders_by_account"] = {a: [{"id": t.get("id"), "status": t.get("status")} for t in v]
                                for a, v in by_acct.items()}
    if missing:
        res["class"] = "dropped_no_order"
        res["missing_accounts"] = missing
    return res


# ── controls ─────────────────────────────────────────────────────────────────

def _placed(trade: dict) -> bool:
    st = str(trade.get("status") or "").lower()
    return st not in ("rejected", "refused", "failed", "error", "cancelled_unfilled")


def liveness(bundle: dict, since: float) -> dict:
    pk = bundle.get("order_packages") or {}
    tr = bundle.get("trades") or {}
    if pk.get("error") or tr.get("error"):
        return {"state": "could_not_check",
                "reason": f"order_packages: {pk.get('error')}; trades: {tr.get('error')}"}
    trades_by_pkg: dict[str, list] = {}
    for t in tr.get("rows") or []:
        if t.get("order_package_id"):
            trades_by_pkg.setdefault(t["order_package_id"], []).append(t)
    chains = []
    for p in pk.get("rows") or []:
        ct = to_epoch(p.get("created_at"))
        if ct is None or ct < since:
            continue
        placed = [t for t in trades_by_pkg.get(p.get("order_package_id"), []) if _placed(t)]
        if placed:
            chains.append({"strategy": p.get("strategy_name"), "created_at": p.get("created_at"),
                           "package": p.get("order_package_id"),
                           "accounts": sorted({str(t.get("account_id")) for t in placed})})
    chains.sort(key=lambda c: c["created_at"], reverse=True)
    return {"state": "pass" if chains else "fail",
            "chains_in_window": len(chains), "latest": chains[:3],
            "accounts_seen": sorted({a for c in chains for a in c["accounts"]})}


def historical_positive(results: list[dict], bundle: dict, legs: list[Leg]) -> dict:
    """Every live package of a covered leg, inside the checked bars, must sit in
    a matched_signal bar. Plus the named ANCHORS, individually."""
    by_leg = {r["leg"]: r for r in results}
    rows = (bundle.get("order_packages") or {}).get("rows") or []
    checked = []
    for p in rows:
        r = by_leg.get(p.get("strategy_name"))
        if not r or r["state"] != "checked":
            continue
        ct = to_epoch(p.get("created_at"))
        bar = _bar_of(r, ct)
        if bar is None:
            continue
        checked.append({"leg": r["leg"], "package": p.get("order_package_id"),
                        "created_at": p.get("created_at"), "bar": bar["bar"],
                        "status": bar["status"],
                        "matched": bar["status"] == "matched_signal"})
    anchors = []
    for a in ANCHORS:
        r = by_leg.get(a["leg"])
        t = to_epoch(a["t"])
        bar = _bar_of(r, t) if r and r["state"] == "checked" else None
        if bar is None or bar["status"] == "not_comparable":
            anchors.append({**a, "state": "could_not_check",
                            "reason": "outside this run's checked bars (window / 15m reach)"})
        else:
            anchors.append({**a, "state": "matched" if bar["status"] == "matched_signal" else "FAILED",
                            "bar_status": bar["status"]})
    n_ok = sum(1 for c in checked if c["matched"])
    a_fail = [a for a in anchors if a["state"] == "FAILED"]
    a_ok = [a for a in anchors if a["state"] == "matched"]
    if not checked and not a_ok and not a_fail:
        state = "could_not_check"
    elif n_ok == len(checked) and not a_fail:
        state = "pass"
    else:
        state = "fail"
    return {"state": state, "packages_checked": len(checked), "packages_matched": n_ok,
            "packages": checked[:40], "anchors": anchors}


def _bar_of(r: dict | None, t: float | None) -> dict | None:
    if not r or t is None or not r.get("bars"):
        return None
    from src.runtime.closed_bars import TF_SECONDS
    tf_s = TF_SECONDS[r["timeframe"]]
    for b in r["bars"]:
        b0 = to_epoch(b["bar"])
        lo, hi = (b0 + tf_s, b0 + 2 * tf_s) if r["decision_bar"] == "closed" else (b0, b0 + tf_s)
        if lo <= t < hi:
            return b
    return None


def planted_defect(legs: list[Leg], results: list[dict], bundle: dict,
                   since: float, until: float) -> dict:
    """Fabricate live records the check MUST flag, on this run's real candles."""
    out: dict = {"state": "could_not_check", "plants": []}
    by_leg = {r["leg"]: r for r in results}
    target = None
    for lg in legs:
        r = by_leg.get(lg.name)
        if r and r["state"] == "checked" and r.get("replay_signal_windows") and lg.decision_bar == "forming":
            target = (lg, r)
            break
    plants = []
    # Plant 1 (the mandated one): replay=signal, live=no-signal.
    if target:
        lg, r = target
        w, _side = r["replay_signal_windows"][0].split("|")
        wt = to_epoch(w)
        fake = [EvalRow(t=wt + 30 + 120 * k, side="none", reason="planted", adx=None,
                        confidence=None, close=None, channel=None) for k in range(5)]
        evals, _ = _evals_and_events((bundle["signals"].get(lg.name) or {}).get("rows") or [], lg.name)
        evals = [e for e in evals if not (wt <= e.t < wt + FINE_S)] + fake
        evals.sort(key=lambda e: e.t)
        res = analyse_leg(lg, bundle, since, until, evals_override=evals)
        hit = any(d["class"] == "missed_signal" and to_epoch(d.get("t")) and wt <= to_epoch(d["t"]) < wt + FINE_S
                  for d in res.get("divergence_detail") or [])
        plants.append({"plant": "replay_signal_live_none", "leg": lg.name, "window": w,
                       "flagged": hit})
    else:
        plants.append({"plant": "replay_signal_live_none", "flagged": None,
                       "reason": "no robust replay-signal window on any forming leg in this run"})
    # Plant 2: a lost bar; Plant 3: a live signal the replay cannot produce.
    for lg in legs:
        r = by_leg.get(lg.name)
        if not r or r["state"] != "checked" or lg.decision_bar != "forming":
            continue
        quiet = [b for b in r["bars"] if b["status"] == "matched_no_signal" and b.get("evals")]
        if not quiet:
            continue
        b0 = to_epoch(quiet[0]["bar"])
        from src.runtime.closed_bars import TF_SECONDS
        tf_s = TF_SECONDS[lg.timeframe]
        evals, _ = _evals_and_events((bundle["signals"].get(lg.name) or {}).get("rows") or [], lg.name)
        lost = [e for e in evals if not (b0 <= e.t < b0 + tf_s)]
        res = analyse_leg(lg, bundle, since, until, evals_override=lost)
        plants.append({"plant": "lost_bar", "leg": lg.name, "bar": quiet[0]["bar"],
                       "flagged": any(d["class"] == "lost_bar" and d["bar"] == quiet[0]["bar"]
                                      for d in res.get("divergence_detail") or [])})
        robust_none = None
        quiet_ws = [w for w in r.get("replay_none_windows") or []
                    if b0 <= to_epoch(w) < b0 + tf_s]
        if quiet_ws:
            wt = to_epoch(quiet_ws[0])
            fake = [e for e in evals if not (wt <= e.t < wt + FINE_S)] + [EvalRow(
                t=wt + 30, side="long", reason="planted", adx=None, confidence=None,
                close=None, channel=None)]
            fake.sort(key=lambda e: e.t)
            probe = analyse_leg(lg, bundle, since, until, evals_override=fake,
                                packages_override=[])
            robust_none = any(d["class"] == "live_only_signal" and d.get("t") == iso(wt + 30)
                              for d in probe.get("divergence_detail") or [])
        plants.append({"plant": "live_signal_replay_none", "leg": lg.name,
                       "flagged": robust_none,
                       **({"window": quiet_ws[0]} if quiet_ws else
                          {"reason": "no robust no-signal window in the quiet bar"})})
        break
    out["plants"] = plants
    vals = [p["flagged"] for p in plants]
    if any(v is False for v in vals):
        out["state"] = "fail"
    elif vals and all(v is True for v in vals):
        out["state"] = "pass"
    else:
        out["state"] = "partial" if any(v is True for v in vals) else "could_not_check"
    return out


# ── the run ──────────────────────────────────────────────────────────────────

def run(bundle: dict, legs: list[Leg], since: float, until: float) -> dict:
    results = [analyse_leg(lg, bundle, since, until) for lg in legs]
    real = [r for r in results if r["real_money"]]
    real_checked = [r for r in real if r["state"] == "checked"]
    real_cnc = [r for r in real if r["state"] != "checked"]
    divs_real = sum(r["divergences"] or 0 for r in real_checked)
    divs_all = sum(r["divergences"] or 0 for r in results if r["state"] == "checked")
    controls = {
        "planted_defect": planted_defect(legs, results, bundle, since, until),
        "historical_positive": historical_positive(results, bundle, legs),
        "liveness": liveness(bundle, since),
    }
    ctl_bad = [k for k, v in controls.items() if v["state"] in ("fail",)]
    if real_cnc:
        status = "could_not_check"
    elif divs_all:
        status = "divergent"
    elif ctl_bad:
        status = "controls_failed"
    else:
        status = "ok"
    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso(datetime.now(timezone.utc).timestamp()),
        "window": {"since": iso(since), "until": iso(until)},
        "bundle": {"fetched_at": bundle.get("fetched_at"), "requests": bundle.get("requests"),
                   "order_packages_complete": (bundle.get("order_packages") or {}).get("complete"),
                   "trades_complete": (bundle.get("trades") or {}).get("complete")},
        "status": status,
        "totals": {
            "real_money_legs": len(real), "real_money_legs_checked": len(real_checked),
            "real_money_legs_could_not_check": [r["leg"] for r in real_cnc],
            "legs_checked": sum(1 for r in results if r["state"] == "checked"),
            "bars_checked": sum(r["bars_checked"] or 0 for r in results),
            "real_money_bars_checked": sum(r["bars_checked"] or 0 for r in real_checked),
            "evals_checked": sum(r["evals_checked"] or 0 for r in results),
            "divergences_real_money": None if real_cnc else divs_real,
            "divergences_all": divs_all,
        },
        "controls": controls,
        "legs": results,
    }
    doc["headline"] = summary_line(doc)
    return doc


def summary_line(doc: dict | None) -> str:
    if not doc:
        return "Live↔replay parity — COULD NOT CHECK (no result artifact: we did not look; this is not zero)"
    t = doc["totals"]
    c = doc.get("controls") or {}
    day = (doc.get("generatedAt") or "")[:10]
    ctl = ", ".join(f"{k} {v.get('state')}" for k, v in c.items())
    if t["real_money_legs_could_not_check"]:
        head = (f"COULD NOT CHECK {len(t['real_money_legs_could_not_check'])} of "
                f"{t['real_money_legs']} real-money legs "
                f"({', '.join(t['real_money_legs_could_not_check'])}); ")
    else:
        head = ""
    return (f"Live↔replay parity ({day}, {doc['window']['since'][:10]}→{doc['window']['until'][:10]}): "
            f"{head}{t['legs_checked']} legs checked ({t['real_money_legs_checked']} real-money), "
            f"{t['bars_checked']} bars, divergences = {t['divergences_all']}"
            f" · controls: {ctl}")


def render_md(doc: dict) -> str:
    L = [f"# Live↔replay parity — {doc['generatedAt']}", "",
         "_Generated by `scripts/ops/live_replay_parity.py`. Do not hand-edit._", "",
         f"**{doc['headline']}**", "", f"Status: `{doc['status']}`", "",
         "| leg | real money | accounts | decision bar | state | bars | evals | divergences | matched signal bars |",
         "|---|---|---|---|---|--:|--:|--:|---|"]
    for r in doc["legs"]:
        L.append(f"| `{r['leg']}` | {'yes' if r['real_money'] else 'paper'} | {', '.join(r['accounts'])} "
                 f"| {r['decision_bar']} | {r['state']}{(' — ' + r['reason']) if r.get('reason') else ''} "
                 f"| {r['bars_checked'] if r['bars_checked'] is not None else '—'} "
                 f"| {r['evals_checked'] if r['evals_checked'] is not None else '—'} "
                 f"| {r['divergences'] if r['divergences'] is not None else '—'} "
                 f"| {', '.join(x[5:16] for x in r.get('matched_signal_bars') or []) or '—'} |")
    L += ["", "## Controls", ""]
    for k, v in doc["controls"].items():
        L.append(f"- **{k}**: `{v['state']}` — `{json.dumps({x: y for x, y in v.items() if x != 'state'}, default=str)[:900]}`")
    divs = [(r["leg"], d) for r in doc["legs"] for d in (r.get("divergence_detail") or [])]
    L += ["", f"## Divergences ({len(divs)})", ""]
    for leg, d in divs[:60]:
        L.append(f"- `{leg}` **{d['class']}** bar {d.get('bar')} t {d.get('t')} — "
                 f"`{json.dumps({k: v for k, v in d.items() if k not in ('class', 'bar', 't')}, default=str)[:400]}`")
    return "\n".join(L) + "\n"


RERUN = ("python3 scripts/ops/live_replay_parity.py --base-url <tunnel to the live "
         "web-api> --days 7  (or label an issue `live-replay-parity`; the workflow "
         ".github/workflows/live-replay-parity.yml runs it daily and commits "
         "comms/live_parity/latest.json)")


def findings(doc: dict) -> list[dict]:
    """One finding per (leg, divergence class), per real-money leg that could
    not be checked, and per failed control. These become pipeline rows."""
    out = []
    for r in doc["legs"]:
        if r["state"] != "checked":
            if r["real_money"]:
                out.append({"ref": f"live-replay-parity:{r['leg']}:could_not_check",
                            "leg": r["leg"], "class": "could_not_check", "n": None,
                            "real_money": True, "detail": r.get("reason")})
            continue
        for k, n in (r.get("by_class") or {}).items():
            if n:
                ex = [d for d in r.get("divergence_detail") or [] if d["class"] == k][:3]
                out.append({"ref": f"live-replay-parity:{r['leg']}:{k}", "leg": r["leg"],
                            "class": k, "n": n, "real_money": r["real_money"],
                            "detail": json.dumps(ex, default=str)[:900]})
    for name, c in (doc.get("controls") or {}).items():
        if c.get("state") == "fail":
            out.append({"ref": f"live-replay-parity:control:{name}", "leg": None,
                        "class": f"control_{name}_failed", "n": None, "real_money": True,
                        "detail": json.dumps(c, default=str)[:900]})
    return out


def file_pipeline(doc: dict, store: Path) -> list[str]:
    """Append a pipeline row per NEW finding; an open row with the same
    ``origin.ref`` already carries it, so it is not filed twice."""
    import hashlib
    from scripts.ops import pipeline
    res = pipeline.read_log(store)
    open_refs = {(it.get("origin") or {}).get("ref") for it in res.items.values()
                 if it.get("state") in pipeline.OPEN_STATES}
    day = (doc.get("generatedAt") or "")[:10].replace("-", "")
    filed = []
    for f in findings(doc):
        if f["ref"] in open_refs:
            continue
        pid = f"PI-{day}-LRP-{hashlib.sha1(f['ref'].encode()).hexdigest()[:8].upper()}"
        who = f"leg `{f['leg']}`" if f["leg"] else "the parity run"
        what = (f"Live-vs-replay parity: {who} — {f['class']}"
                + (f" ×{f['n']}" if f["n"] else "")
                + (" (REAL-MONEY roster)" if f["real_money"] else " (paper roster)")
                + f" in window {doc['window']['since']}..{doc['window']['until']}. "
                f"MEASURED by scripts/ops/live_replay_parity.py; artifact "
                f"comms/live_parity/{doc['generatedAt'][:10]}.json. Examples: {f['detail']}")
        item = {"id": pid, "what": what,
                "origin": {"kind": "audit", "ref": f["ref"], "rerun": RERUN},
                "due_when": {"kind": "observation", "check_every_days": 1,
                             "clears_when": "the daily live-replay-parity run reports 0 of this "
                                            "class for this leg, with the cause root-caused, or "
                                            "killed with a stated reason"},
                "next_action": "dispatch_lane", "state": "queued"}
        pipeline.append(item, store, intent="new")
        filed.append(pid)
    return filed


def _load_yaml(p: Path) -> dict:
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url")
    ap.add_argument("--token-env", default="DIAG_READ_TOKEN")
    ap.add_argument("--bundle", help="analyse a saved bundle instead of fetching")
    ap.add_argument("--save-bundle", help="write the fetched bundle here")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--until", help="ISO end of window (default: now, floored to 15m)")
    ap.add_argument("--no-paper", action="store_true")
    ap.add_argument("--out", help="directory for latest.json / latest.md / <date>.json")
    ap.add_argument("--summary-line", metavar="LATEST_JSON")
    ap.add_argument("--file-pipeline", metavar="STORE",
                    help="append a pipeline row per new finding to this store")
    args = ap.parse_args(argv)

    if args.summary_line:
        p = Path(args.summary_line)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None
        print(summary_line(doc))
        return 0

    legs = resolve_legs(_load_yaml(REPO_ROOT / "config/accounts.yaml"),
                        _load_yaml(REPO_ROOT / "config/strategies.yaml"),
                        include_paper=not args.no_paper)
    if args.bundle:
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        until = to_epoch(args.until) or to_epoch(bundle["until"])
        since = until - args.days * 86400 if args.until else to_epoch(bundle["since"])
    else:
        token = os.environ.get(args.token_env, "")
        if not args.base_url or not token:
            print("::error::--base-url and a token in $%s are required to fetch" % args.token_env)
            return 2
        now = datetime.now(timezone.utc).timestamp()
        until = to_epoch(args.until) or (now // FINE_S * FINE_S - FINE_S)
        since = until - args.days * 86400
        bundle = fetch_bundle(Fetcher(args.base_url, token), legs, since, until)
        if args.save_bundle:
            Path(args.save_bundle).write_text(json.dumps(bundle), encoding="utf-8")
    doc = run(bundle, legs, since, until)
    text = json.dumps(doc, indent=1, default=str, sort_keys=False)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "latest.json").write_text(text + "\n", encoding="utf-8")
        (out / f"{doc['generatedAt'][:10]}.json").write_text(text + "\n", encoding="utf-8")
        (out / "latest.md").write_text(render_md(doc), encoding="utf-8")
    if args.file_pipeline:
        filed = file_pipeline(doc, Path(args.file_pipeline))
        print(f"pipeline rows filed: {len(filed)} {filed}")
    print(doc["headline"])
    print(f"status={doc['status']}")
    ping = [f for f in findings(doc) if f["real_money"]]
    print(f"ping_needed={'true' if ping else 'false'}")
    gho = os.environ.get("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a", encoding="utf-8") as fh:
            fh.write(f"status={doc['status']}\nping_needed={'true' if ping else 'false'}\n")
            fh.write(f"headline={doc['headline'].replace(chr(10), ' ')}\n")
    # exit: 0 ok · 1 divergence or failed control · 2 could not check
    return {"ok": 0, "divergent": 1, "controls_failed": 1}.get(doc["status"], 2)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::tp-doctrine-guard (--self-test, then the ratchet scan);
# also the health-review compliance-audit rotation runs it with --strict --json.
"""tp-doctrine-guard — every rostered leg carries a REAL, REVISABLE take-profit (ratchet).

THE DOCTRINE THIS MEASURES
--------------------------
`docs/ARCHITECTURE-CANONICAL.md` § "TP doctrine" (operator, 2026-10-06, verbatim
there): there are no fictional take-profits. Every leg's bracket carries a real
prediction of where the move runs out, that prediction is re-estimated through
the trade's life, and compliance is MEASURED in audits rather than asserted.

This guard is the measurement. Its population is every **(account, leg)
routing** in `config/accounts.yaml::strategies`, on every account, whatever its
`mode` -- a dry account can be flipped live by one system-action, so what it
would trade is in scope. One leg fanned out to four accounts is four routings,
because the exit PATH differs by venue and the money at stake differs by
account class.

TWO DIMENSIONS, NEVER COLLAPSED
-------------------------------
`entry_tp`     Does the bracket carry a prediction AT ENTRY?
               `declared`  -- a finite target (`target_r` / `tp_r` / `tp_at_r`
                              below SENTINEL_R_FLOOR) ................... PASS
               `sentinel`  -- the effective target is >= 50 R, declared or
                              inherited from the unit's class default, so the
                              level that RESTS is the venue cap (`entry x
                              1.099`) wearing a target's label ........... FAIL
               `no_target` -- no target key and no class default ......... FAIL
               A `tp_intent: {mode: none}` declaration is reported, and it
               does NOT exempt: the doctrine says a trail-is-the-exit leg
               still predicts where momentum runs out.
`tp_revision`  Can the prediction MOVE while the trade is open? Two facts,
               both required:
               `producer` -- the leg DECLARES a registered `tp_revision` rule
                              AND its monitor unit's `monitor()` calls
                              `plan_tp_revision` (directly or via a module
                              function it calls) and returns through
                              `merge_verdict` (AST-read, not grepped). A bare
                              `{"tp": ...}` literal does NOT count (B1,
                              2026-10-07): it proves a key was typed, not that
                              a prediction is computed for this leg;
               `path`     -- the account's exit path CONSUMES the verdict:
                              exchange -- `interpret_verdict` parses `tp` and
                              `order_monitor._apply_update` forwards it to
                              `_send_modify_to_exchange`, and the venue's
                              EXCHANGE_MANAGEMENT_CAPS carries `modify`;
                              prop -- per the account's platform in
                              `config/prop_platforms.yaml`: the adapter class
                              declares `TP_AMEND_SUPPORTED = True` and
                              `prop_trail` passes `plan_tp_revision`'s TP to
                              `modify_bracket`; a phone account -- the phone
                              executor calls `plan_tp_revision`.
               Missing either is FAIL, and the reason names which.

The entry grade reuses `scripts/research/bracket_expectation_census.py`
(declared vs inherited class default, the fleet's sentinel idiom), so the two
instruments cannot disagree about what a sentinel is.

THE RATCHET, AND WHY IT IS REPORT-FIRST
---------------------------------------
MEASURED on `main` 2026-10-06 (233c9c28): 52 of 52 routings fail
`tp_revision` -- no strategy module has ever produced a `tp` verdict (the
plumbing downstream of it is live on Bybit / IB / Alpaca and absent on prop) --
and 15 of the 26 distinct legs are sentinels. Failing all of that on day one
would red-wall every PR and get this reverted, which this repo has measured
(`check_guard_liveness.py`, `check_cadence_liveness.py`). So:

  * the existing debt is carried in a dated BASELINE below, one visible line per
    routing naming WHICH dimension fails, under a comment saying the list may
    only SHRINK -- the `check_soak_registered.py` pattern, acceptable because it
    is not silent: adding a line is a deliberate act a reviewer sees in the diff;
  * a NEW non-compliant routing (not in the baseline) FAILS -- the exact case
    the operator named: a newly rostered leg with a fictional TP;
  * DEBT GROWTH fails: a baselined routing that now fails a dimension it did
    not fail before;
  * a STALE entry fails: a baselined dimension that became compliant and was
    not removed, or a routing that left the roster. A baseline may only shrink;
  * `--strict` fails on the baselined debt too. That is the shape the
    health-review compliance-audit rotation runs, because an audit asks whether
    the debt SHRANK, not whether it grew.

⚠️ Adding a BASELINE line for a real-money or prop routing is a roster
decision about what trades with real money against a venue cap, and it belongs
to the operator (Tier-3). The guard cannot tell who typed the line; the diff
can.

STATES THE SCAN ITSELF CAN BE IN (exit code)
--------------------------------------------
  0  clean -- every finding is baselined (debt printed), or `--strict` and no debt
  1  findings -- NEW / GROWTH / STALE, or `--strict` with debt
  2  could not look -- a config or source this reads is unreadable, or a
     positive control inside the scan found nothing (zero alias pairs, zero
     venue caps). Never reported as clean, never reported as findings.

Run:
    python3 scripts/ci/check_tp_doctrine.py              # table + ratchet
    python3 scripts/ci/check_tp_doctrine.py --json       # machine-readable
    python3 scripts/ci/check_tp_doctrine.py --strict     # audit shape
    python3 scripts/ci/check_tp_doctrine.py --self-test  # planted controls
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.research.bracket_expectation_census import (  # noqa: E402
    CLASS_DEFAULT_SOURCES,
    SENTINEL_R_FLOOR,
    _read_class_default,
    cap_r_for,
    resolve_intent,
    resolve_target,
)

EXIT_CLEAN, EXIT_FINDINGS, EXIT_COULD_NOT_LOOK = 0, 1, 2

DIM_ENTRY = "entry_tp"
DIM_REVISION = "tp_revision"

# entry states
ENTRY_DECLARED = "declared"
ENTRY_SENTINEL = "sentinel"
ENTRY_NO_TARGET = "no_target"
# path states
PATH_PLUMBED = "plumbed"
PATH_ABSENT = "absent"
PATH_UNVERIFIED = "unverified"      # we could not read the path -- NOT absent
# producer states
PRODUCER_YES = "produces_tp_verdict"
PRODUCER_NO = "no_tp_verdict"
PRODUCER_UNRESOLVED = "unit_unresolved"
PRODUCER_UNDECLARED = "rule_undeclared"     # unit wired, leg declares no rule
PRODUCER_UNKNOWN_RULE = "rule_unknown"      # leg declares a rule nobody registers

#: Money-at-stake ranking. Lower sorts first. `paper_role: portfolio` is the
#: mirror marker `config/accounts.yaml` itself uses.
CLASS_RANK = {"real_money": 0, "prop": 1, "mirror": 2, "paper": 3}

REFERENCE_ATR_OVER_ENTRY = 0.02   # the census's reference; a REFERENCE, not a measurement


@dataclass
class Paths:
    accounts: Path = REPO / "config" / "accounts.yaml"
    strategies: Path = REPO / "config" / "strategies.yaml"
    units_dir: Path = REPO / "src" / "units" / "strategies"
    builders: Path = REPO / "src" / "runtime" / "strategy_signal_builders.py"
    clients: Path = REPO / "src" / "units" / "accounts" / "clients.py"
    prop_trail: Path = REPO / "src" / "prop" / "prop_trail.py"
    monitor_verdict: Path = REPO / "src" / "runtime" / "monitor_verdict.py"
    order_monitor: Path = REPO / "src" / "runtime" / "order_monitor.py"
    tp_revision: Path = REPO / "src" / "runtime" / "tp_revision.py"
    prop_platforms: Path = REPO / "config" / "prop_platforms.yaml"
    platform_dir: Path = REPO / "src" / "prop" / "platform"
    phone_executor: Path = REPO / "src" / "prop" / "phone_executor.py"
    repo: Path = REPO


# ---------------------------------------------------------------------------
# THE BASELINE -- dated debt, one line per routing, may only SHRINK.
# ---------------------------------------------------------------------------
#: key "account/leg" -> the dimensions that FAILED on 2026-10-06 at 233c9c28.
#: `R` = tp_revision only (a real finite target, but nothing can move it);
#: `ER` = entry_tp AND tp_revision (a sentinel or no target, and nothing can
#: move it). Generated by `--print-baseline` and pasted, then re-verified by
#: the scan itself: a line that no longer matches FAILS the guard.
R: frozenset[str] = frozenset({DIM_REVISION})
ER: frozenset[str] = frozenset({DIM_ENTRY, DIM_REVISION})
BASELINE_2026_10_06: dict[str, frozenset[str]] = {
    # ── real_money ──────────────────────────────────────────────────────────
    "alpaca_live/iaum_pullback_1d": ER,
    "alpaca_live/ief_pullback_1d": ER,
    "alpaca_live/slv_pullback_1d": ER,
    "bybit_2/ada_pullback_2h": R,
    "bybit_2/trend_donchian_eth_4h": ER,
    "bybit_2/trend_donchian_xrp_4h": R,
    "bybit_2/xrp_pullback_2h": R,
    # ── prop ────────────────────────────────────────────────────────────────
    # breakout_1 legs removed 2026-10-07 (account retired, roster cleared).
    "breakout_2/trend_donchian_eth_prop": R,
    "breakout_2/trend_donchian_sol_prop": R,
    "tradeify_1/trend_donchian_eth_prop": R,
    "tradeify_1/trend_donchian_sol_prop": R,
    # velotrade_1 was rostered by #16705 (VELOTRADE-GOLIVE, Tier-3 HELD) on
    # 2026-10-06, hours before this guard existed, so its two routings are
    # day-one debt of the same class as tradeify_1's -- not a roster change
    # made against the doctrine. This guard named them itself on its first CI
    # run against main at 233c9c28 (PR #16739), which is the positive control
    # for the NEW-routing branch.
    "velotrade_1/trend_donchian_eth_prop": R,
    "velotrade_1/trend_donchian_sol_prop": R,
    # ── mirror (paper_role: portfolio) ──────────────────────────────────────
    "alpaca_portfolio/ief_pullback_1d": ER,
    "alpaca_portfolio/slv_pullback_1d": ER,
    "bybit_portfolio/ada_pullback_2h": R,
    "bybit_portfolio/trend_donchian_eth_4h": ER,
    "bybit_portfolio/trend_donchian_xrp_4h": R,
    "bybit_portfolio/xrp_pullback_2h": R,
    # ── paper ───────────────────────────────────────────────────────────────
    "alpaca_options_paper/gdx_pullback_1d": ER,
    "alpaca_options_paper/slv_pullback_1d": ER,
    "alpaca_options_paper/slv_trend_1h": ER,
    "alpaca_paper/gdx_pullback_1d": ER,
    "alpaca_paper/gld_pullback_1d": ER,
    "alpaca_paper/iaum_pullback_1d": ER,
    "alpaca_paper/ief_pullback_1d": ER,
    "alpaca_paper/iwm_trend_long_1d": R,
    "alpaca_paper/qld_trend_long_1d": ER,
    "alpaca_paper/qqq_trend_long_1d": R,
    "alpaca_paper/scha_trend_long_1d": R,
    "alpaca_paper/slv_pullback_1d": ER,
    "alpaca_paper/slv_trend_1h": ER,
    "alpaca_paper/splg_trend_long_1d": ER,
    "alpaca_paper/spy_trend_long_1d": R,
    "alpaca_paper/tlt_pullback_1d": ER,
    "alpaca_paper/tqqq_trend_long_1d": ER,
    "bybit_1/ada_pullback_2h": R,
    "bybit_1/trend_donchian_1h": ER,
    "bybit_1/trend_donchian_eth_4h": ER,
    "bybit_1/trend_donchian_xrp_4h": R,
    "bybit_1/xrp_pullback_2h": R,
    "ib_paper/ict_scalp_mgc_15m": R,
    "ib_paper/iwm_trend_long_1d": R,
    "ib_paper/mes_trend_long_1d": ER,
    "ib_paper/mgc_pullback_1d": R,
    "ib_paper/mgc_trend_1h": ER,
    "ib_paper/mhg_pullback_1d": ER,
    "ib_paper/qqq_trend_long_1d": R,
    "ib_paper/spy_trend_long_1d": R,
    "ib_paper/tlt_pullback_1d": ER,
}


class CouldNotLook(RuntimeError):
    """A source this scan depends on is unreadable or its probe found nothing."""


# ---------------------------------------------------------------------------
# readers -- each is a probe that must find a POSITIVE to be trusted
# ---------------------------------------------------------------------------
def _yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CouldNotLook(f"{path}: unreadable ({type(exc).__name__}: {exc})") from exc
    if not isinstance(data, dict):
        raise CouldNotLook(f"{path}: top level is not a mapping")
    return data


_ALIAS_RE = re.compile(r"\(\s*([A-Za-z0-9_]+)_signal_builder\s*,\s*\"([A-Za-z0-9_]+)\"\s*\)")


def read_monitor_aliases(builders: Path) -> dict[str, str]:
    """leg -> unit module, from the `monitor_unit` table in strategy_signal_builders.

    A SOURCE read rather than `pipeline.monitor_unit_for` so the guard does not
    import the runtime (pandas, the exchange clients) to answer a config
    question. The runtime resolver is the authority; `tests/test_check_tp_doctrine.py`
    pins that the two agree on every rostered leg.
    """
    try:
        text = builders.read_text(encoding="utf-8")
    except OSError as exc:
        raise CouldNotLook(f"{builders}: unreadable ({exc})") from exc
    pairs = {leg: unit for leg, unit in _ALIAS_RE.findall(text)}
    if not pairs:
        raise CouldNotLook(f"{builders}: the alias probe found ZERO (builder, unit) pairs "
                           f"-- the table moved or the regex broke; refusing to grade")
    return pairs


def unit_for(leg: str, aliases: dict[str, str], units_dir: Path) -> str | None:
    if leg in aliases:
        return aliases[leg]
    if (units_dir / f"{leg}.py").is_file():
        return leg
    return None


def _calls(node: ast.AST) -> set[str]:
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def monitor_produces_tp(unit_src: str) -> bool | None:
    """Is `monitor()` in this module WIRED to the TP-revision contract?

    True only when `monitor()` calls `merge_verdict` AND `plan_tp_revision` is
    called by `monitor()` itself or by a module-level function `monitor()`
    calls (one level). A literal `{"tp": ...}` dict is not enough: it can be
    typed without any prediction behind it. `None` when there is no `monitor`.
    """
    try:
        tree = ast.parse(unit_src)
    except SyntaxError:
        return None
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    mon = funcs.get("monitor")
    if mon is None:
        return None
    called = _calls(mon)
    if "merge_verdict" not in called:
        return False
    if "plan_tp_revision" in called:
        return True
    return any("plan_tp_revision" in _calls(funcs[c]) for c in called if c in funcs and c != "monitor")


_RULE_RE = re.compile(r"@register_rule\(\s*[\"']([A-Za-z0-9_]+)[\"']\s*\)")


def read_registered_rules(*sources: Path) -> set[str]:
    """Rule names registered with `tp_revision.register_rule` in these files."""
    names: set[str] = set()
    for src in sources:
        try:
            names |= set(_RULE_RE.findall(src.read_text(encoding="utf-8")))
        except OSError:
            continue
    return names


def leg_declared_rule(cfg: dict[str, Any]) -> str | None:
    raw = cfg.get("tp_revision")
    if isinstance(raw, dict):
        raw = raw.get("rule")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def exchange_consumes_tp(monitor_verdict: Path, order_monitor: Path) -> str:
    """Does the exchange path consume a `tp` verdict end to end?"""
    try:
        mv = monitor_verdict.read_text(encoding="utf-8")
        om = order_monitor.read_text(encoding="utf-8")
    except OSError:
        return PATH_UNVERIFIED
    parses = re.search(r'for key in \("sl", "tp"\)', mv) is not None
    start = om.find("def _apply_update(")
    body = om[start:om.find("\ndef ", start + 10)] if start >= 0 else ""
    forwards = re.search(r'_send_modify_to_exchange\(\s*leg,(?:(?!\n\s*\)).)*?tp=updates\.get\("tp"\)', body, re.DOTALL)
    if start < 0:
        return PATH_UNVERIFIED
    return PATH_PLUMBED if parses and forwards else PATH_ABSENT


def read_platform_tp_caps(platform_dir: Path) -> dict[str, bool]:
    """platform name -> does its adapter class declare TP_AMEND_SUPPORTED = True."""
    caps: dict[str, bool] = {}
    try:
        files = sorted(platform_dir.glob("*.py"))
    except OSError:
        return caps
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            plat, tp_ok = None, False
            for st in cls.body:
                tgt = val = None
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    tgt, val = st.targets[0].id, st.value
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    tgt, val = st.target.id, st.value
                if tgt == "platform" and isinstance(val, ast.Constant) and val.value:
                    plat = str(val.value)
                if tgt == "TP_AMEND_SUPPORTED" and isinstance(val, ast.Constant):
                    tp_ok = val.value is True
            if plat:
                caps[plat] = tp_ok
    return caps


_PROP_MODIFY_RE = re.compile(r"modify_bracket\(\s*page\s*,\s*\w+\s*,\s*([\w.]+)\s*,\s*([\w.]+)")


def prop_trail_forwards_tp(prop_trail: Path) -> str:
    """Does the prop trail pass `plan_tp_revision`'s TP to modify_bracket?"""
    try:
        text = prop_trail.read_text(encoding="utf-8")
    except OSError:
        return PATH_UNVERIFIED
    calls = _PROP_MODIFY_RE.findall(text)
    if not calls:
        return PATH_UNVERIFIED
    if "plan_tp_revision(" in text and any(tp != "None" for _sl, tp in calls):
        return PATH_PLUMBED
    return PATH_ABSENT


def read_prop_platforms(path: Path) -> dict[str, str]:
    """account -> platform, from config/prop_platforms.yaml (accounts + phone_accounts)."""
    try:
        data = _yaml(path)
    except CouldNotLook:
        return {}
    out: dict[str, str] = {}
    for section in ("accounts", "phone_accounts"):
        for acc, v in (data.get(section) or {}).items():
            if isinstance(v, dict) and v.get("platform"):
                out[str(acc)] = str(v["platform"])
    return out


def phone_consumes_tp(phone_executor: Path) -> str:
    """Contract with lane PROP-TRAIL-PHONE: the phone amend channel is plumbed
    for TPs when the phone executor calls `plan_tp_revision`."""
    try:
        text = phone_executor.read_text(encoding="utf-8")
    except OSError:
        return PATH_UNVERIFIED
    return PATH_PLUMBED if "plan_tp_revision(" in text else PATH_ABSENT


_CAPS_RE = re.compile(r"\"([a-z_]+)\"\s*:\s*frozenset\(\s*(\{[^}]*\}|\))", re.DOTALL)


def read_venue_caps(clients: Path) -> dict[str, frozenset[str]]:
    """exchange -> management caps, from EXCHANGE_MANAGEMENT_CAPS' source."""
    try:
        text = clients.read_text(encoding="utf-8")
    except OSError as exc:
        raise CouldNotLook(f"{clients}: unreadable ({exc})") from exc
    start = text.find("EXCHANGE_MANAGEMENT_CAPS")
    if start < 0:
        raise CouldNotLook(f"{clients}: EXCHANGE_MANAGEMENT_CAPS not found")
    body = text[start:]
    end = body.find("\n}\n")
    body = body[: end if end > 0 else None]
    caps: dict[str, frozenset[str]] = {}
    for venue, inner in _CAPS_RE.findall(body):
        names = set(re.findall(r"\"([a-z_]+)\"", inner))
        caps[venue] = frozenset(names)
    if not caps:
        raise CouldNotLook(f"{clients}: the caps probe parsed ZERO venues")
    return caps


# ---------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------
@dataclass
class Routing:
    account: str
    account_class: str      # real_money | prop | mirror | paper
    mode: str
    exchange: str
    leg: str
    execution: str
    enabled: bool
    unit: str | None
    entry_state: str
    target_r: float | None
    target_source: str | None
    target_origin: str
    tp_intent_mode: str
    cap_r_ref: float | None
    producer_state: str
    path_state: str
    fails: frozenset[str] = field(default_factory=frozenset)

    @property
    def key(self) -> str:
        return f"{self.account}/{self.leg}"

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["fails"] = sorted(self.fails)
        d["key"] = self.key
        d["compliant"] = not self.fails
        return d


def _class_of(acc: dict[str, Any]) -> str:
    cls = str(acc.get("account_class") or "").strip().lower()
    if cls == "paper" and str(acc.get("paper_role") or "").strip().lower() == "portfolio":
        return "mirror"
    return cls or "unknown"


def scan(paths: Paths | None = None) -> list[Routing]:
    paths = paths or Paths()
    accounts = _yaml(paths.accounts).get("accounts")
    strategies = _yaml(paths.strategies).get("strategies")
    if not isinstance(accounts, dict) or not isinstance(strategies, dict):
        raise CouldNotLook("accounts.yaml::accounts or strategies.yaml::strategies is not a mapping")
    aliases = read_monitor_aliases(paths.builders)
    caps = read_venue_caps(paths.clients)
    exchange_state = exchange_consumes_tp(paths.monitor_verdict, paths.order_monitor)
    trail_state = prop_trail_forwards_tp(paths.prop_trail)
    plat_caps = read_platform_tp_caps(paths.platform_dir)
    prop_plat = read_prop_platforms(paths.prop_platforms)
    phone_state = phone_consumes_tp(paths.phone_executor)
    rules = read_registered_rules(paths.tp_revision, *sorted(paths.units_dir.glob("*.py")))

    def prop_path(account: str) -> str:
        plat = prop_plat.get(account)
        if plat is None:
            return PATH_UNVERIFIED
        if plat == "breakout_phone":
            return phone_state
        if plat not in plat_caps:
            return PATH_UNVERIFIED
        if not plat_caps[plat]:
            return PATH_ABSENT
        return trail_state
    defaults = {fam: _read_class_default(str(paths.repo / rel), key) if paths.repo != REPO
                else _read_class_default(rel, key)
                for fam, (rel, key) in CLASS_DEFAULT_SOURCES.items()}

    producer_cache: dict[str, str] = {}

    def producer_for(unit: str | None) -> str:
        if unit is None:
            return PRODUCER_UNRESOLVED
        if unit not in producer_cache:
            src_path = paths.units_dir / f"{unit}.py"
            try:
                verdict = monitor_produces_tp(src_path.read_text(encoding="utf-8"))
            except OSError:
                verdict = None
            producer_cache[unit] = (PRODUCER_UNRESOLVED if verdict is None
                                    else PRODUCER_YES if verdict else PRODUCER_NO)
        return producer_cache[unit]

    out: list[Routing] = []
    for acc_name, acc in accounts.items():
        if not isinstance(acc, dict):
            continue
        exchange = str(acc.get("exchange") or "").strip().lower()
        for leg in acc.get("strategies") or []:
            cfg = strategies.get(leg)
            if not isinstance(cfg, dict):
                cfg = {}
            eff, src_key, origin = resolve_target(leg, cfg, defaults)
            if eff is None:
                entry = ENTRY_NO_TARGET
            elif eff >= SENTINEL_R_FLOOR:
                entry = ENTRY_SENTINEL
            else:
                entry = ENTRY_DECLARED
            sm = cfg.get("atr_stop_mult")
            try:
                sm_f = float(sm) if sm is not None else None
            except (TypeError, ValueError):
                sm_f = None
            unit = unit_for(leg, aliases, paths.units_dir)
            producer = producer_for(unit)
            if producer == PRODUCER_YES:
                rule = leg_declared_rule(cfg)
                if rule is None:
                    producer = PRODUCER_UNDECLARED
                elif rule not in rules:
                    producer = PRODUCER_UNKNOWN_RULE
            if exchange == "breakout":
                path = prop_path(acc_name)
            elif exchange in caps:
                path = (exchange_state if "modify" in caps[exchange] else PATH_ABSENT)
            else:
                path = PATH_UNVERIFIED
            fails = set()
            if entry != ENTRY_DECLARED:
                fails.add(DIM_ENTRY)
            if producer != PRODUCER_YES or path != PATH_PLUMBED:
                fails.add(DIM_REVISION)
            out.append(Routing(
                account=acc_name, account_class=_class_of(acc),
                mode=str(acc.get("mode") or "live"), exchange=exchange, leg=leg,
                execution=str(cfg.get("execution") or "live"),
                enabled=bool(cfg.get("enabled", True)), unit=unit,
                entry_state=entry, target_r=eff, target_source=src_key,
                target_origin=origin, tp_intent_mode=resolve_intent(cfg)["mode"],
                cap_r_ref=cap_r_for(sm_f, REFERENCE_ATR_OVER_ENTRY),
                producer_state=producer, path_state=path, fails=frozenset(fails)))
    out.sort(key=lambda r: (CLASS_RANK.get(r.account_class, 9), r.account, r.leg))
    return out


# ---------------------------------------------------------------------------
# the ratchet
# ---------------------------------------------------------------------------
def ratchet(routings: list[Routing], baseline: dict[str, frozenset[str]],
            *, strict: bool = False) -> tuple[list[str], dict[str, Any]]:
    findings: list[str] = []
    seen = set()
    debt_routings = 0
    for r in routings:
        seen.add(r.key)
        base = baseline.get(r.key)
        if r.fails:
            if base is None:
                findings.append(
                    f"NEW non-compliance: {r.key} fails {sorted(r.fails)} "
                    f"(entry={r.entry_state}, producer={r.producer_state}, "
                    f"path={r.path_state}) and is not in the baseline. A newly "
                    f"rostered leg carries a real, revisable TP (docs/ARCHITECTURE-"
                    f"CANONICAL.md § TP doctrine) -- or its debt is added to "
                    f"BASELINE_2026_10_06 as a visible, deliberate line.")
                continue
            grown = r.fails - base
            shrunk = base - r.fails
            if grown:
                findings.append(f"DEBT GROWTH: {r.key} now also fails {sorted(grown)} "
                                f"(baselined as {sorted(base)}).")
            if shrunk:
                findings.append(f"BASELINE STALE: {r.key} is baselined for {sorted(shrunk)} "
                                f"but is now compliant there -- remove it; the list may only shrink.")
            if not grown and not shrunk:
                debt_routings += 1
                if strict:
                    findings.append(f"STRICT: baselined debt {r.key} still fails {sorted(r.fails)}.")
        elif base is not None:
            findings.append(f"BASELINE STALE: {r.key} is fully compliant but still baselined "
                            f"for {sorted(base)} -- remove it.")
    for key in sorted(set(baseline) - seen):
        findings.append(f"BASELINE STALE: {key} is no longer rostered -- remove it.")

    by_class: dict[str, dict[str, int]] = {}
    for r in routings:
        c = by_class.setdefault(r.account_class, {"routings": 0, "entry_fail": 0,
                                                  "revision_fail": 0, "compliant": 0})
        c["routings"] += 1
        c["entry_fail"] += DIM_ENTRY in r.fails
        c["revision_fail"] += DIM_REVISION in r.fails
        c["compliant"] += not r.fails
    summary = {
        "routings": len(routings),
        "distinct_legs": len({r.leg for r in routings}),
        "compliant": sum(1 for r in routings if not r.fails),
        "baselined_debt_routings": debt_routings,
        "baseline_entries": len(baseline),
        "by_class": by_class,
    }
    return findings, summary


def render_table(routings: list[Routing]) -> str:
    hdr = (f"{'class':<10} {'account':<21} {'leg':<26} {'mode':<8} {'exec':<7} "
           f"{'entry_tp':<10} {'tp_r':>6} {'intent':<11} {'producer':<20} {'path':<11} verdict")
    lines = [hdr, "-" * len(hdr)]
    for r in routings:
        tp = "-" if r.target_r is None else f"{r.target_r:g}"
        verdict = "PASS" if not r.fails else "FAIL:" + "+".join(sorted(r.fails))
        lines.append(f"{r.account_class:<10} {r.account:<21} {r.leg:<26} {r.mode:<8} "
                     f"{r.execution:<7} {r.entry_state:<10} {tp:>6} {r.tp_intent_mode:<11} "
                     f"{r.producer_state:<20} {r.path_state:<11} {verdict}")
    return "\n".join(lines)


def print_baseline(routings: list[Routing]) -> str:
    lines = []
    for r in routings:
        if not r.fails:
            continue
        tag = "ER" if DIM_ENTRY in r.fails else "R"
        lines.append(f'    "{r.key}": {tag},')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# planted controls
# ---------------------------------------------------------------------------
_FAKE_BUILDERS = '''
for _builder, _monitor_unit in (
    (good_leg_signal_builder, "good_unit"),
    (bad_leg_signal_builder, "sentinel_unit"),
    (mover_leg_signal_builder, "good_unit"),
    (undeclared_leg_signal_builder, "good_unit"),
    (unknown_rule_leg_signal_builder, "good_unit"),
):
    _builder.monitor_unit = _monitor_unit
'''
_FAKE_CLIENTS = '''
EXCHANGE_MANAGEMENT_CAPS: dict[str, frozenset[str]] = {
    "bybit": frozenset(
        {"modify", "close", "open_positions"}
    ),
    "oanda": frozenset({"close", "open_positions"}),
    "breakout": frozenset(),
}
'''
_FAKE_PROP_TRAIL = 'r = adapter.modify_bracket(page, p, target, None, arm=live, rollout=rollout)\n'
_FAKE_PROP_TRAIL_TP = (_FAKE_PROP_TRAIL + 'rev = plan_tp_revision(leg=leg)\n'
                       'r = adapter.modify_bracket(page, p, None, rev.tp, arm=live, rollout=rollout)\n')
_FAKE_MONITOR_VERDICT = '    for key in ("sl", "tp"):\n        pass\n'
_FAKE_ORDER_MONITOR = ('def _apply_update(db, pkg, verdict, summary):\n'
                       '    ex_result = _send_modify_to_exchange(\n        leg,\n'
                       '        sl=updates.get("sl"),\n        tp=updates.get("tp"),\n    )\n'
                       '\ndef other():\n    pass\n')
_FAKE_TP_REVISION = '@register_rule("measured_move")\ndef _mm(**kw):\n    return None\n'
_FAKE_PLATFORMS = """
accounts:
  prop: {platform: api_plat}
  prop_browser: {platform: browser_plat}
phone_accounts:
  phone: {platform: breakout_phone}
"""
_FAKE_ADAPTERS = {
    "api.py": 'class A:\n    platform: str = "api_plat"\n    TP_AMEND_SUPPORTED = True\n',
    "browser.py": 'class B:\n    platform: str = "browser_plat"\n    TP_AMEND_SUPPORTED = False\n',
}
_GOOD_UNIT = ('def _rev(cfg, df, pkg):\n    return plan_tp_revision(leg=pkg)\n'
              'def monitor(cfg, df, pkg):\n    v = {"sl": 2.0}\n'
              '    return merge_verdict(v, _rev(cfg, df, pkg))\n')
_SENTINEL_UNIT = '_DEFAULTS = {\n    "tp_r": 50.0,\n}\ndef monitor(cfg, df, pkg):\n    return {"sl": 1.0}\n'


def _plant(tmp: Path, accounts: str, strategies: str) -> Paths:
    (tmp / "config").mkdir(exist_ok=True)
    (tmp / "units").mkdir(exist_ok=True)
    (tmp / "config" / "accounts.yaml").write_text(accounts)
    (tmp / "config" / "strategies.yaml").write_text(strategies)
    (tmp / "builders.py").write_text(_FAKE_BUILDERS)
    (tmp / "clients.py").write_text(_FAKE_CLIENTS)
    (tmp / "prop_trail.py").write_text(_FAKE_PROP_TRAIL)
    (tmp / "units" / "good_unit.py").write_text(_GOOD_UNIT)
    (tmp / "units" / "sentinel_unit.py").write_text(_SENTINEL_UNIT)
    (tmp / "monitor_verdict.py").write_text(_FAKE_MONITOR_VERDICT)
    (tmp / "order_monitor.py").write_text(_FAKE_ORDER_MONITOR)
    (tmp / "tp_revision.py").write_text(_FAKE_TP_REVISION)
    (tmp / "config" / "prop_platforms.yaml").write_text(_FAKE_PLATFORMS)
    (tmp / "platform").mkdir(exist_ok=True)
    for name, body in _FAKE_ADAPTERS.items():
        (tmp / "platform" / name).write_text(body)
    (tmp / "phone_executor.py").write_text("def tick():\n    pass\n")
    return Paths(accounts=tmp / "config" / "accounts.yaml",
                 strategies=tmp / "config" / "strategies.yaml",
                 units_dir=tmp / "units", builders=tmp / "builders.py",
                 clients=tmp / "clients.py", prop_trail=tmp / "prop_trail.py",
                 monitor_verdict=tmp / "monitor_verdict.py",
                 order_monitor=tmp / "order_monitor.py", tp_revision=tmp / "tp_revision.py",
                 prop_platforms=tmp / "config" / "prop_platforms.yaml",
                 platform_dir=tmp / "platform", phone_executor=tmp / "phone_executor.py",
                 repo=tmp)


_ACCOUNTS = """
accounts:
  real:    {exchange: bybit, account_class: real_money, mode: live, strategies: [good_leg, bad_leg]}
  mirror:  {exchange: bybit, account_class: paper, paper_role: portfolio, mode: live, strategies: [good_leg]}
  prop:    {exchange: breakout, account_class: prop, mode: dry_run, strategies: [good_leg]}
  prop_browser: {exchange: breakout, account_class: prop, mode: live, strategies: [good_leg]}
  phone:   {exchange: breakout, account_class: prop, mode: live, strategies: [good_leg]}
  fx:      {exchange: oanda, account_class: paper, mode: dry_run, strategies: [mover_leg]}
  undecl:  {exchange: bybit, account_class: paper, mode: live, strategies: [undeclared_leg, unknown_rule_leg]}
"""
_STRATEGIES = """
strategies:
  good_leg:  {tp_r: 3.0, atr_stop_mult: 2.0, tp_revision: {rule: measured_move}}
  undeclared_leg: {tp_r: 3.0}
  unknown_rule_leg: {tp_r: 3.0, tp_revision: nobody_registers_this}
  bad_leg:   {tp_r: 50.0, tp_intent: {mode: none, reason: trail_is_the_profit_exit}}
  mover_leg: {tp_r: 2.0}
"""


def _self_test() -> int:
    ok = total = 0

    def chk(label: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  FAIL {label}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        p = _plant(tmp, _ACCOUNTS, _STRATEGIES)
        rows = {r.key: r for r in scan(p)}
        chk("population is every routing", len(rows) == 9)
        g = rows["real/good_leg"]
        chk("declared + producer + plumbed -> compliant", not g.fails)
        chk("mirror class resolved from paper_role", rows["mirror/good_leg"].account_class == "mirror")
        b = rows["real/bad_leg"]
        chk("sentinel fails entry", DIM_ENTRY in b.fails and b.entry_state == ENTRY_SENTINEL)
        chk("sentinel unit without tp verdict fails revision", DIM_REVISION in b.fails
            and b.producer_state == PRODUCER_NO)
        chk("tp_intent none is reported, not exempting", b.tp_intent_mode == "none")
        pr = rows["prop/good_leg"]
        chk("prop path reads absent from modify_bracket(None)", pr.path_state == PATH_ABSENT
            and DIM_REVISION in pr.fails and DIM_ENTRY not in pr.fails)
        chk("browser adapter without TP_AMEND_SUPPORTED -> absent",
            rows["prop_browser/good_leg"].path_state == PATH_ABSENT)
        chk("phone without plan_tp_revision -> absent", rows["phone/good_leg"].path_state == PATH_ABSENT)
        chk("wired unit, leg declares no rule -> rule_undeclared (no cosmetic pass)",
            rows["undecl/undeclared_leg"].producer_state == PRODUCER_UNDECLARED
            and DIM_REVISION in rows["undecl/undeclared_leg"].fails)
        chk("declared rule nobody registers -> rule_unknown",
            rows["undecl/unknown_rule_leg"].producer_state == PRODUCER_UNKNOWN_RULE)
        fx = rows["fx/mover_leg"]
        chk("venue without modify cap -> path absent", fx.path_state == PATH_ABSENT)
        # ratchet controls
        f, s = ratchet(list(rows.values()), {})
        chk("NEW non-compliance fires with an empty baseline",
            sum("NEW non-compliance" in x for x in f) == 7)
        chk("summary ranks classes", s["by_class"]["real_money"]["routings"] == 2)
        base = {"real/bad_leg": ER, "prop/good_leg": R, "fx/mover_leg": R,
                "prop_browser/good_leg": R, "phone/good_leg": R,
                "undecl/undeclared_leg": R, "undecl/unknown_rule_leg": R}
        f, s = ratchet(list(rows.values()), base)
        chk("exact baseline -> clean", f == [] and s["baselined_debt_routings"] == 7)
        f, _ = ratchet(list(rows.values()), base, strict=True)
        chk("--strict fails on baselined debt", sum("STRICT" in x for x in f) == 7)
        f, _ = ratchet(list(rows.values()), {**base, "real/bad_leg": R})
        chk("debt growth fires", any("DEBT GROWTH" in x for x in f))
        f, _ = ratchet(list(rows.values()), {**base, "prop/good_leg": ER})
        chk("stale dimension fires", any("BASELINE STALE" in x and "prop/good_leg" in x for x in f))
        f, _ = ratchet(list(rows.values()), {**base, "real/good_leg": R})
        chk("stale compliant routing fires", any("fully compliant" in x for x in f))
        f, _ = ratchet(list(rows.values()), {**base, "gone/leg": R})
        chk("stale unrostered routing fires", any("no longer rostered" in x for x in f))
        # a bare {"tp": ...} literal is NOT a producer (cosmetic-pass control)
        (tmp / "units" / "sentinel_unit.py").write_text(
            _SENTINEL_UNIT.replace('return {"sl": 1.0}', 'return {"sl": 1.0, "tp": 2.0}'))
        rows2 = {r.key: r for r in scan(p)}
        chk("tp literal in monitor does NOT flip producer", rows2["real/bad_leg"].producer_state == PRODUCER_NO)
        # consumption: the trail forwarding plan_tp_revision's TP flips the TP-capable platform only
        (tmp / "prop_trail.py").write_text(_FAKE_PROP_TRAIL_TP)
        (tmp / "phone_executor.py").write_text("rev = plan_tp_revision(leg=leg)\n")
        rows3 = {r.key: r for r in scan(p)}
        chk("prop trail forwarding the revised TP -> API platform plumbed + compliant",
            rows3["prop/good_leg"].path_state == PATH_PLUMBED and not rows3["prop/good_leg"].fails)
        chk("browser platform stays absent even when the trail forwards",
            rows3["prop_browser/good_leg"].path_state == PATH_ABSENT)
        chk("phone executor calling plan_tp_revision -> plumbed",
            rows3["phone/good_leg"].path_state == PATH_PLUMBED)
        # exchange consumption: an order_monitor that stops forwarding tp -> absent
        (tmp / "order_monitor.py").write_text(_FAKE_ORDER_MONITOR.replace('tp=updates.get("tp")', 'tp=None'))
        rows4 = {r.key: r for r in scan(p)}
        chk("order_monitor not forwarding tp -> exchange path absent",
            rows4["real/good_leg"].path_state == PATH_ABSENT and DIM_REVISION in rows4["real/good_leg"].fails)
        (tmp / "order_monitor.py").write_text(_FAKE_ORDER_MONITOR)
        # could-not-look controls
        (tmp / "builders.py").write_text("nothing here\n")
        try:
            scan(p)
            chk("zero alias pairs -> CouldNotLook", False)
        except CouldNotLook:
            chk("zero alias pairs -> CouldNotLook", True)
        (tmp / "builders.py").write_text(_FAKE_BUILDERS)
        (tmp / "clients.py").write_text("x = 1\n")
        try:
            scan(p)
            chk("missing caps -> CouldNotLook", False)
        except CouldNotLook:
            chk("missing caps -> CouldNotLook", True)
        chk("tp producer detector: positive", monitor_produces_tp(_GOOD_UNIT) is True)
        chk("tp producer detector: negative", monitor_produces_tp(_SENTINEL_UNIT) is False)
        chk("tp producer detector: plan without merge is not wired",
            monitor_produces_tp('def monitor(a,b,c):\n    return plan_tp_revision(leg=c)\n') is False)
        chk("tp literal inside monitor is not a producer",
            monitor_produces_tp('def monitor(a,b,c): return {"tp": 1.0}\n') is False)
        chk("tp producer detector: no monitor", monitor_produces_tp("x = 1\n") is None)
        chk("tp key outside monitor is not a producer",
            monitor_produces_tp('def build(): return {"tp": 1}\ndef monitor(a,b,c): return None\n') is False)

    # the live repo: its own aliases must cover every rostered leg, and the
    # baseline must match the scan exactly (a stale baseline is a finding).
    try:
        live = scan()
    except CouldNotLook as exc:
        print(f"  FAIL live scan could not look: {exc}")
        total += 1
        live = []
    if live:
        chk("live repo: every rostered leg resolves to a unit",
            all(r.unit is not None for r in live))
        chk("live repo: the probe finds at least one sentinel (positive control)",
            any(r.entry_state == ENTRY_SENTINEL for r in live))
        f, _ = ratchet(live, BASELINE_2026_10_06)
        for x in f:
            print("  live:", x)
        chk("live repo: baseline matches the scan exactly", f == [])
    print(f"self-test: {ok}/{total} passed")
    return 0 if ok == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tp-doctrine-guard (ratchet)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also FAIL on baselined debt (the audit shape)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    ap.add_argument("--print-baseline", action="store_true",
                    help="print the current debt as BASELINE lines (for a deliberate paste)")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        routings = scan()
    except CouldNotLook as exc:
        print(f"tp-doctrine-guard: COULD NOT LOOK -- {exc}")
        return EXIT_COULD_NOT_LOOK
    if args.print_baseline:
        print(print_baseline(routings))
        return EXIT_CLEAN
    findings, summary = ratchet(routings, BASELINE_2026_10_06, strict=args.strict)
    if args.json:
        print(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "doctrine": "docs/ARCHITECTURE-CANONICAL.md § TP doctrine",
            "strict": args.strict,
            "summary": summary,
            "findings": findings,
            "routings": [r.as_dict() for r in routings],
        }, indent=2, sort_keys=True))
    else:
        print(render_table(routings))
        print()
        bc = summary["by_class"]
        print(f"population: {summary['routings']} routings / {summary['distinct_legs']} "
              f"distinct legs, every account in config/accounts.yaml")
        for cls in sorted(bc, key=lambda c: CLASS_RANK.get(c, 9)):
            c = bc[cls]
            print(f"  {cls:<10} routings={c['routings']:>2}  entry_tp FAIL={c['entry_fail']:>2}  "
                  f"tp_revision FAIL={c['revision_fail']:>2}  compliant={c['compliant']:>2}")
        print(f"baselined debt: {summary['baselined_debt_routings']} routing(s) "
              f"({summary['baseline_entries']} baseline lines; the list may only shrink)")
        for x in findings:
            print("FAIL", x)
    if findings:
        return EXIT_FINDINGS
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())

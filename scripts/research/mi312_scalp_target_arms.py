#!/usr/bin/env python3
"""MI-312 — the target-setting arm for the legs MI-307 could not grade.

WHAT THIS IS FOR. MI-307 (`docs/research/offline-mfe-distribution-2026-09-18.md`)
produced the offline MFE distribution for 19 legs and graded **8** of them
`not_capped_capable` — the 7 crypto `ict_scalp_*` legs plus `fvg_range_15m` —
on the ground that their harnesses implement no `--tp-cap-pct`, so "no capped
arm, and therefore no positive control, is possible". That is the gap this unit
was dispatched to close, and MI-307 named it the one that matters most: MI-278
U2 measured take-profit as the ONLY exit lever with attributed mass on
`ict_scalp`, so the instrument answered the question everywhere except where
the answer would change a decision.

⚠️ THE DISPATCHED REMEDY WAS THE WRONG ONE, AND ADDING IT ALONE WOULD HAVE
MANUFACTURED A PASS. Measured at `25bad2e31`:

  * These 8 legs' live units apply **no venue clamp**. `TP_VENUE_CAP_PCT` is
    applied in exactly four unit modules (`trend_donchian`,
    `htf_pullback_trend_2h`, `fade_breakout_4h`, `squeeze_breakout_4h`) and
    nowhere downstream; `src/runtime/tp_venue_cap.py::CLAMPING_FAMILIES` says
    so itself. `ict_scalp.py:525` is `tp = entry ± tp_at_r * risk`, unclamped.
  * So the **default** harness arm — which exits at the live unit's own `tp` —
    is ALREADY the live-comparable book. `m20_fleet_exit_sweep.tp_geometry_for`
    calls exactly this condition `live_parity_uncapped` and documents it as
    *"no cap applied AND the live unit does not clamp, so this IS parity"*, and
    `base_args` already refuses to pass `--tp-cap-pct` outside
    `LIVE_TP_CAPPED_FAMILIES`.
  * What was genuinely missing is **the opposite arm**: the harnesses had no
    way to switch the target OFF, so the *target-setting* basis did not exist.
    With the target live, MFE is truncated at it and can say nothing about what
    lies above — which is why no positive control could be run. Same conclusion
    as the dispatch, opposite cause, different flag.

⚠️ AND THE COUNTERFACTUAL CAP IS A MEASURED NO-OP ON THIS FAMILY, so the
dispatched arm would have been indistinguishable from the default. Over the
BTCUSDT 5m validation slice (2025-01-01..2025-04-01, n=45) a `--tp-cap-pct
0.099` run was byte-identical to the default — `unit_tp_cap_inert` on 45 of 45,
identical outcomes, identical MFE — because a 1.5R target on a 5m bar sits at a
p50 of **0.95% of entry** against a **9.9%** clamp. A positive control run
against that arm would have compared a book to itself and passed trivially.
That is the contaminated instrument `CY-20260906-TRADING-TRUTH` says is worse
than no instrument, because it gets acted on.

THE TWO ARMS
------------
  ``live``   the harness default — the live unit's own target. Live parity.
  ``no_tp``  ``--no-tp``: the target disabled, so a trade runs to its stop or
             timeout and MFE is uncensored by the target. The target-setting
             basis, i.e. MI-307's "uncapped" arm.

THE POSITIVE CONTROL, AND WHY IT IS WEAKER HERE THAN MI-307's
-------------------------------------------------------------
Same form as MI-307 § 2.1: the reach computed on the target-setting arm must
predict the OBSERVED take-profit exit share of the live arm. The reach
expression is MI-307's, unchanged —
``P(mfe_r >= min(cap_r, x))`` — with two substitutions that are forced by this
family rather than chosen:

  * ``x`` is **per trade**, not a fleet-wide grid value. `ict_scalp` declares
    `tp_at_r` (1.5 on every leg) and `fvg_range` targets a range boundary whose
    distance in R differs every trade, so the would-be target is read from each
    trade's own emitted `unit_tp` as ``|unit_tp - entry| / risk``.
  * ``cap_r`` is effectively **infinite**, because the family does not clamp —
    so ``min(cap_r, x)`` reduces to ``x``. The formula is not special-cased;
    it is evaluated and the clamp term simply never binds, which is itself
    reported (`clamp_would_bind_rate`) rather than assumed.

⚠️ **STATED RATHER THAN GLOSSED: on this family the two arms may share their
ENTRY population, which MI-307's did not.** Its arms differed in trade count
(e.g. `sol_pullback_2h`: 200 uncapped vs 277 capped), and that independence is
what made its control non-circular. Here `next_eligible_idx = exit_index + 1 +
cooldown_bars`, so a different exit *can* shift later entries — but on the
validation slice both arms produced the identical 45-entry sequence. The
control is therefore **weaker** than MI-307's: it still tests a real
prediction (an uncensored excursion distribution predicting a different exit
rule's hit rate) but the two books are not independent draws. `entry_overlap`
is reported per leg so a reader meets that limit instead of inheriting a claim
of parity with MI-307's design.

Tier-1 research tooling. Writes no config, proposes no `tp_at_r`, and runs no
parameter sweep — the e35 corpus already swept ~180 cells per leg.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import exit_capture  # noqa: E402  (the ONE MFE reader)
import target_basis  # noqa: E402  (the ONE take-profit-basis definition)
from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS,
    LIVE_TP_CAPPED_FAMILIES,
    base_args,
    classify,
    harness_implements_flag,
    resolve_data,
    tp_geometry_for,
)
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402  (the ONE clamp)

OUT_JSON = REPO / "docs" / "research" / "mi312-scalp-target-arms-2026-09-18.json"
EMIT_DIR = REPO / "runtime_logs" / "mi312"

#: Reported alongside the per-trade control so the two are comparable to
#: MI-307's table. Same grid, deliberately.
REACH_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]

#: Per-run subprocess cap. A full-history 5m scalp run was measured at 955s by
#: the 2026-08-10 census and ~27 min here on 600k bars, so 1800s is tight and
#: 5400s is the honest bound. A timeout is reported as a STATE, never retried
#: silently into a shorter window that would change the population.
RUN_TIMEOUT_S = 5400


def load_legs() -> dict[str, dict]:
    doc = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    return doc.get("strategies", doc)


def target_legs(legs: dict[str, dict]) -> list[str]:
    """The legs this unit is for — DERIVED, never a hardcoded list of eight.

    A leg qualifies when its family does NOT clamp live (so the harness default
    is already parity and the missing arm is the target-setting one) AND its
    harness can now switch the target off. Reading both from source is what
    keeps membership from going stale the day a harness or a family changes —
    the same reason `harness_implements_flag` reads the harness source rather
    than consulting a table.
    """
    out = []
    for leg, cfg in sorted(legs.items()):
        if not isinstance(cfg, dict):
            continue
        fam = classify(leg)
        harness = FAMILY_HARNESS.get(fam or "")
        if not harness or fam in LIVE_TP_CAPPED_FAMILIES:
            continue
        if harness_implements_flag(harness, "--no-tp") is True:
            out.append(leg)
    return out


def run_leg(leg: str, cfg: dict, *, no_tp: bool, reuse: bool = False) -> dict[str, Any]:
    """Run one leg's harness with its OWN declared params, one arm.

    States are never collapsed: `ok` / `no_harness` / `no_data` (the feed limit,
    NOT a statement about the leg) / `no_tp_unsupported` (the harness cannot
    switch the target off — *this arm is impossible*, distinct from it running
    and finding nothing) / `harness_failed` (*we could not look*).
    """
    fam = classify(leg)
    harness = FAMILY_HARNESS.get(fam or "")
    sym = (cfg.get("symbols") or [None])[0]
    tf = str(cfg.get("timeframe") or "1h")
    arm = "no_tp" if no_tp else "live"
    base = {"leg": leg, "family": fam, "symbol": sym, "timeframe": tf, "arm": arm,
            "execution": cfg.get("execution"), "enabled": cfg.get("enabled")}
    if not harness:
        return {**base, "state": "no_harness", "why": f"no harness for family {fam!r}"}
    data, proxy, resample = resolve_data(str(sym), tf, REPO / "data")
    if not data:
        return {**base, "state": "no_data",
                "why": f"no candle file for {sym}/{tf} under data/"}
    if no_tp and harness_implements_flag(harness, "--no-tp") is not True:
        return {**base, "state": "no_tp_unsupported",
                "why": f"{os.path.basename(harness)} cannot disable its target"}

    args = base_args(leg, cfg, fam or "", data, resample)
    # ⚠️ fvg parity is `--exit-style far`, NOT this harness's own `mid` default:
    # the live unit sets `tp = R` (src/units/strategies/fvg_range_15m.py:376).
    # `base_args` passes `--exit-style` only when the YAML declares one, and no
    # fvg leg does, so without this the "live" arm would run the WRONG target
    # and the control would grade a book production does not run.
    if fam == "fvg" and "--exit-style" not in args:
        args += ["--exit-style", "far"]
    if no_tp:
        args += ["--no-tp"]

    EMIT_DIR.mkdir(parents=True, exist_ok=True)
    emit = EMIT_DIR / f"{leg}__{arm}.jsonl"
    cmd = [sys.executable, str(REPO / harness), *args, "--emit-trades", str(emit)]
    if harness == FAMILY_HARNESS.get("scalp"):
        cmd += ["--strategy-name", leg]
    # `--reuse` exists ONLY so the sweep can be fanned across cores: a full 5m
    # leg is ~27 min and there are 16 runs. It reuses an emit that is already
    # on disk, which is safe here because the emit path is keyed on
    # (leg, arm) and the args are rebuilt identically from config each time —
    # but it CANNOT know the file was produced by the current harness source,
    # so a run after a harness edit must clear runtime_logs/mi312/ first. The
    # reuse is recorded in the record rather than being invisible.
    if reuse:
        existing = read_emit(emit)
        if existing:
            return {**base, "state": "ok", "data_file": os.path.basename(str(data)),
                    "proxy": bool(proxy), "resample": resample,
                    "emit_path": str(emit.relative_to(REPO)), "n_rows": len(existing),
                    "cmd": " ".join(cmd[1:]), "reused_emit": True,
                    "tp_geometry": tp_geometry_for([fam], 0.0)}
    try:
        # check=False deliberately: a non-zero rc is a STATE this function
        # reports, not an exception that aborts the sweep over the other legs.
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=RUN_TIMEOUT_S, cwd=str(REPO), check=False)
    except subprocess.TimeoutExpired:
        return {**base, "state": "harness_failed", "why": f"timeout>{RUN_TIMEOUT_S}s"}
    if p.returncode != 0:
        return {**base, "state": "harness_failed",
                "why": (p.stderr or "").strip()[-400:] or f"rc={p.returncode}"}
    rows = read_emit(emit)
    if rows is None:
        return {**base, "state": "harness_failed", "why": "emit unreadable"}
    return {**base, "state": "ok", "data_file": os.path.basename(str(data)),
            "proxy": bool(proxy), "resample": resample,
            "emit_path": str(emit.relative_to(REPO)), "n_rows": len(rows),
            "cmd": " ".join(cmd[1:]),
            "tp_geometry": tp_geometry_for([fam], 0.0)}


def read_emit(path: Path) -> Optional[list[dict]]:
    try:
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return None


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def target_r_of(row: dict) -> Optional[float]:
    """The would-be target in R for one trade: ``|unit_tp - entry| / risk``.

    Read from the row's OWN emitted `unit_tp`, which both arms carry, so the
    target-setting arm can be asked what target it would have had. `None` when
    any term is missing — never 0.0, which would read as "the target is at
    entry" rather than "we could not compute it".
    """
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    unit_tp = _f(row.get("unit_tp", meta.get("unit_tp")))
    entry = _f(row.get("entry"))
    risk = _f(row.get("risk"))
    if unit_tp is None or entry is None or risk is None or risk <= 0:
        return None
    return abs(unit_tp - entry) / risk


def basis_of(row: dict) -> Optional[str]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return row.get("tp_basis", meta.get("tp_basis"))


def is_tp_exit(row: dict) -> bool:
    """Did this trade end AT its target? The two harnesses spell it differently
    (`tp_hit` / `target`), so the mapping lives here once."""
    return str(row.get("outcome") or row.get("exit_reason") or "") in {"tp_hit", "target"}


def summarise(leg: str, live_rows: list[dict], notp_rows: list[dict],
              cfg: dict) -> dict[str, Any]:
    """The distribution, the counterfactual clamp, and the positive control."""
    mfes = [m for m in (exit_capture.mfe_r_of(r) for r in notp_rows) if m is not None]
    mfes.sort()
    n = len(mfes)

    def pct(p: float) -> Optional[float]:
        # Nearest-rank on the SORTED all-trades list. Deliberately the same
        # convention `m31_mfe_parity._pct` uses; a third convention for a
        # natively countable quantity is the drift both modules warn about.
        if not n:
            return None
        return round(mfes[min(n - 1, max(0, int(round(p * (n - 1)))))], 4)

    # --- the positive control -------------------------------------------
    # PREDICTED: over the target-setting arm, the share of trades whose
    # uncensored excursion reached that trade's own would-be target. MI-307's
    # expression, with cap_r and x both per trade.
    pairs = []
    for r in notp_rows:
        m = exit_capture.mfe_r_of(r)
        x = target_r_of(r)
        c = target_basis.cap_r(entry=_f(r.get("entry")) or 0.0,
                               risk=_f(r.get("risk")) or 0.0,
                               cap_pct=TP_VENUE_CAP_PCT)
        if m is None or x is None:
            continue
        pairs.append((m, x, c))
    predicted = (sum(1 for m, x, c in pairs
                     if m >= (min(c, x) if c is not None else x)) / len(pairs)
                 if pairs else None)
    # OBSERVED: the live arm's actual take-profit exit share.
    observed = ((sum(1 for r in live_rows if is_tp_exit(r)) / len(live_rows))
                if live_rows else None)

    # --- the counterfactual clamp, analytically -------------------------
    # Validated against a real `--tp-cap-pct 0.099` run on the BTCUSDT 5m
    # validation slice: basis agreed on 45 of 45 (see the memo). Derived rather
    # than run so the sweep costs two arms per leg, not three.
    binds = [1 for m, x, c in pairs if c is not None and c < x]
    dist_pct = []
    for r in live_rows:
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        unit_tp = _f(r.get("unit_tp", meta.get("unit_tp")))
        entry = _f(r.get("entry"))
        if unit_tp is not None and entry:
            dist_pct.append(abs(unit_tp - entry) / entry)
    dist_pct.sort()

    # --- arm independence ------------------------------------------------
    le = [r.get("entry_time") for r in live_rows]
    ne = [r.get("entry_time") for r in notp_rows]
    shared = len(set(le) & set(ne))
    union = len(set(le) | set(ne))

    tgt_rs = sorted(x for _, x, _ in pairs)
    return {
        "leg": leg,
        "declared_tp_at_r": cfg.get("tp_at_r"),
        "declared_tp_r": cfg.get("tp_r"),
        "n_live": len(live_rows),
        "n_no_tp": len(notp_rows),
        "n_mfe": n,
        # THE DISTRIBUTION, from the target-setting arm — all trades, not
        # winners-only, for MI-307 § 3.1's reason: a take-profit is hit by any
        # trade whose excursion reaches it, including the ones that gave it all
        # back, and those are exactly the trades a target would have changed.
        "mfe_r_p50": pct(0.50), "mfe_r_p80": pct(0.80),
        "mfe_r_p90": pct(0.90), "mfe_r_max": round(max(mfes), 4) if n else None,
        "reach_rate": ({f"{x:g}": round(sum(1 for m in mfes if m >= x) / n, 4)
                        for x in REACH_GRID} if n else None),
        "target_r_p50": round(tgt_rs[len(tgt_rs) // 2], 4) if tgt_rs else None,
        # THE CONTROL.
        "control_predicted": round(predicted, 4) if predicted is not None else None,
        "control_observed": round(observed, 4) if observed is not None else None,
        "control_delta_pp": (round((observed - predicted) * 100, 2)
                             if (predicted is not None and observed is not None)
                             else None),
        "control_n_pairs": len(pairs),
        # THE COUNTERFACTUAL — would a venue clamp have bound here at all?
        "clamp_would_bind_rate": (round(len(binds) / len(pairs), 4)
                                  if pairs else None),
        "target_dist_pct_of_entry_p50": (round(dist_pct[len(dist_pct) // 2], 6)
                                         if dist_pct else None),
        "target_dist_pct_of_entry_max": round(max(dist_pct), 6) if dist_pct else None,
        "tp_venue_cap_pct": TP_VENUE_CAP_PCT,
        # ARM INDEPENDENCE — stated so the control's weakness travels with it.
        "entry_overlap": round(shared / union, 4) if union else None,
        "basis_live": _counts(basis_of(r) for r in live_rows),
        "basis_no_tp": _counts(basis_of(r) for r in notp_rows),
    }


def _counts(it) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in it:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def run_all(only: Optional[list[str]] = None, reuse: bool = False) -> int:
    legs = load_legs()
    targets = [x for x in target_legs(legs) if not only or x in only]
    print(f"MI-312 — {len(targets)} leg(s) DERIVED as non-clamping + target-disable "
          f"capable: {', '.join(targets)}")
    runs, summaries = [], []
    for leg in targets:
        cfg = legs[leg]
        arms = {}
        for no_tp in (False, True):
            rec = run_leg(leg, cfg, no_tp=no_tp, reuse=reuse)
            runs.append(rec)
            print(f"  {leg:22} {rec['arm']:6} {rec['state']:18} n={rec.get('n_rows')}")
            if rec["state"] == "ok":
                arms[rec["arm"]] = read_emit(REPO / rec["emit_path"]) or []
        if "live" in arms and "no_tp" in arms:
            s = summarise(leg, arms["live"], arms["no_tp"], cfg)
            summaries.append(s)
            print(f"      control: predicted {s['control_predicted']} vs observed "
                  f"{s['control_observed']}  (delta {s['control_delta_pp']} pp)")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "unit": "MI-312",
        "tp_venue_cap_pct": TP_VENUE_CAP_PCT,
        "clamping_families": sorted(LIVE_TP_CAPPED_FAMILIES),
        "reach_grid": REACH_GRID,
        "runs": runs,
        "legs": summaries,
        "control_summary": control_summary(summaries),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nwrote {OUT_JSON.relative_to(REPO)}")
    cs = payload["control_summary"]
    print(f"CONTROL over {cs.get('n_legs')} leg(s): mean {cs.get('mean_delta_pp')} pp · "
          f"median {cs.get('median_delta_pp')} pp · max abs {cs.get('max_abs_delta_pp')} pp")
    return 0


def control_summary(summaries: list[dict]) -> dict[str, Any]:
    d = [s["control_delta_pp"] for s in summaries if s.get("control_delta_pp") is not None]
    if not d:
        # Never 0.0 — a missing control is *we could not run it*, which must not
        # read as a perfect agreement.
        return {"n_legs": 0, "mean_delta_pp": None, "median_delta_pp": None,
                "max_abs_delta_pp": None}
    return {"n_legs": len(d),
            "mean_delta_pp": round(statistics.mean(d), 2),
            "median_delta_pp": round(statistics.median(d), 2),
            "max_abs_delta_pp": round(max(abs(x) for x in d), 2)}


def selftest() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    tb = target_basis
    check("1 default arm returns the unit target untouched",
          tb.resolve_target(entry=100, direction="long", unit_tp=101.5)
          == (101.5, tb.UNIT_TP))
    check("2 --no-tp yields no target, and is distinct from no_unit_tp",
          tb.resolve_target(entry=100, direction="long", unit_tp=101.5,
                            disabled=True) == (None, tb.NO_TARGET)
          and tb.resolve_target(entry=100, direction="long", unit_tp=None)
          == (None, tb.NO_UNIT_TP))
    check("3 a cap that does NOT move the target reads inert, not clamped",
          tb.resolve_target(entry=100, direction="long", unit_tp=101.5,
                            cap_pct=0.099)[1] == tb.UNIT_TP_CAP_INERT)
    check("4 a cap that DOES move the target reads clamped, both directions",
          tb.resolve_target(entry=100, direction="long", unit_tp=120,
                            cap_pct=0.099)[1] == tb.UNIT_TP_CLAMPED
          and tb.resolve_target(entry=100, direction="short", unit_tp=80,
                                cap_pct=0.099)[1] == tb.UNIT_TP_CLAMPED)
    check("5 disabled beats a cap — the arm wins over the clamp",
          tb.resolve_target(entry=100, direction="long", unit_tp=120,
                            cap_pct=0.099, disabled=True) == (None, tb.NO_TARGET))
    check("6 cap_r is None (not 0.0) on non-positive risk",
          tb.cap_r(entry=100, risk=0, cap_pct=0.099) is None
          and tb.cap_r(entry=100, risk=2, cap_pct=0.099) == 4.95)

    # NEGATIVE CONTROL: if resolve_target stopped honouring `disabled`, the
    # target-setting arm would silently become the live arm and the control
    # would compare a book to itself. This asserts the two are DIFFERENT.
    check("7 NEGATIVE CONTROL — the two arms differ (disabled != default)",
          tb.resolve_target(entry=100, direction="long", unit_tp=101.5)[0]
          != tb.resolve_target(entry=100, direction="long", unit_tp=101.5,
                               disabled=True)[0])

    check("8 target_r_of reads the per-trade would-be target from unit_tp",
          abs(target_r_of({"entry": 100.0, "risk": 2.0,
                           "meta": {"unit_tp": 103.0}}) - 1.5) < 1e-9)
    check("9 target_r_of is None (not 0.0) when a term is missing",
          target_r_of({"entry": 100.0, "risk": 2.0, "meta": {}}) is None
          and target_r_of({"entry": 100.0, "risk": 0.0,
                           "meta": {"unit_tp": 103.0}}) is None)
    check("10 both harness spellings of a target exit are recognised",
          is_tp_exit({"outcome": "tp_hit"}) and is_tp_exit({"outcome": "target"})
          and not is_tp_exit({"outcome": "sl_hit"})
          and not is_tp_exit({"outcome": "timeout"}))

    # NEGATIVE CONTROL: reading only `tp_hit` would score every fvg target exit
    # as a non-target exit and drive that leg's observed share to zero, which
    # would read as a control FAILURE caused by the reader, not the instrument.
    check("11 NEGATIVE CONTROL — dropping the 'target' spelling loses fvg exits",
          is_tp_exit({"outcome": "target"}) is not False)

    check("12 the 8 legs are DERIVED from source, never hardcoded",
          set(target_legs(load_legs())) and all(
              classify(x) not in LIVE_TP_CAPPED_FAMILIES
              for x in target_legs(load_legs())))
    check("13 the clamping families exclude scalp and fvg (the unit's premise)",
          "scalp" not in LIVE_TP_CAPPED_FAMILIES
          and "fvg" not in LIVE_TP_CAPPED_FAMILIES
          and "donchian" in LIVE_TP_CAPPED_FAMILIES)
    check("14 an uncapped run on a non-clamping family IS live parity",
          tp_geometry_for(["scalp"], 0.0) == "live_parity_uncapped"
          and tp_geometry_for(["donchian"], 0.0) == "NO_TAKE_PROFIT")

    # NEGATIVE CONTROL: a missing control must never summarise as agreement.
    check("15 NEGATIVE CONTROL — no gradeable leg summarises as None, not 0.0",
          control_summary([])["mean_delta_pp"] is None
          and control_summary([])["n_legs"] == 0)
    check("16 control_summary reports the worst leg, not just the mean",
          control_summary([{"control_delta_pp": 1.0},
                           {"control_delta_pp": -9.0}])["max_abs_delta_pp"] == 9.0)

    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--only", default=None,
                   help="comma-separated leg ids to restrict the run to")
    p.add_argument("--reuse", action="store_true",
                   help="reuse an emit already on disk instead of re-running "
                        "(for fanning the sweep across cores). Clear "
                        "runtime_logs/mi312/ after any harness edit.")
    a = p.parse_args(argv[1:])
    if a.selftest:
        return selftest()
    if a.run:
        return run_all(only=[x.strip() for x in a.only.split(",")] if a.only else None,
                       reuse=bool(a.reuse))
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

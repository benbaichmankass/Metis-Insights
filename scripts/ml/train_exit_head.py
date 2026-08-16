#!/usr/bin/env python3
"""M20 E1 — exit-head training + offline policy evaluation.

Consumes one E0 family dataset (``build_exit_head_dataset.py`` rows.jsonl)
and runs the full E1 protocol from docs/research/M20-exit-head-PROGRAM.md:

* **Model** — LightGBM classifier on ``holding_pays`` (per-family; the
  pooled-model comparison is a follow-up once >1 family gates in).
* **Splits** — purged walk-forward by TIME: per-year test folds over the
  harness rows; each fold trains on strictly-earlier harness trades with a
  7-day embargo before the fold start (an overlapping hold can't leak).
* **Model metric** — per-fold OOS AUC + a 10-bin reliability curve.
* **Decision metric** — the τ-policy replay: exit at the FIRST bar where
  P(holding pays) < τ; exit value = that bar's observed close mark
  (``open_r``) — pure truncation, identical honesty to the M20
  counterfactuals (no barrier re-simulation). Compared per fold vs
  (a) actual exits and (b) the best hard levers replayed on the SAME rows
  (stale-stop 8 bars/<0R; giveback 1.0R @ MFE>=1R).
* **Capital efficiency** — net_R per position-day for every arm.
* **Live validation** — a model trained on ALL harness rows is applied to
  the live-source trades (never trained on): AUC + τ-policy replay. The
  E1→E2 gate requires the live set to agree in SIGN with the walk-forward.

Output: ``<family_dir>/e1_report.json`` + a printed summary. Advisory
only — this script never touches config or the registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# The min-OOS-trades floor is SINGLE-HOMED in the fleet sweep (operator-set
# 2026-08-11, value 25). Imported rather than mirrored so one matrix is never
# governed by two floors. An import failure yields None, which `per_leg_summary`
# treats as "cannot grade" — never as a licence to substitute a local default.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "research"))
sys.path.insert(0, str(_REPO / "scripts" / "ml"))
from _heavy_queue import take_heavy_queue  # noqa: E402
try:  # pragma: no cover - exercised by the import-failure path in tests
    from m20_fleet_exit_sweep import MIN_OOS_TRADES  # noqa: E402
except Exception:  # noqa: BLE001 - any import failure is the same third state
    MIN_OOS_TRADES = None

FEATURES = [
    "age_bars", "open_r", "mfe_r", "mae_r", "giveback_r",
    "chop_frac_so_far", "stagnation_run", "dist_to_stop_r",
    "vol_ratio_vs_entry", "atr_ratio_vs_entry", "donchian_mid_dist_atr",
    "hour_of_day", "dayofweek", "is_long",
]
# M20 P4.3 exhaustion feature block (momentum-exhaustion design § P4.3) —
# opt-in via --features extended so re-runs on pre-P4 datasets are unchanged.
FEATURES_EXH = [
    "bars_since_peak", "mom_8", "mom_decay", "atr_impulse_phase",
    "vol_at_peak_ratio", "band_ext_pctile", "failure_swing",
]
# M20 P4.2: classification target — holding_pays (the original head) or
# peak_is_in (predict the favourable extreme is already behind us; acts on
# HIGH probability, so its policy arms use TAUS_HI). Set from --target.
TARGET = "holding_pays"
EMBARGO_S = 7 * 86400

# Minimum TRAINING rows for a fold to be usable. Named rather than inlined as a
# bare `500` so the skip message can quote the bound it actually applied — an
# unquoted threshold is why a whole round's worth of skips read as unexplained.
# Unlike `--min-fold-trades` this is not a CLI knob today; it is a floor on
# fitting a model at all, not a research choice about OOS width.
_MIN_FOLD_TRAIN_ROWS = 500
TAUS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
TAUS_HI = [0.60, 0.70, 0.80]
TF_S = {"5m": 300, "15m": 900, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


def load_rows(path: Path) -> List[dict]:
    rows = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        r["is_long"] = 1 if r.get("direction") == "long" else 0
        rows.append(r)
    return rows


def group_trades(rows: List[dict]) -> Dict[str, List[dict]]:
    """trade_key -> bars sorted by age."""
    out: Dict[str, List[dict]] = {}
    for r in rows:
        out.setdefault(str(r["trade_key"]), []).append(r)
    for bars in out.values():
        bars.sort(key=lambda r: r["age_bars"])
    return out


def matrix(rows: List[dict]):
    X = np.array([[float(r.get(f) if r.get(f) is not None else np.nan)
                   for f in FEATURES] for r in rows], dtype=float)
    y = np.array([int(r[TARGET]) for r in rows], dtype=int)
    return X, y


def auc_score(y, p) -> Optional[float]:
    if len(set(y.tolist())) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def reliability(y, p, bins: int = 10) -> List[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        m = (p >= lo) & (p < hi if b < bins - 1 else p <= hi)
        if m.sum() == 0:
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
                    "mean_p": round(float(p[m].mean()), 4),
                    "frac_pos": round(float(y[m].mean()), 4)})
    return out


def train_model(rows: List[dict]):
    import lightgbm as lgb
    X, y = matrix(rows)
    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31,
        min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
        reg_lambda=1.0, random_state=7, verbose=-1)
    clf.fit(X, y)
    return clf


# ------------------------------------------------------------- policy replay
def replay_trade(bars: List[dict], exit_idx: Optional[int]) -> dict:
    """Exit at bar exit_idx (mark-to-close truncation) or ride to actual."""
    if exit_idx is None or exit_idx >= len(bars) - 1:
        r = float(bars[0]["final_r"])
        held = len(bars)
    else:
        r = float(bars[exit_idx]["open_r"])
        held = exit_idx + 1
    return {"r": r, "bars": held}


def policy_model(bars: List[dict], probs: np.ndarray, tau: float) -> dict:
    idx = None
    for i in range(len(bars)):
        if probs[i] < tau:
            idx = i
            break
    return replay_trade(bars, idx)


# E1.5 conditional shapes (memo § 8 queued item 1): arm the head ONLY in the
# states where the chop-hold loss lives, so a running trend is never
# truncated by a low score alone. Motivated by live trade 3344 (BTC donchian
# held 2d+ around flat, P(pays) ~0.12-0.24 the whole tail, but the trade sat
# marginally ABOVE the stale-stop's <0R reference cell).
_SHAPES = {
    # only cut while the trade has not proven itself (< +0.5R at the bar close)
    "below_half_r": lambda b, i: float(b[i]["open_r"]) < 0.5,
    # only cut before the trade ever reached +1R MFE (past that, the
    # chandelier trail / giveback owns the exit)
    "pre_mfe1": lambda b, i: float(b[i]["mfe_r"]) < 1.0,
    # only cut mature trades (>= 8 bars — the stale-stop's age gate)
    "age8": lambda b, i: b[i]["age_bars"] >= 8,
    # combined: mature AND unproven
    "age8_below_half_r": lambda b, i: (b[i]["age_bars"] >= 8
                                       and float(b[i]["open_r"]) < 0.5),
}


def policy_model_cond(bars: List[dict], probs: np.ndarray, tau: float,
                      cond) -> dict:
    idx = None
    for i in range(len(bars)):
        if probs[i] < tau and cond(bars, i):
            idx = i
            break
    return replay_trade(bars, idx)


# ---- P4.2 peak-is-in policy arms (act on HIGH probability) ---------------
def policy_peak_full(bars: List[dict], probs: np.ndarray, tau: float) -> dict:
    """Full close at the first bar where P(peak_is_in) > tau."""
    idx = None
    for i in range(len(bars)):
        if probs[i] > tau:
            idx = i
            break
    return replay_trade(bars, idx)


def policy_peak_winner(bars: List[dict], probs: np.ndarray, tau: float) -> dict:
    """Close on the signal only if the trade is a proven winner (>= +0.5R) —
    bank winners near their peak; losers stay with the stop/stale levers."""
    idx = None
    for i in range(len(bars)):
        if probs[i] > tau and float(bars[i]["open_r"]) >= 0.5:
            idx = i
            break
    return replay_trade(bars, idx)


def policy_peak_lock(bars: List[dict], probs: np.ndarray, tau: float,
                     g: float) -> dict:
    """The signal ARMS an R-lock instead of closing: after the first bar with
    P(peak_is_in) > tau, exit at the first bar whose close has given back
    >= g R from the trade's MFE (a truncation-observable stand-in for a
    trail-tighten — uses only the observed mark path, no barrier
    re-simulation)."""
    armed = None
    for i in range(len(bars)):
        if armed is None and probs[i] > tau:
            armed = i
        if armed is not None and float(bars[i]["giveback_r"]) >= g:
            return replay_trade(bars, i)
    return replay_trade(bars, None)


def policy_stale(bars: List[dict], n: int = 8, below_r: float = 0.0) -> dict:
    idx = None
    for i, b in enumerate(bars):
        if b["age_bars"] >= n and float(b["open_r"]) < below_r:
            idx = i
            break
    return replay_trade(bars, idx)


def policy_giveback(bars: List[dict], min_mfe: float = 1.0,
                    gb: float = 1.0) -> dict:
    idx = None
    for i, b in enumerate(bars):
        if float(b["mfe_r"]) >= min_mfe and float(b["giveback_r"]) >= gb:
            idx = i
            break
    return replay_trade(bars, idx)


# ------------------------------------------------------------- per-leg cut
#
# WHY THIS EXISTS. Everything above is per-FAMILY: one E0 dir pools every
# symbol in the family, and `eval_split` aggregates over all of them. That is
# the right unit for TRAINING (it is what breaks the n-wall the program doc
# describes) but it is the wrong unit for a VERDICT, because the coverage
# matrix carries one row per LEG.
#
# Recording a pooled verdict against each of a family's leg rows is exactly
# `BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS` — bundled rows carried
# one status for a whole family, so the status described only the leg that
# passed, and the roll-up over-counted. The matrix rows were exploded per-leg
# to kill that failure; feeding them a pooled number would reintroduce it one
# layer up, where it is harder to see.
#
# So: same model, same folds, same replay — partitioned by the leg each trade
# belongs to, with each leg's own denominator stated.

def leg_of(bars: List[dict]) -> str:
    """The strategy leg a trade belongs to — the coverage-matrix row key."""
    return str(bars[0].get("strategy") or "unknown")


def split_by_leg(trades: Dict[str, List[dict]]) -> Dict[str, Dict[str, List[dict]]]:
    out: Dict[str, Dict[str, List[dict]]] = {}
    for tk, bars in trades.items():
        out.setdefault(leg_of(bars), {})[tk] = bars
    return out


def _best_tau(block: dict) -> Optional[tuple]:
    """(name, stats) of the tau policy with the highest net_R, or None."""
    model = block.get("model") or {}
    if not model:
        return None
    return max(model.items(), key=lambda kv: kv[1].get("net_r") or -1e9)


def per_leg_summary(folds: List[dict], floor: Optional[int]) -> dict:
    """Aggregate the per-fold, per-leg blocks into one verdict per leg.

    ⚠️ THE FLOOR IS REUSED, NOT INVENTED. `floor` is
    `m20_fleet_exit_sweep.MIN_OOS_TRADES` (25, operator-set 2026-08-11) — the
    repo's established denominator requirement for a per-cell verdict. A
    per-leg exit-head verdict is the same object as a per-cell lever verdict:
    a claim about one matrix cell, which is worthless below some n. Picking a
    different number here would mean two floors governing one matrix.

    `floor is None` means the floor could not be imported. That is a THIRD
    state, not a licence to default: verdicts are withheld entirely rather
    than graded against a number this module made up. "We could not apply the
    floor" and "the floor passed" are opposite statements.
    """
    legs: Dict[str, dict] = {}
    for fold in folds:
        for leg, block in (fold.get("per_leg") or {}).items():
            acc = legs.setdefault(leg, {
                "oos_trades": 0, "folds": 0, "aucs": [],
                "beats_actual_folds": 0, "beats_hard_folds": 0,
                "usable_folds": 0, "per_fold": [],
            })
            n = block.get("n_trades") or 0
            acc["oos_trades"] += n
            acc["folds"] += 1
            if block.get("auc") is not None:
                acc["aucs"].append(block["auc"])

            best = _best_tau(block)
            actual = block.get("actual") or {}
            hard = [block.get("stale_8_0") or {}, block.get("giveback_1_1") or {}]
            row = {"year": fold.get("year"), "n_trades": n,
                   "auc": block.get("auc"),
                   "best_tau": best[0] if best else None,
                   "best_tau_net_r": (best[1].get("net_r") if best else None),
                   "actual_net_r": actual.get("net_r")}
            # A fold with no trades cannot vote either way — count usable
            # folds explicitly so a leg absent from most folds cannot look
            # like a leg that lost them.
            if best and n > 0 and actual.get("net_r") is not None:
                acc["usable_folds"] += 1
                b_net, b_dd = best[1].get("net_r"), best[1].get("max_dd_r")
                a_net, a_dd = actual.get("net_r"), actual.get("max_dd_r")
                if (b_net is not None and a_net is not None
                        and b_net > a_net
                        and (b_dd is None or a_dd is None or b_dd <= a_dd)):
                    acc["beats_actual_folds"] += 1
                    row["beats_actual"] = True
                hard_best = max((h.get("net_r") for h in hard
                                 if h.get("net_r") is not None), default=None)
                if (b_net is not None and hard_best is not None
                        and b_net > hard_best):
                    acc["beats_hard_folds"] += 1
                    row["beats_hard"] = True
            acc["per_fold"].append(row)

    for leg, acc in legs.items():
        acc["mean_auc"] = (round(sum(acc["aucs"]) / len(acc["aucs"]), 4)
                           if acc["aucs"] else None)
        acc.pop("aucs")
        acc["min_oos_trades_floor"] = floor
        u = acc["usable_folds"]
        # The mechanical read of the E1->E2 gate, per leg. Advisory: a human
        # (or the coverage matrix) records the verdict; this states the
        # arithmetic behind it so it need not be re-derived by eye.
        candidate = (u >= 2
                     and acc["mean_auc"] is not None and acc["mean_auc"] > 0.55
                     and acc["beats_actual_folds"] * 3 >= u * 2
                     and acc["beats_hard_folds"] * 3 >= u * 2)
        if floor is None:
            acc["verdict"] = "ungraded_no_floor"
            acc["ungraded_why"] = (
                "MIN_OOS_TRADES could not be imported from "
                "m20_fleet_exit_sweep; refusing to grade against a locally "
                "invented floor")
        elif acc["oos_trades"] < floor:
            acc["would_have_been"] = "candidate" if candidate else "honest_negative"
            acc["verdict"] = "insufficient_base"
            acc["insufficient_base_why"] = (
                f"OOS base {acc['oos_trades']} trades < floor {floor}")
        else:
            acc["verdict"] = "candidate" if candidate else "honest_negative"
    return legs


def agg(results: List[dict], tf_s: int) -> dict:
    if not results:
        return {"trades": 0}
    rs = [x["r"] for x in results]
    days = sum(x["bars"] for x in results) * tf_s / 86400.0
    net = float(sum(rs))
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return {"trades": len(rs), "net_r": round(net, 2),
            "max_dd_r": round(dd, 2),
            "mean_hold_bars": round(sum(x["bars"] for x in results) / len(rs), 1),
            "net_r_per_pos_day": round(net / days, 4) if days > 0 else None}


def eval_split(model, trades: Dict[str, List[dict]], tf_s: int) -> dict:
    """AUC + reliability + per-τ / hard-lever / actual replay on a trade set."""
    all_rows = [b for bars in trades.values() for b in bars]
    X, y = matrix(all_rows)
    p = model.predict_proba(X)[:, 1]
    # slice probs back per trade
    probs: Dict[str, np.ndarray] = {}
    i = 0
    for tk, bars in trades.items():
        probs[tk] = p[i:i + len(bars)]
        i += len(bars)
    out = {
        "n_trades": len(trades), "n_rows": len(all_rows),
        "auc": auc_score(y, p),
        "reliability": reliability(y, p),
        "actual": agg([replay_trade(b, None) for b in trades.values()], tf_s),
        "stale_8_0": agg([policy_stale(b) for b in trades.values()], tf_s),
        "giveback_1_1": agg([policy_giveback(b) for b in trades.values()], tf_s),
        "model": {},
    }
    if TARGET == "peak_is_in":
        # P4.2 arms: act on HIGH probability that the peak is behind us
        for tau in TAUS_HI:
            out["model"][f"peak_full_tau_{tau}"] = agg(
                [policy_peak_full(b, probs[tk], tau)
                 for tk, b in trades.items()], tf_s)
            out["model"][f"peak_winner_tau_{tau}"] = agg(
                [policy_peak_winner(b, probs[tk], tau)
                 for tk, b in trades.items()], tf_s)
            for g in (0.5, 1.0):
                out["model"][f"peak_lock{g:g}_tau_{tau}"] = agg(
                    [policy_peak_lock(b, probs[tk], tau, g)
                     for tk, b in trades.items()], tf_s)
        out["model_cond"] = {}
        return out
    for tau in TAUS:
        out["model"][f"tau_{tau}"] = agg(
            [policy_model(b, probs[tk], tau) for tk, b in trades.items()], tf_s)
    # E1.5 conditional shapes on a focused tau grid
    out["model_cond"] = {}
    for shape, cond in _SHAPES.items():
        for tau in (0.10, 0.15, 0.20):
            out["model_cond"][f"{shape}_tau_{tau}"] = agg(
                [policy_model_cond(b, probs[tk], tau, cond)
                 for tk, b in trades.items()], tf_s)
    return out


def fold_blocks(h_trades: dict, mode: str, block_n: int, t_entry,
                offset: int = 0, total_sort: bool = False) -> list:
    """Walk-forward test folds as `(label, year, test_dict, cutoff_ts)`.

    `offset` SHIFTS WHERE THE BLOCKING STARTS, AT FIXED `block_n`. It exists for
    exactly one job: measuring how much a verdict depends on *where the fold
    boundaries happen to fall*, which nothing could measure before
    (BL-20260814-EXIT-HEAD-AUC-MOVES-MORE-THAN-ITS-OWN-GATE-MARGIN-ACROSS-A-ONE-DAY-RE-MEASUREMENT:
    six legs re-measured one day apart moved -0.110 to +0.042 in mean_auc
    against a gate bar of 0.55, and one leg was graded `candidate` on a 0.0025
    margin the same night).

    ⚠️ DO NOT MEASURE THAT BY SWEEPING `--min-fold-trades` INSTEAD. That knob's
    own comment forbids it: P_detect is not monotonic in `b`, and every apparent
    optimum lands on a `u` where the 2/3 bar is cheap to hit by luck. A b-sweep
    also changes fold SIZE, so the spread it produced would partly measure "AUC
    is noisier on smaller folds" — a different quantity, reported under this
    one's name. Holding `b` fixed and moving only the start is what isolates
    boundary sensitivity.

    Bounded to `0 <= offset < block_n`: those are the only distinct partitions.
    An offset of `block_n` is not a new partition, it is the same one with a
    whole block discarded.

    The skipped head is REPORTED, never silently dropped — the same reason the
    trailing partial block is announced below. And in `years` mode a non-zero
    offset RAISES rather than being ignored: a silent no-op would let a
    dispersion run report five offsets measured when all five were identical,
    which is the failure this flag exists to detect, inverted.

    WHY `trades` IS THE DEFAULT. The original cut was one test fold per CALENDAR
    YEAR, which silently makes a strategy's TRADE FREQUENCY the thing that
    decides whether it can be graded at all. A daily-bar leg trades ~20x/year,
    so every year-fold lands 12-42 trades against a 50-trade floor and is
    skipped — while the pool holds 371 trades across 19 years. Measured
    2026-08-13: BOTH 1d family pools returned ZERO usable folds, and the
    conclusion drawn from that was "the 1d fleet cannot be graded", which was
    wrong. The data was there; the slicing discarded it
    (BL-20260813-E1-PER-YEAR-FOLD-UNSATISFIABLE-ON-DAILY-BARS).

    Slicing sequentially by trade count is NOT a weaker bar. What carries the
    statistics in a fold is the number of TRADES in it, not the calendar span
    they happen to cover; `--min-fold-trades` is then honoured by construction
    rather than by rejection. Time ordering, the purge on each trade's LAST bar,
    and the embargo are all unchanged — only the definition of a test block
    moves.

    HOW MANY TRADES A LEG NEEDS, stated because this docstring was SILENT on it
    and the silence cost real time. The loop starts at `block_n` (the first
    block is training) and steps by `block_n`, so

        u = max(0, floor(N / block_n) - 1)     usable folds

    and `per_leg_summary`'s gate requires `u >= 2`. Therefore a leg needs
    **N >= 3 * block_n** to be graded at all — 150 trades at the default 50,
    NOT 100. 100 yields exactly ONE fold, which the gate rejects. A 2026-08-13
    session read "the 100 needed for a single 50-trade block" off a coverage-
    matrix ref, took it as the bar, and built a whole remedy on it; the ref was
    literally true about ONE block and silent about the gate needing two.
    Anything reasoning about whether a leg CAN be graded belongs against
    `3 * block_n`.

    `years` is kept to reproduce any pre-2026-08-13 result exactly.
    """
    if offset and mode != "trades":
        raise ValueError(
            f"--fold-offset={offset} is meaningless with --fold-mode={mode}; "
            "it shifts a sequential trade-block boundary and there is none to "
            "shift in a per-calendar-year cut. Refusing rather than ignoring "
            "it, so a dispersion run cannot report distinct offsets that were "
            "all the same partition.")
    if not 0 <= offset < max(block_n, 1):
        raise ValueError(
            f"--fold-offset={offset} out of range: must be 0 <= offset < "
            f"block_n ({block_n}). Only those give distinct partitions; "
            f"offset >= block_n repeats one while discarding a whole block.")

    if mode == "years":
        years = sorted({r["year"] for tk, b in h_trades.items() for r in b})
        out = []
        for ytest in years[1:]:
            y0 = datetime(ytest, 1, 1, tzinfo=timezone.utc).timestamp()
            test = {tk: b for tk, b in h_trades.items()
                    if datetime.fromtimestamp(t_entry(b), tz=timezone.utc).year == ytest}
            out.append((str(ytest), ytest, test, y0))
        return out

    # TIE-BREAK. `sorted` is STABLE, so trades sharing an entry `bar_t` keep the
    # order they appear in `rows.jsonl` — which is the order the LEGS were passed
    # on the command line (m20_exit_head_round.py:157 ->
    # build_exit_head_dataset.py:583,634,730). On a 2h family every leg entering
    # on the same bar carries an IDENTICAL bar_t, so those tie groups span every
    # pooled leg and the argument order moves fold membership.
    #
    # Measured 2026-08-15 (BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER):
    # the same 7 legs in two orders gave identical trade counts (2220), identical
    # rows (71199) and an identical 43x50 fold shape — yet 8 of 43 folds differed,
    # AUC moved up to 0.0331, and two legs LOST a usable fold.
    #
    # `trade_key` makes the sort TOTAL, so the partition stops depending on
    # argument order at all. It is OPT-IN and DEFAULT-OFF, and that default is
    # deliberate rather than timid: switching it changes recorded AUCs across the
    # committed corpus, so it is a re-measure to be decided and dated, not a
    # silent drive-by that would leave old and new verdicts pooled in one file
    # with nothing marking which convention produced them.
    #
    # This is a MIGRATION flag, not a permanent gate: the intended end state is
    # total-by-default once the corpus is re-measured. Do not read the default as
    # an endorsement of the unstable sort.
    if total_sort:
        ordered = sorted(h_trades.items(), key=lambda kv: (t_entry(kv[1]), str(kv[0])))
        print("  note: --total-sort — ties broken by trade_key, so the partition "
              "does not depend on --legs order. NOT the recorded-corpus "
              "convention; verdicts from this run are not comparable to rows "
              "produced without it.")
    else:
        ordered = sorted(h_trades.items(), key=lambda kv: t_entry(kv[1]))
    if offset:
        print(f"  note: --fold-offset {offset} — skipping the first {offset} "
              f"trade(s) before blocking; {len(ordered) - offset} of "
              f"{len(ordered)} remain. Boundary shifted, block size unchanged "
              f"at {block_n}.")
        ordered = ordered[offset:]
    out = []
    # Start at `block_n` so the first test block has a training set behind it —
    # the `years` cut achieved the same thing with `years[1:]`.
    for start in range(block_n, len(ordered) - block_n + 1, block_n):
        items = ordered[start:start + block_n]
        test = dict(items)
        t0 = min(t_entry(b) for _, b in items)
        y_last = datetime.fromtimestamp(t_entry(items[-1][1]),
                                        tz=timezone.utc).year
        y_first = datetime.fromtimestamp(t0, tz=timezone.utc).year
        label = (f"t{start}-{start + block_n} ({y_first}"
                 + (f"-{y_last}" if y_last != y_first else "") + ")")
        out.append((label, y_last, test, t0))
    # NEVER let a dropped tail read as full coverage. A trailing partial block
    # is below the floor by construction, so it is excluded — and SAID so,
    # rather than silently shortening the population.
    covered = block_n + len(out) * block_n
    if out and covered < len(ordered):
        print(f"  note: {len(ordered) - covered} trailing trade(s) not in any "
              f"test fold (partial block below --min-fold-trades)")
    return out


def _select_tau_holdout(h_trades: dict, y0: float, a, tf_s: int, t_entry) -> dict:
    """Pick a tau WITHOUT looking at the test block.

    The E1 gate scores `max(net_r over ~7 tau arms)` on the test fold, which
    selects the arm using the outcome it is about to be graded on. Measured
    2026-08-13 over 514 folds (relay #9077, recorded in
    docs/research/M20-E1-block-size-derivation-2026-08-13.md section 9), that
    hindsight IS the fleet's edge: +1.217R / 70.2% of folds positive with
    best-arm, versus -0.341R / 54.1% when tau is chosen causally from earlier
    folds. This function measures the honest upper end of that interval.

    The carve, all of it inside the fold's own training window (`< y0`):

        [ ............ fit ............ ][embargo][ validation ][embargo][ TEST ]
                                                                          ^ y0

    A selection model is fit on `fit`, every tau arm is replayed on
    `validation`, and the argmax is returned. The embargo between fit and
    validation is the SAME `EMBARGO_S` the outer split uses and is not
    optional: a trade whose hold spans the boundary would leak its `final_r`
    into the model that scores it, which is the leak the outer purge exists to
    stop, one level in.

    RETURNS A THREE-STATE RESULT, never a bare tau:

        selected            a tau was chosen on a real validation block
        no_validation_block the training window could not spare one -- tau is
                            None, and the caller MUST NOT fall back to
                            best-arm. "We could not select" and "we selected"
                            are opposite statements, and collapsing them would
                            restore the hindsight figure under an honest key.

    The thresholds are deliberately the SAME ones the outer fold uses
    (`--min-fold-trades` for the validation block, `_MIN_FOLD_TRAIN_ROWS` for
    the fit rows). A validation block held to a laxer standard than a test
    block would be picking tau on evidence we have already declared too thin
    to grade with.
    """
    # Trades wholly inside this fold's training window, in entry order.
    train_trades = {tk: b for tk, b in h_trades.items()
                    if b[-1]["bar_t"] < y0 - EMBARGO_S}
    if not train_trades:
        return {"tau": None, "state": "no_validation_block", "n_val": 0}
    ordered = sorted(train_trades.items(), key=lambda kv: t_entry(kv[1]))

    n_val = a.min_fold_trades
    if len(ordered) <= n_val:
        # Not enough to hold any out and still have something to fit on.
        return {"tau": None, "state": "no_validation_block", "n_val": 0}
    val = dict(ordered[-n_val:])
    val_start = min(t_entry(b) for b in val.values())
    fit_rows = [r for tk, b in ordered[:-n_val]
                if b[-1]["bar_t"] < val_start - EMBARGO_S
                for r in b]
    if len(fit_rows) < _MIN_FOLD_TRAIN_ROWS:
        return {"tau": None, "state": "no_validation_block", "n_val": len(val)}

    sel_model = train_model(fit_rows)
    val_res = eval_split(sel_model, val, tf_s)
    best = _best_tau(val_res)
    if not best:
        return {"tau": None, "state": "no_validation_block", "n_val": len(val)}
    return {"tau": best[0], "state": "selected", "n_val": len(val)}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True,
                    help="E0 family dir containing rows.jsonl")
    ap.add_argument("--tf", required=True, choices=sorted(TF_S))
    # 50 IS DERIVED, not a convention — basis:
    # docs/research/M20-E1-block-size-derivation-2026-08-13.md (operator
    # decision 2026-08-13: derive it and accept the answer, including stricter).
    # Measured over 21 e1_report.json / 262 folds / 15 (family,tf) groups: the
    # per-trade paired difference d_i = R_best_tau,i - R_actual,i has
    # sigma_d ~ 0.747R (median) and a standardized effect delta ~ 0.105
    # (median; FIVE of fifteen groups negative). A fold votes "beats" on
    # sum(d_i) > 0, so P(correct vote) = Phi(sqrt(b) * delta) and
    # b = (z_p/delta)^2 puts the 0.75-0.80 reliability band at 41-64 trades.
    # 50 gives 0.771 and sits inside it.
    #
    # DO NOT TUNE THIS TO UNBLOCK LEGS. Modelling the whole gate (which needs
    # >=2/3 of u folds, and u = floor(N/b)-1 shrinks as b grows) shows P_detect
    # is NOT monotonic in b, and every apparent optimum lands on a u where 2/3
    # is cheap to hit by luck -- N=98/b=20, N=200/b=50 and N=300/b=75 all carry
    # a 50% single-condition false-positive rate. Maximising power over b
    # therefore selects the settings easiest to pass BY CHANCE. The derivation
    # also establishes that no b rescues a short leg: at N=98 the only
    # gradeable options give either 0.49 power or that 50% FP rate.
    #
    # The measurement is biased in the model's favour (_best_tau is a max over
    # ~7 arms), so 41-64 is a LOWER bound and the honest reading is
    # "delta <= 0.105 typical".
    ap.add_argument("--min-fold-trades", type=int, default=50)
    ap.add_argument("--fold-offset", type=int, default=0,
                    help="Shift where trade-blocking starts, at FIXED block "
                         "size (0 <= k < --min-fold-trades). For measuring how "
                         "much a verdict depends on where the fold boundaries "
                         "fall. Do NOT sweep --min-fold-trades for that — see "
                         "fold_blocks() and that flag's own comment.")
    ap.add_argument("--total-sort", action="store_true",
                    help="Break entry-time ties by trade_key, making the fold "
                         "sort TOTAL so the partition does not depend on the "
                         "order --legs were passed. DEFAULT OFF: turning it on "
                         "changes recorded AUCs across the committed corpus, so "
                         "it is a deliberate re-measure, not a drive-by. A "
                         "MIGRATION flag — the intended end state is total-by-"
                         "default once the corpus is re-measured "
                         "(BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER).")
    ap.add_argument("--fold-mode", choices=["trades", "years"], default="trades",
                    help="How walk-forward TEST folds are cut. `trades` "
                         "(default) slices sequentially by trade count so a "
                         "low-frequency leg is gradeable; `years` is the legacy "
                         "per-calendar-year cut, kept ONLY to reproduce "
                         "pre-2026-08-13 results. See fold_blocks().")
    ap.add_argument("--target", choices=["holding_pays", "peak_is_in"],
                    default="holding_pays",
                    help="M20 P4.2: classification target. peak_is_in needs a "
                         "post-P4 dataset (rows carry the label).")
    ap.add_argument("--features", choices=["base", "extended"], default="base",
                    help="M20 P4.3: 'extended' adds the exhaustion block "
                         "(needs a post-P4 dataset; missing cols become NaN).")
    a = ap.parse_args(argv[1:])
    # Bound to a name for the process lifetime: the flock releases when the fd
    # closes, so letting this be garbage-collected would silently unlock.
    _heavy_lock = take_heavy_queue("train_exit_head")  # noqa: F841
    global TARGET, FEATURES
    TARGET = a.target
    if a.features == "extended":
        FEATURES = FEATURES + FEATURES_EXH

    fam_dir = Path(a.family_dir)
    rows = load_rows(fam_dir / "rows.jsonl")
    if TARGET == "peak_is_in" and rows and "peak_is_in" not in rows[0]:
        print("dataset predates the peak_is_in label — rebuild with the "
              "post-P4 builder first", file=sys.stderr)
        return 2
    tf_s = TF_S[a.tf]
    harness = [r for r in rows if r["source"] == "harness"]
    live = [r for r in rows if r["source"] == "live"]
    h_trades = group_trades(harness)
    l_trades = group_trades(live)
    print(f"{fam_dir.name}: {len(h_trades)} harness trades "
          f"({len(harness)} rows), {len(l_trades)} live trades "
          f"({len(live)} rows)")

    # ---- purged walk-forward by year over harness trades
    def t_entry(bars):  # first bar time as trade entry proxy
        return bars[0]["bar_t"]
    folds = []
    blocks = fold_blocks(h_trades, a.fold_mode, a.min_fold_trades, t_entry,
                         offset=a.fold_offset, total_sort=a.total_sort)
    print(f"  fold-mode={a.fold_mode} -> {len(blocks)} candidate fold(s)")
    for label, ytest, test, y0 in blocks:
        # purge on the trade's LAST bar: a hold spanning into the test block
        # (or the embargo) would leak its final_r label into training. Unchanged
        # by the fold-mode switch — only what defines the block boundary moved.
        train_rows = [r for tk, b in h_trades.items() for r in b
                      if b[-1]["bar_t"] < y0 - EMBARGO_S]
        # NAME THE FAILING CONDITION AND ITS BOUND. This printed one message for
        # two independent conditions and stated neither threshold, so
        # `fold 2024: skipped (test=42 trades, train=12402 rows)` could not tell
        # a reader that 42 was being compared against 50 -- you had to open the
        # source to learn why. Measured 2026-08-13: a 1d round skipped all 19
        # folds this way and reported `no usable folds`, and the reason (a
        # per-calendar-year fold gate that daily bars cannot satisfy, max fold 42
        # vs default 50) took a source read to recover
        # (BL-20260813-E1-PER-YEAR-FOLD-UNSATISFIABLE-ON-DAILY-BARS).
        thin_test = len(test) < a.min_fold_trades
        thin_train = len(train_rows) < _MIN_FOLD_TRAIN_ROWS
        if thin_test or thin_train:
            why = []
            if thin_test:
                why.append(f"test {len(test)} < {a.min_fold_trades} "
                           f"(--min-fold-trades)")
            if thin_train:
                why.append(f"train {len(train_rows)} rows < "
                           f"{_MIN_FOLD_TRAIN_ROWS}")
            print(f"  fold {label}: skipped — {'; '.join(why)}")
            continue
        model = train_model(train_rows)
        res = eval_split(model, test, tf_s)
        # ---- HINDSIGHT-FREE tau selection (measured 2026-08-13, relay #9077).
        # Everything else in this fold reports max-over-arms, which is the same
        # selection the gate makes and is HINDSIGHT: the arm is picked using the
        # test block's own outcome. Measured over 514 folds, replacing that with
        # a causal rule moved the fleet from +1.217R (70.2% of folds positive)
        # to -0.341R (54.1%) -- i.e. the fleet-level edge WAS the hindsight.
        # Those causal rules picked tau from EARLIER folds, so they also ate
        # regime drift and are a lower bound. This is the upper end: carve a
        # validation block off the TAIL of this fold's own training window, fit
        # a selection model on train-minus-validation (with the same embargo, so
        # the validation block cannot leak into the model that scores it), pick
        # tau there, and apply it to the test block using the FULL-train model.
        # tau is a hyperparameter chosen on held-out data -- never on test.
        sel = _select_tau_holdout(h_trades, y0, a, tf_s, t_entry)
        res["selected_tau"] = sel["tau"]
        # THREE STATES, never collapsed (CLAUDE.md 'Collapsed states'):
        #   selected            - a tau was picked on a real validation block
        #   no_validation_block - the training window could not spare one; tau
        #                         is None and a consumer MUST NOT fall back to
        #                         best-tau, which is the hindsight this exists
        #                         to avoid
        #   absent              - a report predating this field (older runs)
        # Collapsing 'could not select' into 'selected nothing' would silently
        # restore the hindsight figure under an honest-looking key.
        res["selected_tau_state"] = sel["state"]
        res["selected_tau_val_trades"] = sel["n_val"]
        # `year` stays an int for schema compatibility (per_leg_summary uses
        # it as a display label); `fold_label` carries the real identity, which
        # under trade-folds is a trade-index range, not a calendar year.
        res["year"] = ytest
        res["fold_label"] = label
        res["train_rows"] = len(train_rows)
        # Same model, same fold, same replay — cut by leg, because the
        # coverage matrix's unit is the leg and the family's is not.
        res["per_leg"] = {
            leg: eval_split(model, sub, tf_s)
            for leg, sub in sorted(split_by_leg(test).items())
        }
        folds.append(res)
        print(f"  fold {label}: AUC={res['auc'] and round(res['auc'],3)} "
              f"actual net_R={res['actual']['net_r']} "
              f"best_tau={max(res['model'].items(), key=lambda kv: kv[1].get('net_r') or -1e9)[0]}")

    # ---- live validation: train on ALL harness rows, apply to live trades
    live_eval = None
    if l_trades:
        model_all = train_model(harness)
        live_eval = eval_split(model_all, l_trades, tf_s)
        print(f"  live: AUC={live_eval['auc'] and round(live_eval['auc'],3)} "
              f"n={live_eval['n_trades']} actual net_R={live_eval['actual']['net_r']}")

    leg_summary = per_leg_summary(folds, MIN_OOS_TRADES)
    if leg_summary:
        print("  per-leg (matrix unit):")
        for leg, s in sorted(leg_summary.items()):
            print(f"    {leg:<26} n_oos={s['oos_trades']:<5} "
                  f"auc={s['mean_auc']} "
                  f"beats_actual={s['beats_actual_folds']}/{s['usable_folds']} "
                  f"beats_hard={s['beats_hard_folds']}/{s['usable_folds']} "
                  f"-> {s['verdict']}")
    else:
        print("  per-leg: no fold produced a leg block (no usable folds)")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "family": fam_dir.name, "tf": a.tf, "features": FEATURES,
        "target": TARGET,
        "taus": TAUS, "embargo_days": EMBARGO_S // 86400,
        "harness_trades": len(h_trades), "live_trades": len(l_trades),
        "folds": folds, "live_validation": live_eval,
        "per_leg": leg_summary,
        "min_oos_trades_floor": MIN_OOS_TRADES,
        # A verdict must state its own derivation. Trade-folds and
        # calendar-folds are NOT comparable evidence, so a report that
        # does not say which one produced it invites exactly the
        # apples-to-oranges read that the 2026-08-13 re-run exists to
        # avoid.
        "fold_mode": a.fold_mode,
        "min_fold_trades": a.min_fold_trades,
        "per_leg_note": (
            "One verdict per STRATEGY LEG — the coverage matrix's unit. The "
            "family-level blocks above pool every symbol in the family, which "
            "is the right unit to TRAIN on and the wrong one to record a "
            "verdict from "
            "(BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS). "
            "`insufficient_base` means the leg's OOS book was too "
            "thin to judge — NOT that the head failed on it; "
            "`would_have_been` records the counterfactual so the floor's "
            "effect stays auditable."),
        "gate_note": ("E1->E2 gate: OOS AUC materially > 0.55 AND a tau-policy "
                      "beats the best hard rule on net_R AND maxDD in the "
                      "walk-forward AND the live set agrees in sign."),
    }
    out = fam_dir / "e1_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

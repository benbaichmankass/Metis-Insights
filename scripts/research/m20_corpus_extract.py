#!/usr/bin/env python3
"""Flatten M20 fleet-sweep `verdicts.json` files into ONE durable per-cell corpus.

WHY THIS EXISTS. `m20_fleet_exit_sweep.py` already emits everything a Path B
threshold needs — per-cell IS/OOS deltas, capital rates, the derived drawdown
allowance, walk-forward folds — into `verdicts.json`. That file goes to a GitHub
Actions **artifact**, which no Claude session can download (`CLAUDE.md` § PM-side
session capabilities: no artifact download), and the PR comment carries a
**top-30** slice of one table. So the evidence for the operator's standing ask —
*"use capital-utilisation and PnL optimisation to decide what the correct number
is, database decisions and not arbitrary guesses"* — was being produced and then
discarded on every run, and each sweep started the population over from zero.

This turns the artifact into an accumulating, versioned corpus in the repo.

TWO PROPERTIES IT EXISTS TO PRESERVE, both about the denominator:

  1. **A leg that produced no cells still gets a row.** A harness error, a
     missing frame, or a skipped leg is recorded as `kind:"leg_status"`. A corpus
     that silently omits them would let a later analysis report "38 of 40 cells
     generalised" over a fleet where a third of the legs never ran — the
     unasserted-denominator failure, one level up from where it usually bites.

  2. **A cell that was never walk-forwarded is distinguishable from one that was
     and scored 0.** `wf_ran` is an explicit boolean and `wf_wins`/`wf_usable`
     are `None`, never `0`, when no walk-forward ran. Those are opposite
     statements about generalisation.

MERGE SEMANTICS — keyed on the MEASUREMENT, not on the run.

A row's identity is `(leg, cell, split, tp_cap_pct)`: what was measured, over
which windows, against which exit geometry. The newest `sweep_generated_at`
wins. Keying on the run id instead — the obvious choice, and the one this file
shipped with first — is wrong in a way that corrupts the analysis silently:
re-sweeping the same legs produces a NEW run id, so both copies survive and the
population doubles without gaining one bit of information. Tonight's 4th and 5th
dispatches are the worked example — byte-identical numbers on every leg, two run
ids. A floor analysis reading that corpus would see 22 cells over 9 legs instead
of 11 and report a denominator twice its real size.

`tp_cap_pct` is IN the key, not metadata. The same `(leg, cell, split)` measured
at the legacy no-TP geometry and at live parity are two different numbers about
two different books (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`),
and collapsing them would re-commit that defect one level up. A run predating
the field records `tp_cap_pct: null`, which keys DISTINCTLY from any known
geometry — "we do not know which book this measured" is its own bucket, never
silently merged into the current one.

Every row still carries `sweep_generated_at` and `run_id`, so a vintage can be
excluded explicitly rather than by hoping it was overwritten — which is what the
2026-08-10 config-exactness defect needed
(`BL-20260810-SWEEP-BASE-NOT-CONFIG-EXACT-TRAILVOL` — kept on one line: a
backlog id hyphen-broken across a wrap resolves to nothing and reads as tracked
while being tracked by nobody, which `artifact-validity-guard` fails on).

Usage:
    python3 scripts/research/m20_corpus_extract.py \
        --in out/ --corpus docs/research/m20-sweep-corpus.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORPUS_DEFAULT = "docs/research/m20-sweep-corpus.jsonl"


def _num(d: dict | None, key: str):
    """Read a numeric field, preserving None. Never coerces a missing value to 0."""
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _win(block: dict | None, window: str) -> dict:
    if not isinstance(block, dict):
        return {}
    got = block.get(window)
    return got if isinstance(got, dict) else {}


def measurement_key(row: dict) -> tuple:
    """WHAT this row measured — the merge identity. Never includes the run.

    `tp_cap_pct` is part of it: the same cell at the legacy no-TP geometry and at
    live parity are two different measurements. `None` (a run predating the
    field) keys distinctly from every known geometry rather than merging into
    one — an unknown book is its own state, not the current book.

    `regime_router` joins it for exactly the same reason, one axis over (added
    2026-08-11): the same cell measured with the hard gate off and with it on are
    two different books for any policy-named leg, and merging them would average
    a gated and an ungated measurement under one label. `None` (a run predating
    the field) again keys distinctly rather than being assumed `"off"` — even
    though every run to date WAS off, asserting that of a row we never recorded
    is the substitution this field exists to stop.

    `regime_gate_delta` is deliberately NOT in the key: it describes how the base
    compares to the CURRENT live policy, not what the run measured, so a policy
    edit must not retroactively split rows that measured the same book.

    `fee_bps_roundtrip` joins it as well, and SRQ-20260618-003 is the reason it must:
    the same three 15m scalp legs read +15.09/+1.98/-2.79 IS at 7.5bps and
    -1.98/-10.58/-16.51 at 15bps. Two different books by any standard, and a corpus
    that merged them would average a surviving leg with a dead one.

    `min_oos_trades_floor` joins it too (operator decision 2026-08-11, value 25):
    the same cell graded with no floor and graded at 25 can carry DIFFERENT
    verdicts — `is_oos_pass` vs `insufficient_base` — so merging the vintages
    would let an ungraded thin cell and a floor-refused one share a row. `None`
    is "ungraded by any floor", which is NOT floor 0.

    `min_confidence_override` joins it on the same grounds as the fee, one axis
    over: a leg swept at its config-declared entry floor and the same leg swept
    at an imposed one measured two different populations, and the arm exists
    precisely because they are expected to score differently. `None` = no
    override was applied, which is NOT "floor 0" — a leg may declare its own.

    THE LEVER-OFF ARM KEYS ON `declared_levers_dropped`, NOT on the run-level
    `without_declared_levers`, and the difference is not cosmetic: a run asking
    to drop `stale_stop` across the fleet removes NOTHING from a leg that never
    declared one, so that leg measured the ordinary config-exact base and must
    merge with config-exact rows rather than fragment away from them. The
    request is what was asked; the drop is what was measured, and the key
    describes the measurement.

    A MISSING `declared_levers_dropped` IS NORMALISED TO "nothing dropped",
    which is the one deliberate exception to the "unknown keys distinctly" rule
    applied to every field above — and it is legitimate for a reason those
    fields cannot claim: the flag DID NOT EXIST before this field did, so a
    legacy run provably carried every declared lever in its base. That is known
    by construction, not assumed from a default. Keying it as unknown instead
    would fragment all 808 pre-existing rows away from every future
    config-exact row for no informational gain.
    """
    _dropped = row.get("declared_levers_dropped")
    return (row.get("kind"), row.get("leg"), row.get("cell"),
            row.get("split"), row.get("tp_cap_pct"), row.get("regime_router"),
            row.get("min_oos_trades_floor"), row.get("fee_bps_roundtrip"),
            row.get("min_confidence_override"),
            tuple(sorted(_dropped)) if isinstance(_dropped, list) else ())


def rows_from_verdicts(doc: dict, run_id: str) -> list[dict]:
    """One row per (leg, cell), plus one per leg that produced no cells."""
    out: list[dict] = []
    gen = doc.get("generated_at")
    # THE RUN-LEVEL split, which is now only the LEGACY location.
    #
    # PR #8965 made the IS/OOS boundary PER-LEG (`resolve_split`, so a leg's
    # trade FREQUENCY stops deciding whether it can be graded), and the sweep
    # stopped writing a doc-level `split` — it writes `split_fallback_date` /
    # `split_mode` / `split_target_oos` here and the ACTUAL boundary inside each
    # leg's verdict. This read was never updated, so every row produced by a
    # post-#8965 run recorded `split: null`.
    #
    # MEASURED 2026-08-13, and it is not cosmetic. `trend_donchian_sol_prop` at
    # the SAME tp_cap=0.099 across two runs:
    #     2026-08-10  split=2025-07-01  IS 245 / OOS 65  -> is_oos_fail (graded)
    #     2026-08-13  split=null        IS 285 / OOS 24  -> insufficient_base
    # The split alone moved OOS from 65 to 24, under the 25 floor, turning every
    # gradeable cell ungradeable. A reader comparing those rows would attribute
    # the difference to the geometry, which is identical in both.
    #
    # Two consequences, both fixed by resolving per-leg below:
    #   1. `measurement_key` includes `split`, so post-#8965 rows key on None and
    #      never supersede their pre-#8965 counterparts — the corpus accumulates
    #      duplicate measurements of one cell under two identities.
    #   2. A row could not state its own boundary, so no verdict here was
    #      reproducible. That is the same discipline `tp_cap_pct` /
    #      `regime_router` / `fee_bps_roundtrip` are held to three lines down —
    #      except those degrade because a field did not EXIST yet, whereas this
    #      one degraded because the field MOVED.
    split = doc.get("split")
    # HOW the boundary was chosen, carried so a verdict states its own
    # derivation rather than leaving `split` to look like a free choice.
    split_mode = doc.get("split_mode")
    split_target_oos = doc.get("split_target_oos")
    # A run predating the field records None — NOT a default of the current
    # geometry, which would silently relabel a legacy no-TP measurement as
    # live-parity and merge two different books under one key.
    tp_cap = doc.get("tp_cap_pct")
    # WHICH REGIME BOOK this run measured. A run predating the field records
    # None — the same discipline as `tp_cap_pct` directly above, and for the same
    # reason: a legacy row must not be relabelled with the current run's state.
    # `"off"` means the harness ran with REGIME_ROUTER_DISABLED=1, i.e. the base
    # book is the UNGATED book while the live router is baseline-on.
    regime_router = doc.get("regime_router")
    # The FLOOR that graded this run (operator decision 2026-08-11: 25). None on a
    # run predating it -- which is NOT floor 0, it is "ungraded by any floor", and
    # keying them distinctly stops a thin-but-unflagged cell sharing a row with a
    # refused one. Same discipline as `tp_cap_pct` two lines up.
    min_oos_floor = doc.get("min_oos_trades_floor")
    # THE FEE BAND this run measured. Shipped into verdicts.json in the same commit
    # that added the --fee-bps-roundtrip flag, and then NOT propagated here -- so the
    # first 15bps run produced 12 rows that read `fee: None`, i.e. claimed not to have
    # declared a fee while being the entire point of the run. Caught by reading the
    # corpus after the run, not by the tests, because the tests covered base_args and
    # the verdicts doc but never the extractor hop. None = the run did not declare one.
    fee_bps = doc.get("fee_bps_roundtrip")
    # THE ENTRY-SELECTIVITY BAND this run measured. Threaded here in the SAME
    # commit that adds the flag, deliberately: the fee field two lines up was
    # shipped into verdicts.json and then not propagated to this hop, and the
    # result was 12 rows reading `fee: None` while being the whole 15bps arm.
    # Repeating that on a second axis would be a choice, not an oversight.
    # None = no override, i.e. each leg ran its own declared floor.
    min_conf_override = doc.get("min_confidence_override")
    # THE LEVER-OFF ARM this run measured. Threaded in the SAME commit that adds
    # the flag, for the reason recorded two fields up: the fee was shipped into
    # verdicts.json and then not propagated to this hop, and the whole 15bps arm
    # landed as 12 rows reading `fee: None`. `[]` = no lever removed (the
    # ordinary config-exact base); `None` = a run predating the field, which is
    # NOT the same claim and keys distinctly.
    _wdl = doc.get("without_declared_levers")
    without_levers = (tuple(sorted(_wdl)) if isinstance(_wdl, list) else None)
    # Per-leg gate delta. The sweep stamps it onto each verdict, but SKIPPED legs
    # never reach `verdicts`, so it is also derivable here from the doc-level
    # off-leg list. Three states preserved end-to-end: None on a legacy run (the
    # field did not exist), "unknown" when the run could not read the policy,
    # "none"/"narrower_live" otherwise.
    _off = doc.get("regime_policy_off_legs")
    _readable = doc.get("regime_policy_readable")

    def _gate_delta(leg: str, stamped: object = None) -> str | None:
        if isinstance(stamped, str):
            return stamped
        if regime_router is None and _readable is None:
            return None                       # legacy run: the field did not exist
        if not _readable:
            return "unknown"                  # the run looked and could not read it
        return "narrower_live" if leg in (_off or []) else "none"

    verdicts = doc.get("verdicts") or {}

    # Legs the planner skipped never reach `verdicts` at all. They are part of
    # the fleet denominator, so they are rows too.
    for s in doc.get("skipped") or []:
        out.append({"kind": "leg_status", "run_id": run_id,
                    "sweep_generated_at": gen, "split": split, "tp_cap_pct": tp_cap,
                    "regime_router": regime_router,
                    "min_oos_trades_floor": min_oos_floor,
                    "fee_bps_roundtrip": fee_bps,
                    "min_confidence_override": min_conf_override,
                    "regime_gate_delta": _gate_delta(str(s.get("leg"))),
                    "leg": s.get("leg"), "cell": None,
                    "leg_status": "skipped", "leg_status_why": s.get("reason")})

    for leg, v in verdicts.items():
        if not isinstance(v, dict):
            continue
        if "levers" not in v:
            out.append({"kind": "leg_status", "run_id": run_id,
                        "sweep_generated_at": gen, "split": split,
                        "tp_cap_pct": tp_cap, "leg": leg, "cell": None,
                        "regime_router": regime_router,
                        "min_oos_trades_floor": min_oos_floor,
                        "fee_bps_roundtrip": fee_bps,
                        "min_confidence_override": min_conf_override,
                        "regime_gate_delta": _gate_delta(
                            leg, v.get("regime_gate_delta")),
                        "leg_status": v.get("status") or "no_levers",
                        "leg_status_why": v.get("error")})
            continue

        base = v.get("base_book") or {}
        sel = v.get("selection") or {}
        # A leg whose sweep predates the `base_book` block (added 2026-08-10)
        # carries no rate. That is recorded as its own state rather than left to
        # look like an ungradeable book — an OLD CORPUS and an UNPROFITABLE BOOK
        # would otherwise be indistinguishable, and only one of them is evidence.
        base_present = bool(base)
        # PER-LEG boundary first, run-level only as the legacy fallback. A
        # pre-#8965 verdict carries no per-leg `split` and correctly keeps the
        # doc-level one; a post-#8965 verdict carries the real derived date.
        leg_split = v.get("split") or split
        leg_common = {
            "run_id": run_id, "sweep_generated_at": gen, "split": leg_split,
            "split_mode": v.get("split_mode") or split_mode,
            "split_target_oos": v.get("split_target_oos") or split_target_oos,
            # WHY this leg's boundary is what it is. `resolve_split` falls back
            # to the fixed date when a leg cannot support the target (a 33-trade
            # leg giving 25 to OOS leaves 8 for IS), and it never does so
            # silently — carrying the reason means a thin OOS is attributable to
            # the leg's lifetime rather than read as a property of the cell.
            "split_fallback": v.get("split_fallback"),
            "split_lifetime_trades": v.get("split_lifetime_trades"),
            "tp_cap_pct": tp_cap, "leg": leg, "proxy": v.get("proxy"),
            # WHICH REGIME BOOK the base figures below describe. `narrower_live`
            # means the live gate refuses trades this base includes, so
            # `base_net_r_*` / `base_rate_*` — and Path B's tolerance derived from
            # them — are NOT statements about the book production trades. The
            # per-cell deltas are unaffected (both arms share this base).
            "regime_router": regime_router,
            "min_oos_trades_floor": min_oos_floor,
            "fee_bps_roundtrip": fee_bps,
            "min_confidence_override": min_conf_override,
            # RUN-LEVEL request vs WHAT THIS LEG ACTUALLY HAD REMOVED. Both are
            # carried because they disagree routinely: a run asking to drop
            # `stale_stop` across the fleet removes nothing from a leg that never
            # declared one, and that leg's rows measured the ordinary
            # config-exact base. Keying on the request alone would label them a
            # lever-OFF measurement of a lever that was never on.
            "without_declared_levers": (list(without_levers)
                                        if without_levers is not None else None),
            "declared_levers_present": v.get("declared_levers_present"),
            "declared_levers_dropped": v.get("declared_levers_dropped"),
            "regime_gate_delta": _gate_delta(leg, v.get("regime_gate_delta")),
            "base_book_present": base_present,
            "cells_tried": sel.get("cells_tried"),
            "cells_withheld_inert": sel.get("cells_withheld_inert"),
        }
        for w in ("IS", "OOS"):
            b = _win(base, w)
            leg_common[f"base_net_r_{w}"] = _num(b, "net_total_r")
            leg_common[f"base_max_dd_{w}"] = _num(b, "max_drawdown_r")
            leg_common[f"base_rate_{w}"] = _num(b, "net_r_per_drawdown_r")
            leg_common[f"base_rate_ungradeable_why_{w}"] = (
                b.get("rate_ungradeable_why") if base_present else "no_base_book_in_run")
            leg_common[f"base_cap_day_{w}"] = _num(b, "net_r_per_capital_day")
            leg_common[f"base_trades_{w}"] = b.get("total_trades")
            # DID THE CAP THIS ROW NAMES ACTUALLY BIND? `tp_cap_pct` records what
            # was REQUESTED, and until now that was the only thing any row said
            # about the geometry — so a row reading `tp_cap_pct: 0.099` was read
            # as "measured at live parity" when all it establishes is that the
            # flag was passed. The two are not the same claim, and the gap is not
            # theoretical: `trend_donchian_eth_prop` came back BYTE-IDENTICAL at
            # `tp_cap_pct: 0.099` and at `null` — same base book, all seven shared
            # cells agreeing to 4dp — across two books that cannot be the same,
            # since the harness leaves `tp_price = None` entirely when the cap is
            # off (`scripts/backtest_trend.py:463`). Nothing in the corpus could
            # distinguish "the cap bound and changed nothing" from "the cap was
            # never applied", which is the question the anomaly turns on.
            #
            # `tp_r_effective_*` is the harness's own measurement of how far the
            # placed TP sat, in R, and `run_cell` already returned it — the sweep
            # has stamped it per-leg since #8933 and this hop simply dropped it.
            # `None` is THREE-WAY here and the states are not collapsed:
            # a run predating the field, a run with the cap OFF (legacy no-TP
            # geometry — a different book, not a distant TP), and a leg the cap
            # was on for but that placed no measurable TP. `_n` beside the median
            # is what separates them: `_n: 0` is "the cap was on and reached
            # nothing", `_n: None` is "we did not look".
            t = _win(v.get("live_tp_reach_r"), w)
            leg_common[f"live_tp_reach_r_n_{w}"] = t.get("n")
            leg_common[f"live_tp_reach_r_median_{w}"] = _num(t, "median")
            leg_common[f"live_tp_reach_r_min_{w}"] = _num(t, "min")
            leg_common[f"live_tp_reach_r_max_{w}"] = _num(t, "max")

        if not v.get("levers"):
            out.append({**leg_common, "kind": "leg_status", "cell": None,
                        "leg_status": "no_cells", "leg_status_why": None})
            continue

        for lever, entries in (v.get("levers") or {}).items():
            for e in entries or []:
                if not isinstance(e, dict):
                    continue
                g_is, g_oos = _win(e.get("gate"), "IS"), _win(e.get("gate"), "OOS")
                c_is, c_oos = _win(e.get("capital"), "IS"), _win(e.get("capital"), "OOS")
                x_is, x_oos = (_win(e.get("dd_exchange_rate"), "IS"),
                               _win(e.get("dd_exchange_rate"), "OOS"))
                wf = e.get("walkforward")
                wins = usable = None
                if isinstance(wf, str) and "/" in wf:
                    try:
                        a, b_ = wf.split("/", 1)
                        wins, usable = int(a), int(b_)
                    except ValueError:
                        wins = usable = None
                # WHICH OTHER LEVERS WERE ABSENT from the base this cell was
                # measured against. A leg declaring two levers and dropping both
                # yields a cell restoring ONE — a clean A/B for that lever, but
                # in a book still missing the other, which is not the live
                # configuration. Derivable from `declared_levers_dropped` minus
                # this row's own `lever`; STATED anyway, because "the reader can
                # compute it" is how a caveat gets lost. `[]` = the base differed
                # from live in this row's lever only. None = pre-arm run.
                _dl = leg_common.get("declared_levers_dropped")
                _other = (sorted(set(_dl) - {lever}) if isinstance(_dl, list)
                          else None)
                row = {**leg_common, "kind": "cell", "lever": lever,
                       "base_missing_other_levers": _other,
                       "cell": e.get("cell"), "verdict": e.get("verdict"),
                       "is_oos_pass": e.get("is_oos_pass"),
                       "path_b_candidate": bool(e.get("path_b_candidate")),
                       # THREE-STATE, and NOT `bool(...)`: True / False / None
                       # ("no window was gradeable") are three different findings
                       # and `bool()` would silently turn the third into the
                       # second. Absent on rows written before the sweep emitted
                       # it — which reads as None, correctly, since those rows
                       # genuinely carry no verdict on the rate gate.
                       "path_b_rate_ok": e.get("path_b_rate_ok"),
                       # `wf_ran` is the honest flag: a cell that never reached a
                       # walk-forward is not a cell that failed one.
                       "wf_ran": wf is not None,
                       "wf_summary": wf, "wf_wins": wins, "wf_usable": usable,
                       "wf_folds": e.get("walkforward_folds")}
                for tag, g in (("IS", g_is), ("OOS", g_oos)):
                    row[f"d_net_r_{tag}"] = _num(g, "d_net_r")
                    row[f"d_max_dd_{tag}"] = _num(g, "d_max_dd")
                    row[f"gate_passed_{tag}"] = g.get("passed")
                    row[f"gate_reason_{tag}"] = g.get("reason")
                for tag, c in (("IS", c_is), ("OOS", c_oos)):
                    row[f"d_cap_day_{tag}"] = _num(c, "d_net_r_per_capital_day")
                    row[f"cell_cap_day_{tag}"] = _num(c, "cell_net_r_per_capital_day")
                    row[f"net_r_retained_frac_{tag}"] = _num(c, "net_r_retained_frac")
                    row[f"d_mean_bars_held_{tag}"] = _num(c, "d_mean_bars_held")
                for tag, x in (("IS", x_is), ("OOS", x_oos)):
                    row[f"headroom_{tag}"] = _num(x, "headroom")
                    row[f"allowed_d_max_dd_{tag}"] = _num(x, "allowed_d_max_dd")
                    row[f"rate_ok_{tag}"] = x.get("passes")
                    row[f"rate_reason_{tag}"] = x.get("reason")
                out.append(row)
    return out


def find_verdicts(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("verdicts.json"))


def run_id_for(path: Path, doc: dict) -> str:
    """Stable id for the run a verdicts file came from.

    Prefers the sweep's own timestamp: two legs of the SAME matrix run land in
    different `out/<leg>/<date>/` directories, so a path-derived id would split
    one run into N and make a per-run row count meaningless.
    """
    gen = doc.get("generated_at")
    if isinstance(gen, str) and gen:
        return gen
    return str(path.parent)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True,
                    help="Directory to search for verdicts.json (recursive), or one file.")
    ap.add_argument("--corpus", default=CORPUS_DEFAULT,
                    help=f"JSONL corpus to merge into (default {CORPUS_DEFAULT}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be merged; write nothing.")
    a = ap.parse_args(argv[1:])

    src = Path(a.inp)
    if not src.exists():
        print(f"error: --in path does not exist: {src}", file=sys.stderr)
        return 2
    files = find_verdicts(src)
    if not files:
        # A silent zero here would commit an unchanged corpus and read as "the
        # sweep added nothing", which is a different statement from "no verdicts
        # file was produced". Fail instead.
        print(f"error: no verdicts.json under {src} — nothing to extract. "
              "This is a failed extraction, NOT an empty sweep.", file=sys.stderr)
        return 1

    fresh: list[dict] = []
    runs: set[str] = set()
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: unreadable {f}: {exc}", file=sys.stderr)
            return 1
        rid = run_id_for(f, doc)
        runs.add(rid)
        fresh.extend(rows_from_verdicts(doc, rid))

    corpus = Path(a.corpus)
    kept: list[dict] = []
    superseded = malformed = 0
    fresh_keys = {measurement_key(r) for r in fresh}
    if corpus.exists():
        for line in corpus.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                # COUNTED, not skipped silently. A corpus quietly shedding rows
                # to a parse error would shrink its own denominator invisibly.
                malformed += 1
                continue
            # Supersede by MEASUREMENT, not by run: re-sweeping the same leg
            # re-measures the same cell, and keeping both would double the
            # population without adding information.
            if measurement_key(r) in fresh_keys:
                superseded += 1
                continue
            kept.append(r)

    merged = kept + fresh
    # The invariant the merge exists to hold. Asserting it here means a future
    # edit to the key cannot silently reintroduce duplicates — the failure mode
    # is invisible in the corpus itself (rows look fine; only the COUNT is wrong).
    keys = [measurement_key(r) for r in merged]
    if len(keys) != len(set(keys)):
        from collections import Counter
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        print(f"error: merge produced {len(keys) - len(set(keys))} duplicate "
              f"measurement key(s), e.g. {dupes[:3]}. The corpus would "
              "over-count its own population.", file=sys.stderr)
        return 1
    cells = sum(1 for r in merged if r.get("kind") == "cell")
    statuses = sum(1 for r in merged if r.get("kind") == "leg_status")
    rated = sum(1 for r in merged
                if r.get("kind") == "cell" and r.get("base_rate_IS") is not None)
    print(f"runs merged: {len(runs)}  new rows: {len(fresh)}  "
          f"superseded: {superseded}  malformed-dropped: {malformed}")
    print(f"corpus now: {len(merged)} rows = {cells} cells + {statuses} leg-status; "
          f"{rated}/{cells} cells carry a base rate")
    if a.dry_run:
        print("(dry run — nothing written)")
        return 0
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in merged))
    print("wrote", corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""M20 exit-head ROUND driver — one command per (family, tf) exit-head round.

Codifies the E0→E1 round the donchian-1h head went through (program doc
docs/research/M20-exit-head-PROGRAM.md; skill .claude/skills/exit-refinement)
so the remaining matrix rounds (4h donchians, 2h alt pullbacks, equities) are
one invocation each instead of hand-run stages:

  1. For each leg: resolve its family/harness/data/params CONFIG-EXACT from
     config/strategies.yaml (reusing m20_fleet_exit_sweep's resolvers) and run
     the harness with --emit-trades (the E0 volume source).
  2. One E0 build over all emitted trades at --tf
     (scripts/ml/build_exit_head_dataset.py; per-symbol candle CSVs threaded).
  3. One E1 train+τ-replay per produced family dir
     (scripts/ml/train_exit_head.py) — prints the gate verdict.

Advisory research tooling (Tier-1): never touches config or the registry;
E2/E3 graduation stays operator-gated. Run on the trainer, detached:
  nohup .venv/bin/python3 scripts/research/m20_exit_head_round.py \
      --legs trend_donchian_eth_4h,trend_donchian_sol_4h --tf 4h \
      --out runtime_logs/m20_exit_head/4h >/tmp/eh_round.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, LIVE_TP_CAPPED_FAMILIES, base_args, classify, resolve_data)

# IMPORTED, never re-implemented. `block_unit` below must answer "what did the
# BUILD actually group these trades into?", so it has to be the build's own
# predicate — a local copy would be a second definition of the grouping rule,
# free to drift from the one that cuts the blocks. Same reasoning as
# regime_flip_exit delegating to the live gate rather than mirroring it.
sys.path.insert(0, str(REPO / "scripts" / "ml"))
from build_exit_head_dataset import family_of as _family_of  # noqa: E402


def sh(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=timeout)


_ACCEPTS_STRATEGY_NAME: dict[str, bool | None] = {}


def accepts_strategy_name(harness: str) -> bool | None:
    """Does this harness take `--strategy-name`? ASKED, not declared.

    THREE STATES, never collapsed to a boolean:

      ``True``   the flag is there — pass the real leg name.
      ``False``  --help ran and the flag is genuinely absent (fvg, squeeze).
                 A real answer: proceed, and say what attribution is lost.
      ``None``   WE COULD NOT LOOK. Not the same as "no", and the caller must
                 SKIP the leg rather than guess.

    The `None` case is why this is not a boolean. Folding it into ``False``
    would mean a probe failure silently produces rows stamped with the family
    literal — unattributable rows, which is the exact defect this function
    exists to prevent. `silent-empty-guard` caught precisely that in the first
    version, which returned False on any exception and merely printed about it:
    a print does not stop the round from emitting the bad rows.

    This replaced the literal `fam == "scalp"`, correct on the day it was
    written and silently wrong the moment the trend and pullback harnesses
    gained the flag (2026-08-13) — a hardcoded capability list drifts exactly
    when someone adds the capability, which is the moment it matters. Probing
    `--help` costs one subprocess per harness per round and cannot go stale.
    """
    if harness in _ACCEPTS_STRATEGY_NAME:
        return _ACCEPTS_STRATEGY_NAME[harness]
    verdict: bool | None
    try:
        p = subprocess.run([sys.executable, str(REPO / harness), "--help"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        # Narrow: the only failures a `--help` invocation can legitimately
        # produce. Anything else is a bug in this function and propagates.
        print(f"    !! {harness} --help probe FAILED ({type(exc).__name__}: "
              f"{exc}) — cannot determine attribution support.", flush=True)
        verdict = None
    else:
        verdict = ("--strategy-name" in (p.stdout or "")
                   if p.returncode == 0 else None)
        if verdict is None:
            print(f"    !! {harness} --help exited {p.returncode} — cannot "
                  f"determine attribution support. stderr: "
                  f"{(p.stderr or '')[-200:]}", flush=True)
    _ACCEPTS_STRATEGY_NAME[harness] = verdict
    return verdict


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legs", required=True, help="CSV of strategy leg names")
    ap.add_argument("--tf", required=True,
                    choices=["5m", "15m", "1h", "2h", "4h", "1d"])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--tp-cap-pct", type=float, default=0.099,
                    help="TP geometry for the E0 emit. DEFAULT 0.099 = LIVE "
                         "PARITY, what production actually places: "
                         "tp = min(entry*(1+pct), entry + tp_r*risk). It is a "
                         "DEFAULT and not an opt-in deliberately — until "
                         "2026-08-14 this driver could not pass the flag at "
                         "all (it called base_args positionally, so tp_cap_pct "
                         "took 0.0, and base_args only forwards --tp-r/"
                         "--tp-cap-pct when that is > 0), so EVERY round on "
                         "disk was built on a book with NO take-profit: 11 of "
                         "13 audited round dirs contain zero take-profit exits "
                         "(BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP). "
                         "A head tuned on a book that cannot take profit is "
                         "tuned on a book production does not run. Pass 0 ONLY "
                         "to reproduce one of those historical no-TP verdicts, "
                         "and say so when you quote it.")
    ap.add_argument("--db", default=None,
                    help="optional trade_journal.db for the live-source split")
    ap.add_argument("--target", default=None,
                    choices=["holding_pays", "peak_is_in"],
                    help="pass through to train_exit_head (P4.2)")
    ap.add_argument("--features", default=None, choices=["base", "extended"],
                    help="pass through to train_exit_head (P4.3)")
    ap.add_argument("--fold-offset", type=int, default=0,
                    help="pass through to train_exit_head: shift where "
                         "trade-blocking starts, at FIXED block size, so a "
                         "verdict's dependence on WHERE the fold boundaries "
                         "fall can be measured. Recorded in _round_meta, "
                         "because N rounds at N offsets that do not say which "
                         "offset they used are not a dispersion measurement.")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    out = Path(a.out)
    (out / "emit").mkdir(parents=True, exist_ok=True)
    data_dir = Path(a.data_dir)

    emits: list[str] = []
    candles: dict[str, str] = {}
    # Families actually EMITTED (not requested) — the geometry stamp is
    # derived from this, so a skipped leg never contributes to the label.
    fams_seen: set[str] = set()
    # leg -> {symbol, family}, recorded at emit so the evidence rows carry the
    # same facts the round actually ran on rather than a re-read of YAML.
    emitted_meta: dict[str, dict] = {}
    for leg in a.legs.split(","):
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            print(f"SKIP {leg}: not in strategies.yaml", flush=True)
            continue
        fam = classify(leg)
        if fam is None or fam not in FAMILY_HARNESS:
            print(f"SKIP {leg}: no harness family", flush=True)
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        if tf != a.tf:
            print(f"SKIP {leg}: leg tf {tf} != round tf {a.tf}", flush=True)
            continue
        # prefer_native: this round REFUSES proxied data two lines down, so it
        # must look for the native spelling FIRST or the refusal is
        # unconditional for every symbol in PROXY_DATA regardless of what is on
        # disk — which is what kept the mes/mgc/mhg `exit_head_ml` cells
        # unreachable (BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA). The lever
        # sweeps keep the proxy-first default because the proxy is the DEEPER
        # series (940 native rows vs 2,512 at 1d, measured 2026-08-14) and
        # because their recorded verdicts keep the basis they were measured
        # against. NOTE: only the IBKR contract shards under
        # data/ibkr_datasets/ are native; datasets-out/market_raw/MGC/1d is
        # yfinance GC=F, i.e. the proxy under another name.
        data, proxy, resample = resolve_data(str(sym), tf, data_dir,
                                             prefer_native=True)
        if data is None:
            print(f"SKIP {leg}: data_missing:{sym}", flush=True)
            continue
        if proxy:
            # Head training needs native data (matrix rule: proxy OK for
            # levers only) — refuse rather than silently train on a proxy.
            print(f"SKIP {leg}: proxy data ({sym}) — native history required "
                  "for head training", flush=True)
            continue
        emit = out / "emit" / f"{leg}.jsonl"
        args = base_args(leg, cfg, fam, data, resample, a.tp_cap_pct)
        # Every harness stamped a HARDCODED family literal on each emitted row
        # -- `ict_scalp_5m`, `trend_donchian`, `htf_pullback_trend_2h` -- so the
        # E0 dataset, which buckets by that field, could not tell a 15m ETH
        # trade from a 5m XRP one, or `gld_pullback_1d` from `tlt_pullback_1h`.
        # Every per-leg verdict would have been attributed to one arbitrary leg.
        # The scalp harness was fixed first; trend and pullback followed
        # (2026-08-13), which is what makes the 26 non-scalp `exit_head_ml`
        # cells runnable at all.
        #
        # ASKED, not assumed -- see `accepts_strategy_name`. The old `fam ==
        # "scalp"` test was correct when written and would have silently kept
        # excluding trend/pullback after they gained the flag.
        supports = accepts_strategy_name(FAMILY_HARNESS[fam])
        if supports is None:
            # WE COULD NOT LOOK -> skip, never guess. Running the leg anyway
            # would emit rows stamped with the family literal, which is the
            # unattributable-row defect this whole change exists to fix; a leg
            # missing from the round is visible, a leg silently mis-attributed
            # is not.
            print(f"SKIP {leg}: could not determine whether "
                  f"{FAMILY_HARNESS[fam]} supports --strategy-name, so its rows "
                  f"might not be attributable to this leg. Fix the harness probe "
                  f"and re-run rather than accepting family-level rows.",
                  flush=True)
            continue
        if supports:
            args = [*args, "--strategy-name", leg]
        else:
            print(f"    NOTE {leg}: {FAMILY_HARNESS[fam]} has no "
                  f"--strategy-name; its rows will carry the family literal and "
                  f"this leg's verdict will NOT be separately attributable.",
                  flush=True)
        p = sh([sys.executable, REPO / FAMILY_HARNESS[fam], *args,
                "--emit-trades", emit, "--json", "/tmp/eh_round_cell.json"])
        if p.returncode != 0:
            print(f"HARNESS FAIL {leg}: {(p.stderr or p.stdout)[-300:]}",
                  flush=True)
            continue
        n = sum(1 for _ in emit.open()) if emit.exists() else 0
        print(f"emitted {leg}: {n} trades", flush=True)
        if n:
            emits.append(str(emit))
            candles[str(sym)] = data
            # Recorded HERE, after the harness actually produced trades — a leg
            # that skipped or failed must not colour the round's geometry stamp.
            fams_seen.add(fam)
            emitted_meta[leg] = {"symbol": str(sym), "family": fam}

    if not emits:
        print("no emitted trades — nothing to build")
        return 1

    build_cmd = [sys.executable, REPO / "scripts/ml/build_exit_head_dataset.py",
                 "--tf", a.tf, "--out", out,
                 "--instruments", REPO / "config/instruments.yaml"]
    for e in emits:
        build_cmd += ["--trades", e]
    for sym, path in candles.items():
        build_cmd += ["--candles", f"{sym}={path}"]
    if a.db:
        build_cmd += ["--db", a.db]
    p = sh(build_cmd, timeout=21600)
    print(p.stdout[-2000:], p.stderr[-500:], flush=True)
    if p.returncode != 0:
        return 1

    report = {}
    for fam_dir in sorted(d for d in out.iterdir()
                          if d.is_dir() and (d / "rows.jsonl").exists()):
        train_cmd = [sys.executable, REPO / "scripts/ml/train_exit_head.py",
                     "--family-dir", fam_dir, "--tf", a.tf]
        if a.target:
            train_cmd += ["--target", a.target]
        if a.features:
            train_cmd += ["--features", a.features]
        if a.fold_offset:
            train_cmd += ["--fold-offset", str(a.fold_offset)]
        p = sh(train_cmd, timeout=21600)
        print(p.stdout[-3000:], p.stderr[-500:], flush=True)
        e1 = fam_dir / "e1_report.json"
        if e1.exists():
            try:
                report[fam_dir.name] = json.loads(e1.read_text())
            except json.JSONDecodeError:
                pass
    # STAMP THE GEOMETRY. round_report.json previously recorded ONLY the
    # per-family e1 payloads, so nothing on disk said which exit geometry the
    # underlying book was built with. An audit that searched these reports for
    # the harness flags therefore came back "no --tp-r" for every round — a
    # TRUE-looking answer produced by a file that records no args at all, and
    # it agreed with the auditor's prior. The exit-reason distribution of the
    # emitted trades was the only thing that could actually settle it
    # (BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP). A round is now
    # self-describing on the one parameter that decides whether its verdict
    # transfers to production.
    # ...BUT THE STAMP MUST DESCRIBE WHAT RAN, NOT WHAT WAS ASKED FOR.
    #
    # `base_args` applies `--tp-cap-pct` ONLY when the leg's family is in
    # `LIVE_TP_CAPPED_FAMILIES` ({donchian, pullback, fade, squeeze}), because
    # only those live units carry `_TP_SENTINEL_CAP_PCT` — verified 2026-08-14:
    # `grep -c _TP_SENTINEL_CAP_PCT src/units/strategies/ict_scalp.py` -> 0,
    # against 4 for `trend_donchian.py`. So withholding the cap from a scalp
    # round is CORRECT; the live scalp unit does not clamp.
    #
    # The defect was the label. This block read the RUN-LEVEL flag and stamped
    # `live_parity` for every leg regardless of family, so a scalp round would
    # self-report a geometry its harness never received — `diagnostic-
    # provenance-guard` sub-class A, in the one field whose entire job is to
    # tell a reader which geometry produced the numbers. `m20_fleet_exit_sweep`
    # was fixed for exactly this on 2026-08-10 ("THE GEOMETRY THIS LEG ACTUALLY
    # RAN, not the one the run requested") and the fix never reached this
    # sibling driver, which is how a round launched 2026-08-14 came within one
    # relay of writing a false stamp into the committed evidence file.
    #
    # Three states, never collapsed — the distinction between the last two is
    # the whole point, since both run without a cap for OPPOSITE reasons:
    #   live_parity_capped   — cap applied; the live unit clamps
    #   live_parity_uncapped — no cap applied AND the live unit does not clamp,
    #                          so this IS parity for that unit
    #   NO_TAKE_PROFIT       — no cap on a family that DOES clamp live: a book
    #                          production does not run
    capped_fams = {f for f in fams_seen if f in LIVE_TP_CAPPED_FAMILIES}
    uncapped_fams = {f for f in fams_seen if f not in LIVE_TP_CAPPED_FAMILIES}
    if a.tp_cap_pct > 0.0:
        geometry = ("live_parity_capped" if not uncapped_fams
                    else "live_parity_uncapped" if not capped_fams
                    else "MIXED_capped_and_uncapped_families")
    else:
        geometry = ("NO_TAKE_PROFIT" if capped_fams else "live_parity_uncapped")
    meta = {
        "tf": a.tf,
        "legs": [s.strip() for s in a.legs.split(",") if s.strip()],
        "tp_cap_pct": a.tp_cap_pct,
        "tp_geometry": geometry,
        # The families actually seen, so a reader can check the stamp rather
        # than trust it. `cap_applied` is the fact the label is derived from.
        "families": sorted(fams_seen),
        "cap_applied_to_families": sorted(capped_fams),
        "cap_withheld_from_families": sorted(uncapped_fams),
        "target": a.target,
        "features": a.features,
        # ALWAYS stamped, including the 0 default. An absent key would leave
        # an offset-0 round indistinguishable from a round predating the flag,
        # and a dispersion series is only readable if every arm states its own
        # offset — `0` is one of the arms, not the absence of one.
        "fold_offset": a.fold_offset,
    }
    (out / "round_report.json").write_text(json.dumps(
        {"_round_meta": meta, **{k: v for k, v in report.items()}},
        indent=1, default=str))

    # EMIT THE EVIDENCE ROWS THE REPO CAN KEEP.
    #
    # Until 2026-08-14 an exit-head round left NOTHING behind: `--out` is a
    # required, ephemeral trainer directory, so every verdict reached the repo
    # only as PROSE hand-copied into a matrix `ref`. That is why 141 of 376
    # coverage cells rest on refs no guard can check
    # (BL-20260814-CORPUS-AGREEMENT-COUNTS-141-UNCHECKABLE-CELLS-AS-CHECKED),
    # and it is what makes operator decision (d) — "establish the base rate
    # from the corpus" — not executable for this lever.
    #
    # This writes `rounds.jsonl` in the canonical shape of
    # `docs/research/m20-exit-head-rounds.jsonl`, so promoting a round's
    # evidence is a `cat` and an append rather than transcription. The
    # GEOMETRY in particular is written by the same derivation that computed
    # `meta` above — on 2026-08-14 a scalp round had to be hand-corrected from
    # `live_parity` to `live_parity_uncapped` on the way in, and a label a
    # human has to remember to fix is one they will eventually not fix.
    #
    # Field names are read from `per_leg_summary`'s actual output
    # (`oos_trades`, `mean_auc`, `beats_actual_folds`, `beats_hard_folds`),
    # NOT the names the matrix refs happen to print. A first probe guessed
    # `n_oos`/`beats_actual` and got `None` back for every leg — the data was
    # there under other keys, and reading the producer is what settled it.
    rows = []
    for fam_name, payload in report.items():
        for leg, blk in (payload.get("per_leg") or {}).items():
            info = emitted_meta.get(leg) or {}
            rows.append({
                "leg": leg,
                "symbol": info.get("symbol"),
                "tf": a.tf,
                "lever": "exit_head_ml",
                "n_oos": blk.get("oos_trades"),
                "mean_auc": blk.get("mean_auc"),
                "beats_actual": blk.get("beats_actual_folds"),
                "beats_hard": blk.get("beats_hard_folds"),
                "usable_folds": blk.get("usable_folds"),
                "verdict": blk.get("verdict"),
                "tp_cap_pct": a.tp_cap_pct,
                "tp_geometry": geometry,
                "family": info.get("family") or fam_name,
                "prop_sibling": leg.endswith("_prop") or "_prop_" in leg,
                # WHICH TRADES THE E1 BLOCKS WERE CUT OVER — the thing that
                # decides whether two rows here are comparable at all, and the
                # one axis `tp_geometry` does NOT cover.
                #
                # `build_exit_head_dataset.family_of` collapses every
                # *pullback* / *donchian* / `trend_*` leg into ONE family dir,
                # so its blocks are cut over the FAMILY's pooled trades and a
                # per-leg verdict is that leg's slice within them. Scalp legs
                # fall through the branch chain and keep their own name, so
                # their blocks are cut on the leg alone.
                #
                # This is not a nicety: `train_exit_head`'s own `per_leg_note`
                # calls the pooled block "the right unit to TRAIN on and the
                # wrong one to record a verdict from", and
                # `iwm_trend_long_1d`'s matrix cell explicitly DECLINES to
                # grade from a pooled verdict for exactly that reason. Measured
                # 2026-08-14 over the committed evidence file: 17 of 23 rows
                # were pooled and 6 per-leg, with nothing in the schema saying
                # which — so the file invited precisely the comparison the
                # repo already knows is invalid.
                #
                # Derived from the same function the build uses rather than
                # from the round's directory layout, so the two cannot drift.
                "block_unit": ("per_leg" if _family_of(leg) == leg
                               else "family_pooled"),
                "provenance": f"round {out.name}; driver-emitted",
            })
    (out / "rounds.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in rows))
    print(f"evidence rows -> {out / 'rounds.jsonl'} ({len(rows)} rows, "
          f"tp_geometry={geometry})")
    if a.tp_cap_pct <= 0.0:
        print("WARNING: tp_cap_pct=0 — this round's book models NO TAKE-PROFIT "
              "and is NOT live parity. Any verdict from it describes a book "
              "production does not run.", flush=True)
    print("round done ->", out, "| tp_geometry:", meta["tp_geometry"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

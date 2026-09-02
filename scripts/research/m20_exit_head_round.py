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
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, LIVE_TP_CAPPED_FAMILIES, base_args, classify, resolve_data,
    tp_geometry_for)

# IMPORTED, never re-implemented. `block_unit` below must answer "what did the
# BUILD actually group these trades into?", so it has to be the build's own
# predicate — a local copy would be a second definition of the grouping rule,
# free to drift from the one that cuts the blocks. Same reasoning as
# regime_flip_exit delegating to the live gate rather than mirroring it.
sys.path.insert(0, str(REPO / "scripts" / "ml"))
from build_exit_head_dataset import family_of as _family_of  # noqa: E402

sys.path.insert(0, str(REPO))
# Routed through `_heavy_queue`, NOT `src.utils.trainer_heavy_lock` directly.
# The direct import is what this file used, and from a git worktree it resolves
# a lock file under the WORKTREE — a private mutex that serializes against
# nothing while still logging `heavy_lock_acquired`. Measured mid-arm on the
# trainer (#9497): worktree lock HELD, canonical lock FREE, a probe from the
# canonical clone acquired immediately. See _heavy_queue.canonical_lock_file().
from _heavy_queue import take_heavy_queue  # noqa: E402


def sh(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=timeout)


_ACCEPTS_STRATEGY_NAME: dict[str, bool | None] = {}


def interpreter_defect(stderr: str) -> str | None:
    """Name the module the LAUNCHING interpreter is missing, else ``None``.

    A `--help` probe that dies on `ModuleNotFoundError` says nothing about the
    harness and everything about the python that ran it: every harness is
    invoked with `sys.executable`, so the driver's own interpreter decides
    whether they can even import. On the trainer that is
    `.venv/bin/python3` (the docstring's documented invocation) — a bare
    `python3` has no pandas, and `scripts/backtest_trend.py` imports it at
    module level.

    This exists because the message that DIDN'T say so cost a round. Trainer
    relay #9531 launched with bare `python3`, every harness probe returned
    `None`, and both legs were refused with *"could not determine whether
    <harness> supports --strategy-name … fix the harness probe"* — a sentence
    that names a cause no code path tested. The refusal itself was correct
    (unattributable rows are worse than a missing leg); the DIAGNOSIS pointed
    at the harness and the probe, and the actual defect was the command line.
    CLAUDE.md § "Diagnostic provenance" sub-class A prescribes the remedy
    taken here: branch on the actual failure STAGE, do not reword the label.
    """
    m = re.search(r"(?:ModuleNotFoundError|ImportError): No module named "
                  r"['\"]([\w.]+)['\"]", stderr or "")
    return m.group(1) if m else None


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
            missing = interpreter_defect(p.stderr or "")
            if missing:
                print(f"    !! INTERPRETER, NOT HARNESS: {harness} --help died "
                      f"on `No module named '{missing}'` under "
                      f"{sys.executable}. Every harness runs with this same "
                      f"interpreter, so NO leg can run — relaunch the round "
                      f"with the venv python (.venv/bin/python3, per this "
                      f"file's docstring). Nothing is wrong with {harness}.",
                      flush=True)
            else:
                print(f"    !! {harness} --help exited {p.returncode} — cannot "
                      f"determine attribution support. stderr: "
                      f"{(p.stderr or '')[-200:]}", flush=True)
    _ACCEPTS_STRATEGY_NAME[harness] = verdict
    return verdict


def empty_round_reason(n_legs: int, n_invoked: int, n_failed: int) -> str:
    """Why did this round emit nothing? The three causes, never collapsed.

    `n_invoked` is legs that actually reached their harness; `n_failed` is how
    many of those returned non-zero. The distinction the old single message
    could not carry: a round where NO leg reached a harness has not measured
    anything, and reporting it as "no emitted trades" invites the reader to
    conclude the strategies produced no trades — a clean negative that was
    never observed.
    """
    if n_invoked == 0:
        return (f"NOTHING RAN: all {n_legs} leg(s) were skipped before their "
                f"harness was invoked, so this round MEASURED NOTHING — it is "
                f"not evidence that these legs produce no trades. Read the "
                f"SKIP lines above for the cause and re-run; do not record "
                f"this as an empty result.")
    if n_failed == n_invoked:
        return (f"NOTHING RAN CLEANLY: {n_invoked} of {n_legs} leg(s) reached "
                f"their harness and ALL {n_failed} failed, so no trade "
                f"population was observed. See the HARNESS FAIL lines.")
    return (f"no emitted trades: {n_invoked} of {n_legs} leg(s) ran "
            f"({n_failed} failed) and the ones that succeeded produced zero "
            f"trades in this window — a real empty result, nothing to build.")


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
    ap.add_argument("--total-sort", action="store_true",
                    help="pass through to train_exit_head: break entry-time "
                         "ties by trade_key so the fold partition does not "
                         "depend on the order --legs was given. Recorded in "
                         "_round_meta AND on every emitted row, because a "
                         "re-measured round and a legacy one that do not say "
                         "which convention produced them are not "
                         "distinguishable — which is the defect this flag "
                         "exists to end "
                         "(BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER).")
    ap.add_argument("--fold-offset", type=int, default=0,
                    help="pass through to train_exit_head: shift where "
                         "trade-blocking starts, at FIXED block size, so a "
                         "verdict's dependence on WHERE the fold boundaries "
                         "fall can be measured. Recorded in _round_meta, "
                         "because N rounds at N offsets that do not say which "
                         "offset they used are not a dispersion measurement.")
    a = ap.parse_args(argv[1:])

    # ---------------------------------------------------------------------
    # CAPABILITY PRE-FLIGHT — assert the trainer accepts the flags we intend to
    # forward, BEFORE spending an hour producing a book it will then reject.
    #
    # This is the second half of the 2026-08-15 failure and it is a different
    # bug from the unchecked returncode below. The trainer VM re-checks out this
    # repo from origin/main roughly every 15 minutes. `--fold-offset` lives only
    # on a research branch, so a reset landing between the driver's checkout and
    # its train invocation removes the flag from train_exit_head.py while THIS
    # file still passes it. Measured twice: a 2h arm burned 73 minutes of emit +
    # build and a 5m arm burned 73 minutes, each dying at the very last step on
    # `unrecognized arguments: --fold-offset N`.
    #
    # A file-hash gate CANNOT catch this. The screen harness hashed both files
    # at ARM START and passed, because the reset arrived afterwards; hashing at
    # the end tells you only that the run was void, and by then the hour is
    # spent. Asking the trainer whether it accepts the flag AT THE MOMENT OF USE
    # is the check that actually holds -- and doing it here, before the emit
    # loop, converts a 73-minute silent loss into a two-second loud one.
    #
    # Scoped to flags we would actually pass: `--fold-offset 0` is never
    # forwarded (see the `if a.fold_offset:` guard at the train call), so a
    # control arm must NOT be blocked by a trainer that lacks the flag. That
    # asymmetry is real and load-bearing -- it is why the off0 arm of the dead
    # 5m round produced rows while off4 produced none.
    trainer = REPO / "scripts/ml/train_exit_head.py"
    forwarded = ["--fold-offset"] if a.fold_offset else []
    if forwarded:
        probe = sh([sys.executable, str(trainer), "--help"], timeout=120)
        if probe.returncode != 0:
            print(f"PRE-FLIGHT FAILED: `{trainer.name} --help` exited "
                  f"{probe.returncode}, so this round cannot establish whether "
                  f"the trainer accepts {forwarded}. Refusing to start rather "
                  f"than discovering it after the build.\n"
                  f"{(probe.stderr or '')[-600:]}", flush=True)
            return 3
        # WORD-BOUNDARY MATCH, not `flag in help_text`. A bare substring test
        # reports a capability the trainer does not have: `--fold-offset` is a
        # substring of `--fold-offsets`, of `--fold-offset-mode`, and of any
        # rename. Caught on 2026-08-15 by the negative test for this very
        # pre-flight -- the test renamed the trainer's argument to
        # `--fold-offset-REMOVED-BY-SIMULATED-RESET` and the substring check
        # printed "pre-flight OK" against a trainer that would have rejected the
        # flag. The check is only worth having if it can fail, and a probe that
        # answers a question adjacent to the one asked is CLAUDE.md
        # § "Diagnostic provenance" sub-class A inside the guard itself.
        help_text = probe.stdout or ""
        missing = [f for f in forwarded
                   if not re.search(re.escape(f) + r"(?![\w-])", help_text)]
        if missing:
            print(f"PRE-FLIGHT FAILED: {trainer} does not accept "
                  f"{', '.join(missing)}. On the trainer VM this is almost "
                  f"always the ~15-min `Reset to origin/main` having replaced a "
                  f"branch-only trainer while this driver still forwards the "
                  f"flag. Re-check out the branch and re-launch. Nothing has "
                  f"been built, so nothing is wasted.", flush=True)
            return 3
        print(f"pre-flight OK: trainer accepts {', '.join(forwarded)}",
              flush=True)

    # ---------------------------------------------------------------------
    # TAKE THE TRAINER HEAVY-JOB QUEUE. Required by
    # docs/claude/trainer-resource-protocol.md § Rule 1, and it stops this round
    # contending with other heavy jobs on the 6 GB box.
    #
    # 🔴 IT DOES NOT STOP THE ~15-MIN RESET, and an earlier version of this
    # comment claimed it did. CORRECTED 2026-08-15T16:05Z against measurement.
    # There are TWO reset paths, not one:
    #   * run_training_cycle.sh:138 — inside this lock, ~daily. The lock DOES
    #     hold this one off.
    #   * scripts/ops/trainer_git_sync.sh via ict-trainer-git-sync.timer,
    #     OnUnitActiveSec=15min — DELIBERATELY LOCK-FREE. Its own header: "a
    #     tiny, frequent, lock-free force-sync so 'keep the code current' can
    #     never be blocked by 'run training'" (BL-20260718-TRAINER-GITSYNC-STALE:
    #     gating sync behind the heavy lock once left the trainer 495 commits
    #     behind and froze the forecast producer). That is the one whose cadence
    #     matches, and no lock can stop it — correctly so.
    # PROOF, not inference: the 5m screen's off0 arm ran the whole 74 min under
    # a held lock and its AFTER hashes were 6f6458ac22d8 / 08541341e093, which
    # are byte-identical to origin/main's copies of this file and of
    # train_exit_head.py. off0 survived only because `--fold-offset 0` is falsy
    # and never forwarded.
    #
    # SO: a branch-only research run CANNOT be protected by this lock. Run it
    # from a git worktree (which `git checkout -B main` in the main worktree
    # cannot touch), or land the flag on main. Do NOT mask the timer — it exists
    # to prevent a worse failure. The capability pre-flight above remains the
    # thing that makes the residual race cheap, not this.
    #
    # `docs/claude/trainer-resource-protocol.md` § Rule 1 is binding: every
    # memory-heavy job on the 6 GB trainer takes ONE shared blocking lock, and
    # the three timer wrappers take it. What that buys is CONTENTION control --
    # this round no longer runs beside a 4 GB sibling on a 6 GB box. What it
    # does NOT buy is protection from the reset, per the correction above: the
    # `run_training_cycle.sh` checkout is inside the lock, but the 15-min
    # `trainer_git_sync.sh` checkout is not, and the 15-min one is the one that
    # voided the arms.
    #
    # This round took the queue nowhere at all before the change below, so it
    # had neither. The `ml` CLI's enforced backstop (src/utils/
    # trainer_heavy_lock.py, wired in ml/cli.py) does not cover it either --
    # that fires for `python -m ml train|build-dataset`, and this driver shells
    # out to `scripts/ml/train_exit_head.py`, which is not the CLI. The whole
    # M20 exit-head path (this driver, train_exit_head.py, m20_fleet_exit_sweep)
    # sits outside both halves of the enforcement.
    #
    # It also explains the 2026-08-15T05:33Z slowdown: identical arms differing
    # only in `--fold-offset` took 7.9 / 15.9 / 20.5 / 25.8+ min as an
    # unqueued 4.08 GB `replay_pregate_fleet.py` swap-thrashed the box. Arms
    # that cannot cost different amounts of time DID, which is contention.
    #
    # ORDERING IS DELIBERATE: pre-flight FIRST, lock SECOND. A missing flag
    # should fail in two seconds, not after waiting up to an hour in a queue to
    # discover the trainer would have rejected it anyway.
    #
    # Inert off the trainer VM (the helper gates on the role marker), so this is
    # a no-op in CI, in a sandbox, and on the live VM. A clean queue timeout
    # raises SystemExit(75) -- "the box is genuinely busy", which is the queue
    # working, not a failure. The handle is bound to a name so the flock lives
    # for the whole process; the helper also exports TRAINER_HEAVY_LOCK_HELD=1
    # so the backtest/build/train subprocesses below skip re-acquiring it
    # instead of deadlocking against their own parent.
    _heavy_lock = take_heavy_queue("m20_exit_head_round")  # noqa: F841

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
    # An empty round has THREE causes and they demand opposite responses, so
    # they are counted apart rather than collapsed into "nothing to build":
    # no leg ever reached a harness (we never looked — fix the invocation),
    # harnesses ran and failed, or harnesses ran cleanly and the strategies
    # genuinely produced no trades in the window (the only real negative).
    # Relay #9531 printed the one message for the first case, which is
    # CLAUDE.md § "Diagnostic provenance" sub-class C — an empty result
    # reading as a clean answer.
    n_invoked = 0
    n_harness_failed = 0
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
        n_invoked += 1
        p = sh([sys.executable, REPO / FAMILY_HARNESS[fam], *args,
                "--emit-trades", emit, "--json", "/tmp/eh_round_cell.json"])
        if p.returncode != 0:
            n_harness_failed += 1
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
        print(empty_round_reason(len(a.legs.split(",")), n_invoked,
                                 n_harness_failed))
        return 1

    build_cmd = [sys.executable, REPO / "scripts/ml/build_exit_head_dataset.py",
                 "--tf", a.tf, "--out", out,
                 "--instruments", REPO / "config/instruments.yaml"]
    for e in emits:
        build_cmd += ["--trades", e]
    for sym, path in candles.items():
        build_cmd += ["--candles", f"{sym}={path}"]
    if a.db:
        # SCOPE THE LIVE ARM TO THIS ROUND'S OWN LEGS. Unscoped, the builder
        # loads every strategy-attributed closed trade in the journal and
        # `family_of()` buckets same-symbol siblings into families this round
        # never named — measured 2026-08-13 (relays #8854/#8855): a scalp round
        # produced `donchian {live: 3}` and `pullback {live: 6}` while the leg
        # it was grading read live=0, and the 0-harness families it invented
        # then crashed training. `--legs` is a no-op without `--db`, so this is
        # the only place it needs to be passed
        # (BL-20260813-EXIT-HEAD-LIVE-ARM-DROPPED-ON-NO-CANDLES defect 1).
        build_cmd += ["--db", a.db, "--legs", a.legs]
    p = sh(build_cmd, timeout=21600)
    print(p.stdout[-2000:], p.stderr[-500:], flush=True)
    if p.returncode != 0:
        return 1

    report = {}
    # WHY THIS LIST EXISTS. The dataset-build subprocess above is checked
    # (`if p.returncode != 0: return 1`); the TRAINING subprocess below was not,
    # and control fell straight through to `if e1.exists()`. So a training run
    # that died left `report` empty, the round wrote a ZERO-ROW rounds.jsonl,
    # and `main` returned 0 — a failed arm reporting success.
    #
    # Measured twice on 2026-08-15, both times because the trainer's ~15-min
    # `Reset to origin/main` removed a branch-only flag MID-ARM and argparse
    # rejected it (`unrecognized arguments: --fold-offset 4`):
    #   * 2h round arm off12 — 73 min of emit+build, then 0 rows, exit 0.
    #   * 5m round arm off4  — identical, while arm off0 of the same round
    #     produced 3 rows (offset 0 is FALSY, so that arm never passes the flag
    #     and needs no branch code — which is exactly why the failure read as a
    #     partial success rather than a broken run).
    # Both were caught only by an external row-count assertion; `exit=0` and a
    # `[ -f ]` existence check BOTH passed on a dead arm.
    #
    # Deliberately NOT `return 1` on the first failure, unlike the build step: a
    # 3-leg round whose second leg fails should still train the third, and the
    # operator wants the partial evidence. What must not happen is REPORTING
    # SUCCESS. So failures are collected, named on stdout, recorded in
    # `round_report.json`, and turned into a non-zero exit at the end.
    train_failures: list[dict] = []
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
        if a.total_sort:
            train_cmd += ["--total-sort"]
        p = sh(train_cmd, timeout=21600)
        print(p.stdout[-3000:], p.stderr[-500:], flush=True)
        e1 = fam_dir / "e1_report.json"
        if p.returncode != 0:
            # Report the FAILURE, not merely the absence of a report file —
            # "no e1_report.json" and "training exited 3" are different facts
            # and a reader must not have to infer the second from the first.
            train_failures.append({"family": fam_dir.name,
                                   "returncode": p.returncode,
                                   "stderr_tail": (p.stderr or "")[-400:]})
            print(f"!! TRAINING FAILED for {fam_dir.name}: exit "
                  f"{p.returncode}. This family contributes NO evidence rows.",
                  flush=True)
            continue
        if e1.exists():
            try:
                report[fam_dir.name] = json.loads(e1.read_text())
            except json.JSONDecodeError:
                pass
        else:
            # Exited 0 and still wrote nothing: a third state, distinct from
            # both success and a non-zero exit. Recorded rather than silently
            # skipped, for the same reason as above.
            train_failures.append({"family": fam_dir.name, "returncode": 0,
                                   "stderr_tail": "exited 0 but wrote no "
                                                  "e1_report.json"})
            print(f"!! {fam_dir.name}: training exited 0 but produced no "
                  f"e1_report.json. No evidence rows from this family.",
                  flush=True)
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
    #
    # EXTRACTED 2026-08-16 to `m20_fleet_exit_sweep.tp_geometry_for`, unchanged,
    # at the moment `m20_flip_replay_sweep.py` needed the same answer. Two
    # copies of this derivation is the shape that produced the defect the
    # comment above describes — the fleet sweep was fixed on 2026-08-10 and the
    # fix never reached this sibling.
    capped_fams = {f for f in fams_seen if f in LIVE_TP_CAPPED_FAMILIES}
    uncapped_fams = {f for f in fams_seen if f not in LIVE_TP_CAPPED_FAMILIES}
    geometry = tp_geometry_for(fams_seen, a.tp_cap_pct)
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
        # ALWAYS stamped, including False. Same reasoning as fold_offset above:
        # a round that does not state its tie-break convention cannot be told
        # apart from one measured under the other, and the whole migration
        # depends on telling them apart.
        "total_sort": bool(a.total_sort),
        # A round that lost families must SAY SO on disk. Without this, the
        # artifact of a partial round is indistinguishable from a complete one
        # whose legs happened to be fewer — the reader would have to know the
        # expected leg count from somewhere else to notice. Empty list on a
        # clean round, so "no failures" is stated rather than implied by
        # absence.
        "train_failures": train_failures,
        "families_trained": sorted(report),
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
                # THE ORDERED LEG SET THIS ROW'S MODEL WAS TRAINED OVER.
                # BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER.
                #
                # Order, not just membership, because the order is load-bearing:
                # `--legs` order becomes the row order in `rows.jsonl`
                # (build_exit_head_dataset.py:583,634,730), which becomes the
                # tie-break in `sorted(h_trades.items(), key=bars[0]["bar_t"])`
                # (train_exit_head.py:518) — and Python's sort is STABLE, so
                # ties inherit it. On a 2h family every leg entering on the same
                # bar carries an IDENTICAL bar_t, so the tie groups span every
                # pooled leg.
                #
                # Measured 2026-08-15: the same 7 legs in two different orders
                # gave identical trade counts (2220), identical rows (71199) and
                # an identical 43x50 fold shape, yet 8 of 43 folds differed, AUC
                # moved up to 0.0331, and two legs LOST a usable fold. That is
                # ~2/3 of the deliberate fold-boundary dispersion the M20 study
                # set out to measure.
                #
                # Recording it does NOT fix it — a total sort key would (see the
                # backlog row's option (a), which changes recorded numbers and so
                # is an operator call). This makes two rows that differ by it
                # DETECTABLE, which they were not: `legs` was stamped only in
                # `round_report.json::_round_meta`, nothing compared it, and the
                # committed evidence row carried nothing at all. Four relays went
                # into re-deriving from the artifacts what this one field states.
                #
                # For a `per_leg` row the pooled set is the leg alone, so the
                # field is `[leg]` rather than null — null would conflate "this
                # row is immune" with "we did not record it", and those are
                # opposite statements.
                "pooled_legs_ordered": (
                    [leg] if _family_of(leg) == leg
                    else [x for x in meta["legs"] if _family_of(x) == fam_name]),
                # Which tie-break convention produced this row. Legacy rows
                # predate the flag and carry False; a row from a re-measured
                # corpus carries True. Without it the two conventions pool
                # invisibly in one evidence file.
                "total_sort": bool(a.total_sort),
                # WHICH FOLD PARTITION THIS ROW WAS MEASURED UNDER.
                # BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET.
                #
                # This is the axis the entire fold-dispersion study VARIES, and
                # it was the one axis the evidence rows did not carry — while
                # `total_sort` and `block_unit`, both stamped right here, are
                # the axes it holds FIXED. `_round_meta` recorded it, so the
                # round knew; the rows did not, and rows are what get
                # consolidated. `m20_consolidate_dispersion_arms.py` exists in
                # part to reach back into each arm's `round_report.json` and
                # staple the offset on afterwards — carrying an explicit
                # `offset_source` of `round_meta` vs `unavailable` precisely
                # because a row whose report is missing can then only be
                # recorded as UNKNOWN. That recovery is correct and stays (it
                # is the only way to read the 234 rows already committed), but
                # it should never have been the primary path.
                #
                # 0 IS A RECORDED VALUE HERE, NOT AN ABSENCE. The train call
                # above deliberately does not forward `--fold-offset 0` (the
                # `if a.fold_offset:` guard — 0 is falsy and the unshifted
                # partition is the trainer's own default), so the control arm
                # never needs the branch-only flag. That asymmetry is real and
                # is exactly why off0 survived the reset that voided its
                # siblings — but it is a fact about the ARGV, not about the
                # measurement, and the row must state which partition produced
                # it either way. Consumers distinguish "control arm" from "we
                # did not record it" by the presence of the key, never by 0.
                "fold_offset": a.fold_offset,
                # HOW WE KNOW THAT OFFSET — three states, never collapsed, the
                # same shape as the consolidator's `offset_source`:
                #   argv                      — read from this run's argument
                #   predates_flag_<commit>    — the producing driver had no
                #                               --fold-offset at all, so the
                #                               partition was necessarily the
                #                               unshifted one (backfill only)
                #   unavailable               — we could not establish it
                # A live round always knows, so it always writes `argv`. The
                # value exists so a backfilled 0 and a measured 0 are not the
                # same claim: one is inferred from the code's history, the other
                # is what was passed. Without it, backfilling the corpus would
                # have produced 33 rows indistinguishable from measured ones.
                "fold_offset_basis": "argv",
                "provenance": f"round {out.name}; driver-emitted",
            })
    # EXISTENCE MUST IMPLY ROWS. A zero-row `rounds.jsonl` is the worst of the
    # three artifacts a dead arm can leave, because it is the one every
    # readiness check believes: `[ -f rounds.jsonl ]` passes, and a readout loop
    # that `sed`s the file prints nothing — so the arm reads as ABSENT rather
    # than as FAILED, and a screen tallying arms loses it silently. (I wrote
    # exactly that loop on 2026-08-15 and it swallowed the off12 arm; the file's
    # mtime was the only thing that proved the arm had run at all.)
    #
    # So an empty round writes a DIFFERENTLY-NAMED marker instead. The three
    # states are then distinguishable from the filesystem alone, with no exit
    # code and no stdout:
    #   rounds.jsonl present  -> the round produced evidence (>= 1 row)
    #   rounds.EMPTY present  -> the round ran and produced NO evidence
    #   neither present       -> the round did not reach the emit step
    if rows:
        (out / "rounds.jsonl").write_text(
            "".join(json.dumps(r, default=str) + "\n" for r in rows))
        print(f"evidence rows -> {out / 'rounds.jsonl'} ({len(rows)} rows, "
              f"tp_geometry={geometry})")
    else:
        (out / "rounds.EMPTY").write_text(json.dumps({
            "reason": ("no per-leg summaries survived; "
                       + (f"{len(train_failures)} family/families failed to "
                          f"train: "
                          + ", ".join(f["family"] for f in train_failures)
                          if train_failures
                          else "every family trained but emitted no gradeable "
                               "per-leg summary")),
            "train_failures": train_failures,
            "families_trained": sorted(report),
            "tp_geometry": geometry,
        }, indent=2) + "\n")
        print(f"!! NO EVIDENCE ROWS. Wrote {out / 'rounds.EMPTY'} instead of a "
              f"zero-row rounds.jsonl, so an existence check on rounds.jsonl "
              f"correctly reads this arm as having produced nothing.",
              flush=True)
    if a.tp_cap_pct <= 0.0:
        print("WARNING: tp_cap_pct=0 — this round's book models NO TAKE-PROFIT "
              "and is NOT live parity. Any verdict from it describes a book "
              "production does not run.", flush=True)
    print("round done ->", out, "| tp_geometry:", meta["tp_geometry"])
    if train_failures:
        # NON-ZERO EXIT IS THE POINT. A caller looping arms records `exit=$?`;
        # returning 0 here is what let two dead arms be written into a
        # dispersion screen as completed ones. The row count is printed above,
        # but a wrapper should not have to parse stdout to learn the run failed.
        print(f"!! ROUND INCOMPLETE: {len(train_failures)} of "
              f"{len(train_failures) + len(report)} families failed to train "
              f"({', '.join(f['family'] for f in train_failures)}). "
              f"rounds.jsonl holds {len(rows)} row(s) and is NOT a complete "
              f"round.", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""A lever that never fired has not passed anything.

`m20_regime_flip_replay.replay` sets `flip_r = actual_r` on a `no_flip` row, so
a fold in which the regime label never flipped has the two series IDENTICAL and
its `beats` test — `flip_net >= actual_net and flip_dd <= actual_dd` — holds
with equality, by construction. The verdict counted such a fold toward `wins`
exactly as if the lever had helped.

Caught mid-sweep on 2026-08-16: the first leg of the fleet re-sweep returned

    trend_donchian (BTCUSDT 1h): PASS wf=6/6 flip%=0.0 net 37.3918 -> 37.3918

a perfect six-of-six walk-forward over a lever that never once acted, on a leg
whose matrix cell is `honest_negative`. Nothing in that line distinguishes it
from a lever that genuinely helped in all six folds, and left alone it lands in
the corpus as a floor-clearing PASS contradicting a live cell — which is exactly
what `matrix-corpus-agreement` escalates for adjudication.

Same class as the inert walk-forward folds
`m20_ack_corpus_disagreements.fold_quality` reports, one level worse: there
inertness was 3 of 6 folds, here it can be the entire verdict.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "scripts/research/m20_regime_flip_replay.py"


def _load():
    spec = importlib.util.spec_from_file_location("m20_regime_flip_replay", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_regime_flip_replay"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


R = _load()


def test_no_flip_really_does_copy_the_actual_return() -> None:
    """The premise. If this stops holding, the rest of this file is moot."""
    src = SRC.read_text()
    assert 'reason = "no_flip"' in src
    i = src.index('reason = "no_flip"')
    assert "r = actual_r" in src[max(0, i - 200):i], (
        "a no_flip row no longer copies actual_r — re-derive the inertness rule")


def test_an_inert_fold_scores_beats_by_construction() -> None:
    """Why `wins` could never be trusted on its own.

    Asserted as the MECHANISM, not as desired behaviour: identical series make
    the `beats` test hold with equality, which is how a fold the lever never
    touched became a free win. The remedy is not to change this comparison —
    it is right for a fold that did fire — but to stop counting an inert fold
    toward PASS at all.
    """
    rows = [{"year": "2024", "actual_r": 1.5, "flip_r": 1.5,
             "flip_reason": "no_flip"},
            {"year": "2024", "actual_r": -0.7, "flip_r": -0.7,
             "flip_reason": "no_flip"}]
    act = R.fold_metrics(rows, "actual_r")
    flp = R.fold_metrics(rows, "flip_r")
    assert (flp["net_total_r"] >= act["net_total_r"]
            and flp["max_drawdown_r"] <= act["max_drawdown_r"]), (
        "identical series no longer compare equal — the premise of this whole "
        "file has changed and the inertness rule needs re-deriving")


# --------------------------------------------------------- the verdict itself

def test_a_never_flipped_run_is_INERT_not_PASS() -> None:
    src = SRC.read_text()
    assert "INERT_NEVER_FLIPPED" in src
    # The guard order matters: inertness is checked BEFORE the win ratio, or a
    # 6/6 of free wins reaches the PASS branch first.
    i_inert = src.index('"INERT_NEVER_FLIPPED" if flipped == 0')
    i_pass = src.index('else "PASS" if usable >= 4')
    assert i_inert < i_pass, "the inert check must come first"


def test_pass_is_decided_on_real_wins_not_total_wins() -> None:
    src = SRC.read_text()
    assert 'else "PASS" if usable >= 4 and real_wins * 3 >= usable * 2' in src, (
        "the PASS criterion is back on `wins`, so inert folds can carry it")


def test_inertness_is_reported_per_fold_and_in_the_rollup() -> None:
    """A reader must be able to check the verdict, not just trust it."""
    src = SRC.read_text()
    for key in ('"flips": n_flips', '"inert": inert',
                '"walkforward_real"', '"inert_folds"'):
        assert key in src, f"missing {key}"


def test_the_legacy_walkforward_string_is_unchanged() -> None:
    """`wins` stays as-was so an existing consumer reads the same number."""
    src = SRC.read_text()
    assert '"walkforward": f"{wins}/{usable}"' in src


# ------------------------------------------------- the downstream contract

def test_the_agreement_guard_does_not_treat_INERT_as_counter_evidence() -> None:
    """The whole reason this matters: an inert PASS would contradict a live cell."""
    spec = importlib.util.spec_from_file_location(
        "_mca", REPO / "scripts/ci/check_matrix_corpus_agreement.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)  # type: ignore[union-attr]
    assert "INERT_NEVER_FLIPPED" not in guard.PASS_VERDICTS
    assert "PASS" in guard.PASS_VERDICTS, "the probe cannot find a positive"


def test_it_still_runs() -> None:
    """Smoke: --help must work, so the edit did not break the entry point."""
    p = subprocess.run([sys.executable, str(SRC), "--help"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-400:]
    assert "--policy-key" in p.stdout

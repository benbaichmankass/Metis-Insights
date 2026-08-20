#!/usr/bin/env python3
"""Is E2's shuffled-label null VALID on this panel — and does that depend on the panel?

`BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER` records two questions
and binds them to be answered SEPARATELY. This module answers the first one:

    (1) WHY IS IT SOL? Four of four discards on one leg is the finding to chase
        first -- compare the negative control's null distribution between the SOL
        and XRP panels at matched horizons; if SOL's control null is systematically
        narrower, the trade-block shuffle is not producing an equally valid null on
        that panel and THAT is a defect in the null, not in the gate.

The measurement is direct rather than inferential. E2 injects **one** negative
control, so a single run yields a single Bernoulli draw and cannot tell 5% bad luck
from a broken null -- which is precisely the ambiguity that produced the backlog
row. This module injects a **bank of K independent noise columns** through the
identical fold/shuffle machinery and reports the EMPIRICAL false-invalidation rate
of each candidate gate bar, per panel. A rate is a measurement; one draw is not.

WHY THIS IS NOT A SECOND IMPLEMENTATION OF E2. The splitter, the block builder and
the shuffle are **imported** from `e2_feature_information`, never re-derived --
the same discipline that module applies to `analyze_exit_head._grouped_purged_folds`
and for the same reason: a second copy is free to drift, and then the diagnostic and
the tool disagree about the null for reasons nobody can locate. What this module adds
is a vectorised scorer for K columns x N replicates, which is an arithmetic
rearrangement of `_corr_from_centered`, asserted equal to it in `--selftest`.

THE MECHANISM UNDER SUSPICION, stated so the measurement can refute it.
`block_shuffled_labels` permutes whole trades' label sequences among trades and
CYCLES a donor block shorter than its recipient (`src[pos % len(src)]`), which also
TRUNCATES a donor longer than its recipient. Both distortions are absent under the
identity assignment -- the observed statistic is computed on undistorted labels
while every null draw is computed on distorted ones. If the distortion narrows the
null, observed statistics clear the (1-alpha) quantile MORE often than alpha, and
the size of the effect scales with how unequal the panel's trade lengths are. That
is a panel-dependent invalidity, and it is testable: measure the rate, and measure
it against a length-matched permutation that has no distortion to make.

Outputs a JSON report per panel. Observe-only, Tier-1: reads a panel file, writes a
report, touches nothing live.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "scripts").is_dir() and (p / "src").is_dir():
            return p
    return here.parent.parent


_ROOT = _repo_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.research.e2_feature_information import (  # noqa: E402
    TARGETS,
    _centered_label_ranks,
    _corr_from_centered,
    _grouped_purged_folds,
    average_ranks,
    block_shuffled_labels,
    load_panel,
    quantile,
    trade_blocks,
)

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is a declared dep of this lane
    np = None


SCHEMES = ("trade_block_cyclic", "length_matched")


# ---------------------------------------------------------------------------
# the length-matched comparison null
# ---------------------------------------------------------------------------


def length_matched_shuffled_labels(
    blocks: Sequence[Sequence[int]],
    labels: Dict[int, float],
    rng: random.Random,
) -> Dict[int, float]:
    """Permute label sequences ONLY among trades of identical length.

    The comparison arm for the suspected defect. Because donor and recipient have
    the same length there is no cycling and no truncation, so the identity
    assignment is not distinguishable from any other -- the exchangeability a
    permutation test needs holds exactly, at the cost of immobilising any trade
    whose length is unique in the panel.

    That cost is REPORTED, never hidden: `mobile_fraction` below says how much of
    the panel this null can actually move. A length-matched null on a panel where
    every trade has a unique length is the identity, which is not a null at all --
    so the fraction is the denominator that makes this arm's rate readable.
    """
    by_len: Dict[int, List[int]] = defaultdict(list)
    for bi, b in enumerate(blocks):
        by_len[len(b)].append(bi)
    out: Dict[int, float] = {}
    for _, group in by_len.items():
        donors = list(group)
        rng.shuffle(donors)
        for recipient, donor in zip(group, donors):
            src, dst = blocks[donor], blocks[recipient]
            for pos, row_i in enumerate(dst):
                out[row_i] = labels[src[pos]]
    return out


def block_length_profile(blocks: Sequence[Sequence[int]]) -> Dict[str, Any]:
    """How unequal are this fold's trade lengths, and how much distortion follows?

    `mobile_fraction` is the share of ROWS sitting in a length class with more than
    one trade -- i.e. the share a length-matched null can move at all. `cv_length`
    is the coefficient of variation of block length: the suspected mechanism's
    severity should scale with it, so it is the covariate the cross-panel
    comparison reads.
    """
    lens = [len(b) for b in blocks if b]
    if not lens:
        return {"n_blocks": 0, "mobile_fraction": None, "cv_length": None}
    n_rows = sum(lens)
    counts = Counter(lens)
    mobile_rows = sum(L * c for L, c in counts.items() if c > 1)
    mean = sum(lens) / len(lens)
    var = sum((L - mean) ** 2 for L in lens) / len(lens)
    return {
        "n_blocks": len(lens),
        "n_rows": n_rows,
        "mean_length": mean,
        "cv_length": (math.sqrt(var) / mean) if mean else None,
        "distinct_lengths": len(counts),
        "mobile_fraction": mobile_rows / n_rows if n_rows else None,
        "max_length": max(lens),
        "min_length": min(lens),
    }


# ---------------------------------------------------------------------------
# vectorised scoring of a bank of noise columns
# ---------------------------------------------------------------------------


def _rank_matrix_centered(mat) -> Tuple[Any, Any]:
    """Row-wise average ranks, centered, with L2 norms. Mirrors `average_ranks`."""
    order = np.argsort(mat, axis=1, kind="stable")
    n = mat.shape[1]
    ranks = np.empty_like(mat, dtype=float)
    rows = np.arange(mat.shape[0])[:, None]
    ranks[rows, order] = np.arange(1, n + 1, dtype=float)[None, :]
    # average-rank tie correction, per row
    for r in range(mat.shape[0]):
        srt = mat[r][order[r]]
        i = 0
        while i < n:
            j = i
            while j + 1 < n and srt[j + 1] == srt[i]:
                j += 1
            if j > i:
                ranks[r, order[r][i:j + 1]] = (i + j + 2) / 2.0
            i = j + 1
    cen = ranks - ranks.mean(axis=1, keepdims=True)
    norm = np.sqrt((cen ** 2).sum(axis=1))
    return cen, norm


def calibrate_panel(
    rows: List[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    target: str,
    n_folds: int = 4,
    embargo_bars: int = 12,
    n_shuffles: int = 1000,
    n_controls: int = 200,
    alpha: float = 0.05,
    seed: int = 20260820,
    min_fold_rows: int = 20,
    scheme: str = "trade_block_cyclic",
) -> Dict[str, Any]:
    """Empirical false-invalidation rate of each candidate gate bar, on this panel.

    Every noise column is independent of everything, so under a VALID null each
    column's observed statistic is exchangeable with its own replicate draws and
    the rate of clearing the pointwise bar is alpha by construction. The measured
    rate is therefore a direct read on the null's validity -- not an inference from
    one draw, which is what the current gate has.
    """
    if np is None:
        raise SystemExit("numpy is required for the control bank")
    if scheme not in SCHEMES:
        raise SystemExit(f"unknown scheme {scheme!r}; expected one of {SCHEMES}")
    shuffler = (
        block_shuffled_labels if scheme == "trade_block_cyclic" else length_matched_shuffled_labels
    )

    usable = [r for r in rows if r.get(target) is not None]
    folds = list(_grouped_purged_folds(usable, n_folds=n_folds, embargo_bars=embargo_bars))
    report: Dict[str, Any] = {
        "step": "E2-null-calibration",
        "target": target,
        "scheme": scheme,
        "symbol": (manifest or {}).get("symbol"),
        "timeframe": (manifest or {}).get("timeframe"),
        "population": {
            "n_rows_total": len(rows),
            "n_rows_with_target": len(usable),
            "n_trades": len({r.get("trade_id") for r in usable}),
        },
        "config": {
            "n_folds": n_folds,
            "embargo_bars": embargo_bars,
            "n_shuffles": n_shuffles,
            "n_controls": n_controls,
            "alpha": alpha,
            "seed": seed,
        },
        "folds_formed": len(folds),
    }
    if not folds:
        report["error"] = "no folds formed"
        return report

    rng = random.Random(seed)
    n_usable = len(usable)
    # The control bank. Independent of the label, of the features, and of each
    # other -- so every column is a fresh draw from the same null the gate reads.
    noise = np.array(
        [[rng.gauss(0.0, 1.0) for _ in range(n_usable)] for _ in range(n_controls)],
        dtype=float,
    )

    true_labels = {i: float(usable[i][target]) for i in range(n_usable)}
    fold_blocks = [trade_blocks(usable, te) for _, te in folds]

    report["block_profile"] = [block_length_profile(b) for b in fold_blocks]
    report["block_profile_pooled"] = block_length_profile([b for fb in fold_blocks for b in fb])

    # Per fold: the control bank's centered ranks, computed ONCE (a label shuffle
    # never moves a feature's own ranks -- the same precompute e2 relies on).
    prep: List[Optional[Tuple[List[int], Any, Any]]] = []
    for k, (_, te) in enumerate(folds):
        idx = [i for i in te if true_labels.get(i) is not None]
        if len(idx) < max(3, min_fold_rows):
            prep.append(None)
            continue
        cen, norm = _rank_matrix_centered(noise[:, idx])
        prep.append((idx, cen, norm))

    def _stats_for(labels: Dict[int, float]):
        """|mean over folds of Spearman| for every control column, at once."""
        acc = np.zeros(n_controls, dtype=float)
        used = np.zeros(n_controls, dtype=float)
        for k, p in enumerate(prep):
            if p is None:
                continue
            idx, cen, norm = p
            lab = _centered_label_ranks(idx, labels)
            if lab is None:
                continue
            cen_l, norm_l = lab
            v = np.asarray(cen_l, dtype=float)
            denom = norm * float(norm_l)
            with np.errstate(divide="ignore", invalid="ignore"):
                corr = np.where(denom > 0, (cen @ v) / np.where(denom > 0, denom, 1.0), 0.0)
            acc += corr
            used += 1.0
        return np.abs(np.where(used > 0, acc / np.where(used > 0, used, 1.0), 0.0)), used

    observed, folds_used = _stats_for(true_labels)

    null = np.empty((n_shuffles, n_controls), dtype=float)
    srng = random.Random(seed + 1)
    for j in range(n_shuffles):
        shuffled: Dict[int, float] = {}
        for blocks in fold_blocks:
            shuffled.update(shuffler(blocks, true_labels, srng))
        null[j], _ = _stats_for(shuffled)

    # Per-column pointwise bar = the (1-alpha) quantile of that column's OWN null,
    # exactly as `_verdict_for` computes it.
    pt_thr = np.quantile(null, 1.0 - alpha, axis=0, method="linear")
    cleared_pointwise = observed > pt_thr
    p_emp = (np.sum(null >= observed[None, :], axis=0) + 1.0) / (n_shuffles + 1.0)

    # The bar the DECISION uses is the max-statistic threshold over the REAL
    # feature family, which this diagnostic does not recompute -- it is read from
    # the matching e2 report when one is supplied, so the two can never disagree.
    report["control_bank"] = {
        "n_controls": n_controls,
        "rate_cleared_pointwise": float(cleared_pointwise.mean()),
        "expected_rate_if_null_valid": alpha,
        "n_cleared_pointwise": int(cleared_pointwise.sum()),
        "p_empirical_mean": float(p_emp.mean()),
        "p_empirical_median": float(np.median(p_emp)),
        # A valid permutation p is Uniform(0,1); its mean is 0.5. A mean BELOW 0.5
        # says observed statistics sit high in their own nulls across the board --
        # i.e. the null is systematically too narrow, which is the suspected defect.
        "p_uniformity_note": "valid null => mean p ~ 0.5, rate_cleared ~ alpha",
        "observed_mean": float(observed.mean()),
        "null_mean": float(null.mean()),
        "null_sd": float(null.std()),
        "observed_over_null_mean_ratio": (
            float(observed.mean() / null.mean()) if null.mean() else None
        ),
        "folds_used_min": int(folds_used.min()) if n_controls else None,
    }
    # Per-column verdicts, so the SAME bank can be cross-tabulated across targets.
    # This is the field that makes the sweep's independence assumption checkable:
    # `inject_controls` seeds from the run seed and the panel row order, so every
    # target on one panel is scored against the SAME noise column -- three runs,
    # one underlying draw. A binomial tail over "24 runs" silently assumes 24.
    report["control_bank"]["cleared_pointwise_by_control"] = [
        int(v) for v in cleared_pointwise
    ]
    report["control_bank"]["p_empirical_by_control"] = [float(v) for v in p_emp]
    # Binomial tail: is the measured rate above alpha by more than sampling noise?
    k = int(cleared_pointwise.sum())
    tail = sum(
        math.comb(n_controls, i) * (alpha ** i) * ((1 - alpha) ** (n_controls - i))
        for i in range(k, n_controls + 1)
    )
    report["control_bank"]["binom_p_rate_above_alpha"] = tail
    report["control_bank"]["verdict"] = (
        "null_miscalibrated" if tail < 0.01 else "null_consistent_with_alpha"
    )
    return report


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    checks: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    if np is None:
        print("numpy missing — cannot self-test the vectorised scorer")
        return 1

    rng = random.Random(11)

    # 1. The vectorised rank/correlation must agree with e2's own scalar path.
    #    This is the whole licence for using a different implementation here.
    n = 240
    cols = np.array([[rng.gauss(0, 1) for _ in range(n)] for _ in range(5)])
    labels = {i: rng.gauss(0, 1) for i in range(n)}
    idx = list(range(n))
    cen, norm = _rank_matrix_centered(cols)
    lab = _centered_label_ranks(idx, labels)
    cen_l, norm_l = lab
    vec = (cen @ np.asarray(cen_l, dtype=float)) / (norm * float(norm_l))
    for r in range(5):
        prep_r = (idx, list(cen[r]), float(norm[r]))
        scalar = _corr_from_centered(prep_r[1], prep_r[2], cen_l, norm_l)
        check(
            f"vectorised_matches_scalar_{r}",
            abs(scalar - float(vec[r])) < 1e-9,
            f"{scalar} vs {float(vec[r])}",
        )

    # 2. Tie handling must match `average_ranks`, or a binary label (label_hold!)
    #    would be scored differently by the two paths.
    tied = np.array([[1.0, 1.0, 2.0, 2.0, 2.0, 3.0]])
    cen_t, _ = _rank_matrix_centered(tied)
    ref = average_ranks([1.0, 1.0, 2.0, 2.0, 2.0, 3.0])
    ref_cen = [v - sum(ref) / len(ref) for v in ref]
    check(
        "tie_ranks_match_average_ranks",
        max(abs(a - b) for a, b in zip(list(cen_t[0]), ref_cen)) < 1e-9,
        f"{list(cen_t[0])} vs {ref_cen}",
    )

    # 3. The length-matched null must never cycle or truncate: every recipient row
    #    receives a label from a DISTINCT donor position.
    blocks = [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9], [10, 11, 12, 13]]
    labs = {i: float(i) for i in range(14)}
    out = length_matched_shuffled_labels(blocks, labs, random.Random(3))
    check("length_matched_covers_all_rows", len(out) == 14, str(len(out)))
    check(
        "length_matched_preserves_multiset",
        sorted(out.values()) == sorted(labs.values()),
        "a length-matched permutation must be a bijection on labels",
    )

    # 4. ...whereas the cyclic scheme demonstrably is NOT a bijection when block
    #    lengths differ. This is the defect under test, asserted so a future
    #    'tidy-up' of the shuffle cannot silently remove the evidence.
    saw_non_bijection = False
    for s in range(40):
        o = block_shuffled_labels(blocks, labs, random.Random(s))
        if sorted(o.values()) != sorted(labs.values()):
            saw_non_bijection = True
            break
    check(
        "cyclic_scheme_is_not_a_bijection_on_unequal_blocks",
        saw_non_bijection,
        "expected label multiset distortion from cycling/truncation",
    )

    # 5. Equal-length blocks: the cyclic scheme has nothing to distort, so the two
    #    schemes must agree that the multiset is preserved. This localises the
    #    defect to length INEQUALITY rather than to block permutation as such.
    eq = [[0, 1], [2, 3], [4, 5], [6, 7]]
    eqlabs = {i: float(i) for i in range(8)}
    ok_eq = all(
        sorted(block_shuffled_labels(eq, eqlabs, random.Random(s)).values())
        == sorted(eqlabs.values())
        for s in range(40)
    )
    check("cyclic_scheme_is_a_bijection_on_equal_blocks", ok_eq,
          "equal lengths => no cycling => no distortion")

    # 6. block_length_profile must report inequality, and report a mobile fraction
    #    that excludes singleton length classes.
    prof = block_length_profile(blocks)
    check("profile_counts_blocks", prof["n_blocks"] == 5, str(prof))
    check("profile_mobile_excludes_singletons",
          abs(prof["mobile_fraction"] - (6 + 4) / 14) < 1e-9, str(prof))

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        if not ok:
            print(f"FAIL {name}: {detail}")
    print(f"e2_null_calibration selftest: {passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--panel")
    p.add_argument("--target", default="forward_r", choices=list(TARGETS))
    p.add_argument("--n-folds", type=int, default=4)
    p.add_argument("--embargo-bars", type=int, default=12)
    p.add_argument("--n-shuffles", type=int, default=1000)
    p.add_argument("--n-controls", type=int, default=200)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=20260820)
    p.add_argument("--scheme", default="trade_block_cyclic", choices=list(SCHEMES))
    p.add_argument("--out")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.panel:
        p.error("--panel is required unless --selftest")

    rows, manifest = load_panel(Path(a.panel))
    rep = calibrate_panel(
        rows, manifest,
        target=a.target, n_folds=a.n_folds, embargo_bars=a.embargo_bars,
        n_shuffles=a.n_shuffles, n_controls=a.n_controls, alpha=a.alpha,
        seed=a.seed, scheme=a.scheme,
    )
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=2, sort_keys=True))
    cb = rep.get("control_bank") or {}
    print(f"{rep.get('symbol')} {a.target} scheme={a.scheme} "
          f"cleared={cb.get('n_cleared_pointwise')}/{cb.get('n_controls')} "
          f"rate={cb.get('rate_cleared_pointwise')} (alpha={a.alpha}) "
          f"binom_p={cb.get('binom_p_rate_above_alpha')} -> {cb.get('verdict')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

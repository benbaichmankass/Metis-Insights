"""The acknowledgement drafter must carry the CAVEATS, not just the headline.

`matrix-corpus-agreement`'s remedy is to append the contradicting measurement to
the cell's `ref`. Doing that by hand was fine at n=2 and stopped being fine
within the hour, when the next sweep put ten more legs in the same position.

The risk in automating it is not the arithmetic — it is that a generated ref
reads as authoritative while quietly dropping the part that changes the
conclusion. On the run that motivated this, three such parts existed:

  * `slv_pullback_1d`'s "5/6" walk-forward was **2 real wins against 1 real
    loss with three INERT folds** — folds where the lever never fired, which
    still count `ok`;
  * that same cell was **Path B, not Path A**, with an OOS net_R gain of
    `+0.001` — arithmetically nonzero and economically nothing; and
  * the row was cut at a **per-leg derived split**, so it is a different
    partition from the evidence it contradicts, not a rerun of it.

A drafter that emits the verdict and omits those produces a ref that argues the
opposite of what the data says. So what is pinned here is that each caveat
appears when the data warrants it, and — the other half — that it does NOT
appear when it does not, since a boilerplate caveat on every cell is noise that
trains the reader to skip it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "m20_ack_drafter", REPO / "scripts/research/m20_ack_corpus_disagreements.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_ack_drafter"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ACK = _load()

# slv_pullback_1d / stale12_lt0R, verbatim from the 2026-08-15 22:24Z run --
# the row whose headline most overstates it.
WEAK = {
    "leg": "slv_pullback_1d", "lever": "stale_stop", "cell": "stale12_lt0R",
    "verdict": "path_b_wf_pass", "split": "2022-11-29",
    "run_id": "2026-08-15T22:24:00.788190+00:00",
    "wf_summary": "5/6", "base_trades_OOS": 33,
    "is_oos_pass": False, "rate_ok_OOS": False, "gate_reason_OOS": "maxdd_worse",
    "d_net_r_IS": 0.3572, "d_net_r_OOS": 0.001,
    "d_max_dd_IS": -0.5677, "d_max_dd_OOS": 0.782,
    "wf_folds": [
        {"fold": "2021", "d_net_r": 0.0, "d_max_dd": 0.0, "ok": True},
        {"fold": "2022", "d_net_r": -0.0506, "d_max_dd": 0.2812, "ok": False},
        {"fold": "2023", "d_net_r": 0.2932, "d_max_dd": -0.2932, "ok": True},
        {"fold": "2024", "d_net_r": 0.0, "d_max_dd": 0.0, "ok": True},
        {"fold": "2025", "d_net_r": 0.7831, "d_max_dd": 0.0589, "ok": True},
        {"fold": "2026", "d_net_r": 0.0, "d_max_dd": 0.0, "ok": True},
    ],
}

# sol_pullback_2h / gb1R_afterMFE1R -- a genuine Path-A PASS with NO inert fold.
STRONG = {
    "leg": "sol_pullback_2h", "lever": "giveback_stop", "cell": "gb1R_afterMFE1R",
    "verdict": "PASS", "split": "2025-08-23",
    "run_id": "2026-08-15T22:22:59.613854+00:00",
    "wf_summary": "5/6", "base_trades_OOS": 34, "is_oos_pass": True,
    "d_net_r_IS": 12.0273, "d_net_r_OOS": 1.6526,
    "d_max_dd_IS": -1.6039, "d_max_dd_OOS": -0.5198,
    "wf_folds": [
        {"fold": "2021", "d_net_r": 1.8597, "d_max_dd": -0.8887, "ok": True},
        {"fold": "2022", "d_net_r": -0.8639, "d_max_dd": 0.2972, "ok": False},
        {"fold": "2023", "d_net_r": 7.6491, "d_max_dd": -0.9957, "ok": True},
        {"fold": "2024", "d_net_r": 0.977, "d_max_dd": -0.8198, "ok": True},
        {"fold": "2025", "d_net_r": 3.8578, "d_max_dd": -0.9801, "ok": True},
        {"fold": "2026", "d_net_r": 2.8115, "d_max_dd": -0.5198, "ok": True},
    ],
}

HIT = {"leg": "x", "lever": "y", "status": "honest_negative", "verdict": "PASS"}


# --------------------------------------------------- fold arithmetic is right

def test_inert_folds_are_counted_apart_from_real_wins() -> None:
    assert ACK.fold_quality(WEAK) == (5, 2, 3)
    assert ACK.fold_quality(STRONG) == (5, 5, 0)


def test_the_draft_refuses_to_let_5_of_6_stand_alone() -> None:
    """The number that misleads must not appear without its correction."""
    seg = ACK.draft(HIT, WEAK)
    assert "5/6" in seg
    assert "THE WIN TOTAL IS NOT A COUNT OF WINS" in seg
    assert "2 real win" in seg
    for yr in ("2021", "2024", "2026"):
        assert yr in seg, f"inert fold {yr} not named"


def test_a_clean_walk_forward_gets_NO_inert_caveat() -> None:
    """Boilerplate trains readers to skip caveats; absence has to mean something."""
    seg = ACK.draft(HIT, STRONG)
    assert "THE WIN TOTAL IS NOT A COUNT OF WINS" not in seg
    assert "inert" not in seg.lower()


# ------------------------------------------------------- Path B is called out

def test_path_b_is_named_with_its_own_numbers() -> None:
    seg = ACK.draft(HIT, WEAK)
    assert "PATH-B, NOT PATH A" in seg
    assert "is_oos_pass=False" in seg
    assert "maxdd_worse" in seg
    assert "0.001" in seg, "the economically-nothing OOS gain is missing"


def test_a_path_a_pass_is_not_labelled_path_b() -> None:
    assert "PATH-B" not in ACK.draft(HIT, STRONG)


# ------------------------------------------------- partition, not a rerun

def test_the_derived_split_is_stated_as_a_different_partition() -> None:
    seg = ACK.draft(HIT, WEAK)
    assert "2022-11-29" in seg
    assert "DIFFERENT PARTITION, NOT A RERUN" in seg


# ------------------------------------------------ the contract with the guard

def test_every_draft_contains_the_phrase_the_guard_matches() -> None:
    """If this drifts, the drafter silently stops clearing the finding."""
    import re
    guard = ACK._guard()
    for row in (WEAK, STRONG):
        assert guard.ACK.search(ACK.draft(HIT, row)), "guard would not accept the draft"
    assert isinstance(guard.ACK, re.Pattern)


def test_the_draft_never_claims_the_status_changed() -> None:
    """The one thing this tool must never imply it did."""
    for row in (WEAK, STRONG):
        seg = ACK.draft(HIT, row)
        assert "NOT FLIPPED" in seg
        assert "Tier-3" in seg


def test_it_writes_nothing_without_apply(tmp_path: Path) -> None:
    """Dry-run must be genuinely dry -- asserted on bytes, not on the log line."""
    m = json.loads((REPO / "docs/research/exit-refinement-coverage.json").read_text())
    p = tmp_path / "matrix.json"
    p.write_text(json.dumps(m))
    before = p.read_bytes()
    ACK.main(["prog", "--matrix", str(p)])
    assert p.read_bytes() == before

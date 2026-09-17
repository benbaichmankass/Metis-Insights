"""Controls for the kill-case admissibility gate.

Each test below is a defect this gate must catch, and each was verified by
PLANTING that defect and watching this file fail.

The gate answers one question: does a leg's HEADLINE loss survive restriction
to the rows anyone can stand behind (`totalPnlMeasured`, which sums MEASURED +
ESTIMATED and excludes fabricated and unverified)? It never proposes a
disposition — killing a leg is Tier-3.
"""
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "kill_case_admissibility",
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts" / "research" / "kill_case_admissibility.py",
)
K = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(K)


def row(**kw):
    base = {"name": "x", "trades": 10, "pnlMeasuredCount": 5, "pnlEstimatedCount": 5,
            "totalPnl": -100.0, "totalPnlMeasured": -80.0, "pnlCoverage": 0.5}
    base.update(kw)
    return base


def test_a_loss_that_survives_restriction_is_distinct_from_one_that_inverts():
    """The whole point. A headline loss whose admissible part is a GAIN must not
    grade the same as one whose admissible part is also a loss — the first
    cannot carry a kill argument and the second can."""
    assert K.grade(row())[0] == "loss_survives"
    assert K.grade(row(totalPnlMeasured=+120.0))[0] == "loss_inverts"


def test_zero_admissible_rows_is_we_could_not_look_not_a_verdict_on_the_loss():
    """`no_admissible_rows` must never be folded into `loss_inverts`. Folding
    them would assert the loss is unreal, which is a different and unsupported
    claim — the honest reading is that nothing here can be graded."""
    v, terms = K.grade(row(pnlMeasuredCount=0, pnlEstimatedCount=0, totalPnlMeasured=0.0))
    assert v == "no_admissible_rows"
    assert v != "loss_inverts"
    assert terms["admissible_n"] == 0


def test_an_absent_leg_is_absent_never_a_zero_pnl_reading():
    """A leg with no row in a block had zero closes. Rendering that as a
    0.0 PnL would put a flat, healthy-looking number where there is no
    observation at all."""
    v, terms = K.grade(None)
    assert v == "absent_from_block"
    assert terms == {}


def test_the_n_floor_is_checked_before_the_sign():
    """A 2-row cell must report insufficient_admissible_n, its real reason,
    rather than a sign one row could flip."""
    assert K.grade(row(pnlMeasuredCount=1, pnlEstimatedCount=1))[0] == "insufficient_admissible_n"
    assert K.grade(row(pnlMeasuredCount=1, pnlEstimatedCount=1,
                       totalPnlMeasured=+120.0))[0] == "insufficient_admissible_n"


def test_a_gain_whose_admissible_part_is_a_loss_has_its_own_verdict():
    """The mirror case — a leg that looks fine and is not. Collapsing it into
    agrees_positive would hide the more dangerous direction."""
    assert K.grade(row(totalPnl=+100.0, totalPnlMeasured=-20.0))[0] == "gain_inverts"
    assert K.grade(row(totalPnl=+100.0, totalPnlMeasured=+80.0))[0] == "agrees_positive"


def test_a_missing_admissible_total_is_not_read_as_zero():
    """`totalPnlMeasured: None` means the sum was not produced. Treating it as
    0.0 would grade every loss as inverting."""
    v, terms = K.grade(row(totalPnlMeasured=None))
    assert v == "no_admissible_rows"
    assert terms["unmeasured_pnl"] is None


def test_unmeasured_pnl_is_the_residual_the_untrustworthy_rows_carry():
    assert K.grade(row())[1]["unmeasured_pnl"] == -20.0
    assert K.grade(row(totalPnl=-6107.0, totalPnlMeasured=-4214.86))[1]["unmeasured_pnl"] == -1892.14


def test_unmeasured_count_cannot_go_negative_on_an_inconsistent_payload():
    """A row claiming more measured+estimated than trades is malformed; the
    gate must not emit a negative count that a reader would take as a real
    quantity."""
    assert K.grade(row(trades=2, pnlMeasuredCount=5, pnlEstimatedCount=5))[1]["unmeasured_n"] == 0


def test_the_top_level_block_is_discovered_because_it_is_the_real_money_book():
    """Reading only the named sub-blocks would silently drop real money, which
    is the only book where a kill decision spends actual funds."""
    bl = K.blocks_of({"perStrategy": [{"name": "a"}],
                      "paper": {"perStrategy": []},
                      "notablock": {"x": 1}})
    assert "real_money(top)" in bl
    assert "paper" in bl
    assert "notablock" not in bl


def test_the_report_carries_a_positive_control_so_an_empty_read_cannot_pass():
    """A block that returned nothing and a leg that simply did not trade look
    identical unless the reader is told how many OTHER strategies came back."""
    res = K.assess({"window": "w", "since": "s", "perStrategy": [row(name="L")]}, ["L", "absent"])
    text = "\n".join(K.report(res))
    assert "POSITIVE CONTROL" in text
    assert res["control"]["real_money(top)"] == 1
    assert "absent — 0 closes" in text


def test_an_entirely_empty_read_is_called_a_read_failure():
    text = "\n".join(K.report(K.assess({"window": "w", "since": "s",
                                        "paper": {"perStrategy": []}}, ["L"])))
    assert "READ FAILURE" in text


def test_loss_inverts_is_loud_and_the_report_says_admissible_is_not_a_recommendation():
    """Two separate duties: the finding must be impossible to skim past, and the
    gate must not read as a licence to kill anything."""
    text = "\n".join(K.report(K.assess(
        {"window": "w", "since": "s",
         "perStrategy": [row(name="L", totalPnlMeasured=+120.0)]}, ["L"])))
    assert "THE DEFENSIBLE RECORD IS A GAIN" in text
    assert "NOT A RECOMMENDATION" in text


def test_every_verdict_is_in_the_declared_vocabulary():
    cases = [None, row(), row(totalPnlMeasured=+1.0), row(totalPnl=+1.0),
             row(totalPnl=0.0, totalPnlMeasured=0.0),
             row(pnlMeasuredCount=0, pnlEstimatedCount=0),
             row(pnlMeasuredCount=1, pnlEstimatedCount=1)]
    assert all(K.grade(c)[0] in K.VERDICTS for c in cases)


def test_the_min_admissible_floor_is_overridable_so_the_choice_is_visible():
    """MIN_ADMISSIBLE is a CHOSEN number, not a tuned one. It must be a
    parameter so a reader can see what the verdict depends on."""
    r = row(pnlMeasuredCount=1, pnlEstimatedCount=1)
    assert K.grade(r, min_admissible=2)[0] == "loss_survives"
    assert K.grade(r, min_admissible=5)[0] == "insufficient_admissible_n"

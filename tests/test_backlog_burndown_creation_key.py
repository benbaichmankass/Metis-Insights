"""The burn-down counted a row as opened only if it spelled the date one way.

`scripts/ops/backlog_append.py` does NOT stamp a creation key — it is on the
caller — so the registers accumulated four spellings of "when this was filed"
and five of "when it was closed", while `backlog_burndown()` read exactly one
of each. Every other row fell out of the series with no trace.

MEASURED 2026-09-12 over ALL 1732 rows in the three backlogs (health 1494,
performance 127, ml 111), shipped read vs corrected read:

    month      SHIPPED op/cl    CORRECTED op/cl
    2026-07        249/175          259/178
    2026-08        536/330          680/338
    2026-09        186/36           444/39
    lifetime      1245/643         1658/657

September's file-to-close ratio is 5.2:1 as shipped and **11.4:1** read
correctly. That is the number the operator reframed the review around on
2026-08-31 ("making sure that we're working correctly to actually get through
the backlog and not just let it grow"), so a review could believe it was
holding the line while the pile grew at twice the rate the instrument showed.

⚠️ THE TWO BIASES POINT OPPOSITE WAYS and neither is "conservative": missing
creation dates UNDER-count opens and flatter the burn-down, missing close dates
UNDER-count closes and damn it. 80 rows carry no creation key at all and 121 of
764 closed rows carry no close date at all, so both had to be PUBLISHED rather
than silently dropped — "we could not tell when" is not "nothing happened that
month", and a series that quietly omits them looks complete and is not.

Run: ``python3 -m pytest tests/test_backlog_burndown_creation_key.py``
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "system_review_checklist.py"


def _load():
    spec = importlib.util.spec_from_file_location("_srchecklist", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CL = _load()
B = CL.backlog_burndown()


# ── NEGATIVE CONTROL FIRST: the scan is not vacuous ─────────────────────────
# Without this the identities below are all satisfiable by a function that
# reads `opened_at` alone and dumps everything else into `undated` — the exact
# shipped behaviour, wearing the new fields.

def test_rows_are_actually_reached_through_the_alternate_spellings():
    basis = B["creation_key_basis"]
    assert basis.get("opened", 0) > 0, (
        f"no row was dated via `opened` — either the register changed shape or "
        f"the vocabulary is not being consulted: {basis}")
    assert basis.get("opened_at", 0) > 0, "the ORIGINAL spelling must still work"
    reachable_only_by_widening = sum(
        basis.get(k, 0) for k in CL.CREATION_KEYS if k != "opened_at")
    assert reachable_only_by_widening > 100, (
        f"measured 413 such rows on 2026-09-12; {reachable_only_by_widening} now")


def test_the_undated_state_is_populated_so_it_is_not_decorative():
    """A state no row reaches is untested. Both were non-zero when measured."""
    assert B["undated"]["opened"] > 0
    assert B["undated"]["closed"] > 0


# ── THE IDENTITIES — what makes `undated` a publication and not a comment ───

def test_every_row_is_either_in_a_month_or_declared_undated():
    assert sum(m["opened"] for m in B["by_month"]) + B["undated"]["opened"] \
        == B["rows_total"], (
        "rows went missing between the census and the series — which is the "
        "whole defect, one level up")


def test_every_closed_row_is_either_in_a_month_or_declared_undated():
    assert sum(m["closed"] for m in B["by_month"]) + B["undated"]["closed"] \
        == B["closed_total"] == B["undated"]["closed_of"]


def test_open_now_is_the_direct_status_census_and_no_date_can_move_it():
    assert B["open_now"] + B["closed_total"] == B["rows_total"]


def test_net_is_still_opened_minus_closed():
    for m in B["by_month"]:
        assert m["net"] == m["opened"] - m["closed"]
        assert m["opened"] >= 0 and m["closed"] >= 0


# ── THE VOCABULARY — widening is the risk, so pin what is in and what is out ─

def test_a_row_dated_only_by_an_alternate_creation_spelling_is_counted():
    for key in ("opened", "filed_at", "date"):
        month, which = CL._row_month({key: "2026-07-20"}, CL.CREATION_KEYS)
        assert (month, which) == ("2026-07", key)


def test_a_row_closed_under_an_alternate_spelling_is_counted():
    for key in ("resolved", "resolved_on", "closed", "superseded_at"):
        month, which = CL._row_month({key: "2026-07-20"}, CL.CLOSE_KEYS)
        assert (month, which) == ("2026-07", key)


def test_opened_at_still_wins_when_a_row_carries_two_spellings():
    """15 health rows carry both. Preference order must be stable, or the
    `creation_key_basis` census reads differently run to run."""
    month, which = CL._row_month(
        {"opened": "2026-01-02", "opened_at": "2026-09-09"}, CL.CREATION_KEYS)
    assert which == "opened_at" and month == "2026-09"


def test_updated_at_is_NOT_a_close_date():
    """138 rows carry it, and it is when the row was last TOUCHED. Folding it in
    would manufacture closures — the one widening that makes the metric LIE in
    the flattering direction."""
    assert "updated_at" not in CL.CLOSE_KEYS
    assert "updated_at" not in CL.CREATION_KEYS


def test_a_non_date_is_not_read_as_a_date():
    for junk in (True, 12, "yes", "2026", "", None, [], {"a": 1}, "20260912"):
        assert CL._row_month({"opened_at": junk}, CL.CREATION_KEYS) == ("", "(none)")


def test_a_row_with_no_recognised_key_reports_that_rather_than_a_month():
    assert CL._row_month({"title": "x"}, CL.CREATION_KEYS) == ("", "(none)")


# ── THE ROW'S OWN CRITERION: "the two numbers must be shown to agree" ───────

def test_open_now_agrees_with_an_INDEPENDENT_status_census():
    """`BL-20260908-BACKLOG-BURNDOWN-READS-ONLY-OPENED-AT...` says in terms that
    a code change alone does not clear it — the burn-down block and a direct
    status census have to agree. This is that census, computed here from the
    raw JSON without going through the function under test, so it is a second
    reading rather than a restatement of the first.
    """
    import json
    closed = {"resolved", "wont_fix", "invalid", "superseded"}
    files = ["docs/claude/health-review-backlog.json",
             "docs/claude/performance-review-backlog.json",
             "docs/claude/ml-review-backlog.json"]
    rows = [r for f in files
            for r in json.loads((REPO / f).read_text(encoding="utf-8"))["items"]]
    assert len(rows) == B["rows_total"]
    assert sum(1 for r in rows if r.get("status") not in closed) == B["open_now"]
    assert sum(1 for r in rows if r.get("status") in closed) == B["closed_total"]

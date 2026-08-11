"""Soak-window censoring on the shadow-prediction log
(BL-20260810-SHADOW-STATS-FIRSTSEEN-IS-LOG-ROTATION-NOT-SOAK-START).

`first_seen` is the oldest SURVIVING row for a model, and the log is rotated.
So for any model already running when the last rotation fired, `first_seen` is
the ROTATION BOUNDARY, not the model's soak start. Measured 2026-08-10, all 19
live models reported a `first_seen` inside the same two-minute band despite
promotions spanning weeks.

That field is the denominator of the shadow->advisory promotion gate, and the
error runs in the dangerous direction: a long soak looks short, so a promotion
that is DUE reads as not-yet-ready. These tests pin the three-state distinction
that makes a truncated window legible as truncated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ml.shadow.inspector import (
    SOAK_START_LOG_CENSORED,
    SOAK_START_OBSERVED,
    SOAK_START_REGISTRY,
    SOAK_START_REGISTRY_REGISTRATION,
    SOAK_START_UNKNOWN,
    ModelStats,
    ShadowRecord,
    aggregate,
    coverage,
    mean_cadence_seconds,
    resolve_soak_start,
    soak_start_basis,
    stage_entry_times,
    stage_registration_times,
)

BASE = datetime(2026, 8, 5, 23, 36, tzinfo=timezone.utc)


def _rec(model_id: str, when: datetime, *, stage: str = "shadow") -> ShadowRecord:
    return ShadowRecord(
        predicted_at_utc=when,
        model_id=model_id,
        stage=stage,
        score=0.5,
        row_keys=("confidence",),
    )


def _series(model_id: str, start_offset_s: float, cadence_s: float, n: int):
    """A model writing every `cadence_s` from `BASE + start_offset_s`."""
    return [
        _rec(model_id, BASE + timedelta(seconds=start_offset_s + i * cadence_s))
        for i in range(n)
    ]


def _stats_for(records, model_id: str) -> ModelStats:
    return next(s for s in aggregate(records) if s.model_id == model_id)


# --- coverage is the log's window, not any model's -------------------------


def test_coverage_reports_the_whole_retained_window():
    recs = _series("edge", 0, 300, 10) + _series("late", 50_000, 300, 10)
    cov = coverage(recs)
    assert cov.oldest == BASE
    assert cov.newest == BASE + timedelta(seconds=50_000 + 9 * 300)
    assert cov.total_records == 20
    assert cov.present is True


def test_coverage_of_an_empty_log_is_absent_not_zero_width():
    cov = coverage([])
    assert cov.present is False
    assert cov.oldest is None and cov.newest is None


# --- the censoring test ----------------------------------------------------


def test_model_at_the_log_edge_is_censored_not_short():
    """The real-world case: a 5m head running long before the rotation. Its
    first surviving row sits at the log's edge, so first_seen is a LOWER BOUND."""
    recs = _series("edge-hugger", 0, 300, 100) + _series("other", 0, 300, 100)
    cov = coverage(recs)
    s = _stats_for(recs, "edge-hugger")
    assert soak_start_basis(s, cov) == SOAK_START_LOG_CENSORED


def test_model_starting_well_inside_the_window_is_observed():
    """A model genuinely wired up after the rotation: many cadences of lead-in,
    so the log really did capture its first sighting."""
    old = _series("old", 0, 300, 200)              # defines the log edge
    fresh = _series("fresh", 40_000, 300, 50)      # >100 cadences later
    recs = old + fresh
    cov = coverage(recs)
    assert soak_start_basis(_stats_for(recs, "fresh"), cov) == SOAK_START_OBSERVED
    assert soak_start_basis(_stats_for(recs, "old"), cov) == SOAK_START_LOG_CENSORED


def test_the_two_states_are_distinguishable_in_one_log():
    """The bug was that every model reported the same start. A correct
    implementation must be able to disagree across models in a single log."""
    recs = _series("a", 0, 300, 100) + _series("b", 60_000, 300, 100)
    cov = coverage(recs)
    bases = {s.model_id: soak_start_basis(s, cov) for s in aggregate(recs)}
    assert bases["a"] == SOAK_START_LOG_CENSORED
    assert bases["b"] == SOAK_START_OBSERVED
    assert len(set(bases.values())) == 2, "all models got the same verdict again"


def test_a_slow_cadence_head_is_judged_on_its_own_cadence():
    """Tolerance scales with the model's own cadence: a 1h head 30 min past the
    edge is still edge-hugging, while a 5m head 30 min past it is not."""
    hourly = _series("hourly", 1800, 3600, 50)
    five_min = _series("5m", 1800, 300, 200)
    anchor = _series("anchor", 0, 300, 5)   # sets the log edge at BASE
    recs = anchor + hourly + five_min
    cov = coverage(recs)
    assert soak_start_basis(_stats_for(recs, "hourly"), cov) == SOAK_START_LOG_CENSORED
    assert soak_start_basis(_stats_for(recs, "5m"), cov) == SOAK_START_OBSERVED


# --- "we could not look" stays its own state -------------------------------


def test_single_observation_is_unknown_not_a_guess():
    """One row cannot establish a cadence, so the censoring test cannot be
    applied. Guessing either way would be the collapse this prevents."""
    recs = _series("busy", 0, 300, 100) + [_rec("lonely", BASE + timedelta(seconds=9000))]
    cov = coverage(recs)
    assert soak_start_basis(_stats_for(recs, "lonely"), cov) == SOAK_START_UNKNOWN


def test_no_records_is_unknown():
    empty = ModelStats(model_id="nobody", stage="shadow")
    assert soak_start_basis(empty, coverage([])) == SOAK_START_UNKNOWN


def test_unknown_is_never_silently_read_as_observed():
    """The three states must be mutually exclusive — a caller branching on
    `== observed` must not accidentally catch an unmeasurable one."""
    empty = ModelStats(model_id="nobody", stage="shadow")
    assert soak_start_basis(empty, coverage([])) != SOAK_START_OBSERVED
    assert soak_start_basis(empty, coverage([])) != SOAK_START_LOG_CENSORED


# --- cadence estimate ------------------------------------------------------


def test_mean_cadence_recovers_a_known_spacing():
    recs = _series("m", 0, 300, 50)
    assert mean_cadence_seconds(_stats_for(recs, "m")) == 300.0


def test_mean_cadence_is_none_for_a_single_row():
    recs = [_rec("m", BASE)]
    assert mean_cadence_seconds(_stats_for(recs, "m")) is None


# --- The recovery half: registry-sourced soak start ------------------------
#
# Disclosure (above) says a `first_seen` MAY be a rotation boundary. That still
# leaves the promotion gate without a denominator — knowing a number is a lower
# bound does not give you the real one. `stage_entry_times` / `resolve_soak_start`
# recover the true start from the registry's durable `stage_history`.


def _registry_row(model_id: str, *events):
    return {
        "model_id": model_id,
        "stage_history": [{"to_stage": s, "at": at} for s, at in events],
    }


def test_stage_entry_times_reads_the_transition():
    rows = [_registry_row("m1", ("candidate", "2026-06-01T00:00:00+00:00"),
                          ("shadow", "2026-07-20T10:00:00+00:00"))]
    assert stage_entry_times(rows, stage="shadow") == {
        "m1": datetime(2026, 7, 20, 10, tzinfo=timezone.utc)}


def test_a_re_promoted_model_soaks_from_the_LATEST_entry():
    """Demoted then re-promoted: the soak restarts. Taking the first event
    would credit the model with a soak it spent outside the stage."""
    rows = [_registry_row("m1", ("shadow", "2026-05-01T00:00:00+00:00"),
                          ("candidate", "2026-06-01T00:00:00+00:00"),
                          ("shadow", "2026-07-20T10:00:00+00:00"))]
    assert stage_entry_times(rows, stage="shadow")["m1"].month == 7


def test_legacy_stage_aliases_still_match():
    """The ladder collapsed 7->3 in 2026-06; legacy rows carry the old names.
    A registry row saying `research_only` must still answer a `candidate` query
    or every pre-collapse model silently loses its transition record."""
    rows = [_registry_row("old", ("research_only", "2026-05-01T00:00:00Z"))]
    assert "old" in stage_entry_times(rows, stage="candidate")


def test_no_stage_history_is_ABSENT_not_created_at():
    """`gates.py::_stage_entered_at` falls back to `created_at`; this must not.
    Substituting a creation date inflates the soak of a model created early and
    promoted late — a measurement-shaped guess, which is the whole bug class."""
    rows = [{"model_id": "m1", "created_at": "2026-01-01T00:00:00+00:00"}]
    assert stage_entry_times(rows, stage="shadow") == {}


def test_malformed_rows_do_not_blind_the_whole_map():
    rows = [
        {"model_id": "good", "stage_history": [
            {"to_stage": "shadow", "at": "2026-07-20T10:00:00+00:00"}]},
        {"model_id": "bad_stage", "stage_history": [
            {"to_stage": "not_a_stage", "at": "2026-07-20T10:00:00+00:00"}]},
        {"model_id": "bad_ts", "stage_history": [
            {"to_stage": "shadow", "at": "not-a-date"}]},
        {"stage_history": [{"to_stage": "shadow", "at": "2026-07-20T10:00:00Z"}]},
    ]
    assert list(stage_entry_times(rows, stage="shadow")) == ["good"]


def test_registry_overrides_a_censored_log_and_is_MEASURED():
    """The finding, end to end: a model whose log start is a rotation boundary
    reports its real, much longer soak once the registry is consulted."""
    recs = _series("m1", 0, 300, 100)            # hugs the log's oldest edge
    stats, cov = _stats_for(recs, "m1"), coverage(recs)
    now = BASE + timedelta(days=5)

    log_only = resolve_soak_start(stats, cov, now=now)
    assert log_only.basis == SOAK_START_LOG_CENSORED
    assert log_only.to_dict()["soak_days_is_lower_bound"] is True
    assert not log_only.is_measured

    entered = {"m1": BASE - timedelta(days=16)}   # promoted well before rotation
    with_reg = resolve_soak_start(stats, cov, registry_entered_at=entered, now=now)
    assert with_reg.basis == SOAK_START_REGISTRY
    assert with_reg.is_measured
    assert with_reg.to_dict()["soak_days_is_lower_bound"] is False
    # The whole point: the gate's denominator was understated ~4x.
    assert with_reg.days > log_only.days * 3


def test_registry_absent_falls_back_and_says_so():
    """A model with no registry transition must not silently inherit another
    model's basis — it falls back to the log AND declares which state it is in."""
    recs = _series("edge", 0, 300, 50) + _series("late", 200_000, 300, 50)
    cov = coverage(recs)
    entered = {"edge": BASE - timedelta(days=10)}   # only `edge` is in the registry

    edge = resolve_soak_start(_stats_for(recs, "edge"), cov, registry_entered_at=entered)
    late = resolve_soak_start(_stats_for(recs, "late"), cov, registry_entered_at=entered)
    assert edge.basis == SOAK_START_REGISTRY
    assert late.basis == SOAK_START_OBSERVED
    assert late.days is not None


def test_unknown_publishes_no_duration():
    """One row = no measurable soak. Printing 0.0 days would read as a fact."""
    recs = [_rec("solo", BASE)]
    got = resolve_soak_start(_stats_for(recs, "solo"), coverage(recs))
    assert got.basis == SOAK_START_UNKNOWN
    assert got.days is None
    assert got.to_dict()["soak_days"] is None


def test_the_four_bases_are_all_reachable_and_distinct():
    """Guards the collapse this module exists to prevent: if any two states
    became indistinguishable the surface would still render plausibly."""
    assert len({SOAK_START_REGISTRY, SOAK_START_OBSERVED,
                SOAK_START_LOG_CENSORED, SOAK_START_UNKNOWN}) == 4


# --- Registered-at-stage: the OTHER half of the registry ------------------
#
# Measured on the live trainer registry (trainer-diag #8773, 2026-08-11): only
# 14 of 29 `shadow` models carry a transition event. The other 15 were
# registered straight to shadow and never promoted, so transitions alone would
# leave half the fleet on the censored log basis.


def test_registered_directly_at_stage_uses_created_at():
    rows = [{"model_id": "direct", "target_deployment_stage": "shadow",
             "created_at": "2026-05-22T10:14:14+00:00"}]
    assert list(stage_registration_times(rows, stage="shadow")) == ["direct"]


def test_a_promoted_model_is_NOT_a_registration():
    """It has an event, so the event is the answer. Counting it here as well
    would let `created_at` win for a model created long before it was
    promoted — the exact inflation `stage_entry_times` refuses."""
    rows = [{"model_id": "promoted", "target_deployment_stage": "shadow",
             "created_at": "2026-01-01T00:00:00+00:00",
             "stage_history": [{"to_stage": "shadow",
                                "at": "2026-07-03T11:47:38+00:00"}]}]
    assert stage_registration_times(rows, stage="shadow") == {}


def test_a_model_at_another_stage_never_counts_as_registered_here():
    """A history-less `candidate` was never at shadow, so it has no shadow
    soak. Without the current-stage check this would invent one."""
    rows = [{"model_id": "cand", "target_deployment_stage": "candidate",
             "created_at": "2026-01-01T00:00:00+00:00"}]
    assert stage_registration_times(rows, stage="shadow") == {}


def test_transition_beats_registration_in_the_resolver():
    recs = _series("m1", 0, 300, 100)
    stats, cov = _stats_for(recs, "m1"), coverage(recs)
    got = resolve_soak_start(
        stats, cov,
        registry_entered_at={"m1": BASE - timedelta(days=5)},
        registry_registered_at={"m1": BASE - timedelta(days=90)},
        now=BASE + timedelta(days=1),
    )
    assert got.basis == SOAK_START_REGISTRY
    assert got.days < 10          # the transition, not the far-earlier creation


def test_registration_beats_the_log_and_counts_as_measured():
    recs = _series("m1", 0, 300, 100)          # log start is a rotation edge
    stats, cov = _stats_for(recs, "m1"), coverage(recs)
    got = resolve_soak_start(
        stats, cov,
        registry_registered_at={"m1": BASE - timedelta(days=30)},
        now=BASE + timedelta(days=1),
    )
    assert got.basis == SOAK_START_REGISTRY_REGISTRATION
    assert got.is_measured                      # rotation-proof
    assert got.to_dict()["soak_days_is_lower_bound"] is False
    assert got.days > 30


def test_the_two_registry_bases_stay_distinguishable():
    """Both are measured, but they are different EVIDENCE — an event vs an
    inference from the absence of events. Collapsing them would hide which."""
    assert SOAK_START_REGISTRY != SOAK_START_REGISTRY_REGISTRATION

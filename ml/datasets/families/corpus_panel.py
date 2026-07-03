"""`corpus_panel` dataset family — the encoder-ready aligned corpus matrix (M19 T2 C3).

Assembles the standing multi-asset corpus store (`ml.datasets.corpus_store`) into
the aligned **date × series** matrix the label-free T1.2 self-supervised encoder
pretrains on (design: [`docs/research/T0-data-corpus-DESIGN.md`](../../../docs/research/T0-data-corpus-DESIGN.md)
§ "C3 — encoder-ready panel export").

Each catalog series is a daily `{date, value}` step function on its own date grid.
This family unions those grids into ONE common daily grid and, for each series,
produces a **past-only, forward-filled** column on that grid.

### Leakage discipline — reuse the macro block's contract verbatim (LEAKAGE-CRITICAL)

The panel reuses `ml.datasets.macro_features`'s exact daily one-day-lag rule
(that module: a day-`D` value is built from day `D`'s close, not known until day
`D` ends, so it is stamped/available from the **start of the following day**).
Here the same rule is expressed as a **strictly-prior as-of alignment** — the
single rule in :func:`_align_series_on_grid` that jointly encodes all three
guarantees the T1.2 encoder depends on:

  **The value at grid date `G` is the value of the most recent observation whose
  date is STRICTLY BEFORE `G` (`obs_date < G`).**

- **One-day lag.** An observation dated `D` is never its own row's value (`D` is
  not `< D`); it first becomes visible at the **next grid date `> D`** — exactly
  `macro_features`' "stamped at `<D+1>`" convention, generalised from calendar
  `D+1` to "the next step on the common grid" (the panel's cadence unit).
- **Forward-fill.** That most-recent-prior value is carried across every later
  grid date until a newer observation supersedes it.
- **No backfill.** A grid date earlier than a series' first observation has no
  strictly-prior observation → the cell is `None` (missing), **never** a future
  value pulled backward.

No future information ever appears in a row dated ≤ that information's
availability date. A value on the FINAL grid date is (correctly) never emitted —
its availability would be the next grid step, which does not exist — keeping the
panel leakage-conservative at the right edge.

### Schema choice (dynamic series set → static nested `values` column)

The selected series set is dynamic (default = every catalog series), but
:class:`ml.datasets.builder.DatasetBuilder` locks `allowed_fields`/`schema` from
the class `ClassVar` **before** `iter_rows` runs, so per-series top-level columns
cannot be validated. So — exactly like `market_sequences` carries its variable
window under one static `list` column — this family emits the series columns under
a single static **`values: dict`** column: one row per grid date
`{"date": <grid date>, "values": {<series_id>: <float|None>, ...}}`. The `values`
dict keys ARE the selected series list, so the artifact is self-describing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from .. import corpus_store
from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_FAMILY = "corpus_panel"

# The fixed leakage guarantee this family provides — recorded in the module +
# surfaced via `leakage_test_status=passed` + `label_version` in metadata.json.
LEAKAGE_CONTRACT = "past_only_forward_fill_one_day_lag"


def _clean_obs(rows: list[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """Coerce raw `{date, value}` corpus rows to ascending `(date, float)` pairs.

    Defensive (the store already writes clean, sorted rows, but `read_series`
    returns whatever JSON is on disk): drops rows with a missing/non-finite value
    or missing date, truncates the date to `YYYY-MM-DD`, and sorts ascending.
    """
    out: list[tuple[str, float]] = []
    for r in rows:
        date = r.get("date")
        raw = r.get("value")
        if date is None or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf guard
            continue
        out.append((str(date)[:10], value))
    out.sort(key=lambda x: x[0])
    return out


def _select_series(
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    series: str | None = None,
    groups: str | None = None,
) -> list[str]:
    """The sorted series-id allowlist: default ALL, optionally narrowed.

    ``series`` is a comma-separated series-id allowlist; ``groups`` a
    comma-separated catalog-group filter. Both optional; when both are given a
    series must satisfy BOTH. Sorted for deterministic column ordering.
    """
    allow = (
        {s.strip() for s in series.split(",") if s.strip()} if series else None
    )
    grp = (
        {g.strip() for g in groups.split(",") if g.strip()} if groups else None
    )
    out: list[str] = []
    for sid, entry in catalog.items():
        if allow is not None and sid not in allow:
            continue
        if grp is not None and (entry or {}).get("group") not in grp:
            continue
        out.append(sid)
    return sorted(out)


def _align_series_on_grid(
    grid: list[str], obs: list[tuple[str, float]]
) -> list[float | None]:
    """Past-only strictly-prior as-of alignment of one series onto ``grid``.

    Encodes the one-day-lag + forward-fill + no-backfill contract in a single
    rule: the value at grid date ``G`` is the value of the most recent
    observation whose date is **strictly before** ``G`` (``obs_date < G``).

    Both ``grid`` and ``obs`` are ascending, so this is a linear merge: a single
    pointer advances over every observation strictly before the current grid
    date, carrying the last such value forward. ``last_val`` starts ``None`` so
    grid dates before the first observation stay missing (no backfill).
    """
    out: list[float | None] = []
    n = len(obs)
    j = 0
    last_val: float | None = None
    for g in grid:
        # Advance over every observation strictly before g. The one-day lag lives
        # in the strict `<`: an obs dated exactly g is NOT yet visible at g. The
        # forward-fill lives in `last_val` persisting across grid dates with no
        # newer strictly-prior obs.
        while j < n and obs[j][0] < g:
            last_val = obs[j][1]
            j += 1
        out.append(last_val)
    return out


class CorpusPanelBuilder(DatasetBuilder):
    """Assemble the aligned, past-only date × series corpus panel.

    See the module docstring for the leakage contract and the nested-`values`
    schema rationale.
    """

    family: ClassVar[str] = _FAMILY
    builder_version: ClassVar[str] = "v1"
    # Every column is a strictly-prior as-of read of a past-only daily series, so
    # no future information can enter a row — the panel is leakage-safe by
    # construction (the encoder's masked-reconstruction reads it directly).
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.PASSED
    # The convention is baked into the label_version so metadata.json is
    # self-describing about the alignment contract.
    label_version: ClassVar[str] = "corpus_panel_past_only_ffill_1d_lag_v1"
    default_symbol_scope: ClassVar[str] = "all"
    default_timeframe: ClassVar[str] = "daily"
    schema: ClassVar[Mapping[str, type]] = {
        "date": str,
        "values": dict,
    }

    def iter_rows(
        self,
        *,
        corpus_root: Path | str | None = None,
        series: str | None = None,
        groups: str | None = None,
        **_ignored: Any,
    ) -> Iterator[Mapping[str, Any]]:
        root = corpus_store.corpus_root(corpus_root)
        catalog = corpus_store.load_catalog(root)
        selected = _select_series(catalog, series=series, groups=groups)

        # Read + clean each selected series and union their dates into the grid.
        series_obs: dict[str, list[tuple[str, float]]] = {}
        all_dates: set[str] = set()
        for sid in selected:
            obs = _clean_obs(corpus_store.read_series(sid, root=root))
            series_obs[sid] = obs
            all_dates.update(date for date, _ in obs)

        grid = sorted(all_dates)  # the COMMON DAILY GRID (ascending)

        # One past-only aligned column per series on the shared grid.
        aligned = {
            sid: _align_series_on_grid(grid, obs)
            for sid, obs in series_obs.items()
        }

        # One row per grid date: the date × series matrix, ascending by date.
        for i, g in enumerate(grid):
            values = {sid: aligned[sid][i] for sid in selected}
            yield {"date": g, "values": values}

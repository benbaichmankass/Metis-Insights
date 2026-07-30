#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — off-VM **historical backfill** of PIT economic-release snapshots.

The missing backfill sibling of the forward-only ``econ_calendar_produce.py``.

Why this exists (the 2026-07-30 correction)
-------------------------------------------
The live econ-calendar producer records forward snapshots only, so the M1 event
study capped at **n=7** per event kind against a ``min_honest_n`` of 12 — which a
prior plan read as "no verdict until ~mid-September" and then *scheduled around*.
That was wrong: this repo already solves exactly this shape three times, and
``valuation_snapshot_backfill.py``'s docstring states the principle verbatim —
*"we can reconstruct years of point-in-time snapshots in one shot instead of
waiting."* The econ-calendar producer was simply the one macro producer with no
backfill sibling. This is that sibling.

    config/macro_econ_series.yaml   (kind → FRED series + cadence + symbol)
        + full FRED series history (keyless fredgraph.csv, off-VM-guarded)
        → for each release i: expectation from data STRICTLY BEFORE i
                              (scripts/macro/econ_expectation.py)
        → surprise = actual − expectation
        → PIT snapshot rows in the LIVE producer's schema
        → comms/macro/econ_calendar_snapshots_backfill.jsonl  (full regen each run)

    python scripts/macro/econ_event_study.py \
        --kind eia_natgas_storage \
        --snapshots comms/macro/econ_calendar_snapshots_backfill.jsonl \
        --candles-dir data/macro_candles

Emitted rows use the **same schema the study already consumes**
(``kind`` / ``status:"resolved"`` / ``scheduled_for`` / ``realized_outcome``
{actual, consensus, surprise, surprise_pct, change} / ``observed_at``), so
``econ_event_study.py`` grades them **unchanged**.

Provenance is explicit, never implied
-------------------------------------
``realized_outcome.consensus`` carries the **model expectation**, because that is
what the study means by the anticipated level. To make sure no reader ever mistakes
it for an archived survey poll, every row also carries:

* ``expectation_source`` — e.g. ``model:seasonal_ar_ols_v1`` (the pinned SPEC_VERSION)
* ``pit_basis``          — ``fred_current_vintage`` (keyless FRED serves the CURRENT
                           vintage, NOT first prints; ALFRED is the upgrade path)
* ``backfilled: true``   — so a mixed store can always separate reconstructed rows
                           from genuinely forward-observed ones

The operator-approved 2026-07-30 M1 gate change permits the model-expectation basis
**provided** it is validated against the captured survey consensus on the overlap —
that validation is a separate step (``--validate-against``), not assumed here.

Loud on failure, never silently thin
------------------------------------
A series whose fetch returns nothing is a **hard error** (exit 1), not a skipped
row. Silently skipping is precisely the Class-B "green but vacuous" failure just
removed from the event study (BL-20260730-M1-PRICE-JOIN-DEAD): a run that
reconstructs nothing must not look like a run that reconstructed a little.

Off-VM-guarded (needs ``ICT_OFFVM_BUILD_HOST=1`` unless ``urlopen`` is injected) so
the live trading VM never opens a market-data socket. Stdlib-only apart from PyYAML.
No order path, no DB write, no live-VM touch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import yaml  # noqa: E402

from econ_expectation import (  # noqa: E402
    DEFAULT_HARMONICS,
    DEFAULT_MIN_TRAIN,
    SPEC_VERSION,
    expectation_at,
    period_for_cadence,
)

DEFAULT_CONFIG = os.path.join("config", "macro_econ_series.yaml")
DEFAULT_OUT = os.path.join("comms", "macro", "econ_calendar_snapshots_backfill.jsonl")
PIT_BASIS_FRED_CURRENT = "fred_current_vintage"


def load_series_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    series = cfg.get("series") or {}
    if not isinstance(series, dict) or not series:
        raise SystemExit(f"{path}: no `series:` block — nothing to backfill")
    return series


def _fetch_history(series_id: str, *, urlopen=None, timeout: float = 25.0):
    """Full dated history for one FRED series via the shared adapter."""
    from src.units.strategies.macro_thesis.fred_adapter import (
        fetch_fred_series_history_dated,
    )

    got = fetch_fred_series_history_dated([series_id], urlopen=urlopen, timeout=timeout)
    return got.get(series_id) or []


def rows_for_kind(
    kind: str,
    spec: dict,
    history: list,
    *,
    harmonics: int = DEFAULT_HARMONICS,
    min_train: int = DEFAULT_MIN_TRAIN,
    generated_at: Optional[str] = None,
) -> list[dict]:
    """PIT snapshot rows for one event kind from its dated FRED history.

    ``history`` is ``[(YYYY-MM-DD, value), ...]`` ascending. Row ``i`` is emitted
    only when an expectation is computable from ``history[:i]`` — a thin warm-up
    head yields no rows rather than a fabricated expectation.
    """
    period = period_for_cadence(spec.get("cadence") or "weekly")
    dates = [d for d, _v in history]
    values = [float(v) for _d, v in history]
    out: list[dict] = []

    for i in range(len(values)):
        exp = expectation_at(values, i, period=period, harmonics=harmonics,
                             min_train=min_train)
        if exp is None:
            continue
        actual = values[i]
        surprise = actual - exp
        prior = values[i - 1] if i > 0 else None
        out.append({
            "event_id": f"backfill-{kind}-{dates[i]}",
            "kind": kind,
            "event_name": kind.replace("_", " ").title(),
            "entity": "US",
            "country": "US",
            "scheduled_for": dates[i],
            "scheduled_at": f"{dates[i]}T00:00:00Z",
            "status": "resolved",
            "impact": None,
            "impact_score": None,
            "frequency": spec.get("cadence"),
            "expected": {
                "metric": kind,
                "consensus": exp,
                "consensus_raw": None,
                "prior": prior,
            },
            "realized_outcome": {
                "metric": kind,
                "actual": actual,
                "prior": prior,
                "previous_original": None,
                "consensus": exp,
                "surprise": surprise,
                "surprise_pct": (surprise / abs(exp) * 100.0) if exp else None,
                "change": (actual - prior) if prior is not None else None,
            },
            # --- provenance: never let a model expectation pass as a survey poll ---
            "backfilled": True,
            "expectation_source": f"model:{SPEC_VERSION}",
            "expectation_period": period,
            "pit_basis": PIT_BASIS_FRED_CURRENT,
            "source": f"fred:{spec.get('fred_series')}",
            "source_url": "https://fred.stlouisfed.org",
            "symbol": spec.get("symbol"),
            # The release date IS the observation instant for a reconstructed row:
            # the expectation used only strictly-prior data, so the row is PIT by
            # construction. Distinguishable from a forward capture via `backfilled`.
            "observed_at": f"{dates[i]}T00:00:00Z",
            "generated_at": generated_at,
        })
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill PIT economic-release snapshots from FRED history (observe-only)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--kinds", default=None,
                    help="CSV subset of event kinds (default: every kind in the config)")
    ap.add_argument("--harmonics", type=int, default=DEFAULT_HARMONICS)
    ap.add_argument("--min-train", type=int, default=DEFAULT_MIN_TRAIN)
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    series = load_series_config(args.config)
    wanted = ([k.strip() for k in args.kinds.split(",") if k.strip()]
              if args.kinds else sorted(series))
    unknown = [k for k in wanted if k not in series]
    if unknown:
        print(f"unknown kind(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    failures: list[str] = []
    for kind in wanted:
        spec = series[kind] or {}
        sid = spec.get("fred_series")
        if not sid:
            failures.append(f"{kind}: no `fred_series` declared")
            continue
        try:
            history = _fetch_history(sid)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{kind} ({sid}): fetch raised {type(exc).__name__}: {exc}")
            continue
        if not history:
            # LOUD, not skipped — see the module docstring. An unverified series id
            # is the likeliest cause; fix it in the config, not in code.
            failures.append(
                f"{kind} ({sid}): FRED returned NO history — verify the series id in "
                f"{args.config} (ids are declared from the catalogue, not sandbox-verified)"
            )
            continue
        rows = rows_for_kind(kind, spec, history, harmonics=args.harmonics,
                             min_train=args.min_train, generated_at=args.generated_at)
        print(f"{kind:28} {sid:12} history={len(history):5}  rows={len(rows):5}"
              f"  period={period_for_cadence(spec.get('cadence') or 'weekly')}")
        if not rows:
            failures.append(
                f"{kind} ({sid}): {len(history)} observations but ZERO rows — history is "
                f"shorter than min_train={args.min_train} + one seasonal period"
            )
            continue
        all_rows.extend(rows)

    if failures:
        print("\nFAIL — backfill did not reconstruct every requested kind:", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        print("\nNo output written. A partial backfill that looked successful is the "
              "failure mode this refuses to produce.", file=sys.stderr)
        return 1

    print(f"\ntotal rows: {len(all_rows)}  (expectation={SPEC_VERSION}, "
          f"pit_basis={PIT_BASIS_FRED_CURRENT})")
    if args.dry_run:
        print("dry-run — nothing written")
        return 0

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, default=str) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

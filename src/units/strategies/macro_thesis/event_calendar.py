"""M28 P2 — macro-event calendar feed (config → point-in-time event rows).

The **source** side of the event subsystem: given the watched-event config
(``config/macro_events.yaml``) and a release-date list, emit the ``scheduled``
``macro_events`` rows (M28-P0 schema §2) the :mod:`event_store` persists and the
:mod:`event_resolver` later acts on. On resolution it shapes a
``realized_outcome`` from the released actual (a free FRED series value).

Like :mod:`valuation_feed`, this is the **pure, offline-testable** half: it
never fetches. The caller injects the release dates (the off-VM free-calendar
feed reads BLS/BEA/Treasury/Fed's published schedules) and the released actual
(pulled from the declared free FRED series), so ``live == train`` and the whole
module is unit-testable with no network.

Point-in-time throughout (schema §1): a ``scheduled`` row and its later
``resolved`` row are **two lines** with distinct ``observed_at`` — never an
in-place update — so a backtest reconstructs exactly what was known as-of any
instant. Honest-null: ``surprise`` is ``None`` until a free consensus source is
wired (direction then falls back to the change-vs-prior sign), and a resolution
with no prior yields ``direction=None`` rather than a fabricated label. Nothing
here touches an order path or the network.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional

# Default config lives beside the other config/*.yaml.
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "config", "macro_events.yaml"
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def load_events_config(path: Optional[str] = None) -> dict:
    """Load ``config/macro_events.yaml``. Fail-permissive → ``{}`` on any error."""
    try:
        import yaml  # local import so the pure layer needs no yaml
        with open(path or _DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _slug(text: Any) -> str:
    return _SLUG_RE.sub("-", str(text).strip().lower()).strip("-") or "na"


def event_id_for(kind: str, date: str, entity: str = "macro") -> str:
    """Deterministic ``evt-<kind>-<date>-<entity>`` id (schema §2).

    Same (kind, date, entity) always yields the same id, so a re-emitted
    scheduled row and its later resolved row share one ``event_id``."""
    return f"evt-{_slug(kind)}-{_slug(date)}-{_slug(entity)}"


def required_series(config: Mapping[str, Any]) -> dict[str, str]:
    """Map ``{kind: fred_series}`` — the free series a resolver pulls for each
    event's realized actual. Skips entries with no ``series`` declared."""
    out: dict[str, str] = {}
    for kind, spec in (config.get("events") or {}).items():
        if isinstance(spec, Mapping) and spec.get("series"):
            out[str(kind)] = str(spec["series"])
    return out


def build_scheduled_events(
    config: Mapping[str, Any],
    releases: Mapping[str, Any],
    *,
    observed_at: str,
) -> list[dict]:
    """Emit ``scheduled`` ``macro_events`` rows for each release date.

    ``releases`` is ``{kind: [iso_date, ...]}`` supplied by the caller (the
    off-VM free-calendar feed; tests inject). One row per (kind, date). A kind
    absent from ``config['events']`` is skipped (never a fabricated event)."""
    events = config.get("events") or {}
    out: list[dict] = []
    for kind, dates in (releases or {}).items():
        spec = events.get(kind)
        if not isinstance(spec, Mapping):
            continue
        entity = str(spec.get("entity", "macro"))
        metric = spec.get("metric")
        for date in dates or []:
            date = str(date)
            out.append({
                "event_id": event_id_for(kind, date, entity),
                "kind": str(spec.get("kind", kind)),
                "entity": entity,
                "scheduled_for": date,
                "status": "scheduled",
                "expected": {"metric": metric, "consensus": None, "prior": None},
                "realized_outcome": None,
                "resolved_at": None,
                "source": spec.get("source"),
                "source_url": spec.get("source_url"),
                "observed_at": observed_at,
            })
    return out


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _direction(spec: Mapping[str, Any], change: Optional[float]) -> Optional[str]:
    """Label the outcome by the sign of ``change`` using the kind's
    ``direction_up``/``direction_down`` orientation. ``None`` when the change
    is unknown (no prior) — honest-null, never a fabricated label."""
    if change is None:
        return None
    if change > 0:
        return spec.get("direction_up")
    if change < 0:
        return spec.get("direction_down")
    return "unchanged"


def resolve_scheduled_event(
    event: Mapping[str, Any],
    *,
    actual: Any,
    prior: Any = None,
    consensus: Any = None,
    observed_at: str,
    resolved_at: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Shape the ``resolved`` row for a scheduled event given its released actual.

    Returns a NEW row (a distinct point-in-time line, schema §1) carrying
    ``status='resolved'``, ``resolved_at``, a fresh ``observed_at``, and a
    ``realized_outcome`` ``{metric, actual, prior, consensus, surprise, change,
    direction}``. ``surprise`` = ``actual − consensus`` when a (free) consensus
    is supplied, else ``None`` (honest-null); ``change`` = ``actual − prior``.
    ``direction`` keys off ``surprise`` when known, else ``change`` — ``None``
    when neither is measurable. Never raises; never fabricates a number."""
    kind = str(event.get("kind", ""))
    spec: Mapping[str, Any] = {}
    if config is not None:
        spec = (config.get("events") or {}).get(kind) or {}

    n_actual, n_prior, n_cons = _num(actual), _num(prior), _num(consensus)
    surprise = (n_actual - n_cons) if (n_actual is not None and n_cons is not None) else None
    change = (n_actual - n_prior) if (n_actual is not None and n_prior is not None) else None
    # Direction prefers the surprise sign (vs consensus) when available, else change.
    direction = _direction(spec, surprise if surprise is not None else change)

    resolved = dict(event)
    resolved["status"] = "resolved"
    resolved["resolved_at"] = resolved_at or observed_at
    resolved["observed_at"] = observed_at
    resolved["realized_outcome"] = {
        "metric": (event.get("expected") or {}).get("metric") if isinstance(event.get("expected"), Mapping) else spec.get("metric"),
        "actual": n_actual if n_actual is not None else actual,
        "prior": n_prior,
        "consensus": n_cons,
        "surprise": surprise,
        "change": change,
        "direction": direction,
    }
    return resolved

"""Canonical signal-component vector adapter (signal-research framework §7).

`signal_logic` is strategy-specific — each builder writes its own
idiosyncratic keys (`src/runtime/strategy_signal_builders.py` +
`src/units/strategies/*.py`). Rather than force every unit onto a rigid
schema (brittle, and the recombination doc warns against forking unit logic),
this module is a thin **read-side adapter**: one `extract(strategy_name,
signal_logic, extra)` that maps each unit's keys into a shared canonical
component namespace, so every layer of the framework consumes one rubric.

Design (§7): **pure + table-driven**. A registry maps each strategy to a list
of `ComponentSpec`s; each spec carries a canonical name, a `kind`, and a pure
extractor (a key lookup or a small lambda over the merged signal_logic). The
adapter is **tolerant**: a missing / None / unparseable input → the component
is simply absent from the result, never an exception. An unknown strategy
yields only the common (regime) components derivable from the stamped keys.

Component kinds (§3):
  * ``graded``      — continuous numeric edge; bucketable / edge-analysable.
  * ``categorical`` — an enum (regime, vol_regime, mitigation mode).
  * ``gate``        — a boolean / near-always-true condition (HTF bias align).

The graded values are exactly the edge-analysis inputs the L1a report buckets.
Hard gates are surfaced for completeness but, per §3/§4.1b, their marginal
edge is **not** measurable from traded rows alone (a censored sample) — the
report records them but does not bucket-attribute them.

Schema-drift guard (§10): the registry is a *reference* to live unit keys. A
unit-logic key rename must update its spec here; the adapter unit-tests
(`tests/test_component_vector.py`) lock the mapping against captured fixture
rows so drift fails loudly.

The real keys each builder persists (read off the code 2026-06-30):
  * ict_scalp (``ict_scalp_5m`` meta): ``sweep_level``, ``sweep_extreme``,
    ``displacement_body_to_range``, ``fvg_low``, ``fvg_high``, ``fvg_size``,
    ``mitigation_mode``, ``atr``, ``htf_filter_active``.
  * turtle_soup (meta): ``level``, ``sweep_extreme``, ``body_to_range``,
    ``atr``.
  * vwap (meta): ``deviation_std``, ``std_dev``, ``vwap``, ``policy_threshold``.
  * trend_donchian / fade_breakout_4h / htf_pullback_trend_2h: ``atr`` (+ the
    top-level ``confidence``, threaded through ``extra``).
  * common (stamped by ``_stamp_regime_on_meta`` on every signal):
    ``regime``, ``adx_14``, ``vol_regime``, ``rolling_log_return_vol``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# Canonical component kinds.
KIND_GRADED = "graded"
KIND_CATEGORICAL = "categorical"
KIND_GATE = "gate"

_VALID_KINDS = frozenset({KIND_GRADED, KIND_CATEGORICAL, KIND_GATE})


@dataclass(frozen=True)
class Component:
    """A single extracted, canonicalised signal component.

    ``value`` is a float (graded), a lowercased string (categorical), or a
    bool (gate). ``kind`` is one of :data:`KIND_GRADED` / :data:`KIND_CATEGORICAL`
    / :data:`KIND_GATE`.
    """

    value: Any
    kind: str


# An extractor takes the merged signal_logic dict and returns the raw value
# for its component (or None when not derivable from this row).
Extractor = Callable[[Dict[str, Any]], Any]


@dataclass(frozen=True)
class ComponentSpec:
    """A canonical component's name, kind, and pure extractor."""

    name: str
    kind: str
    extract: Extractor


# ---------------------------------------------------------------------------
# Coercion helpers (pure, never raise)
# ---------------------------------------------------------------------------


def _f(value: Any) -> Optional[float]:
    """Best-effort float, or None for missing / non-finite / unparseable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def _abs_over_atr(numer_a: str, numer_b: str) -> Extractor:
    """|sl[a] - sl[b]| / atr, or None when any input is missing / atr<=0."""

    def _ex(d: Dict[str, Any]) -> Optional[float]:
        a = _f(d.get(numer_a))
        b = _f(d.get(numer_b))
        atr = _f(d.get("atr"))
        if a is None or b is None or atr is None or atr <= 0:
            return None
        return abs(a - b) / atr

    return _ex


def _ratio_over_atr(numer: str) -> Extractor:
    """signal_logic[numer] / atr, or None when missing / atr<=0."""

    def _ex(d: Dict[str, Any]) -> Optional[float]:
        n = _f(d.get(numer))
        atr = _f(d.get("atr"))
        if n is None or atr is None or atr <= 0:
            return None
        return n / atr

    return _ex


def _graded_key(*keys: str) -> Extractor:
    """First present numeric value among ``keys`` (graded)."""

    def _ex(d: Dict[str, Any]) -> Optional[float]:
        for k in keys:
            v = _f(d.get(k))
            if v is not None:
                return v
        return None

    return _ex


def _categorical_key(*keys: str) -> Extractor:
    """First present non-empty string among ``keys``, lowercased (categorical)."""

    def _ex(d: Dict[str, Any]) -> Optional[str]:
        for k in keys:
            v = d.get(k)
            if v is None or isinstance(v, bool):
                continue
            s = str(v).strip().lower()
            if s and s not in ("none", "unknown", "null"):
                return s
        return None

    return _ex


def _gate_key(key: str) -> Extractor:
    """Boolean truthiness of ``key`` (gate), or None when absent."""

    def _ex(d: Dict[str, Any]) -> Optional[bool]:
        if key not in d:
            return None
        v = d.get(key)
        if v is None:
            return None
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    return _ex


# ---------------------------------------------------------------------------
# Common components — stamped on EVERY signal by _stamp_regime_on_meta.
# Derivable for any strategy, including unknown ones.
# ---------------------------------------------------------------------------


def _common_specs() -> List[ComponentSpec]:
    return [
        # The composite confidence — present for all strategies. Read from the
        # merged dict (the report folds order_packages.confidence in via extra).
        ComponentSpec("confidence", KIND_GRADED, _graded_key("confidence")),
        # ADX-14 — the trend-strength axis (graded), stamped on every signal.
        ComponentSpec("adx_14", KIND_GRADED, _graded_key("adx_14", "adx")),
        # rolling realised vol — graded, stamped on every signal.
        ComponentSpec(
            "rolling_log_return_vol",
            KIND_GRADED,
            _graded_key("rolling_log_return_vol"),
        ),
        # Trend / vol regime — categorical context axes.
        ComponentSpec("regime", KIND_CATEGORICAL, _categorical_key("regime")),
        ComponentSpec(
            "vol_regime", KIND_CATEGORICAL, _categorical_key("vol_regime")
        ),
    ]


# ---------------------------------------------------------------------------
# Per-strategy strategy-specific components (idiosyncratic keys → canonical).
# ---------------------------------------------------------------------------

_STRATEGY_SPECS: Dict[str, List[ComponentSpec]] = {
    # ict_scalp v1 (strategy_name == "ict_scalp_5m"). Liquidity sweep +
    # displacement + FVG + mitigation + HTF bias.
    "ict_scalp_5m": [
        ComponentSpec(
            "sweep_depth_atr",
            KIND_GRADED,
            _abs_over_atr("sweep_extreme", "sweep_level"),
        ),
        ComponentSpec(
            "displacement_strength",
            KIND_GRADED,
            _graded_key("displacement_body_to_range"),
        ),
        ComponentSpec("fvg_size_atr", KIND_GRADED, _ratio_over_atr("fvg_size")),
        ComponentSpec(
            "mitigation_mode",
            KIND_CATEGORICAL,
            _categorical_key("mitigation_mode"),
        ),
        ComponentSpec(
            "htf_bias_aligned", KIND_GATE, _gate_key("htf_filter_active")
        ),
    ],
    # turtle_soup — sweep + reversal. ``level`` is the swept swing extreme,
    # ``sweep_extreme`` how far price pierced; ``body_to_range`` the reversal
    # body strength.
    "turtle_soup": [
        ComponentSpec(
            "sweep_depth_atr",
            KIND_GRADED,
            _abs_over_atr("sweep_extreme", "level"),
        ),
        ComponentSpec(
            "displacement_strength", KIND_GRADED, _graded_key("body_to_range")
        ),
    ],
    # vwap mean-reversion. Deviation in std-dev units is the headline graded
    # edge; ``policy_threshold`` is the regime-policy entry threshold.
    "vwap": [
        ComponentSpec(
            "vwap_deviation_std",
            KIND_GRADED,
            # build_vwap_signal writes ``deviation_std``; tolerate ``deviation``.
            _graded_key("deviation_std", "deviation"),
        ),
        ComponentSpec(
            "vwap_policy_threshold",
            KIND_GRADED,
            _graded_key("policy_threshold"),
        ),
    ],
    # trend_donchian / fade_breakout_4h / htf_pullback_trend_2h are
    # ATR-trailing breakout/pullback units whose graded entry edge is the
    # composite ``confidence`` (breakout/pierce/pullback depth ÷ ATR), already
    # covered by the common ``confidence`` spec. fade_breakout_4h additionally
    # stamps a raw ``adx`` in meta — surface it as a strategy-specific graded
    # axis (its fade thesis is ADX-gated).
    "fade_breakout_4h": [
        ComponentSpec("fade_adx", KIND_GRADED, _graded_key("adx")),
    ],
    "trend_donchian": [],
    "htf_pullback_trend_2h": [],
}


def specs_for(strategy_name: str) -> List[ComponentSpec]:
    """Return the full ComponentSpec list (common + strategy-specific) for
    ``strategy_name``. An unknown strategy yields only the common specs.

    Pure — never raises. The result is a fresh list each call.
    """
    out = list(_common_specs())
    out.extend(_STRATEGY_SPECS.get(str(strategy_name or ""), []))
    return out


def known_strategies() -> List[str]:
    """Strategy names with a strategy-specific spec table (sorted)."""
    return sorted(_STRATEGY_SPECS.keys())


def graded_component_names(strategy_name: str) -> List[str]:
    """Canonical names of the GRADED components for ``strategy_name`` (the
    edge-analysable set the L1a report buckets), in spec order."""
    return [s.name for s in specs_for(strategy_name) if s.kind == KIND_GRADED]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract(
    strategy_name: str,
    signal_logic: Optional[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Component]:
    """Map a strategy's ``signal_logic`` row to canonical components.

    Parameters
    ----------
    strategy_name : str
        The emitting strategy (``order_packages.strategy_name``). Unknown
        names resolve only the common (regime / confidence) components.
    signal_logic : dict | None
        The decoded ``order_packages.signal_logic`` JSON (the meta the builder
        wrote). ``None`` / non-dict is treated as empty — only components
        derivable from ``extra`` come through.
    extra : dict | None
        Extra values merged on TOP of ``signal_logic`` before extraction —
        e.g. the report passes ``{"confidence": order_packages.confidence}``
        because the composite confidence lives in its own column, not always
        inside ``signal_logic``. ``signal_logic`` keys win only where ``extra``
        is silent (extra overrides, matching "the more authoritative column").

    Returns
    -------
    dict[str, Component]
        Canonical-name → :class:`Component`. A component whose extractor
        returns ``None`` (key missing / unparseable / atr<=0) is **omitted**
        entirely — callers test membership, not for a sentinel.

    Notes
    -----
    Pure + total: any malformed input degrades to fewer components, never an
    exception (§7 tolerance contract).
    """
    merged: Dict[str, Any] = {}
    if isinstance(signal_logic, dict):
        merged.update(signal_logic)
    if isinstance(extra, dict):
        # extra overrides signal_logic (the report's authoritative columns).
        for k, v in extra.items():
            if v is not None:
                merged[k] = v

    out: Dict[str, Component] = {}
    for spec in specs_for(strategy_name):
        if spec.kind not in _VALID_KINDS:
            continue
        try:
            raw = spec.extract(merged)
        except Exception:  # noqa: BLE001 — pure adapter, never raise on a row
            raw = None
        if raw is None:
            continue
        # A graded value must be finite-numeric; a gate must be bool; a
        # categorical must be a non-empty string. The extractors already
        # enforce this, but coerce defensively so the record kind is honest.
        if spec.kind == KIND_GRADED:
            val = _f(raw)
            if val is None:
                continue
        elif spec.kind == KIND_GATE:
            val = bool(raw)
        else:  # categorical
            val = str(raw).strip().lower()
            if not val:
                continue
        out[spec.name] = Component(value=val, kind=spec.kind)
    return out

"""Strategy Config UI — Streamlit web app (S-011 PR #4).

Run locally:
    streamlit run src/web/config_ui.py

Allows editing risk_pct, timeframe, enabled, symbols, and strategy-specific
params in config/strategies.yaml without touching code.  Changes are saved
back to the YAML and can be applied at runtime via /reload_strats.

The data helpers are importable without Streamlit for unit tests.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_STRATEGIES_YAML = os.path.join(_REPO_ROOT, "config", "strategies.yaml")

# Editable params shown in the UI (in display order)
_EDITABLE_PARAMS = [
    ("enabled", bool, "Enable strategy"),
    ("risk_pct", float, "Risk % per trade"),
    ("timeframe", str, "Timeframe (e.g. 5m, 15m, 1h)"),
    ("confidence_threshold", float, "Confidence threshold (0.0–1.0)"),
    ("threshold", float, "Strategy-specific threshold"),
]


# ---------------------------------------------------------------------------
# Helpers importable without Streamlit
# ---------------------------------------------------------------------------

def get_editable_fields(strategy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the editable subset of one strategy's config dict."""
    result = {}
    editable_keys = {k for k, _, _ in _EDITABLE_PARAMS}
    for key in editable_keys:
        if key in strategy_cfg:
            result[key] = strategy_cfg[key]
    # Always include symbols (list field)
    if "symbols" in strategy_cfg:
        result["symbols"] = strategy_cfg["symbols"]
    return result


def apply_edits(
    current: Dict[str, Dict[str, Any]],
    edits: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Apply *edits* on top of *current* strategy config.

    Parameters
    ----------
    current : dict
        Full strategy config loaded from strategies.yaml.
    edits : dict
        ``{strategy_name: {param: new_value}}`` — partial updates.

    Returns
    -------
    dict
        Merged config with edits applied (non-edited fields preserved).
    """
    result = {name: dict(cfg) for name, cfg in current.items()}
    for name, updates in edits.items():
        if name in result:
            result[name].update(updates)
        else:
            result[name] = dict(updates)
    return result


def validate_strategy_params(params: Dict[str, Any]) -> List[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors = []
    risk_pct = params.get("risk_pct")
    if risk_pct is not None:
        try:
            v = float(risk_pct)
            if not (0.0 < v <= 100.0):
                errors.append(f"risk_pct must be between 0 and 100, got {v}")
        except (TypeError, ValueError):
            errors.append(f"risk_pct must be a number, got {risk_pct!r}")

    conf = params.get("confidence_threshold")
    if conf is not None:
        try:
            v = float(conf)
            if not (0.0 <= v <= 1.0):
                errors.append(f"confidence_threshold must be 0.0–1.0, got {v}")
        except (TypeError, ValueError):
            errors.append(f"confidence_threshold must be a float, got {conf!r}")

    threshold = params.get("threshold")
    if threshold is not None:
        try:
            float(threshold)
        except (TypeError, ValueError):
            errors.append(f"threshold must be a number, got {threshold!r}")

    return errors


# ---------------------------------------------------------------------------
# Streamlit app entry point
# ---------------------------------------------------------------------------

def run_app(config_path: Optional[str] = None) -> None:
    """Launch the Strategy Config UI Streamlit app."""
    import streamlit as st
    from src.units.strategies import load_strategy_config, save_strategy_config

    path = config_path or _DEFAULT_STRATEGIES_YAML
    st.set_page_config(page_title="Strategy Config UI", layout="wide")
    st.title("⚙️ ICT Trading Bot — Strategy Config")
    st.caption(f"Config file: `{path}`  |  Use `/reload_strats` in Telegram to apply changes.")

    try:
        cfg = load_strategy_config(path)
    except FileNotFoundError:
        st.error(f"strategies.yaml not found at: `{path}`")
        return

    if not cfg:
        st.warning("No strategies found in strategies.yaml.")
        return

    st.info("Edit parameters below and click **Save** to write back to YAML.")

    edits: Dict[str, Dict[str, Any]] = {}

    for strategy_name, strategy_cfg in cfg.items():
        with st.expander(f"**{strategy_name}**", expanded=True):
            col_left, col_right = st.columns(2)

            with col_left:
                enabled = st.checkbox(
                    "Enabled",
                    value=bool(strategy_cfg.get("enabled", True)),
                    key=f"{strategy_name}_enabled",
                )
                risk_pct = st.number_input(
                    "Risk % per trade",
                    min_value=0.0, max_value=100.0, step=0.1,
                    value=float(strategy_cfg.get("risk_pct", 1.0)),
                    key=f"{strategy_name}_risk_pct",
                )
                timeframe = st.text_input(
                    "Timeframe",
                    value=str(strategy_cfg.get("timeframe", "5m")),
                    key=f"{strategy_name}_timeframe",
                )

            with col_right:
                symbols_raw = strategy_cfg.get("symbols", [])
                symbols_str = st.text_area(
                    "Symbols (one per line)",
                    value="\n".join(symbols_raw) if symbols_raw else "",
                    key=f"{strategy_name}_symbols",
                )

                if "confidence_threshold" in strategy_cfg:
                    conf = st.number_input(
                        "Confidence threshold",
                        min_value=0.0, max_value=1.0, step=0.01,
                        value=float(strategy_cfg["confidence_threshold"]),
                        key=f"{strategy_name}_confidence_threshold",
                    )
                    edits.setdefault(strategy_name, {})["confidence_threshold"] = conf

                if "threshold" in strategy_cfg:
                    thr = st.number_input(
                        "Strategy threshold",
                        step=0.001,
                        value=float(strategy_cfg["threshold"]),
                        key=f"{strategy_name}_threshold",
                    )
                    edits.setdefault(strategy_name, {})["threshold"] = thr

            symbols_list = [s.strip() for s in symbols_str.splitlines() if s.strip()]
            edits.setdefault(strategy_name, {}).update({
                "enabled": enabled,
                "risk_pct": risk_pct,
                "timeframe": timeframe,
                "symbols": symbols_list,
            })

            # Inline validation
            errors = validate_strategy_params(edits[strategy_name])
            for err in errors:
                st.warning(f"⚠️ {err}")

    st.divider()
    if st.button("💾 Save to strategies.yaml"):
        any_errors = any(
            validate_strategy_params(p) for p in edits.values()
        )
        if any_errors:
            st.error("Fix validation errors above before saving.")
        else:
            try:
                save_strategy_config(edits, path)
                st.success("✅ Saved. Run `/reload_strats` in Telegram to apply.")
            except Exception as exc:
                st.error(f"Save failed: {exc}")


if __name__ == "__main__":
    run_app()

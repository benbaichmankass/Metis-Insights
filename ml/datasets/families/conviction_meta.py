"""`conviction_meta` dataset family — v2 conviction meta-model (stacker).

The training dataset for the **learned v2 conviction meta-model** that will
eventually replace the formulaic v1 conviction blend
(`docs/unified-confidence-risk-DESIGN.md` § 3.2, § 4a, § 4b). Each row is one
historical decision (an order package) paired with its realized outcome, and
the feature vector is *exactly* the calibrated conviction-lens inputs the live
observe-only stamp computes (`src.runtime.conviction_inputs.build_conviction_inputs`)
plus the decision context the strategy recorded.

## Source-of-truth + row scope

One row per **closed, filled, non-backtest** order package:
`trades` JOIN `order_packages` ON `trades.order_package_id =
order_packages.order_package_id` (the populated back-reference —
`order_packages.linked_trade_id` is almost always NULL here),
filtered to `trades.status = 'closed' AND trades.is_backtest = 0` with a
non-null `trades.pnl` (an unlabelled outcome can't train a P(win) target). The
order package is the canonical "decision" record (it carries the signal-time
`confidence`, `model_scores`, `signal_logic`, and `meta`); the linked trade
carries the realized `pnl` / `pnl_percent`. Paper / backtest rows are excluded
exactly like `/api/bot/stats` and the other live-outcome families.

## Features (the calibrated lens inputs + context)

`build_conviction_inputs(strategy_name, confidence, model_scores)` produces the
``[0,1]`` calibrated lens inputs — `c_strat` (always present) plus `c_setup`,
`c_wr`, `c_reg` when the matching heads scored the decision. We deliberately
**reuse the live adapter** so the meta-model trains on the same feature space
the live observe-only stamp produces (no train/serve skew). Calibrators are NOT
loaded at build time — the dataset captures the *raw-normalized* lens inputs
(identity normalization), the same default the live path uses when the
calibrator artifact is absent; a downstream calibration pass is applied in the
adapter, not baked into the dataset.

Alongside the lens inputs each row carries the decision **context** decoded from
`order_packages.meta` / `signal_logic`: `regime`, `adx_14`, `vol_regime`,
`symbol`, `direction`, `strategy_name`. These are the categorical/numeric
context columns the v2 stacker may condition on (design § 3.2: "all member head
outputs + context").

## Targets

- **`won`** (primary, binary) — `pnl > 0`. The classification target.
- **`r_multiple`** (alternate) — `clip(pnl_percent / risk_pct, ±r_cap)`
  (defaults `risk_pct=1.0`, `r_cap=3.0`), mirroring `setup_labels`. A trainer
  may target either; the v1 manifest targets `won`.

## Leakage discipline (`leakage_test_status: skipped`)

`pnl`, `pnl_percent`, `won`, and `r_multiple` are all outcomes. A trainer
consuming this family MUST scope its `feature_columns` to the lens inputs +
context and exclude every outcome column — same contract as `trade_outcomes` /
`setup_labels`. Leakage prevention is the trainer's responsibility, hence
`skipped` (the LightGBM trainer's `_OUTCOME_FORBIDDEN` gate enforces it at
`fit()`).

Builder is read-only against the live DB (SQLite ``mode=ro`` URI).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from src.runtime.conviction_inputs import build_conviction_inputs
from src.runtime.local_pnl import canon_direction

from ..builder import DatasetBuilder
from ..embedding_features import EMBEDDING_FEATURE_COLUMNS, _finite_or_zero
from ..metadata import LeakageStatus
from .market_features import _align_asof, _load_funding_oi_rows

# Lens-input feature columns produced by build_conviction_inputs. c_strat is
# always present; the head slots are present only when a matching head scored
# the decision (so they are nullable in the schema / NaN-on-missing for LGBM).
_LENS_COLUMNS: tuple[str, ...] = ("c_strat", "c_setup", "c_wr", "c_reg")


def _clip(value: float, cap: float) -> float:
    if value > cap:
        return cap
    if value < -cap:
        return -cap
    return value


def _decode_json_obj(raw: Any) -> dict[str, Any]:
    """Decode a JSON TEXT column to a dict; ``{}`` on null / non-object / error."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _context_from(meta: Mapping[str, Any], signal_logic: Mapping[str, Any]) -> dict[str, Any]:
    """Pull the decision-context fields the v2 stacker conditions on.

    `meta` is the canonical carrier (regime / adx_14 / vol_regime stamped by the
    signal builder — see `src/runtime/intents.py`); `signal_logic` is consulted
    as a fallback for the same keys when `meta` didn't carry them.
    """
    def _pick(key: str) -> Any:
        if key in meta and meta.get(key) is not None:
            return meta.get(key)
        return signal_logic.get(key)

    regime = _pick("regime")
    adx_14 = _pick("adx_14")
    vol_regime = _pick("vol_regime")
    return {
        "regime": str(regime) if regime is not None else "",
        "adx_14": float(adx_14) if isinstance(adx_14, (int, float)) else None,
        "vol_regime": str(vol_regime) if vol_regime is not None else "",
    }


class ConvictionMetaBuilder(DatasetBuilder):
    family: ClassVar[str] = "conviction_meta"
    # v2 (M19 T0.3): adds the optional pretrained-TSFM embedding block
    # (`tsfm_emb_0..31`, as-of joined from an `embedding_path` side-stream). The
    # columns are ALWAYS present (0.0 when no `embedding_path` given), so the v1
    # manifest — which never lists them in `feature_columns` — is unaffected.
    builder_version: ClassVar[str] = "v2"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "won-from-pnl-v1"
    schema: ClassVar[Mapping[str, type]] = {
        # identity / join
        "order_package_id": str,
        "trade_id": int,
        "created_at": str,
        # context features (decoded from meta / signal_logic)
        "strategy_name": str,
        "symbol": str,
        "direction": str,
        "regime": str,
        "adx_14": float,
        "vol_regime": str,
        # calibrated conviction-lens inputs (build_conviction_inputs)
        "c_strat": float,
        "c_setup": float,
        "c_wr": float,
        "c_reg": float,
        # optional pretrained-TSFM embedding block (as-of; 0.0 when absent)
        **{col: float for col in EMBEDDING_FEATURE_COLUMNS},
        # raw decision signal (kept for provenance; NOT a feature — leakage-safe)
        "confidence": float,
        # outcomes / labels
        "pnl": float,
        "pnl_percent": float,
        "won": bool,
        "r_multiple": float,
        "source": str,  # "live" (paper/backtest excluded by construction)
    }

    def iter_rows(
        self,
        *,
        db_path: Path | str | None = None,
        risk_pct: float = 1.0,
        r_cap: float = 3.0,
        strategy_name: str | None = None,
        symbol: str | None = None,
        embedding_path: Path | str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if risk_pct <= 0:
            raise ValueError(f"risk_pct must be > 0; got {risk_pct}")
        if r_cap <= 0:
            raise ValueError(f"r_cap must be > 0; got {r_cap}")

        if db_path is None:
            # Canonical resolver (env → $DATA_DIR/trade_journal.db → repo-root).
            from src.utils.paths import trade_journal_db_path  # noqa: PLC0415

            db_path = trade_journal_db_path()
        db_path = Path(db_path)
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")

        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            sql = (
                "SELECT "
                "  op.order_package_id   AS order_package_id, "
                "  op.strategy_name      AS strategy_name, "
                "  op.symbol             AS symbol, "
                "  op.direction          AS direction, "
                "  op.confidence         AS confidence, "
                "  op.signal_logic       AS signal_logic, "
                "  op.meta               AS meta, "
                "  op.model_scores       AS model_scores, "
                "  op.created_at         AS created_at, "
                "  t.id                  AS trade_id, "
                "  t.pnl                 AS pnl, "
                "  t.pnl_percent         AS pnl_percent "
                # Join on the trade->package back-reference: in this system
                # `trades.order_package_id` is the populated link, while
                # `order_packages.linked_trade_id` is almost always NULL (a
                # filled package closes via the reconciler without writing it
                # back). Joining on linked_trade_id produced an EMPTY dataset
                # (manifest_skipped:empty_dataset, 2026-06-16). One package can
                # have multiple trades (per account); the closed/non-backtest/
                # pnl filter keeps one row per filled close.
                "FROM trades t "
                "JOIN order_packages op ON t.order_package_id = op.order_package_id "
                "WHERE t.status = 'closed' "
                "  AND t.is_backtest = 0 "
                "  AND t.pnl IS NOT NULL "
                "  AND t.order_package_id IS NOT NULL"
            )
            params: list[Any] = []
            if strategy_name is not None:
                sql += " AND op.strategy_name = ?"
                params.append(strategy_name)
            if symbol is not None:
                sql += " AND op.symbol = ?"
                params.append(symbol)
            sql += " ORDER BY t.id ASC"

            payloads: list[dict[str, Any]] = []
            for row in conn.execute(sql, params):
                pnl = row["pnl"]
                if pnl is None:
                    continue  # belt-and-braces; the WHERE already filters

                meta = _decode_json_obj(row["meta"])
                signal_logic = _decode_json_obj(row["signal_logic"])
                model_scores = _decode_json_obj(row["model_scores"])

                strategy = row["strategy_name"] or ""
                confidence = (
                    float(row["confidence"])
                    if row["confidence"] is not None
                    else None
                )

                # The calibrated lens inputs — the EXACT live observe-only feature
                # space (no calibrators loaded → identity normalization, matching
                # the live default when the artifact is absent).
                lens_inputs, _prov = build_conviction_inputs(
                    strategy, confidence, model_scores or None
                )

                pnl_percent_raw = row["pnl_percent"]
                pnl_percent = (
                    float(pnl_percent_raw) if pnl_percent_raw is not None else 0.0
                )
                r_multiple = _clip(pnl_percent / float(risk_pct), float(r_cap))

                ctx = _context_from(meta, signal_logic)

                payload: dict[str, Any] = {
                    "order_package_id": str(row["order_package_id"] or ""),
                    "trade_id": int(row["trade_id"]),
                    "created_at": row["created_at"] or "",
                    "strategy_name": str(strategy),
                    "symbol": str(row["symbol"] or ""),
                    "direction": str(
                        canon_direction(row["direction"]) or row["direction"] or ""
                    ),
                    "regime": ctx["regime"],
                    "adx_14": ctx["adx_14"],
                    "vol_regime": ctx["vol_regime"],
                    "confidence": float(confidence) if confidence is not None else 0.0,
                    "pnl": float(pnl),
                    "pnl_percent": pnl_percent,
                    "won": bool(float(pnl) > 0),
                    "r_multiple": float(r_multiple),
                    "source": "live",
                }
                # Lens inputs: present columns get the calibrated value; absent
                # head slots are omitted (serialize as NULL → NaN for LightGBM).
                for col in _LENS_COLUMNS:
                    if col in lens_inputs:
                        payload[col] = float(lens_inputs[col])
                payloads.append(payload)
        finally:
            conn.close()

        # Attach the optional pretrained-TSFM embedding block (M19 T0.3),
        # as-of joined per `created_at` from a single-symbol side-stream. When
        # `embedding_path` is absent (or empty) every row gets a neutral 0.0
        # block — byte-for-byte the v1 feature space plus 32 inert columns the
        # v1 manifest never selects. The side-stream is single-symbol, so a
        # manifest using it MUST scope the build to one `symbol=` (otherwise a
        # non-target symbol's rows would carry-forward the wrong embedding).
        yield from _attach_embeddings(payloads, embedding_path)


def _attach_embeddings(
    payloads: list[dict[str, Any]], embedding_path: Path | str | None
) -> Iterator[Mapping[str, Any]]:
    emb_rows = (
        _load_funding_oi_rows(Path(embedding_path)) if embedding_path is not None else []
    )
    if not emb_rows:
        for payload in payloads:
            for col in EMBEDDING_FEATURE_COLUMNS:
                payload[col] = 0.0
            yield payload
        return

    # As-of (past-only) carry-forward per created_at. `_load_funding_oi_rows`
    # returns ts-ascending; sort the decisions by created_at so the join is
    # monotonic (they arrive id-ordered, which is only approximately time-ordered).
    emb_ts = [str(r.get("ts", "")) for r in emb_rows]
    payloads.sort(key=lambda p: str(p.get("created_at", "")))
    bar_ts = [str(p.get("created_at", "")) for p in payloads]
    for col in EMBEDDING_FEATURE_COLUMNS:
        vals = [(float(r[col]) if r.get(col) is not None else None) for r in emb_rows]
        aligned = _align_asof(bar_ts, emb_ts, vals)
        for payload, value in zip(payloads, aligned):
            payload[col] = _finite_or_zero(value)
    yield from payloads

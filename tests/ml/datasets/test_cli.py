"""Tests for `ml.datasets.cli` — specifically the `_cmd_build` path that
coerces family-arg `key=value` pairs through the builder's
`iter_rows` signature.

Production triggered this test file: every dataset family failed on
2026-05-13 because the CLI was leaving Path-typed and int/float-typed
kwargs as raw strings — e.g.

    setup_labels: TypeError on `risk_pct <= 0` (str vs int)
    account_context: AttributeError on `accounts_yaml_path.is_file()`
    review_journal: AttributeError on `comms_root.is_dir()`
    market_raw: TypeError "multiple values for keyword argument 'timeframe'"

These tests pin the coercion + reserved-kwarg-lift behaviour so the
same regression can't reach prod again.
"""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import Any

import pytest

from ml.datasets import cli


# ---------------------------------------------------------------------------
# _coerce_to_annotation — generic value-by-annotation coercion

class TestCoerceToAnnotation:
    def test_path_annotation_returns_path(self):
        out = cli._coerce_to_annotation("/tmp/x", Path)
        assert isinstance(out, Path)
        assert out == Path("/tmp/x")

    def test_int_annotation_returns_int(self):
        assert cli._coerce_to_annotation("42", int) == 42

    def test_float_annotation_returns_float(self):
        assert cli._coerce_to_annotation("1.5", float) == pytest.approx(1.5)

    def test_bool_true_variants(self):
        for s in ("true", "True", "TRUE", "1", "yes", "y", "on"):
            assert cli._coerce_to_annotation(s, bool) is True

    def test_bool_false_variants(self):
        for s in ("false", "0", "no", "n", "off"):
            assert cli._coerce_to_annotation(s, bool) is False

    def test_bool_invalid_raises(self):
        with pytest.raises(ValueError):
            cli._coerce_to_annotation("maybe", bool)

    def test_str_annotation_passes_through(self):
        assert cli._coerce_to_annotation("BTCUSDT", str) == "BTCUSDT"

    def test_no_annotation_passes_through(self):
        assert cli._coerce_to_annotation("raw", inspect.Parameter.empty) == "raw"

    def test_path_or_str_union_prefers_path(self):
        # market_features.iter_rows declares `market_raw_path: Path | str`
        # — we want Path because that's where .is_dir() lives.
        out = cli._coerce_to_annotation("/tmp/x", Path | str)
        assert isinstance(out, Path)

    def test_optional_int_coerces_int(self):
        out = cli._coerce_to_annotation("7", int | None)
        assert out == 7

    def test_optional_str_with_value_returns_str(self):
        out = cli._coerce_to_annotation("BTCUSDT", str | None)
        assert out == "BTCUSDT"


# ---------------------------------------------------------------------------
# _coerce_iter_kwargs — uses builder.iter_rows signature

class _FakeBuilder:
    def iter_rows(
        self,
        *,
        db_path: Path,
        risk_pct: float = 1.0,
        r_cap: float = 3.0,
        match_window_seconds: int = 60,
        include_archive: bool = True,
        strategy_name: str | None = None,
        **_: Any,
    ):
        yield {}


class TestCoerceIterKwargs:
    def test_full_builder_signature_coercion(self):
        raw = {
            "db_path": "/tmp/trade_journal.db",
            "risk_pct": "1.0",
            "r_cap": "3.0",
            "match_window_seconds": "60",
            "include_archive": "true",
            "strategy_name": "ict",
        }
        out = cli._coerce_iter_kwargs(_FakeBuilder(), raw)
        assert isinstance(out["db_path"], Path)
        assert out["db_path"] == Path("/tmp/trade_journal.db")
        assert out["risk_pct"] == pytest.approx(1.0)
        assert out["r_cap"] == pytest.approx(3.0)
        assert out["match_window_seconds"] == 60
        assert out["include_archive"] is True
        assert out["strategy_name"] == "ict"

    def test_unknown_key_passes_through_as_str(self):
        # iter_rows has **_: Any, so unknown keys have no specific
        # annotation and must remain as strings.
        out = cli._coerce_iter_kwargs(_FakeBuilder(), {"random_extra": "value"})
        assert out["random_extra"] == "value"

    def test_empty_kvs_returns_empty_dict(self):
        assert cli._coerce_iter_kwargs(_FakeBuilder(), {}) == {}


# ---------------------------------------------------------------------------
# _lift_reserved_into_args — collision resolution between kv and CLI flags

def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "output_dir": "/tmp/out",
        "version": "v001",
        "source": "test",
        "symbol_scope": None,
        "timeframe": None,
        "timezone_name": "UTC",
        "commit_sha": None,
        "notes": "",
        "overwrite": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestLiftReservedIntoArgs:
    def test_kv_fills_unset_cli_arg(self):
        # No --timeframe on CLI; `timeframe=1h` in kvs.
        args = _make_args(timeframe=None)
        iter_kwargs = {"timeframe": "1h", "db_path": Path("/tmp/db")}
        remaining = cli._lift_reserved_into_args(iter_kwargs, args)
        assert args.timeframe == "1h"
        assert "timeframe" not in remaining
        assert remaining["db_path"] == Path("/tmp/db")

    def test_explicit_cli_arg_wins_over_kv(self):
        # --timeframe 1h on CLI AND `timeframe=4h` in kvs. CLI wins.
        args = _make_args(timeframe="1h")
        iter_kwargs = {"timeframe": "4h"}
        remaining = cli._lift_reserved_into_args(iter_kwargs, args)
        assert args.timeframe == "1h"
        assert "timeframe" not in remaining  # kv silently dropped

    def test_non_reserved_kv_stays_in_remaining(self):
        args = _make_args()
        iter_kwargs = {"db_path": Path("/tmp/db"), "risk_pct": 1.0}
        remaining = cli._lift_reserved_into_args(iter_kwargs, args)
        assert remaining == {"db_path": Path("/tmp/db"), "risk_pct": 1.0}

    def test_multiple_reserved_kvs_lifted(self):
        args = _make_args(symbol_scope=None, timeframe=None)
        iter_kwargs = {"symbol_scope": "BTCUSDT", "timeframe": "1h", "db_path": Path("/x")}
        remaining = cli._lift_reserved_into_args(iter_kwargs, args)
        assert args.symbol_scope == "BTCUSDT"
        assert args.timeframe == "1h"
        assert remaining == {"db_path": Path("/x")}


# ---------------------------------------------------------------------------
# End-to-end: _cmd_build wiring via a stub builder

class _CapturingBuilder:
    """Captures the kwargs `build()` receives so the test can assert on them."""

    def __init__(self):
        self.captured: dict[str, Any] | None = None

    def iter_rows(
        self,
        *,
        db_path: Path,
        risk_pct: float = 1.0,
        accounts_yaml_path: Path | None = None,
        include_archive: bool = True,
        timeframe: str | None = None,
        **_: Any,
    ):
        yield {}

    def build(self, **kwargs):
        self.captured = kwargs

        class _Paths:
            root = Path("/tmp/fake-out")

        return _Paths()


def test_cmd_build_coerces_path_int_bool_and_lifts_timeframe(monkeypatch):
    """Regression: the 2026-05-13 production failures — Path/str, str vs int,
    and `multiple values for 'timeframe'` — all reproducible in one call.
    """
    fake = _CapturingBuilder()
    monkeypatch.setattr(cli, "get_builder", lambda _name: fake)

    args = _make_args(
        timeframe=None,  # not on CLI; kv should lift in
        symbol_scope="BTCUSDT",  # CLI sets it; matching kv should be dropped
    )
    args.family = "fake"
    args.family_arg = [
        "db_path=/tmp/trade_journal.db",
        "risk_pct=1.0",
        "accounts_yaml_path=/etc/x/accounts.yaml",
        "include_archive=true",
        "timeframe=1h",  # lifted into args.timeframe
        "symbol_scope=BTCUSDT",  # dropped (CLI wins)
        "unknown=raw",  # passes through as str
    ]

    rc = cli._cmd_build(args)
    assert rc == 0
    assert fake.captured is not None
    # Reserved kwargs flowed through the explicit build() params.
    assert fake.captured["timeframe"] == "1h"
    assert fake.captured["symbol_scope"] == "BTCUSDT"
    # iter_rows kwargs are coerced to the right types.
    assert isinstance(fake.captured["db_path"], Path)
    assert fake.captured["risk_pct"] == pytest.approx(1.0)
    assert isinstance(fake.captured["accounts_yaml_path"], Path)
    assert fake.captured["include_archive"] is True
    assert fake.captured["unknown"] == "raw"
    # Most importantly: no duplicate-timeframe TypeError.


def test_cmd_build_missing_eq_in_kv_raises(monkeypatch):
    fake = _CapturingBuilder()
    monkeypatch.setattr(cli, "get_builder", lambda _name: fake)
    args = _make_args()
    args.family = "fake"
    args.family_arg = ["not_a_kv"]
    with pytest.raises(SystemExit):
        cli._cmd_build(args)

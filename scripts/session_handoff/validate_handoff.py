#!/usr/bin/env python3
"""Validate a session-handoff JSON artifact.

Used by:
  * scripts/session_handoff/close_session.py before commit
  * .github/workflows/continue-work.yml inside the runner
  * tests/test_session_handoff_validate.py as the unit-of-truth check

Exit codes:
    0  valid
    1  schema or structural error (malformed JSON, missing fields, bad types)
    2  invariant error (sprint_id mismatch, ready_for_continue=false when
       --require-ready was passed, etc.)
    3  unexpected error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = REPO_ROOT / "automation" / "session_handoff" / "schema" / "handoff.schema.json"


class HandoffError(Exception):
    """Validation failure with a stable exit code."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise HandoffError(f"file not found: {path}", exit_code=1) from e
    except OSError as e:
        raise HandoffError(f"cannot read {path}: {e}", exit_code=1) from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HandoffError(f"{path}: malformed JSON: {e}", exit_code=1) from e


def validate_against_schema(data: Any, schema: dict) -> None:
    """Validate ``data`` against ``schema`` using jsonschema if available,
    otherwise a minimal shape check covering required fields and types."""
    try:
        import jsonschema  # type: ignore
    except ModuleNotFoundError:
        _minimal_validate(data, schema)
        return
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise HandoffError(
            f"schema violation at {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}",
            exit_code=1,
        ) from e


def _minimal_validate(data: Any, schema: dict) -> None:
    """Fallback used when jsonschema isn't installed.

    Covers required-field presence and very basic type rules. Loud
    enough to catch the bugs the workflow cares about (missing
    sprint_id, wrong schema_version, etc.); lighter than a real
    JSON-Schema engine.
    """
    if not isinstance(data, dict):
        raise HandoffError("handoff JSON must be an object at the root.", exit_code=1)
    required = schema.get("required", [])
    missing = [k for k in required if k not in data]
    if missing:
        raise HandoffError(
            f"missing required field(s): {', '.join(missing)}",
            exit_code=1,
        )
    if data.get("schema_version") != schema.get("properties", {}).get(
        "schema_version", {}
    ).get("const", 1):
        raise HandoffError(
            f"schema_version must be {schema['properties']['schema_version']['const']}, "
            f"got {data.get('schema_version')!r}",
            exit_code=1,
        )
    type_map = {
        "sprint_id": str,
        "sprint_title": str,
        "branch": str,
        "created_at": str,
        "created_by": str,
        "handoff_reason": str,
        "checkpoint_summary": str,
        "completed_items": list,
        "open_items": list,
        "next_actions": list,
        "blocked_items": list,
        "files_to_review": list,
        "commands_to_run": list,
        "tests_required": list,
        "guardrails": list,
        "continuation_prompt": str,
        "ready_for_continue": bool,
    }
    for key, expected in type_map.items():
        if key in data and not isinstance(data[key], expected):
            raise HandoffError(
                f"field {key!r} must be {expected.__name__}, got "
                f"{type(data[key]).__name__}",
                exit_code=1,
            )


def check_invariants(
    data: dict,
    *,
    expect_sprint_id: str | None,
    require_ready: bool,
) -> None:
    if expect_sprint_id is not None and data.get("sprint_id") != expect_sprint_id:
        raise HandoffError(
            f"sprint_id mismatch: handoff has {data.get('sprint_id')!r}, "
            f"expected {expect_sprint_id!r}.",
            exit_code=2,
        )
    if require_ready and not data.get("ready_for_continue", False):
        raise HandoffError(
            "ready_for_continue is false; refusing to continue. "
            "Set ready_for_continue=true once the artifact is complete.",
            exit_code=2,
        )
    reason = data.get("handoff_reason")
    if reason == "other" and not (data.get("handoff_reason_note") or "").strip():
        raise HandoffError(
            "handoff_reason='other' requires a non-empty handoff_reason_note.",
            exit_code=2,
        )


def validate_path(
    handoff_path: Path,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    expect_sprint_id: str | None = None,
    require_ready: bool = False,
) -> dict:
    """Validate the handoff at ``handoff_path``. Returns the parsed dict on
    success; raises ``HandoffError`` on failure."""
    schema = load_json(schema_path)
    if not isinstance(schema, dict):
        raise HandoffError(f"schema {schema_path} did not parse to an object.", exit_code=3)
    data = load_json(handoff_path)
    validate_against_schema(data, schema)
    if not isinstance(data, dict):
        raise HandoffError("handoff JSON must be an object at the root.", exit_code=1)
    check_invariants(
        data,
        expect_sprint_id=expect_sprint_id,
        require_ready=require_ready,
    )
    return data


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("handoff", type=Path, help="path to the handoff JSON to validate")
    p.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"path to JSON Schema (default: {DEFAULT_SCHEMA.relative_to(REPO_ROOT)})",
    )
    p.add_argument(
        "--expect-sprint-id",
        default=None,
        help="if set, fail when the handoff's sprint_id doesn't match",
    )
    p.add_argument(
        "--require-ready",
        action="store_true",
        help="fail when ready_for_continue is false (workflow uses this)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        data = validate_path(
            args.handoff,
            schema_path=args.schema,
            expect_sprint_id=args.expect_sprint_id,
            require_ready=args.require_ready,
        )
    except HandoffError as e:
        print(f"handoff validation failed: {e}", file=sys.stderr)
        return e.exit_code
    except Exception as e:  # noqa: BLE001  CLI surface
        print(f"unexpected error: {e}", file=sys.stderr)
        return 3
    print(
        f"handoff OK: sprint_id={data.get('sprint_id')!r} "
        f"branch={data.get('branch')!r} "
        f"ready_for_continue={data.get('ready_for_continue')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

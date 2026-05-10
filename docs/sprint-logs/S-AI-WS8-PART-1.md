# S-AI-WS8-PART-1 — Shadow predictions inspector CLI

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-logs/S-AI-WS7-PART-2.md`](S-AI-WS7-PART-2.md)
**Status:** ✅ COMPLETE

## Goal

Make `runtime_logs/shadow_predictions.jsonl` observable. WS7's
shadow harness writes one JSON line per predictor call, but no
tooling reads it — once an operator opts in via
`shadow_model_ids`, scores accumulate without anyone able to
verify what the model is doing. PART-1 ships the smallest
meaningful operator surface: a CLI inspector + per-model
aggregator.

## Decisions

- **Pure-logic module + thin CLI wrapper.** All parsing,
  filtering, and aggregation lives in
  `ml/shadow/inspector.py` (importable, testable in isolation).
  `ml/cli.py` holds two thin subcommand handlers
  (`shadow-inspect`, `shadow-stats`) that compose those helpers.
  The dashboard endpoint queued for WS8-PART-2 will reuse the
  same module — no duplicate parsing.
- **Streaming + per-line skip on bad JSON.** `iter_records`
  reads the file line-by-line and yields `ShadowRecord`
  instances. Malformed lines (truncated tails from a partial
  fsync, ill-formed JSON, missing required fields,
  unparseable timestamps) are logged at WARNING with
  `lineno=N` and skipped — never raise. The audit log is
  operational data, not a source-of-truth artifact; one bad
  line cannot poison the whole inspector.
- **Typed `ShadowRecord` dataclass.** Frozen dataclass with the
  five fields ShadowPredictor writes (`predicted_at_utc`,
  `model_id`, `stage`, `score`, `row_keys`). Construction
  validates every field — downstream consumers can trust the
  types without re-checking.
- **Two subcommands, not one with modes.**
  `shadow-inspect` returns the most-recent N records (newest
  first); `shadow-stats` returns the per-`(model_id, stage)`
  aggregate. Different output shapes, different cognitive
  loads — keep them separate.
- **Filter API is shared.** `--model-id`, `--stage`, `--since`
  apply to both subcommands and are implemented once in
  `filter_records(records, *, model_id, stage, since)`.
- **`--since` parsed as ISO-8601, naïve assumed UTC.**
  Matches the rest of the codebase (heartbeat, audit log,
  registry). `_parse_since` raises `SystemExit` on a bad
  format with a helpful message rather than silently filtering
  to nothing.
- **Empty-output messaging is explicit.** When no records
  match, the CLI prints `(no shadow predictions matched)` and
  exits 0. Distinguishing "log file missing" from "log file
  exists but no matches" is filed for PART-2 (dashboard
  endpoint can return the difference structurally).
- **Default log path is `runtime_logs/shadow_predictions.jsonl`.**
  Matches the `DEFAULT_LOG_PATH` constant in
  `ml/shadow/factory.py`. `--log` overrides for testing or
  diagnostic snapshots.

## Deliverables

- `ml/shadow/inspector.py` (new):
  - `ShadowRecord` (frozen dataclass) + `record_from_dict(raw)`
    coercer with validation (every field required; unparseable
    timestamps and non-finite scores raise `ValueError`).
  - `iter_records(log_path, *, logger=None)` — streaming JSONL
    reader. Per-line failures logged + skipped.
  - `filter_records(records, *, model_id=None, stage=None,
    since=None)` — composable filter generator.
  - `ModelStats` dataclass + `aggregate(records)` — per-
    `(model_id, stage)` aggregate (count, score mean/min/max,
    first/last seen, `row_keys_seen` set). Result sorted by
    count desc then model_id asc for deterministic output.
  - `format_inspect_table(records, limit)` and
    `format_stats_table(stats)` — fixed-width text rendering.
- `ml/shadow/__init__.py` — re-exports the new public surface.
- `ml/cli.py`:
  - Two new subcommands wired through `_build_parser`:
    `shadow-inspect` and `shadow-stats`. Both accept `--log`,
    `--model-id`, `--stage`, `--since`. `shadow-inspect` also
    accepts `--limit` (default 50).
  - `_parse_since(raw)` — shared ISO-8601 parser; UTC default
    for naïve.
- `tests/ml/test_shadow_inspector.py` (new) — 29 unit tests
  across `record_from_dict`, `iter_records`, `filter_records`,
  `aggregate`, and the table formatters.
- `tests/ml/test_shadow_cli.py` (new) — 6 end-to-end CLI tests
  exercising both subcommands through `ml.cli.main(argv)`:
  default output, filters, no-match messaging, aggregation,
  `--since` filtering, bad `--since` rejection.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/` — 301 / 301 pass
      (266 prior + 29 inspector + 6 CLI). No skips.
- [x] `ruff check` clean on all new + modified files.
- [x] `python -m ml shadow-inspect --help` and
      `python -m ml shadow-stats --help` both exit 0 with
      usable help text (verified by argparse smoke test).
- [x] CLI exits 0 with friendly message when the log file
      doesn't exist.
- [x] CLI exits non-zero with a helpful message when
      `--since` is malformed (don't silently mis-filter).
- [x] Bad JSON / missing fields are logged + skipped, never
      raise (tested via 3 separate scenarios).

## Out of scope (filed for follow-ups)

- **WS8-PART-2 — Dashboard endpoint.** New
  `GET /api/bot/shadow/predictions?...` and
  `GET /api/bot/shadow/stats?...` routes that reuse the same
  inspector module. Consumed by the Vercel dashboard. Same
  filter surface as the CLI.
- **WS8-PART-3 — Drift detector.** Compare shadow score
  distribution against the deterministic decision distribution
  (or against realised outcomes once available); flag
  divergence over a rolling window. Requires per-trade
  outcome wiring that doesn't exist yet — lift from WS5-A's
  `trade_outcomes` once it's populated post-deploy.
- **Audit log rotation.** `runtime_logs/shadow_predictions.jsonl`
  needs daily rotation when shadow mode is actually active.
  Same pattern as `signal_audit.jsonl`. Filed; not urgent
  until real models are loaded.
- **Per-feature distribution tracking.** Today the inspector
  records `row_keys` (just the column names) but not the
  values. Recording values opens privacy/PII questions and
  triples the log size; left explicit until there's a
  concrete drift-detection use case.

## Live runtime impact

None. CLI is operator-facing diagnostic tooling; it reads from
`runtime_logs/shadow_predictions.jsonl` and writes nothing.
ShadowPredictor's audit log writer is unchanged from PART-2.
Fresh dependencies: zero (stdlib-only).

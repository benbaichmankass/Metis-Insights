"""The ping DELIVERY ledger is readable, and it reads the WRITER's path.

TWO DISTINCT REGRESSIONS, and the second is the one enumeration misses.

1. THE LEDGER HAD NO READ SURFACE. `scripts/notify_on_pull.py` records the
   sha256 of every `docs/claude/pending-pings.jsonl` line it enqueues into
   `runtime_logs/pending_pings_delivered.txt`, and nothing could read it back.
   Measured 2026-09-02 against the RUNNING web-api: `_LOG_FILES` carried 45
   names and none was ping/notify/delivery related. So "the operator's work
   digest was delivered" and "the drain never ran" were indistinguishable from
   outside -- alerting's version of the
   `BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE`
   class, at least its fifth instance.

2. ⚠️ THE OBVIOUS ENTRY WOULD HAVE POINTED AT A FILE NOTHING WRITES. Every
   other `_LOG_FILES` entry resolves through `runtime_logs_dir()`, so writing
   this one that way is the natural move -- and here it is WRONG.
   `notify_on_pull.py` never calls the path helpers; it hardcodes its own
   `REPO_ROOT / "runtime_logs"`. It runs from `ict-git-sync.service`, which
   carries no data-dir drop-in, while this reader runs in
   `ict-web-api.service`, which DOES (`deploy/dropins/data-dir.conf`,
   `DATA_DIR=/data/bot-data`). Under the helper the reader would look in
   `/data/bot-data/runtime_logs/` while the writer writes to
   `/home/ubuntu/ict-trading-bot/runtime_logs/`, and the endpoint would report
   an eternally-absent file -- which a reader takes as "nothing was ever
   delivered". That is the writer/reader path split that hid the
   ict-hourly-snapshot balance stall for ~3 weeks (BL-20260611-M15-2).

The second test below derives its expectation FROM THE WRITER rather than
restating a path, so a later refactor that "tidies" the entry onto
`runtime_logs_dir()` -- or that moves the writer -- fails on the commit that
does it, instead of silently serving a file nobody writes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _writer_ledger_path() -> Path:
    """`DELIVERED_HASHES` as the WRITER itself resolves it.

    Imported by file path rather than restated, so this is the writer's own
    answer and not a second copy free to drift from it.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "_notify_on_pull_under_test", ROOT / "scripts" / "notify_on_pull.py"
    )
    assert spec and spec.loader, "could not load scripts/notify_on_pull.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DELIVERED_HASHES


def test_the_delivery_ledger_is_readable():
    from src.web.api.routers import diag

    assert "pending_pings_delivered" in diag._LOG_FILES, (
        "the ping delivery ledger has no diag read surface, so a DELIVERED "
        "operator ping and one the drain never ran for are indistinguishable "
        "from outside"
    )


def test_the_read_surface_points_at_the_writers_path():
    """THE REGRESSION DETECTOR -- expectation derived from the writer."""
    from src.web.api.routers import diag

    served = diag._LOG_FILES["pending_pings_delivered"]
    written = _writer_ledger_path()

    assert served == written, (
        f"diag serves {served} but scripts/notify_on_pull.py writes "
        f"{written}. A reader pointed at a path nothing writes reports an "
        "absent file, and absence here reads as 'nothing was ever delivered' "
        "-- the writer/reader split of BL-20260611-M15-2. If the writer moved, "
        "move this entry with it; do NOT route it through runtime_logs_dir(), "
        "which resolves under DATA_DIR in ict-web-api.service and NOT in "
        "ict-git-sync.service, where the writer runs."
    )


def test_absence_of_the_ledger_is_not_evidence_of_non_delivery():
    """The endpoint must report absence as a fact about the FILE, not a verdict.

    The ledger is .gitignore'd and VM-local, so a re-provision or re-clone
    resets it while the pings it recorded were still sent. `present: false`
    therefore means "the drain has never delivered anything on this VM", never
    "this ping was not delivered" -- and the doc row must say so, because the
    caller reading it is the one at risk of drawing the stronger conclusion.
    """
    doc = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    row = next(
        (ln for ln in doc.splitlines() if "GET /api/diag/log_file" in ln), None
    )
    assert row, "could not locate the log_file row in CLAUDE.md"
    assert "pending_pings_delivered" in row
    lowered = row.lower()
    assert "absent" in lowered and "not that" in lowered, (
        "the log_file row documents pending_pings_delivered but does not warn "
        "what absence does NOT mean; a caller will read an empty ledger as "
        "proof of non-delivery"
    )

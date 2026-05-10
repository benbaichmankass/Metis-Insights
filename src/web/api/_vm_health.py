"""Shared psutil-backed vm_health snapshot.

S-067 follow-up #9 — consolidates the two forks that lived under
``src/web/api/routers/dashboard.py`` and
``src/web/api/routers/diag.py``. Post-S-067 they had identical bodies
(both log on psutil failure, both return ``None`` per field on the
wire). One source of truth eliminates the next "I fixed the dashboard
copy but not the diag copy" round-trip.

Wire shape: ``{"cpu": float | None, "memory": float | None, "disk":
float | None}``. ``None`` per field signals "measurement unavailable"
(distinct from a real ``0.0`` reading) so the dashboard can render
``—`` instead of a fabricated ``0%``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def vm_health() -> Dict[str, Any]:
    """Return the canonical vm_health dict.

    Catches psutil import failure + sampling errors. The
    ``noqa: PLC0415`` import is intentional — psutil is the only
    optional dependency in this module so deferring the import
    keeps the module import-cheap on hosts that don't have it.
    """
    try:
        import psutil  # noqa: PLC0415
        return {
            "cpu": psutil.cpu_percent(interval=0.1),
            "memory": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage("/").percent,
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: psutil import + sampling failures both surface as None per field by design (S-067 contract); the warning log keeps the failure visible.
        logger.warning("vm_health: psutil sample failed: %s", exc)
        return {"cpu": None, "memory": None, "disk": None}

"""ROADMAP_MACRO M1 — keyless EIA **bulk** adapter for the energy event kinds.

Why this exists (BL-20260730-EIA-SERIES-IDS-NOT-FRED)
----------------------------------------------------
The two ENERGY event kinds (weekly crude-oil ending stocks; weekly Lower-48
working natural-gas underground storage) were configured with EIA ``dnav`` series
codes (``WCESTUS1`` / ``WNGSTUS``) pointed at the **FRED** fetcher — a category
error, so ``fredgraph.csv`` 404'd them for the producer's entire life. Verified
2026-08-01 from a FRED-reachable host: both ids 404 on FRED, FRED's *entire*
weekly-EIA catalogue (92 series) is prices with **zero** inventory/storage series,
and ~11 plausible candidate FRED ids all 404. So FRED does not carry these keyless
under any id — the fix is a SOURCE change, not an id edit.

EIA publishes them keyless via its **bulk manifest**
(``https://api.eia.gov/bulk/{DATASET}.zip`` → a single NDJSON ``.txt`` in the zip;
no API key, unlike the ``api.eia.gov/v2`` REST API which returns ``API_KEY_MISSING``).
Confirmed live 2026-08-01:

    PET.WCESTUS1.W          U.S. Ending Stocks excluding SPR of Crude Oil, Weekly (Thousand Barrels)
    NG.NW2_EPG0_SWO_R48_BCF.W  Weekly Lower 48 States Natural Gas Working Underground Storage (Bcf)

Same shape as ``fred_adapter``: the parse is **pure + unit-tested**, only a thin
network layer touches the wire, and that layer is **off-VM-guarded** (won't hit
the network on the live trading VM unless ``ICT_OFFVM_BUILD_HOST`` is set) and
takes an injectable ``urlopen`` so CI needs no network. **Stdlib-only** (urllib /
zipfile / json) — no new dependency, so the backfill stays runnable on a bare
runner. No order path, no DB write, no live-VM touch.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from collections.abc import Sequence

_BULK_URL = "https://api.eia.gov/bulk/{}.zip"  # 301 → www.eia.gov/opendata/bulk (urllib follows)
_TRUTHY = {"1", "true", "yes", "on"}

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure parsing (unit-tested, no network).
# ---------------------------------------------------------------------------


def dataset_of(series_id: str) -> str:
    """The bulk dataset a series lives in — the token before the first dot.

    ``PET.WCESTUS1.W`` → ``PET``; ``NG.NW2_EPG0_SWO_R48_BCF.W`` → ``NG``. Each
    dataset is one downloadable ``{DATASET}.zip``, so grouping ids by this key
    downloads each large bulk file at most once."""
    return series_id.split(".", 1)[0]


def _norm_date(raw: str) -> str | None:
    """EIA bulk dates are ``YYYYMMDD`` (weekly/daily) → ISO ``YYYY-MM-DD``.

    Anything that is not an 8-digit day stamp (monthly ``YYYYMM``, annual
    ``YYYY``) is out of scope for these weekly series and is skipped rather than
    guessed at."""
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def parse_eia_bulk_ndjson(
    text: str, wanted: Sequence[str]
) -> dict[str, list[tuple[str, float]]]:
    """Parse a bulk NDJSON body → ``{series_id: [(date, value), ...]}`` ascending.

    The bulk ``.txt`` is one JSON object per line; series records carry
    ``series_id`` + ``data`` (a ``[[YYYYMMDD, value], ...]`` list, **newest-first**),
    interleaved with category records that have neither. Only the requested ids are
    kept. A ``null`` value or a non-day date stamp is skipped (never fabricated);
    the result is re-sorted ascending by date so ``history[i]`` is chronological —
    the point-in-time backfill depends on that order."""
    want = set(wanted)
    out: dict[str, list[tuple[str, float]]] = {sid: [] for sid in want}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or '"series_id"' not in line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        sid = rec.get("series_id")
        if sid not in want:
            continue
        pairs: list[tuple[str, float]] = []
        for obs in rec.get("data") or []:
            if not isinstance(obs, (list, tuple)) or len(obs) < 2:
                continue
            date_s, val = obs[0], obs[1]
            iso = _norm_date(date_s)
            if iso is None or val is None:
                continue
            try:
                pairs.append((iso, float(val)))
            except (ValueError, TypeError):
                continue
        pairs.sort(key=lambda p: p[0])  # ascending == chronological (ISO dates)
        out[sid] = pairs
    return out


# ---------------------------------------------------------------------------
# Thin network layer (off-VM-guarded, urlopen-injectable).
# ---------------------------------------------------------------------------


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def fetch_eia_series_history_dated(
    series_ids: Sequence[str], *, urlopen=None, timeout: float = 60.0
) -> dict[str, list[tuple[str, float]]]:
    """Dated full history for each EIA bulk series → ``{sid: [(date, val), ...]}``.

    Downloads each needed dataset zip **once** (grouped by :func:`dataset_of`),
    parses the NDJSON in memory, and extracts the requested series. Best-effort per
    dataset: a fetch/parse failure degrades every id in that dataset to ``[]`` (and
    logs), never fatal — the backfill's own "empty history is a hard error" check is
    what turns an empty series into a loud failure, so a silent ``[]`` here can't
    masquerade as success downstream.

    **Off-VM guard:** without an injected ``urlopen``, refuses unless
    ``ICT_OFFVM_BUILD_HOST`` is set, so the live trading VM never opens an EIA
    socket. Tests inject a fake ``urlopen`` returning a small zip."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_eia_series_history_dated: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request

        urlopen = urllib.request.urlopen

    out: dict[str, list[tuple[str, float]]] = {sid: [] for sid in series_ids}
    by_dataset: dict[str, list[str]] = {}
    for sid in series_ids:
        by_dataset.setdefault(dataset_of(sid), []).append(sid)

    for dataset, ids in by_dataset.items():
        try:
            with urlopen(_BULK_URL.format(dataset), timeout=timeout) as resp:
                blob = resp.read()
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                # The bulk zip holds exactly one NDJSON .txt (e.g. PET.txt / NG.txt).
                names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
                if not names:
                    raise ValueError(f"{dataset}.zip has no .txt member")
                text = zf.read(names[0]).decode("utf-8", errors="replace")
            parsed = parse_eia_bulk_ndjson(text, ids)
            for sid in ids:
                out[sid] = parsed.get(sid, [])
        except Exception as exc:  # noqa: BLE001
            # As with the FRED adapter: a source outage degrades to empty; log so
            # the degradation is legible rather than invisible.
            _log.warning("EIA bulk fetch failed for dataset %s (%s): %s", dataset, ids, exc)
            for sid in ids:
                out[sid] = []
    return out

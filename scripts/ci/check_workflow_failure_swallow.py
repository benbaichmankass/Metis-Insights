#!/usr/bin/env python3
"""Forbid `|| echo` / `|| true` on a load-bearing fetch/producer step.

This is the idiom that kept BL-20260730-M1-PRICE-JOIN-DEAD green for the
producer's entire life: the fetch degraded to a warning, the study read an empty
panel as a soft zero, and the run exited 0 with a plausible verdict.

Lifted verbatim out of the inline heredoc in the retired
`artifact-validity-guard.yml` during the CI fan-out consolidation
(BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES). Same producer pattern, same
swallow pattern, same `allow-degraded:` escape hatch, same exit code — the only
change is that it now lives in a file that can be run and unit-tested on its own
instead of only inside a workflow.

    python3 scripts/ci/check_workflow_failure_swallow.py
"""
from __future__ import annotations

import pathlib
import re
import sys
from typing import List

PRODUCER = re.compile(
    r'(fetch_[a-z0-9_]*\.py'
    r'|build_[a-z0-9_]*\.py'
    r'|analyze_[a-z0-9_]*\.py'
    r'|_produce\.py|_backfill\.py|_snapshot\.py|_resolve\.py)'
)
SWALLOW = re.compile(r'\|\|\s*(echo|true)\b')


def find_hits(workflow_dir: pathlib.Path) -> List[str]:
    hits: List[str] = []
    for p in sorted(workflow_dir.glob('*.yml')):
        raw = p.read_text(encoding='utf-8', errors='replace').splitlines()
        buf, start = '', None
        for i, line in enumerate(raw, 1):
            if start is None:
                start = i
            stripped = line.rstrip()
            if stripped.endswith('\\'):
                buf += stripped[:-1] + ' '
                continue
            buf += stripped
            if PRODUCER.search(buf) and SWALLOW.search(buf) \
               and 'allow-degraded:' not in buf:
                hits.append(f'{p}:{start}: {buf.strip()[:200]}')
            buf, start = '', None
    return hits


def main() -> int:
    hits = find_hits(pathlib.Path('.github/workflows'))
    if hits:
        print('::error::A load-bearing producer/fetch step swallows its own '
              'failure. This is the idiom that kept BL-20260730-M1-PRICE-JOIN-DEAD '
              "green for the producer's entire life: the fetch degraded to a "
              'warning, the study read an empty panel as a soft zero, and the run '
              'exited 0 with a plausible verdict.')
        for h in hits:
            print('  ' + h)
        print('')
        print('Fix: let the step fail (set -e) and assert the fetched inputs are '
              'non-empty before using them. If a degraded fetch is genuinely '
              "acceptable, annotate the command with "
              "'# allow-degraded: <BL-id> until:<YYYY-MM-DD> <reason>' (owner + expiry "
              'ENFORCED by check_allow_degraded.py).')
        return 1
    print('OK — no load-bearing producer/fetch step swallows its failure.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

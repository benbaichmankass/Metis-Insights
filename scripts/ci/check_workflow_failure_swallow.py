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

import argparse
import pathlib
import re
import sys
import tempfile
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


def self_test() -> int:
    """Plant a swallowed producer step and require main() to REFUSE it.

    This guard's own subject is a CI failure that gets silently swallowed, so
    shipping it with no exercised failure path was the defect it exists to
    catch, one level up (F-05 of the 2026-09-09 full-system audit). The bar is
    not that these controls print; it is that `main()` RETURNS NON-ZERO on a
    planted violation and zero once the violation is removed — asserted here on
    the return value, never on stdout.
    """
    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f'  FAIL - {label}: got {got!r}, want {want!r}')
        else:
            print(f'  PASS - {label}')

    with tempfile.TemporaryDirectory() as td:
        wf = pathlib.Path(td)
        step = "        run: python3 scripts/macro/fetch_prices.py || echo 'skip'\n"

        # 1. THE PLANT: a producer whose failure is swallowed must FAIL.
        (wf / 'planted.yml').write_text('jobs:\n  x:\n    steps:\n      - name: p\n' + step)
        check('a swallowed producer step makes main() return 1',
              main(['--dir', str(wf)]), 1)
        check('...and it is REPORTED, not merely counted',
              any('planted.yml' in h for h in find_hits(wf)), True)

        # 2. REMOVE THE PLANT: the same tree without it must PASS. Without this
        #    half, a guard that returns 1 unconditionally would look healthy.
        (wf / 'planted.yml').unlink()
        check('main() returns 0 once the plant is removed',
              main(['--dir', str(wf)]), 0)

        # 3. The escape hatch must actually rescue, or authors cannot land a
        #    genuinely-degraded fetch and will delete the guard instead.
        # The marker is SPLIT across two literals on purpose. Spelled whole it
        # is an annotation in this file's own source, and `check_allow_degraded.py`
        # scans source: it read this fixture as a real, un-owned degradation
        # exception and failed artifact-validity-guard. Joined at runtime the
        # fixture is byte-identical, so the control below is unweakened -- do not
        # "tidy" it back into one string.
        (wf / 'allowed.yml').write_text(
            'jobs:\n  x:\n    steps:\n      - name: p\n'
            "        run: python3 scripts/macro/fetch_prices.py || echo 'x'  "
            "# allow-" "degraded: BL-SELFTEST until:2099-01-01 reason\n")
        check('an `allow-degraded:` annotation rescues the step',
              main(['--dir', str(wf)]), 0)
        (wf / 'allowed.yml').unlink()

        # 4. NEGATIVE CONTROLS - each holds ONE half of the conjunction, so a
        #    probe that fired on either alone would be caught here.
        (wf / 'no_swallow.yml').write_text(
            'jobs:\n  x:\n    steps:\n      - name: p\n'
            '        run: python3 scripts/macro/fetch_prices.py\n')
        check('a producer that does NOT swallow is not a hit',
              main(['--dir', str(wf)]), 0)
        (wf / 'no_swallow.yml').unlink()

        (wf / 'not_producer.yml').write_text(
            'jobs:\n  x:\n    steps:\n      - name: p\n'
            "        run: rm -f /tmp/scratch || true\n")
        check('a swallow on a NON-producer step is not a hit',
              main(['--dir', str(wf)]), 0)
        (wf / 'not_producer.yml').unlink()

        # 5. An EMPTY directory returning 0 is the honest reading, and it is
        #    stated so that a future reader cannot mistake control 2's pass for
        #    evidence the probe ran. Controls 1 and 2 together are that evidence.
        check('an empty workflow dir returns 0', main(['--dir', str(wf)]), 0)

    if fails:
        print('\n'.join(fails))
        print('\nSELF-TEST FAILED')
        return 1
    print('\nALL PASS')
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--self-test', action='store_true',
                    help='run the planted-failure controls and exit')
    ap.add_argument('--dir', default='.github/workflows',
                    help=argparse.SUPPRESS)  # test seam; CI never passes it
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    hits = find_hits(pathlib.Path(args.dir))
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

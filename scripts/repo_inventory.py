#!/usr/bin/env python3
# Lightweight repo inventory for cleanup decisions. No third-party deps.
from __future__ import annotations

from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv", "env"}
LARGE_BYTES = 500_000
JUNK_SUFFIXES = (".bak", ".save", ".tmp", "~")

def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path

def main() -> int:
    files = list(iter_files())
    ext_counts = Counter(path.suffix.lower() or "<no_ext>" for path in files)
    large = [(p, p.stat().st_size) for p in files if p.stat().st_size > LARGE_BYTES]
    junk = [p for p in files if p.name.endswith(JUNK_SUFFIXES)]

    print(f"Repo root: {ROOT}")
    print(f"Files scanned: {len(files)}")
    print("\nTop extensions:")
    for ext, count in ext_counts.most_common(15):
        print(f"  {ext}: {count}")

    print(f"\nLarge files > {LARGE_BYTES:,} bytes:")
    if large:
        for path, size in sorted(large, key=lambda x: x[1], reverse=True):
            print(f"  {path.relative_to(ROOT)} — {size:,} bytes")
    else:
        print("  none")

    print("\nJunk candidates:")
    if junk:
        for path in junk:
            print(f"  {path.relative_to(ROOT)}")
    else:
        print("  none")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

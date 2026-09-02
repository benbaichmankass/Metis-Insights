#!/usr/bin/env python3
"""manifest-scope-constants — an ML manifest may not declare a feature its OWN
dataset scope makes structurally constant, nor a column no builder emits, nor a
categorical the trainer will reject.

MB-20260829-MES-1D-DECLARES-A-FEATURE-THAT-CANNOT-VARY-AT-ITS-OWN-TIMEFRAME
MB-20260829-MANIFESTS-DECLARE-COLUMNS-THE-DATASET-NEVER-PROVIDES-AND-NOTHING-CHECKS-AT-COMMIT

THE GAP THIS CLOSES. The manifest<->dataset contract is validated ONLY at train
time, on the trainer, inside a cycle that returns rc=0 and a green service. So a
manifest merges clean, is published, appears in the registry — and then silently
never trains. Measured cost of one instance: `mes-regime-1d-lgbm-v2` declared
`hour_of_day` on a DAILY bar, where the column has one value by construction; the
trainer's dataset audit flagged it `zero_fraction 1.0000` and enforced a skip, and
the model sat 34.0 DAYS untrained against a 7.0-day threshold while the cycle
reported rc=0. The audit was right the whole time. Nobody was reading it.

WHAT THIS GUARD IS, PRECISELY. It is the commit-time half of that contract, and it
checks the three things that are decidable WITHOUT the dataset — i.e. from the
manifest and the builder class alone:

  C1  STRUCTURALLY CONSTANT FEATURE (the mes-1d class). A declared feature that the
      manifest's own `dataset:` scope pins to a single value: `hour_of_day` on a bar
      >= 1d, `dayofweek` on a bar >= 1w, `symbol` when `symbol_scope` names one
      symbol, `timeframe` when `timeframe` is not `all`. These need no dataset to
      falsify — the manifest contradicts itself.

  C2  COLUMN NO BUILDER EMITS. A declared `feature_columns` entry (or
      `target_column`) absent from the declared family builder's `schema` ClassVar.

  C3  CATEGORICAL NOT IN FEATURES. `ml/trainers/lightgbm_multiclass.py:119-123`
      RAISES on `categorical_columns` naming something absent from
      `feature_columns`. That is a hard training FAILURE, strictly worse than the
      silent skip C1 catches — and it is exactly the trap a naive C1 fix falls into,
      because `hour_of_day` appeared in BOTH lists on the mes-1d manifest. Removing
      it from `feature_columns` alone would have converted a skipped manifest into a
      crashing one. C3 exists so that the fix for C1 cannot make things worse.

⚠️ WHAT THIS GUARD DOES **NOT** CATCH, STATED PLAINLY BECAUSE THE BACKLOG ROW
ASSUMED OTHERWISE. The row named at the top of this docstring prescribes a
commit-time check that a manifest's declared columns "are producible by its declared
dataset family", and names 10 columns across 3 manifests as the population it would
catch. MEASURED
2026-09-02 over all 76 manifests under `ml/configs/*.yaml`, with a positive control
(an injected `__NOT_A_REAL_COLUMN__` IS flagged): **C2 finds ZERO offenders, and
all 10 of those columns ARE in their family builder's schema.** The manifest is not
the wrong side of that contract in a single one of those cases.

The real mechanism is on the SHARD side, and it is not visible at commit time:
`ml/datasets/builder.py:45` resolves a shard to
`<root>/<family>/<symbol_scope>/<timeframe>/<version>/`, where `version` is a
hand-chosen label; `ml/datasets/families/market_features.py:412-414` states that
`builder_version` "is metadata-only (it does not gate dataset path resolution)";
and `builder.py:134` REFUSES to rebuild into an existing version dir. So a shard
materialised under an older builder keeps its narrower schema forever, and a
manifest pinning that version reads the stale shard. C2 cannot see that, and this
docstring says so rather than letting a green run imply the class is covered.
The shard-side half belongs where the shards are (the trainer), comparing
`metadata.json`'s own `schema` / `builder_version` against the manifest.

C2 is therefore kept as a REGRESSION guard, honestly labelled: it has never fired
on this repo's history and is not claimed to have.

POPULATION IS ALWAYS PRINTED. A guard that says "OK" without saying over what is
the "green that checked nothing" shape this repo has a rule about.

⚠️ AN UNPARSEABLE TIMEFRAME IS NOT A PASS. `all` is a legitimate wide scope and is
skipped for C1's timeframe rules with that stated. Anything else that does not
parse as `<n><m|h|d|w>` is reported as `unreadable` and FAILS, naming the manifest
— "we could not decide" and "we decided it is fine" are opposite facts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "ml" / "configs"

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_DAY = 86400
_WEEK = 604800

# A declared feature -> the scope condition that makes it structurally constant.
# Keep this table SHORT and only for columns that actually exist in a builder
# schema; a rule for a column no builder emits would be unfalsifiable decoration.
_PERIODIC = {"hour_of_day": _DAY, "dayofweek": _WEEK}


def _timeframe_seconds(tf: str):
    """Return (seconds, readable). `all` -> (None, True); junk -> (None, False)."""
    tf = (tf or "").strip()
    if tf == "all":
        return None, True
    m = re.fullmatch(r"(\d+)([mhdw])", tf)
    if not m:
        return None, False
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)], True


def _builder_schemas() -> Dict[str, set]:
    sys.path.insert(0, str(REPO))
    from ml.datasets.registry import FAMILY_REGISTRY  # noqa: E402

    return {
        fam: set(getattr(cls, "schema", {}) or {})
        for fam, cls in FAMILY_REGISTRY.items()
    }


def findings(manifests: List[Dict[str, Any]], schemas: Dict[str, set]) -> List[dict]:
    out: List[dict] = []
    for man in manifests:
        mid = man.get("model_id") or man.get("__path__")
        tcfg = man.get("trainer_config") or {}
        ds = man.get("dataset") or {}
        feats = [str(c) for c in (tcfg.get("feature_columns") or [])]
        cats = [str(c) for c in (tcfg.get("categorical_columns") or [])]
        target = tcfg.get("target_column")
        tf_raw = str(ds.get("timeframe") or "")
        sym = str(ds.get("symbol_scope") or "")
        fam = str(ds.get("family") or "")
        secs, readable = _timeframe_seconds(tf_raw)

        if feats and not readable:
            out.append({"kind": "unreadable", "model_id": mid, "column": "-",
                        "detail": f"dataset.timeframe {tf_raw!r} does not parse as "
                                  f"'all' or <n><m|h|d|w>; C1 cannot be decided"})

        # C1 — structurally constant under the manifest's own scope
        for col in feats:
            floor = _PERIODIC.get(col)
            if floor is not None and secs is not None and secs >= floor:
                out.append({"kind": "scope_constant", "model_id": mid, "column": col,
                            "detail": f"dataset.timeframe {tf_raw} spans >= the "
                                      f"period of {col}, so it has ONE value by "
                                      f"construction"})
            if col == "symbol" and sym and sym != "all" and "," not in sym:
                out.append({"kind": "scope_constant", "model_id": mid, "column": col,
                            "detail": f"dataset.symbol_scope is pinned to {sym}"})
            if col == "timeframe" and tf_raw and tf_raw != "all":
                out.append({"kind": "scope_constant", "model_id": mid, "column": col,
                            "detail": f"dataset.timeframe is pinned to {tf_raw}"})

        # C2 — column no builder emits (regression guard; see the module docstring)
        schema = schemas.get(fam)
        if schema:
            for col in feats:
                if col not in schema:
                    out.append({"kind": "absent_from_builder", "model_id": mid,
                                "column": col,
                                "detail": f"family {fam!r} builder schema does not "
                                          f"declare it"})
            if target and str(target) not in schema:
                out.append({"kind": "absent_from_builder", "model_id": mid,
                            "column": f"target:{target}",
                            "detail": f"family {fam!r} builder schema does not "
                                      f"declare the target column"})
        elif fam:
            out.append({"kind": "unknown_family", "model_id": mid, "column": "-",
                        "detail": f"dataset.family {fam!r} is not in FAMILY_REGISTRY"})

        # C3 — categorical the trainer will reject at train time
        for col in cats:
            if col not in feats:
                out.append({"kind": "categorical_orphan", "model_id": mid,
                            "column": col,
                            "detail": "categorical_columns names it but "
                                      "feature_columns does not; "
                                      "ml/trainers/lightgbm_multiclass.py:119-123 "
                                      "RAISES on this at train time"})
    return out


def _load_manifests() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # a manifest that will not parse is a finding
            out.append({"model_id": path.name, "__unparseable__": str(exc)})
            continue
        if isinstance(doc, dict):
            doc.setdefault("__path__", path.name)
            out.append(doc)
    return out


def main() -> int:
    manifests = _load_manifests()
    bad_parse = [m for m in manifests if m.get("__unparseable__")]
    good = [m for m in manifests if not m.get("__unparseable__")]
    scored = [m for m in good if (m.get("trainer_config") or {}).get("feature_columns")]
    found = findings(good, _builder_schemas())

    print(f"manifest-scope-constants: {len(manifests)} manifest(s) under "
          f"ml/configs/*.yaml; {len(scored)} declare feature_columns and are scored "
          f"for C1/C2/C3; {len(good) - len(scored)} declare none (baselines) and are "
          f"checked for C2/C3 only.")
    for m in bad_parse:
        print(f"::error::{m['model_id']}: manifest does not parse — "
              f"{m['__unparseable__']}")
    if not found and not bad_parse:
        print("OK — no manifest declares a feature its own dataset scope makes "
              "constant, a column its builder does not emit, or a categorical "
              "absent from feature_columns.")
        return 0
    if found:
        print("::error::a manifest declares something that cannot work. A manifest "
              "merging clean and then silently never training for 34 days is the "
              "failure this guard exists to make impossible:")
        for f in found:
            print(f"  - [{f['kind']}] {f['model_id']}: {f['column']} — {f['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

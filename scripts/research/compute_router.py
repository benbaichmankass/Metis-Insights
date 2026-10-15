#!/usr/bin/env python3
# wiring: scripts/research/dispatch_queue.py (resolve + rank), scripts/research/research_queue.py (validate)
"""RESEARCH-ROUTER (2026-10-13): token-free compute-class resolver + value ranking for the research queue.

THE SPLIT (operator, 2026-10-13, voice): Claude writes the research unit -- id, hypothesis, pre-registered
rule, a *kind of test* and what it is worth. A dispatcher that spends NO Claude tokens sends each unit to the
right compute and spends the scarce resources on the highest-value work. This module is the policy half of
that dispatcher; ``scripts/research/research_compute.yaml`` is the data it reads (nothing about backends is hard-coded
here), and ``dispatch_queue.py`` is the mechanism that calls it.

WHAT A UNIT DECLARES
--------------------
    compute: cpu_light | cpu_long | needs_vm_data | gpu | gpu_preferred | auto
    value:                       # optional; every field optional
      usd_at_stake_per_month: 400   # expected net-P&L impact / money at stake if the answer is acted on
      confidence: 0.4               # P(the answer changes a decision)
      bucket: own_losses | structural | information | ai_trader | frontier

``workflow:`` BEATS ``compute:`` (back-compat: every existing unit keeps its hard-coded runner). A unit with
neither is an error. ``compute: auto`` derives the class from the unit's own ``routing:`` block, so the two
cannot disagree: a class that contradicts ``routing`` is refused, not guessed.

THE VALUE FORMULA (scripts/research/research_compute.yaml § value; every decision line prints its derivation)
    score = usd_per_month * confidence * bucket_boost * hand_written_boost * age_boost / cost
    cost  = (1 + est_hours) * backend.cost_weight
It orders units WITHIN a theme. Theme weights (research/THEMES.yaml) stay the operator's across themes, and a
unit waiting longer than ``starve_hours`` sorts ahead of every score inside its theme: nothing starves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = _REPO / "scripts" / "research" / "research_compute.yaml"

#: Where a Kaggle weekly quota is read from (the dispatch workflow writes `kaggle quota` here).
KAGGLE_QUOTA_FILE = "kaggle-quota.txt"

_cfg_cache: Dict[str, Any] = {}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """scripts/research/research_compute.yaml, validated. Raises ValueError on a malformed file: this is the
    scheduler's only policy input, so a silent default would hide a broken scheduler behind a plausible order."""
    import yaml  # noqa: PLC0415
    p = Path(path) if path else CONFIG_PATH
    key = str(p)
    if key in _cfg_cache and _cfg_cache[key][0] == p.stat().st_mtime_ns:
        return _cfg_cache[key][1]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{p.name}: unreadable -- {exc}") from exc
    errs: List[str] = []
    backends, classes = doc.get("backends"), doc.get("classes")
    if not isinstance(backends, dict) or not backends:
        errs.append("`backends` must be a non-empty mapping")
        backends = {}
    for name, b in backends.items():
        wf = (b or {}).get("workflow") if isinstance(b, dict) else None
        if not (isinstance(wf, str) and wf.endswith(".yml")):
            errs.append(f"backend {name!r}: `workflow` must be a *.yml file")
        cap = b.get("max_concurrent") if isinstance(b, dict) else None
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            errs.append(f"backend {name!r}: `max_concurrent` must be an integer >= 1")
        w = b.get("cost_weight") if isinstance(b, dict) else None
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
            errs.append(f"backend {name!r}: `cost_weight` must be a positive number")
    if not isinstance(classes, dict) or not classes:
        errs.append("`classes` must be a non-empty mapping")
        classes = {}
    for name, c in classes.items():
        bl = (c or {}).get("backends") if isinstance(c, dict) else None
        if not (isinstance(bl, list) and bl and all(b in backends for b in bl)):
            errs.append(f"class {name!r}: `backends` must be a non-empty list of declared backends")
        elif any(backends[b].get("auto_route", True) is False for b in bl):
            errs.append(f"class {name!r}: lists a backend with auto_route: false (select it with an explicit workflow:)")
        elif any(backends[b].get("auto_route") == "fallback" and i == 0 for i, b in enumerate(bl)):
            errs.append(f"class {name!r}: a `fallback` backend cannot be the class's first choice")
    val = doc.get("value")
    if not isinstance(val, dict):
        errs.append("`value` block is required")
    else:
        shares = val.get("buckets")
        if not (isinstance(shares, dict) and shares and abs(sum(shares.values()) - 1.0) < 1e-6):
            errs.append("`value.buckets` must be a mapping whose target shares sum to 1.0")
        else:
            for th, bk in (val.get("theme_bucket") or {}).items():
                if bk not in shares:
                    errs.append(f"value.theme_bucket[{th!r}] = {bk!r} is not a declared bucket")
    if not isinstance(doc.get("limits"), dict):
        errs.append("`limits` block is required")
    if errs:
        raise ValueError("research_compute.yaml: " + "; ".join(errs))
    _cfg_cache[key] = (p.stat().st_mtime_ns, doc)
    return doc


def class_names(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    return sorted((cfg or load_config())["classes"]) + ["auto"]


def backend_for_workflow(wf: str, cfg: Dict[str, Any]) -> Optional[str]:
    wf = wf.split("/")[-1]
    for name, b in cfg["backends"].items():
        if b.get("workflow") == wf:
            return name
    return None


def _explicit_workflow(entry: Dict[str, Any]) -> Optional[str]:
    if entry.get("_injected_workflow"):      # the dispatcher filled run.workflow from the class in memory; not the author's
        return None
    wf = str((entry.get("run") or {}).get("workflow") or "").strip()
    return wf if wf else None


def _num(v: Any) -> Optional[float]:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)


def est_hours(entry: Dict[str, Any]) -> float:
    m = _num((entry.get("routing") or {}).get("est_minutes"))
    return max(m, 5.0) / 60.0 if m is not None else 1.0


def unit_dataset_paths(entry: Dict[str, Any]) -> List[str]:
    """Every dataset path an ml_bakeoff unit reads (single `dataset.path` and per-group `groups.*.path`)."""
    ds = (entry.get("ml_bakeoff") or {}).get("dataset") or {}
    out = [str(ds["path"])] if ds.get("path") else []
    groups = ds.get("groups")
    for g in (groups.values() if isinstance(groups, dict) else []):     # a list of group names (synthetic data) has no paths
        if isinstance(g, dict) and g.get("path"):
            out.append(str(g["path"]))
    # KAGGLE-TRAIN-RUNNER-FIRST (2026-10-15): a train_parity unit reads exactly its manifest's shard. The path is DERIVED
    # from ml/configs/<manifest>.yaml, never declared, so the allowlist check below sees what the script will read.
    man = (entry.get("train_parity") or {}).get("manifest")
    if man:
        out.append(manifest_dataset_path(str(man)))
    return out


def manifest_dataset_path(manifest_id: str) -> str:
    """datasets-out/<family>/<symbol>/<tf>/<version>/data.jsonl for ml/configs/<manifest_id>.yaml ('' when unreadable)."""
    import yaml  # noqa: PLC0415
    p = _REPO / "ml" / "configs" / f"{manifest_id}.yaml"
    if "/" in manifest_id or ".." in manifest_id or not p.is_file():
        return ""
    ds = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("dataset") or {}
    parts = [ds.get(k) for k in ("family", "symbol_scope", "timeframe", "version")]
    return "datasets-out/" + "/".join(str(x) for x in parts) + "/data.jsonl" if all(parts) else ""


def published_problem(entry: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """None when the unit does not claim published data, or the claim holds. Otherwise why the claim is false.
    ML-DATA-OFF-TRAINER (2026-10-13): a unit may leave the trainer only when EVERY dataset path it reads is in the
    exporter's allowlist (scripts/research/market_data_export.py::ALLOW) -- the claim is checked, never trusted."""
    name = (entry.get("routing") or {}).get("published_data")
    if not name:
        return None
    reg = cfg.get("published_data") or {}
    if str(name) not in reg:
        return f"routing.published_data {name!r} is not declared in research_compute.yaml::published_data (have {sorted(reg)})"
    paths = unit_dataset_paths(entry)
    if not paths:
        return "routing.published_data is set but the unit declares no ml_bakeoff.dataset path to check it against"
    from scripts.research import market_data_export as mde  # noqa: PLC0415
    bad = [p for p in paths if not mde.covers(p)]
    return (f"routing.published_data {name!r} but these dataset paths are NOT in the published allowlist: {bad}") if bad else None


def uses_published_data(entry: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    return bool((entry.get("routing") or {}).get("published_data")) and published_problem(entry, cfg) is None


def derive_class(entry: Dict[str, Any]) -> Optional[str]:
    """`compute: auto` -> a class from the unit's own routing block (never a guess: no routing, no class)."""
    r = entry.get("routing")
    if not isinstance(r, dict) or not r:
        return None
    vm = bool(r.get("needs_trainer_resident_data")) and not r.get("published_data")
    if r.get("needs_gpu") and vm:
        return None
    if r.get("needs_gpu"):
        return "gpu"
    if vm:
        return "needs_vm_data"
    m = _num(r.get("est_minutes"))
    return "cpu_long" if (m is not None and m > 30) else "cpu_light"


def declared_class(entry: Dict[str, Any]) -> Optional[str]:
    c = entry.get("compute")
    if not isinstance(c, str) or not c.strip():
        return None
    c = c.strip()
    return derive_class(entry) if c == "auto" else c


#: ml_bakeoff learner kinds that can use a GPU. EMPTY today: every kind in ml_bakeoff.LEARNERS (prior, numpy_logit, lightgbm,
#: catboost, sklearn_*, tabicl, tabpfn) is fit on CPU here. An ml_bakeoff unit whose learners are all outside this set gains
#: nothing from a GPU kernel, and a Kaggle GPU kernel is metered (30 h/week) whether or not the GPU is touched.
GPU_LEARNER_KINDS: frozenset = frozenset()


def cpu_only_bakeoff(entry: Dict[str, Any]) -> bool:
    """True when the unit is an ml_bakeoff whose every learner is CPU-only (KAGGLE-CPU-ROUTE, 2026-10-14)."""
    b = entry.get("ml_bakeoff")
    if not isinstance(b, dict):
        return False
    learners = b.get("learners")
    kinds = {str((v or {}).get("kind")) for v in learners.values() if isinstance(v, dict)} if isinstance(learners, dict) else set()
    return not (kinds & GPU_LEARNER_KINDS)


def class_routing_conflict(cls: str, entry: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """A class that contradicts the unit's own `routing:` block. None when consistent or when there is no block."""
    r = entry.get("routing")
    if not isinstance(r, dict) or not r:
        return None
    pub = published_problem(entry, cfg)
    if pub:
        return pub
    gpu = bool(r.get("needs_gpu"))
    # published data lifts the trainer requirement (the claim was just checked above)
    vm = bool(r.get("needs_trainer_resident_data")) and not r.get("published_data")
    if cls == "needs_vm_data" and r.get("published_data"):
        return "compute: needs_vm_data but the unit's data is published (routing.published_data) -- it need not wait for the trainer"
    bl = cfg["classes"][cls]["backends"]
    if cls in ("gpu", "gpu_preferred") and not gpu and cpu_only_bakeoff(entry):
        return (f"compute: {cls} but every ml_bakeoff learner is CPU-only (routing.needs_gpu is not true): the GPU-first backend "
                "is metered Kaggle GPU quota -- use compute: auto (cpu_light/cpu_long)")
    if cls == "gpu" and not gpu:
        return "compute: gpu but routing.needs_gpu is not true"
    if cls == "needs_vm_data" and not vm:
        return "compute: needs_vm_data but routing.needs_trainer_resident_data is not true"
    if cls in ("cpu_light", "cpu_long", "gpu_preferred") and vm:
        return f"compute: {cls} but routing.needs_trainer_resident_data is true"
    if cls in ("cpu_light", "cpu_long") and gpu:
        return f"compute: {cls} but routing.needs_gpu is true"
    return None if bl else "class has no backends"


# ── backend state: what is busy and what quota is left ─────────────────────────────────────────────────
@dataclass
class BackendState:
    inflight: Dict[str, int] = field(default_factory=dict)      # backend -> runs queued/in progress
    quota_hours: Optional[float] = None                         # kaggle GPU hours remaining; None = unreadable
    quota_refresh: str = ""
    quota_note: str = "quota not read"
    total_inflight: int = 0
    now: Optional[datetime] = None                              # set by the dispatcher; None = do not gate on windows

    def busy(self, backend: str) -> int:
        return int(self.inflight.get(backend, 0))

    def took(self, backend: str) -> None:
        self.inflight[backend] = self.busy(backend) + 1
        self.total_inflight += 1


_QUOTA_ROW = re.compile(r"^\s*(?P<res>[A-Za-z]+)\s+(?P<used>[\d.]+)h\s+(?P<rem>[\d.]+)h\s+(?P<tot>[\d.]+)h\s+(?P<ref>\S+)", re.M)


def parse_quota(text: str, resource: str = "GPU") -> Dict[str, Any]:
    """Parse `kaggle quota` (kaggle 2.2.4, observed 2026-10-13):
         resource  used   remaining  total   refreshAt
         GPU       0.02h  29.98h     30.00h  2026-10-17T00:00:00
    Returns {remaining, used, total, refresh}; remaining is None when the row is absent -- "could not
    read" is never reported as zero hours or as plenty."""
    for m in _QUOTA_ROW.finditer(text or ""):
        if m["res"].upper() == resource.upper():
            return {"remaining": float(m["rem"]), "used": float(m["used"]),
                    "total": float(m["tot"]), "refresh": m["ref"]}
    return {"remaining": None, "used": None, "total": None, "refresh": ""}


def read_quota_file(path: Optional[str], resource: str = "GPU") -> Dict[str, Any]:
    if not path:
        return {"remaining": None, "refresh": "", "note": "no --kaggle-quota-file given"}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"remaining": None, "refresh": "", "note": f"quota file unreadable: {type(exc).__name__}"}
    q = parse_quota(text, resource)
    q["note"] = "" if q["remaining"] is not None else "quota file has no parsable GPU row"
    return q


# ── resolution ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Resolution:
    ok: bool
    backend: str = ""
    workflow: str = ""
    cls: str = ""
    source: str = ""          # explicit | class | auto
    wait: bool = False        # resolvable, but every allowed backend is full / out of quota right now
    reason: str = ""
    error: str = ""


def parse_windows(windows: List[str]) -> List[tuple]:
    """["03:00-05:00", "07:00-24:00"] -> [(180, 300), (420, 1440)] minutes from 00:00 UTC."""
    out = []
    for w in windows:
        a, b = str(w).split("-")
        ah, am = (int(x) for x in a.split(":"))
        bh, bm = (int(x) for x in b.split(":"))
        out.append((ah * 60 + am, bh * 60 + bm))
    return out


def window_problem(backend: str, entry: Dict[str, Any], cfg: Dict[str, Any], now: Optional[datetime]) -> Optional[str]:
    """None when `backend` has no window policy, `now` is unknown, or the unit fits an open window starting now."""
    q = (cfg["backends"][backend].get("queue") or {})
    if not q.get("windows_utc") or now is None:
        return None
    wins = parse_windows(q["windows_utc"])
    t = now.hour * 60 + now.minute
    need = est_hours(entry) * 60.0 * float(q.get("safety_factor", 1.0))
    for a, b in wins:
        if a <= t < b:
            if t + need <= b:
                return None
            return f"{backend} queue: ~{need:.0f} min (est x{q.get('safety_factor', 1.0):g}) does not fit before the window closes at {b // 60:02d}:{b % 60:02d}Z"
    nxt = min((a for a, _ in wins if a > t), default=wins[0][0])
    return f"{backend} queue: {now:%H:%M}Z is in a training/busy window; next idle window opens {nxt // 60:02d}:{nxt % 60:02d}Z"


def ledger_month_to_date(cfg: Dict[str, Any], backend: str, now: Optional[datetime], repo: Path = _REPO) -> Dict[str, Any]:
    """{spent, budget} from the backend's spend ledger, or {error} -- 'could not look' is never zero spend."""
    import json  # noqa: PLC0415
    try:
        d = json.loads((repo / cfg["backends"][backend]["ledger"]).read_text(encoding="utf-8"))
        month = (now or utcnow()).strftime("%Y-%m")
        spent = sum(float(r.get("cost_usd") or 0) for r in d.get("runs", [])
                    if str(r.get("ended_at") or r.get("started_at") or "").startswith(month))
        return {"spent": spent, "budget": float(d["budget_usd_per_month"])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def fallback_problem(backend: str, entry: Dict[str, Any], cfg: Dict[str, Any], st: BackendState,
                     repo: Path = _REPO) -> Optional[str]:
    """None when a `fallback` backend (RunPod) can take the unit: it declared a request, capacity is free and the
    estimated spend fits the monthly ledger cap. Otherwise why not -- the unit then WAITS (never runs on CPU)."""
    b = cfg["backends"][backend]
    req = (entry.get("routing") or {}).get(backend)
    if not (isinstance(req, dict) and req.get("manifest")):
        return f"{backend} fallback: unit declares no routing.{backend}.manifest (the pod trains a registered manifest, not a script)"
    if st.busy(backend) >= int(b["max_concurrent"]):
        return f"{backend} at capacity ({st.busy(backend)}/{b['max_concurrent']} in flight)"
    est = _num(req.get("est_cost_usd"))
    if est is None:
        est = est_hours(entry) * float(b.get("rate_usd_per_h", 0.0)) * float(b.get("estimate_margin", 1.0))
    led = ledger_month_to_date(cfg, backend, st.now, repo)
    if "error" in led:
        return f"{backend} fallback: spend ledger unreadable ({led['error']}); not routed blind"
    if led["spent"] + est > led["budget"]:
        return f"{backend} fallback: ${led['spent']:.2f} spent + ~${est:.2f} would exceed the ${led['budget']:g}/month cap"
    return None


def _available(backend: str, entry: Dict[str, Any], cfg: Dict[str, Any], st: BackendState) -> Optional[str]:
    """None when `backend` can take one more unit now, else why not."""
    b = cfg["backends"][backend]
    if b.get("auto_route") == "fallback":
        return fallback_problem(backend, entry, cfg, st)
    if not b.get("auto_route", True):
        return None     # governed by its own gates (e.g. the RunPod ledger + --max-gpu-dispatches-per-run), not by this table
    if st.busy(backend) >= int(b["max_concurrent"]):
        return f"{backend} at capacity ({st.busy(backend)}/{b['max_concurrent']} in flight)"
    wp = window_problem(backend, entry, cfg, st.now)
    if wp:
        return wp
    q = b.get("quota")
    if q:
        if st.quota_hours is None:
            return f"{backend} quota unreadable ({st.quota_note}); not routed blind"
        need = est_hours(entry) if _num((entry.get("routing") or {}).get("est_minutes")) is not None \
            else float(q.get("default_unit_hours", 1.0))
        floor = float(q.get("floor_hours", 0.0))
        if st.quota_hours - need < floor:
            return (f"{backend} {q.get('resource', 'GPU')} quota {st.quota_hours:.2f} h left, unit needs ~{need:.2f} h, "
                    f"floor {floor:g} h (refreshes {st.quota_refresh or 'unknown'})")
    return None


def effective_backend(be: str, entry: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """A backend may name a `published_variant`: the twin a unit uses when it reads published data (the plain runner
    holds no secret, so the dataset fetch lives in its twin). Only a unit whose published-data claim CHECKS gets it."""
    v = cfg["backends"][be].get("published_variant")
    return v if v and uses_published_data(entry, cfg) and v in cfg["backends"] else be


def plan(entry: Dict[str, Any], cfg: Dict[str, Any]) -> Resolution:
    """Static resolution -- which backend this unit PREFERS, ignoring load. Used for ranking and validation."""
    wf = _explicit_workflow(entry)
    if wf:
        be = backend_for_workflow(wf, cfg)
        return Resolution(True, be or "unmanaged", wf, str(entry.get("compute") or ""), "explicit",
                          reason=f"explicit run.workflow {wf}")
    cls = declared_class(entry)
    raw = entry.get("compute")
    if not raw:
        return Resolution(False, error="neither `run.workflow` nor `compute:` is declared -- the dispatcher "
                                       "cannot place this unit")
    if cls is None:
        return Resolution(False, error="compute: auto needs an unambiguous `routing:` block (no GPU+trainer "
                                       "conflict, no empty block)")
    if cls not in cfg["classes"]:
        return Resolution(False, error=f"compute: {cls!r} is not a class in scripts/research/research_compute.yaml "
                                       f"(have {class_names(cfg)})")
    conflict = class_routing_conflict(cls, entry, cfg)
    if conflict:
        return Resolution(False, cls=cls, error=conflict + " -- the class and the routing block must agree")
    be = effective_backend(cfg["classes"][cls]["backends"][0], entry, cfg)
    return Resolution(True, be, cfg["backends"][be]["workflow"], cls,
                      "auto" if str(raw).strip() == "auto" else "class",
                      reason=f"compute: {raw} -> class {cls} -> prefers {be}")


def resolve(entry: Dict[str, Any], cfg: Dict[str, Any], st: Optional[BackendState] = None) -> Resolution:
    """Place one unit NOW: honour an explicit workflow, else walk the class's backends and take the first with
    room. `wait=True` (not an error) when all are full: the unit stays due and is retried next cycle."""
    base = plan(entry, cfg)
    if not base.ok or base.source == "explicit" or st is None:
        # An explicit run.workflow naming a `fallback` backend (gpu-burst-train.yml) keeps its old behaviour: governed by its
        # own gates (ledger preflight, GPU_BURST_ARMED, the per-run cap), not by the class-fallback request check.
        if base.ok and base.source == "explicit" and st is not None and base.backend in cfg["backends"] \
                and cfg["backends"][base.backend].get("auto_route") != "fallback":
            why = _available(base.backend, entry, cfg, st)
            if why:
                base.wait, base.reason = True, why
        return base
    why_not: List[str] = []
    for be in (effective_backend(b, entry, cfg) for b in cfg["classes"][base.cls]["backends"]):
        why = _available(be, entry, cfg, st)
        if why is None:
            return Resolution(True, be, cfg["backends"][be]["workflow"], base.cls, base.source,
                              reason=f"compute: {entry.get('compute')} -> class {base.cls} -> {be}"
                                     + (f" (fallback; {'; '.join(why_not)})" if why_not else ""))
        why_not.append(why)
    return Resolution(True, base.backend, base.workflow, base.cls, base.source, wait=True,
                      reason="; ".join(why_not))


# ── value ranking ───────────────────────────────────────────────────────────────────────────────────────
def bucket_of(entry: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    v = entry.get("value") if isinstance(entry.get("value"), dict) else {}
    b = v.get("bucket")
    shares = cfg["value"]["buckets"]
    if b in shares:
        return b
    return cfg["value"].get("theme_bucket", {}).get(str(entry.get("theme") or ""), "")


def bucket_boost(bucket: str, fires: Dict[str, float], cfg: Dict[str, Any]) -> float:
    """target_share / observed_share over the share window, clamped. An unknown bucket or an empty window is 1.0."""
    shares = cfg["value"]["buckets"]
    total = sum(fires.values())
    if bucket not in shares or total <= 0:
        return 1.0
    lo, hi = float(cfg["value"]["bucket_boost"]["min"]), float(cfg["value"]["bucket_boost"]["max"])
    observed = fires.get(bucket, 0.0) / total
    boost = hi if observed <= 0 else float(shares[bucket]) / observed
    return max(lo, min(hi, boost))


def score(entry: Dict[str, Any], cfg: Dict[str, Any], *, aging_steps: int, fires: Dict[str, float],
          cost_weight: Optional[float] = None) -> Dict[str, Any]:
    """The value score and its derivation. Pure: same unit + config + window -> same number."""
    val = cfg["value"]
    v = entry.get("value") if isinstance(entry.get("value"), dict) else {}
    prio = entry.get("priority")
    prio = prio if isinstance(prio, int) and not isinstance(prio, bool) else 3
    usd_declared = _num(v.get("usd_at_stake_per_month"))
    usd = usd_declared if usd_declared is not None else float(val["priority_usd"].get(prio, val["priority_usd"][3]))
    conf = _num(v.get("confidence"))
    conf = float(val["default_confidence"]) if conf is None else max(0.0, min(1.0, conf))
    bucket = bucket_of(entry, cfg)
    bb = bucket_boost(bucket, fires, cfg)
    hw = 1.0 if isinstance(entry.get("generated"), dict) else float(val["hand_written_boost"])
    age = 1.0 + float(val["age_step"]) * aging_steps
    if cost_weight is None:
        p = plan(entry, cfg)
        cost_weight = float(cfg["backends"][p.backend]["cost_weight"]) if p.backend in cfg["backends"] else 1.0
    hours = est_hours(entry)
    cost = (1.0 + hours) * cost_weight
    s = usd * conf * bb * hw * age / cost
    deriv = (f"score {s:.1f} = ${usd:g}{'' if usd_declared is not None else f' (prio{prio} default)'}"
             f" x conf {conf:g} x bucket[{bucket or '-'}] {bb:.2f} x hand {hw:g} x age {age:g}"
             f" / cost (1+{hours:.2f}h) x {cost_weight:g} = {cost:.2f}")
    return {"rank_value": s, "derivation": deriv, "usd": usd, "confidence": conf, "bucket": bucket,
            "bucket_boost": bb, "cost": cost}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

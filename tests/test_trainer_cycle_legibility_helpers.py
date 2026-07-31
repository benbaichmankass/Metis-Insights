"""Unit tests for the P1 trainer-honesty helpers (2026-07-31 audit plan):

- scripts/ops/_last_json_object.py — recover the multi-line JSON summary the
  ml CLIs actually print (the grep-'^{' extraction lost model_id on every
  live manifest_ok, trainer-diag #8184);
- scripts/ops/manifest_training_staleness.py — the adder-up over the cycle's
  four independent skip paths (P1.3c);
- scripts/ops/trainer_dataset_gc.py — unpinned-aged dataset-dir reclaim
  (P1.4), report-only by default.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OPS = _REPO_ROOT / "scripts" / "ops"

sys.path.insert(0, str(_OPS))

from _last_json_object import last_json_object  # noqa: E402


class TestLastJsonObject:
    def test_multiline_indent2_summary(self):
        text = (
            "some log line\n"
            "{\n"
            '  "metrics": {\n'
            '    "macro_f1": 0.5\n'
            "  },\n"
            '  "model_id": "m-1",\n'
            '  "registered": true\n'
            "}\n"
        )
        obj = last_json_object(text)
        assert obj == {"metrics": {"macro_f1": 0.5}, "model_id": "m-1",
                       "registered": True}

    def test_last_of_multiple_objects_wins(self):
        text = '{"first": 1}\nnoise\n{\n  "second": 2\n}\n'
        assert last_json_object(text) == {"second": 2}

    def test_none_when_nothing_parses(self):
        assert last_json_object("no json here\n{broken\n") is None

    def test_cli_prints_empty_object_on_missing_file(self, tmp_path: Path):
        r = subprocess.run(
            [sys.executable, str(_OPS / "_last_json_object.py"),
             str(tmp_path / "absent.out")],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}


def _run_staleness(registry: Path, threshold: str, manifests: list[Path]):
    r = subprocess.run(
        [sys.executable, str(_OPS / "manifest_training_staleness.py"),
         str(registry), threshold, *[str(m) for m in manifests]],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    summary = [e for e in events if e["status"] == "training_staleness_summary"]
    stale = [e for e in events if e["status"] == "manifest_untrained_stale"]
    assert len(summary) == 1, "summary line must ALWAYS be emitted"
    return stale, summary[0]


class TestManifestTrainingStaleness:
    def _manifest(self, tmp_path: Path, name: str, model_id: str,
                  age_days: float = 0.0) -> Path:
        p = tmp_path / name
        p.write_text(f"model_id: {model_id}\n", encoding="utf-8")
        if age_days:
            past = time.time() - age_days * 86400
            os.utime(p, (past, past))
        return p

    def _registry_entry(self, registry: Path, model_id: str, at_days_ago: float):
        registry.mkdir(exist_ok=True)
        import datetime as dt
        at = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=at_days_ago)).isoformat()
        (registry / f"{model_id}.json").write_text(
            json.dumps({"model_id": model_id, "runs": [{"at": at}]}),
            encoding="utf-8")

    def test_recent_run_not_stale(self, tmp_path: Path):
        reg = tmp_path / "registry"
        self._registry_entry(reg, "m-fresh", at_days_ago=1.0)
        m = self._manifest(tmp_path, "fresh.yaml", "m-fresh", age_days=30)
        stale, summary = _run_staleness(reg, "7", [m])
        assert stale == []
        assert summary["scanned"] == 1
        assert summary["stale"] == 0

    def test_old_run_is_stale(self, tmp_path: Path):
        reg = tmp_path / "registry"
        self._registry_entry(reg, "m-old", at_days_ago=30.0)
        m = self._manifest(tmp_path, "old.yaml", "m-old", age_days=60)
        stale, summary = _run_staleness(reg, "7", [m])
        assert len(stale) == 1
        assert stale[0]["model_id"] == "m-old"
        assert stale[0]["days_untrained"] > 29
        assert summary["stale"] == 1

    def test_never_trained_old_manifest_is_stale(self, tmp_path: Path):
        reg = tmp_path / "registry"
        reg.mkdir()
        m = self._manifest(tmp_path, "never.yaml", "m-never", age_days=30)
        stale, summary = _run_staleness(reg, "7", [m])
        assert len(stale) == 1
        assert stale[0]["last_trained_at"] is None
        assert summary["never_trained"] == 1

    def test_never_trained_fresh_manifest_gets_grace(self, tmp_path: Path):
        reg = tmp_path / "registry"
        reg.mkdir()
        m = self._manifest(tmp_path, "new.yaml", "m-new")  # mtime now
        stale, summary = _run_staleness(reg, "7", [m])
        assert stale == []
        assert summary["stale"] == 0

    def test_unresolvable_model_id_is_reported_not_dropped(self, tmp_path: Path):
        reg = tmp_path / "registry"
        reg.mkdir()
        p = tmp_path / "broken.yaml"
        p.write_text("name: no-model-id-here\n", encoding="utf-8")
        stale, summary = _run_staleness(reg, "7", [p])
        assert len(stale) == 1
        assert stale[0]["model_id"] is None
        assert summary["unresolvable"] == 1


def _run_gc(root: Path, configs: Path, *extra: str):
    r = subprocess.run(
        [sys.executable, str(_OPS / "trainer_dataset_gc.py"),
         "--datasets-root", str(root), "--configs", str(configs), *extra],
        capture_output=True, text=True,
    )
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    summary = [e for e in events if e["status"] == "gc_summary"][-1]
    cands = [e for e in events if e["status"] == "gc_candidate"]
    return r.returncode, cands, summary


class TestTrainerDatasetGc:
    def _dataset(self, root: Path, family: str, scope: str, tf: str,
                 version: str, age_days: float) -> Path:
        d = root / family / scope / tf / version
        d.mkdir(parents=True)
        (d / "data.jsonl").write_text("x" * 1000, encoding="utf-8")
        past = time.time() - age_days * 86400
        os.utime(d, (past, past))
        return d

    def _configs(self, tmp_path: Path, pins: list[tuple[str, str, str, str]]) -> Path:
        cfg = tmp_path / "configs"
        cfg.mkdir()
        for i, (fam, scope, tf, ver) in enumerate(pins):
            (cfg / f"pin-{i}.yaml").write_text(
                f"model_id: pin-{i}\ndataset:\n  family: {fam}\n"
                f"  symbol_scope: {scope}\n  timeframe: {tf}\n  version: {ver}\n",
                encoding="utf-8")
        return cfg

    def test_pinned_and_canonical_and_fresh_kept_aged_collected(self, tmp_path: Path):
        root = tmp_path / "datasets-out"
        pinned = self._dataset(root, "market_features", "BTCUSDT", "15m", "v514", 60)
        canonical = self._dataset(root, "market_features", "BTCUSDT", "15m", "v002", 60)
        fresh = self._dataset(root, "market_features", "BTCUSDT", "15m", "v903", 2)
        aged = self._dataset(root, "market_features", "BTCUSDT", "15m", "v104", 60)
        cfg = self._configs(tmp_path, [("market_features", "BTCUSDT", "15m", "v514")])

        rc, cands, summary = _run_gc(root, cfg)
        assert rc == 0
        assert [c["path"] for c in cands] == [str(aged)]
        assert summary["mode"] == "report-only"
        assert summary["scanned"] == 4
        assert summary["kept"] == 3
        # Report-only: nothing deleted.
        for d in (pinned, canonical, fresh, aged):
            assert d.is_dir()

    def test_apply_deletes_only_candidates(self, tmp_path: Path):
        root = tmp_path / "datasets-out"
        pinned = self._dataset(root, "market_features", "BTCUSDT", "15m", "v514", 60)
        aged = self._dataset(root, "market_features", "BTCUSDT", "15m", "v104", 60)
        cfg = self._configs(tmp_path, [("market_features", "BTCUSDT", "15m", "v514")])

        rc, cands, summary = _run_gc(root, cfg, "--apply")
        assert rc == 0
        assert summary["mode"] == "APPLIED"
        assert not aged.exists()
        assert pinned.is_dir()

    def test_missing_root_is_an_absent_result_not_a_clean_one(self, tmp_path: Path):
        cfg = self._configs(tmp_path, [])
        rc, cands, summary = _run_gc(tmp_path / "nope", cfg)
        assert rc == 1
        assert "scanned NOTHING" in summary["detail"]

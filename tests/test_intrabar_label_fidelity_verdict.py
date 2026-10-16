"""intrabar_label_fidelity must emit only verdicts script_run accepts (RQ-20261014-da7a-01 landed on 'MIXED')."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
import intrabar_label_fidelity as f  # noqa: E402
import script_run  # noqa: E402


def test_every_domain_class_maps_to_a_contract_verdict():
    assert set(f.VERDICT_MAP) == {"NEEDED", "NOT_NEEDED", "MIXED"}
    assert set(f.VERDICT_MAP.values()) <= set(script_run.VERDICTS)

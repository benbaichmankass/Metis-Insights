"""PI-20260929-SASEC-0003: no real workflow pushes back to an unfiltered push trigger."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_push_back_trigger as guard  # noqa: E402


def test_self_test_passes():
    assert guard.self_test() == 0


def test_real_tree_clean():
    out = [v for p in sorted(guard.WORKFLOWS_DIR.glob("*.yml")) for v in guard.check_file(p)]
    assert out == []

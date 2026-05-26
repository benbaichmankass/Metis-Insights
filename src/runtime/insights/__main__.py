"""Package entry point — makes ``python -m src.runtime.insights`` work.

The cycle wrapper (``scripts/ops/run_insights_cycle.sh``) invokes
``python -m src.runtime.insights generate --endpoint ...``. Without
this file Python returns ``No module named src.runtime.insights.__main__``
because ``-m <pkg>`` requires either a top-level ``__main__.py`` or an
explicit submodule (``-m src.runtime.insights.generator``). The
cleaner contract is the package-level form, so we ship ``__main__.py``.

Caught by the live-VM ``inspect-insights`` action on the first cycle
after activation — every endpoint failed with the missing-module
error. The fix is one-line wide; functional surface is unchanged.
"""
from __future__ import annotations

import sys

from src.runtime.insights.generator import main

if __name__ == "__main__":
    sys.exit(main())

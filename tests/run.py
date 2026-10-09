"""Run every check: python3 tests/run.py (standard library only; nothing ships from tests/)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "maverick_hvac")]

import test_analysis  # noqa: E402
import test_service  # noqa: E402

fails = test_analysis.run() + test_service.run()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {', '.join(fails)}")
sys.exit(1 if fails else 0)

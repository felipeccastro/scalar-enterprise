"""Runs every journey in this folder under pytest, each in its own
process — see _harness.py's Journey class for why — and prints a short
pass/fail summary.

    python3 tests/run_all.py

Run one journey on its own (pytest's own -v prints each test's one-line
docstring alongside the result) via:

    pytest tests/test_getting_started_journey.py -v

Each file gets its own `pytest` subprocess rather than a single `pytest
tests/` invocation: a Journey's setUpClass does a real `import app`, and
this is a single-tenant app with exactly one registration ever allowed
(see pages/__init__.py's bootstrap hook) — importing `app` a second time
in the same process would just reuse the first Journey's already-imported
module (and its already-registered team), not give the second Journey the
fresh app/database pairing it needs. One subprocess per file sidesteps
that entirely, the same way it already sidestepped it for plain unittest.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
FILES = sorted(p for p in HERE.glob("test_*.py"))


def main() -> int:
    failed = []
    for f in FILES:
        print(f"\n=== {f.stem} ===", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", str(f), "-v"])
        if result.returncode != 0:
            failed.append(f.name)

    print("\n" + "=" * 50)
    if failed:
        print(f"{len(failed)} of {len(FILES)} journey(s) failed: {', '.join(failed)}")
        return 1
    print(f"All {len(FILES)} journeys passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

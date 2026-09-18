"""Runs every journey in this folder, each in its own process — see
_harness.py's Journey class for why — and prints a short pass/fail summary.

    python3 tests/run_all.py

Run one journey on its own (with unittest's own -v to print each test's
one-line docstring alongside the result) via:

    python3 tests/test_getting_started_journey.py -v
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
        result = subprocess.run([sys.executable, str(f), "-v"])
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

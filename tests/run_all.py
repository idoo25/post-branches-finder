"""Discovers and runs every test in this package — no network, no real APIs.

Usage from project root:
    python -m tests.run_all
or:
    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

# Ensure the package root is importable when invoked directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    loader = unittest.TestLoader()
    here = Path(__file__).resolve().parent
    suite = loader.discover(start_dir=str(here), pattern="test_*.py", top_level_dir=str(ROOT))
    runner = unittest.TextTestRunner(verbosity=2, buffer=True)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

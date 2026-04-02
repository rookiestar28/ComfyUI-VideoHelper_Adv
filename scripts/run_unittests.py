#!/usr/bin/env python3
import argparse
import pathlib
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description="Run repo-local unittest discovery.")
    parser.add_argument("--start-dir", default="tests", help="Directory to start discovery from.")
    parser.add_argument("--pattern", default="test_*.py", help="Glob pattern for tests.")
    parser.add_argument("--top-level-dir", default=".", help="Top-level project directory.")
    args = parser.parse_args()

    root = pathlib.Path(args.top_level_dir).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    loader = unittest.defaultTestLoader
    suite = loader.discover(
        start_dir=args.start_dir,
        pattern=args.pattern,
        top_level_dir=str(root),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

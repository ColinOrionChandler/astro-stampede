"""Audit all wheel and source distributions in a freshly populated directory."""

import argparse
from pathlib import Path

from audit_public import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    artifacts = sorted(args.directory.glob("*.whl")) + sorted(args.directory.glob("*.tar.gz"))
    if len(artifacts) != 2 or len(list(args.directory.glob("*.whl"))) != 1:
        parser.error("Expected exactly one wheel and one source distribution from a clean build")
    raise SystemExit(bool(audit(Path.cwd(), artifacts, args.history)))


if __name__ == "__main__":
    main()

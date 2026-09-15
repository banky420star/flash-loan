#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/2] ZERO Engine offline unit tests"
python3 -m unittest discover -s tests -v

echo "[2/2] ZERO Engine CLI help smoke test"
python3 -m zero.cli --help >/dev/null

echo "ZERO CLI TESTS: PASS"

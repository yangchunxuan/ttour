#!/bin/bash
set -uo pipefail

FAIL=0

python3 tools/lint_invariants.py || FAIL=1
python3 tools/lint_knowledge.py || FAIL=1
python3 -m pytest tests/ -q || FAIL=1

exit "$FAIL"

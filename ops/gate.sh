#!/usr/bin/env bash
# 一键验收（W-141 的最小版）：lint + 测试（可加内核冒烟）
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
FAIL=0
run() { echo "== $1"; shift; if "$@"; then echo "   ✅ PASS"; else echo "   ❌ FAIL"; FAIL=1; fi; }
run "lint（ruff）"     .venv/bin/ruff check .
run "测试（pytest）"    $PY -m pytest -q
if [ "${1:-}" = "--with-smoke" ]; then run "内核冒烟" $PY -m pytest -q -m smoke -rs; fi
echo
[ $FAIL -eq 0 ] && echo "GATE: ALL_PASS" || echo "GATE: FAILED"
exit $FAIL

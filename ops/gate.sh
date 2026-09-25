#!/usr/bin/env bash
# 一键验收（W-141 的最小版）：lint + 测试（可加内核冒烟）
set -uo pipefail
cd "$(dirname "$0")/.."
# venv 布局兼容：Linux/macOS 是 .venv/bin，Windows 是 .venv/Scripts（P1-4）
if [ -d .venv/Scripts ]; then VENV=.venv/Scripts; else VENV=.venv/bin; fi
PY="$VENV/python"
RUFF="$VENV/ruff"
[ -x "$PY" ] || PY=python3
command -v "$RUFF" >/dev/null 2>&1 || RUFF=ruff
FAIL=0
run() { echo "== $1"; shift; if "$@"; then echo "   ✅ PASS"; else echo "   ❌ FAIL"; FAIL=1; fi; }
run "lint（ruff）"     "$RUFF" check .
run "测试（pytest）"    "$PY" -m pytest -q
if [ "${1:-}" = "--with-smoke" ]; then run "内核冒烟" "$PY" -m pytest -q -m smoke -rs; fi
echo
[ $FAIL -eq 0 ] && echo "GATE: ALL_PASS" || echo "GATE: FAILED"
exit $FAIL

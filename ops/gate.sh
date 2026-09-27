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

# 前端视图的单元测试（M4-01 / #17）：Node 自带的测试运行器，零依赖。
# 没装 node 的机器**明确跳过并写明原因**（宁可看见"少跑一层"，也不要静默通过）。
if command -v node >/dev/null 2>&1; then
  run "视图注册表（node --test）" node --test apps/portal-web/views/*.test.mjs
else
  echo "== 视图注册表（node --test）"
  echo "   ⚠️  跳过：本机没装 node（CI 上会跑；本地想看就装个 node ≥20）"
fi

if [ "${1:-}" = "--with-smoke" ]; then run "内核冒烟" "$PY" -m pytest -q -m smoke -rs; fi
echo
[ $FAIL -eq 0 ] && echo "GATE: ALL_PASS" || echo "GATE: FAILED"
exit $FAIL

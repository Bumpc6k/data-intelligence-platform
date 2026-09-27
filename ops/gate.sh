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

# 参数：--with-smoke（加内核冒烟）、--demo（跑 ops/demo.sh 的 7 步验收路径）
WITH_SMOKE=0
WITH_DEMO=0
for arg in "$@"; do
  case "$arg" in
    --with-smoke) WITH_SMOKE=1 ;;
    --demo) WITH_DEMO=1 ;;
    *) echo "认不出的参数：$arg（可用：--with-smoke / --demo）"; exit 2 ;;
  esac
done

if [ "$WITH_SMOKE" -eq 1 ]; then run "内核冒烟" "$PY" -m pytest -q -m smoke -rs; fi

# 演示路径（M4-03 / #19）：起全栈 + 跑完规划 §5 的 7 步。**会真调模型**，所以只在显式要求时跑。
if [ "$WITH_DEMO" -eq 1 ]; then
  run "演示路径（ops/demo.sh）" bash ops/demo.sh
fi

echo
[ $FAIL -eq 0 ] && echo "GATE: ALL_PASS" || echo "GATE: FAILED"
exit $FAIL

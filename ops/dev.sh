#!/usr/bin/env bash
# 起本地后端（需要先 make venv）；内核不在时 /health 会显示 degraded，不会崩
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn portal_api.main:app --reload --host 127.0.0.1 --port 18100

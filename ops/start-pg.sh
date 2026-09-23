#!/usr/bin/env bash
# 起 PostgreSQL（审计与会话存储，W-117/W-112）；幂等
set -uo pipefail
NAME=dip-pg
PORT=${DIP_PG_PORT:-15432}
if docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then echo "  ${NAME} 已在运行"; exit 0; fi
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" -e POSTGRES_USER=dip -e POSTGRES_PASSWORD=dip -e POSTGRES_DB=dip \
  -p 127.0.0.1:${PORT}:5432 -v dip-pgdata:/var/lib/postgresql/data \
  --health-cmd='pg_isready -U dip -d dip' --health-interval=3s --health-timeout=3s --health-retries=20 \
  postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = healthy ] && break
  sleep 1
done
echo "  PostgreSQL: 127.0.0.1:${PORT} （db=dip user=dip）健康状态=$(docker inspect -f '{{.State.Health.Status}}' "$NAME")"

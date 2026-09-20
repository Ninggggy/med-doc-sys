#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.reproduction.yaml"
ENV_FILE="$PROJECT_DIR/.env"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：未安装 docker 命令。" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "错误：Docker 引擎尚未运行。Docker Desktop 或 Colima 启动后再试。" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  set -- docker compose
elif command -v docker-compose >/dev/null 2>&1; then
  set -- docker-compose
else
  echo "错误：未安装 Docker Compose。" >&2
  exit 1
fi

cd "$PROJECT_DIR"
"$@" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
"$@" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
"$@" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

echo "前端：http://127.0.0.1:${FRONTEND_PORT:-8081}"
echo "后端：http://127.0.0.1:${BACKEND_PORT:-5002}"

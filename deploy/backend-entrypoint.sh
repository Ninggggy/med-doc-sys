#!/bin/sh
set -eu

TARGET_DIR="${MODELSCOPE_CACHE:-/app/agent/agent_backend/data/modelscope_cache}"
SEED_DIR="${MODELSCOPE_CACHE_SEED:-/opt/modelscope_cache_seed}"

mkdir -p "${TARGET_DIR}"

if [ -d "${SEED_DIR}" ] && [ -n "$(ls -A "${SEED_DIR}" 2>/dev/null || true)" ] && [ -z "$(ls -A "${TARGET_DIR}" 2>/dev/null || true)" ]; then
  cp -a "${SEED_DIR}/." "${TARGET_DIR}/"
fi

exec python -m agent.agent_backend.app

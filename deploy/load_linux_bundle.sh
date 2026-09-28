#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGES_DIR="${BUNDLE_DIR}/images"

docker load -i "${IMAGES_DIR}/agent-backend-linux-amd64.tar"

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
  cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
fi

docker compose -f "${SCRIPT_DIR}/docker-compose.images.yaml" up -d agent-backend

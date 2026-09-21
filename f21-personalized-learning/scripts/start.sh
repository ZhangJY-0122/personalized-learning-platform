#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/init-env.sh
docker compose up --build -d
printf '%s\n' '学习平台 http://127.0.0.1:15173' '健康检查 http://127.0.0.1:18083/api/v1/health'

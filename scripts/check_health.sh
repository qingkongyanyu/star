#!/usr/bin/env bash
# 后端健康检查（存活 + 就绪）
set -euo pipefail
BASE="${1:-http://localhost:8070}"

echo "==> GET $BASE/healthz（存活）"
curl -fsS "$BASE/healthz" && echo "" || echo "（后端未就绪，是否已执行 scripts/start_dev.sh？）"

echo "==> GET $BASE/readyz（DB / Redis / MinIO）"
curl -fsS "$BASE/readyz" && echo "" || echo "（有必需依赖不可用，HTTP 非 2xx）"

echo "==> GET $BASE/docs（生产环境 ENV=prod 时按设计不暴露，404 属正常）"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "$BASE/docs" || true

#!/usr/bin/env bash
# ============================================================
# 星枢 StarPivot · 一键开发启动
# 用法：bash scripts/start_dev.sh [--no-frontend]
# 依赖：Docker、uv、Python 3.13.2、npm（前端）
# 对应文档：docs/04-开发指南/01-环境搭建与启动.md
# 说明：本脚本仅用于本地开发；生产请使用 compose.cloud.yml + 独立强口令。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

echo "==> [1/5] 启动基础设施（MySQL/Redis/Kafka/MinIO）..."
docker compose up -d mysql redis kafka minio

wait_port() {
  for _ in $(seq 1 40); do
    (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
    sleep 1
  done
  echo "    ! 端口 $1 未就绪，请检查容器日志：docker compose logs $2"
  return 1
}
echo "    等待 MySQL / MinIO 就绪..."
wait_port 3307 mysql
wait_port 9000 minio

# 定位虚拟环境可执行文件（uv 工程文件在项目根：Windows Git Bash 用 Scripts/，POSIX 用 bin/）
if [ -f "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
else
  PY="$ROOT/.venv/bin/python"
fi

echo "==> [2/5] 后端依赖（Python 3.13.2 + uv）..."
if [ ! -d "$ROOT/.venv" ]; then
  (cd "$ROOT" && uv venv --python 3.13.2)
fi
(cd "$ROOT" && uv pip install -e ".[dev]")

echo "==> [3/5] 初始化数据库 Schema..."
echo "    1) 建库建账号（幂等：task_repo / artifact_repo / midplat）..."
docker compose exec -T mysql mysql -uroot -proot -e "
CREATE DATABASE IF NOT EXISTS task_repo DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS artifact_repo DEFAULT CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'midplat'@'%' IDENTIFIED BY 'secret';
GRANT ALL PRIVILEGES ON task_repo.* TO 'midplat'@'%';
GRANT ALL PRIVILEGES ON artifact_repo.* TO 'midplat'@'%';
FLUSH PRIVILEGES;
"
echo "    2) Alembic 迁移（权威 schema，覆盖 task_repo + artifact_repo 两库）..."
(cd "$BACKEND" && "$PY" -m alembic upgrade head)
echo "    3) 管理员账号检查（无默认账号；首次启动需显式初始化）..."
(cd "$BACKEND" && "$PY" -m app.cli list-admins) || true
echo "       如无管理员：在 backend/.env 设置 BOOTSTRAP_ADMIN_USERNAME/BOOTSTRAP_ADMIN_PASSWORD，"
echo "       或运行 cd backend && python -m app.cli create-admin --username admin"

echo "==> [4/5] 启动后端 uvicorn（日志 backend/uvicorn.log）..."
(cd "$BACKEND" && nohup "$PY" -m uvicorn app.main:app --reload --port 8070 > uvicorn.log 2>&1 & echo $! > "$BACKEND/uvicorn.pid")
echo "    后端 PID: $(cat "$BACKEND/uvicorn.pid")"

if [[ "${1:-}" != "--no-frontend" && -f "$FRONTEND/package.json" ]]; then
  echo "==> [5/5] 启动前端（npm run dev）..."
  (cd "$FRONTEND" && nohup npm run dev > npm.log 2>&1 & echo $! > "$FRONTEND/npm.pid")
  echo "    前端 PID: $(cat "$FRONTEND/npm.pid")"
  echo ""
  echo "  前端  http://localhost:5173"
else
  echo "==> [5/5] 跳过前端（--no-frontend 或 frontend/ 未初始化）"
fi
echo ""
echo "  后端  http://localhost:8070/docs"
echo "  探针  http://localhost:8070/healthz · /readyz"
echo "  MinIO http://localhost:9001  (本地开发默认 minioadmin/minioadmin，生产必须替换)"
echo "  健康检查 bash scripts/check_health.sh"
echo ""
echo "  停止后端：kill 上面对应的 PID；停止基础设施：docker compose down"

#!/bin/bash
# MySQL 容器首次启动初始化脚本（docker-entrypoint-initdb.d 自动执行）
# 作用：创建 artifact_repo 库 + 应用账号 midplat，避免应用迁移因缺库/缺账号失败。
# 商业版：口令从容器环境变量读取（未设置时回退本地开发默认值，仅用于本机）。
set -e

ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"
APP_PASSWORD="${MYSQL_APP_PASSWORD:-secret}"
# SQL 字符串转义（口令中出现单引号时不破坏语句）
APP_PASSWORD_SQL=$(printf '%s' "$APP_PASSWORD" | sed "s/'/''/g")

mysql -uroot -p"${ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS artifact_repo DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'midplat'@'%' IDENTIFIED BY '${APP_PASSWORD_SQL}';
ALTER USER 'midplat'@'%' IDENTIFIED BY '${APP_PASSWORD_SQL}';
GRANT ALL PRIVILEGES ON task_repo.* TO 'midplat'@'%';
GRANT ALL PRIVILEGES ON artifact_repo.* TO 'midplat'@'%';
FLUSH PRIVILEGES;
SQL

echo "[init_mysql] artifact_repo 库与 midplat 账号已就绪"

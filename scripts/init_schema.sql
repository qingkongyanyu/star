-- ============================================================
-- 智能数据分析中台 · 数据库初始化（快速演示用）
-- 三库物理隔离：task_repo（任务）/ artifact_repo（制品）
-- 正式环境以 Alembic 迁移为准，本脚本仅用于本地快速初始化。
-- 用法：docker compose exec -T mysql mysql -uroot -proot < scripts/init_schema.sql
-- 对应文档：docs/02-后端/03-数据模型与迁移.md
-- ============================================================

CREATE DATABASE IF NOT EXISTS task_repo DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS artifact_repo DEFAULT CHARACTER SET utf8mb4;

-- 应用账号（与 backend/.env 的 DATABASE_URL 一致）
CREATE USER IF NOT EXISTS 'midplat'@'%' IDENTIFIED BY 'secret';
GRANT ALL PRIVILEGES ON task_repo.* TO 'midplat'@'%';
GRANT ALL PRIVILEGES ON artifact_repo.* TO 'midplat'@'%';
FLUSH PRIVILEGES;

-- ===================== TaskRepo =====================
USE task_repo;

CREATE TABLE IF NOT EXISTS task (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id       VARCHAR(64)  NOT NULL UNIQUE COMMENT '对外ID',
  req_id        VARCHAR(64)  NOT NULL COMMENT '需求ID（群聊消息）',
  requester     VARCHAR(64)  NOT NULL COMMENT '发起人/角色',
  task_type     VARCHAR(32)  NOT NULL,
  status        VARCHAR(16)  NOT NULL COMMENT 'PENDING/RUNNING/SUCCESS/FAILED/ABORTED',
  priority      TINYINT      NOT NULL DEFAULT 5,
  create_time   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  update_time   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_status (status),
  KEY idx_req (req_id)
) ENGINE=InnoDB COMMENT='任务主表';

CREATE TABLE IF NOT EXISTS dag_node (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  node_id          VARCHAR(64)  NOT NULL UNIQUE,
  task_id          VARCHAR(64)  NOT NULL,
  agent_type       VARCHAR(32)  NOT NULL COMMENT '能力标签',
  status           VARCHAR(16)  NOT NULL DEFAULT 'PENDING'
                   COMMENT 'PENDING/READY/CLAIMED/RUNNING/SUCCESS/FAILED/BLOCKED',
  dep_ids          JSON         NULL,
  input_asset_ids  JSON         NULL,
  output_asset_id  VARCHAR(64)  NULL,
  claim_token      VARCHAR(64)  NULL,
  retry_count      INT          NOT NULL DEFAULT 0,
  deadline         DATETIME(3)  NULL,
  create_time      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  update_time      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_task (task_id),
  KEY idx_status (status)
) ENGINE=InnoDB COMMENT='DAG 节点';

CREATE TABLE IF NOT EXISTS dag_edge (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id    VARCHAR(64) NOT NULL,
  from_node  VARCHAR(64) NOT NULL,
  to_node    VARCHAR(64) NOT NULL,
  edge_type  VARCHAR(16) NOT NULL COMMENT 'data_dep/quality_block/review_reject',
  KEY idx_task (task_id)
) ENGINE=InnoDB COMMENT='DAG 依赖边';

CREATE TABLE IF NOT EXISTS audit_log (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  actor       VARCHAR(64) NOT NULL,
  action      VARCHAR(32) NOT NULL,
  node_id     VARCHAR(64) NULL,
  asset_id    VARCHAR(64) NULL,
  detail      JSON        NULL,
  checksum    CHAR(64)    NOT NULL,
  KEY idx_ts (ts),
  KEY idx_asset (asset_id)
) ENGINE=InnoDB COMMENT='审计日志，追加写（应用层禁 UPDATE/DELETE）';

-- ===================== ArtifactRepo =====================
USE artifact_repo;

CREATE TABLE IF NOT EXISTS artifact (
  asset_id        VARCHAR(64) PRIMARY KEY,
  type            VARCHAR(24) NOT NULL
                  COMMENT 'profile/metric_spec/sql/dataset/dq_report/feature_set/chart/report/review/retrospective/trace',
  title           VARCHAR(255) NOT NULL,
  producer_agent  VARCHAR(32)  NOT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'published',
  create_time     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_type (type),
  KEY idx_producer (producer_agent)
) ENGINE=InnoDB COMMENT='资产主表';

CREATE TABLE IF NOT EXISTS artifact_version (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_id      VARCHAR(64) NOT NULL,
  version       INT         NOT NULL,
  object_key    VARCHAR(255) NOT NULL COMMENT 'MinIO 对象 key',
  digest        CHAR(64)    NOT NULL COMMENT 'SHA-256 内容摘要',
  status        VARCHAR(16) NOT NULL DEFAULT 'active',
  reviewer      VARCHAR(32) NULL,
  review_status VARCHAR(16) NULL COMMENT 'pending/approved/rejected',
  create_time   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_asset_ver (asset_id, version)
) ENGINE=InnoDB COMMENT='资产版本';

CREATE TABLE IF NOT EXISTS artifact_dependency (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_id      VARCHAR(64) NOT NULL,
  version       INT         NOT NULL,
  dep_asset_id  VARCHAR(64) NOT NULL,
  dep_version   INT         NOT NULL,
  edge_type     VARCHAR(16) NOT NULL DEFAULT 'data_dep',
  KEY idx_asset (asset_id),
  KEY idx_dep (dep_asset_id)
) ENGINE=InnoDB COMMENT='资产血缘';

"""task_repo 库的 ORM 模型。

task / dag_node / dag_edge / audit_log 严格镜像 scripts/init_schema.sql 与
docs/02-后端/03-数据模型与迁移.md；``user`` / ``admin_acl`` 文档无 DDL，
按 docs/02-后端/04 API 契约（登录响应 user 结构）与 docs/02-后端/07 表级 ACL
设计补齐。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, JSON, BigInteger, Boolean, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import Role
from app.models.base import TaskRepoBase


class Task(TaskRepoBase):
    """任务主表。"""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外ID")
    req_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="需求ID（群聊消息，幂等键，唯一约束兜底并发重复）"
    )
    requester: Mapped[str] = mapped_column(String(64), nullable=False, comment="发起人/角色")
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement: Mapped[str | None] = mapped_column(Text, comment="原始需求（群聊消息原文，供 Agent 消费）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="PENDING/RUNNING/SUCCESS/FAILED/ABORTED")
    priority: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("5"))
    data_source_id: Mapped[int | None] = mapped_column(BigInteger, comment="关联数据源ID（数据源接入功能）")
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, comment="Planner 产出的结构化分析计划（意图槽位/指标/任务/过滤，供各 Agent 消费）"
    )
    scheduler_token: Mapped[str | None] = mapped_column(
        String(64), comment="PLANNING/收尾仲裁租约 token（多 worker 竞争谁规划/收尾，表内冗余便于审计）"
    )
    scheduler_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=3), comment="租约最近心跳，超时未续视为租约过期（崩溃自愈依据）"
    )
    create_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    update_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_task_task_id"),
        UniqueConstraint("req_id", name="uq_task_req_id"),
        Index("idx_status", "status"),
    )


class DagNode(TaskRepoBase):
    """DAG 节点。"""

    __tablename__ = "dag_node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="能力标签，见 Agent 契约书")
    instruction: Mapped[str | None] = mapped_column(
        Text, comment="Planner 拆解时给该节点的定向任务指令（确定性 agent 作约束、LLM agent 拼入提示词）"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="PENDING", comment="PENDING/READY/CLAIMED/RUNNING/SUCCESS/FAILED/BLOCKED"
    )
    dep_ids: Mapped[list[str] | None] = mapped_column(JSON, comment="依赖节点ID列表")
    input_asset_ids: Mapped[list[str] | None] = mapped_column(JSON, comment="输入资产 ID@version")
    output_asset_id: Mapped[str | None] = mapped_column(String(64))
    claim_token: Mapped[str | None] = mapped_column(String(64), comment="Redis 锁 token（表内冗余，便于审计）")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deadline: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=3), comment="节点执行心跳，超时未续视为崩溃（CLAIMED/RUNNING 复位自愈依据）"
    )
    create_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    update_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )

    __table_args__ = (
        Index("idx_task", "task_id"),
        Index("idx_status", "status"),
    )


class DagEdge(TaskRepoBase):
    """DAG 依赖边。"""

    __tablename__ = "dag_edge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    from_node: Mapped[str] = mapped_column(String(64), nullable=False)
    to_node: Mapped[str] = mapped_column(String(64), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="data_dep/quality_block/review_reject")

    __table_args__ = (Index("idx_task", "task_id"),)


class AuditLog(TaskRepoBase):
    """审计日志，追加写（防篡改：checksum = SHA-256(detail)）。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户或 Agent")
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="task.created/artifact.published/dq.blocked/...")
    node_id: Mapped[str | None] = mapped_column(String(64))
    asset_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False, comment="SHA-256(detail)，防篡改")

    __table_args__ = (
        Index("idx_ts", "ts"),
        Index("idx_asset", "asset_id"),
    )


class User(TaskRepoBase):
    """平台用户。

    文档未给出 DDL，按 /auth/login 响应契约（id/username/role/display_name）与
    三档角色（Role 枚举）设计；密码只存 argon2 哈希。
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default=Role.READONLY.value)
    display_name: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )


class AdminAcl(TaskRepoBase):
    """表级 ACL 规则（docs/02-后端/07：{role_or_user, table, action, allow}）。"""

    __tablename__ = "admin_acl"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_or_user: Mapped[str] = mapped_column(String(64), nullable=False)
    table: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    allow: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))


class AgentConfig(TaskRepoBase):
    """Agent 配置（06-Agent实现指南：提示词版本随代码，管理员后台可覆盖存配置表）。

    文档无 DDL，按 /agents/{id}/config 契约设计；alembic 0004_agent_config 建表。
    """

    __tablename__ = "agent_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )


class MemoryEntry(TaskRepoBase):
    """记忆条目（AgentMemoryRepo 的权威存储）。

    记忆权威存储在 MySQL memory_entry（11 套私有 + 1 套集体，scope 区分）；
    检索走关键词匹配（RAG-lite）。0005 迁移建表。
    """

    __tablename__ = "memory_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, server_default="private")
    source_task: Mapped[str | None] = mapped_column(String(64))
    task_type: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    experience_type: Mapped[str] = mapped_column(String(24))
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    created_ts: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    ttl_ts: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class DataSource(TaskRepoBase):
    """数据源接入配置（数据源接入功能）。

    类型：mysql / postgres / olap / file / stream / api；config 为类型相关配置：
    - mysql/postgres: {host, port, database, username, password}
    - olap: {engine_url, vendor}
    - file: {file_name, object_key, columns, sample_rows, row_count}
    - stream: {bootstrap_servers, topic, format}
    - api: {url, method, headers, auth_type, api_key, columns?}
    """

    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, comment="mysql/postgres/olap/file/stream/api")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, comment="类型相关连接配置")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unverified")
    last_tested_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )


class AgentMessage(TaskRepoBase):
    """Agent 协作消息（v2：Agent-Agent / 用户→Agent 质询的持久化通道）。

    无 Kafka 时的定向消息落点：表可回放、可审计；写表后经 EventHub 广播 agent.message 供 WS 展示。
    subject：inquiry(质询)/feedback(反馈)/revision(修订)/notice(通知)。
    """

    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    from_role: Mapped[str] = mapped_column(String(32), nullable=False, comment="发起方（user 或能力标签）")
    to_role: Mapped[str] = mapped_column(String(32), nullable=False, comment="目标 Agent 能力标签")
    subject: Mapped[str] = mapped_column(String(24), nullable=False, server_default="inquiry")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending", comment="pending/responded")
    created_ts: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (
        Index("idx_to_role", "to_role", "status"),
        Index("idx_task", "task_id"),
    )


class KBIndicator(TaskRepoBase):
    """指标知识库条目。

    文档无任何 DDL，按 /admin/kb 契约（指标知识库条目）与 MetricDefinitionAgent
    RAG 用途设计；alembic 0002_kb_indicator 建表。
    """

    __tablename__ = "kb_indicator"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    indicator_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    formula: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    owner: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
    )

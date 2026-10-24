"""task_repo 库首版迁移。

创建：task / dag_node / dag_edge / audit_log / user / admin_acl（含索引）。
对应 models/task_repo.py 与 scripts/init_schema.sql（task 部分）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0001_task_repo_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- task：任务主表 ----
    op.create_table(
        "task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("req_id", sa.String(length=64), nullable=False),
        sa.Column("requester", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", mysql.TINYINT(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "create_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "update_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        mysql_engine="InnoDB",
        mysql_comment="任务主表",
    )
    op.create_index("idx_status", "task", ["status"])
    op.create_index("idx_req", "task", ["req_id"])

    # ---- dag_node：DAG 节点 ----
    op.create_table(
        "dag_node",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("agent_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("dep_ids", sa.JSON(), nullable=True),
        sa.Column("input_asset_ids", sa.JSON(), nullable=True),
        sa.Column("output_asset_id", sa.String(length=64), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deadline", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "create_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "update_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id"),
        mysql_engine="InnoDB",
        mysql_comment="DAG 节点",
    )
    op.create_index("idx_task", "dag_node", ["task_id"])
    op.create_index("idx_status", "dag_node", ["status"])

    # ---- dag_edge：DAG 依赖边 ----
    op.create_table(
        "dag_edge",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("from_node", sa.String(length=64), nullable=False),
        sa.Column("to_node", sa.String(length=64), nullable=False),
        sa.Column("edge_type", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="DAG 依赖边",
    )
    op.create_index("idx_task", "dag_edge", ["task_id"])

    # ---- audit_log：审计日志（追加写）----
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("asset_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("checksum", sa.CHAR(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="审计日志，追加写",
    )
    op.create_index("idx_ts", "audit_log", ["ts"])
    op.create_index("idx_asset", "audit_log", ["asset_id"])

    # ---- user：平台用户 ----
    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="readonly", nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        mysql_engine="InnoDB",
        mysql_comment="平台用户",
    )

    # ---- admin_acl：表级 ACL 规则 ----
    op.create_table(
        "admin_acl",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("role_or_user", sa.String(length=64), nullable=False),
        sa.Column("table", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("allow", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="表级 ACL 规则",
    )


def downgrade() -> None:
    op.drop_index("idx_asset", table_name="audit_log")
    op.drop_index("idx_ts", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("idx_task", table_name="dag_edge")
    op.drop_table("dag_edge")
    op.drop_index("idx_status", table_name="dag_node")
    op.drop_index("idx_task", table_name="dag_node")
    op.drop_table("dag_node")
    op.drop_index("idx_req", table_name="task")
    op.drop_index("idx_status", table_name="task")
    op.drop_table("task")
    op.drop_table("admin_acl")
    op.drop_table("user")

"""task_repo 库 0010：Agent 协作消息表。

创建：agent_message。对应 models/task_repo.py 的 AgentMessage（v2 定向消息/质询持久化）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0010_agent_message"
down_revision = "0009_scheduler_lease_instruction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("from_role", sa.String(length=32), nullable=False),
        sa.Column("to_role", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=24), nullable=False, server_default="inquiry"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "created_ts",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Column("responded_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="Agent 协作消息",
    )
    op.create_index("idx_to_role", "agent_message", ["to_role", "status"])
    op.create_index("idx_task", "agent_message", ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_task", table_name="agent_message")
    op.drop_index("idx_to_role", table_name="agent_message")
    op.drop_table("agent_message")

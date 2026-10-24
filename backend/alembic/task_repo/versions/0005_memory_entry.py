"""task_repo 库 0005：记忆条目表（MySQL 降级存储，镜像 Qdrant payload）。

创建：memory_entry。对应 models/task_repo.py 的 MemoryEntry。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0005_memory_entry"
down_revision = "0004_agent_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_entry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), server_default="private", nullable=False),
        sa.Column("source_task", sa.String(length=64), nullable=True),
        sa.Column("task_type", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("experience_type", sa.String(length=24), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column(
            "created_ts",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column("ttl_ts", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("access_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="记忆条目（Qdrant 降级存储）",
    )
    op.create_index("idx_memory_scope_agent", "memory_entry", ["scope", "agent_id"])


def downgrade() -> None:
    op.drop_index("idx_memory_scope_agent", table_name="memory_entry")
    op.drop_table("memory_entry")

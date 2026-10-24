"""task_repo 库 0004：Agent 配置表。

创建：agent_config。对应 models/task_repo.py 的 AgentConfig（文档无 DDL，自设计）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0004_agent_config"
down_revision = "0003_task_requirement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id"),
        mysql_engine="InnoDB",
        mysql_comment="Agent 配置",
    )


def downgrade() -> None:
    op.drop_table("agent_config")

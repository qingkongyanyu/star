"""task_repo 库 0002：指标知识库表。

创建：kb_indicator。对应 models/task_repo.py 的 KBIndicator（docs 无 DDL，自设计）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0002_kb_indicator"
down_revision = "0001_task_repo_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_indicator",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("indicator_key", sa.String(length=64), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=True),
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
        sa.UniqueConstraint("indicator_key"),
        mysql_engine="InnoDB",
        mysql_comment="指标知识库条目",
    )


def downgrade() -> None:
    op.drop_table("kb_indicator")

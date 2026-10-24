"""task_repo 库 0006：数据源接入表 + 任务关联数据源。

创建：data_source；task 表新增 data_source_id 列。
对应 models/task_repo.py 的 DataSource。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0006_data_source"
down_revision = "0005_memory_entry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False, comment="mysql/postgres/olap/file/stream/api"),
        sa.Column("config", sa.JSON(), nullable=False, comment="类型相关连接配置"),
        sa.Column("status", sa.String(length=20), server_default="unverified", nullable=False),
        sa.Column("last_tested_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
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
        mysql_engine="InnoDB",
        mysql_comment="数据源接入配置",
    )
    op.add_column("task", sa.Column("data_source_id", sa.BigInteger(), nullable=True, comment="关联数据源ID"))


def downgrade() -> None:
    op.drop_column("task", "data_source_id")
    op.drop_table("data_source")

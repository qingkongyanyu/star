"""artifact_repo 库首版迁移。

创建：artifact / artifact_version / artifact_dependency。
对应 models/artifact_repo.py 与 scripts/init_schema.sql（artifact 部分）。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0001_artifact_repo_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- artifact：资产主表 ----
    op.create_table(
        "artifact",
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("producer_agent", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="published", nullable=False),
        sa.Column(
            "create_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id"),
        mysql_engine="InnoDB",
        mysql_comment="资产主表",
    )
    op.create_index("idx_type", "artifact", ["type"])
    op.create_index("idx_producer", "artifact", ["producer_agent"])

    # ---- artifact_version：资产版本 ----
    op.create_table(
        "artifact_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("digest", sa.CHAR(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("reviewer", sa.String(length=32), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=True),
        sa.Column(
            "create_time",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "version", name="uk_asset_ver"),
        mysql_engine="InnoDB",
        mysql_comment="资产版本",
    )

    # ---- artifact_dependency：资产血缘 ----
    op.create_table(
        "artifact_dependency",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dep_asset_id", sa.String(length=64), nullable=False),
        sa.Column("dep_version", sa.Integer(), nullable=False),
        sa.Column("edge_type", sa.String(length=16), server_default="data_dep", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_comment="资产血缘",
    )
    op.create_index("idx_asset", "artifact_dependency", ["asset_id"])
    op.create_index("idx_dep", "artifact_dependency", ["dep_asset_id"])


def downgrade() -> None:
    op.drop_index("idx_dep", table_name="artifact_dependency")
    op.drop_index("idx_asset", table_name="artifact_dependency")
    op.drop_table("artifact_dependency")
    op.drop_table("artifact_version")
    op.drop_index("idx_producer", table_name="artifact")
    op.drop_index("idx_type", table_name="artifact")
    op.drop_table("artifact")

"""artifact_repo 库的 ORM 模型。

严格镜像 scripts/init_schema.sql 与 docs/02-后端/03-数据模型与迁移.md。
"""
from datetime import datetime

from sqlalchemy import CHAR, BigInteger, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ArtifactRepoBase


class Artifact(ArtifactRepoBase):
    """资产主表。"""

    __tablename__ = "artifact"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="profile/metric_spec/sql/dataset/dq_report/feature_set/chart/report/review/retrospective/trace"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    producer_agent: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="published")
    create_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )

    __table_args__ = (
        Index("idx_type", "type"),
        Index("idx_producer", "producer_agent"),
    )


class ArtifactVersion(ArtifactRepoBase):
    """资产版本。"""

    __tablename__ = "artifact_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, comment="MinIO 对象 key")
    digest: Mapped[str] = mapped_column(CHAR(64), nullable=False, comment="SHA-256 内容摘要")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    reviewer: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str | None] = mapped_column(String(16), comment="pending/approved/rejected")
    create_time: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )

    __table_args__ = (UniqueConstraint("asset_id", "version", name="uk_asset_ver"),)


class ArtifactDependency(ArtifactRepoBase):
    """资产血缘。"""

    __tablename__ = "artifact_dependency"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dep_asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dep_version: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="data_dep")

    __table_args__ = (
        Index("idx_asset", "asset_id"),
        Index("idx_dep", "dep_asset_id"),
    )

"""ArtifactRepo 数据访问（artifact / artifact_version / artifact_dependency）。

同步函数，服务层统一经 infra.db.to_thread 调用；会话用 artifact_repo 库。
血缘方向：asset_id 依赖 dep_asset_id（dep 为上游）。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.artifact_repo import Artifact, ArtifactDependency, ArtifactVersion


class ArtifactRepo:
    """ArtifactRepo 数据访问。"""

    @staticmethod
    def get_by_ids(session: Session, asset_ids: list[str]) -> list[Artifact]:
        """按资产 ID 批量取（顺序按入参，供任务工作台按 DAG 节点顺序展示）。"""
        if not asset_ids:
            return []
        rows = list(
            session.execute(select(Artifact).where(Artifact.asset_id.in_(asset_ids))).scalars()
        )
        order = {aid: i for i, aid in enumerate(asset_ids)}
        rows.sort(key=lambda a: order.get(a.asset_id, 999))
        return rows

    @staticmethod
    def get_by_asset_id(session: Session, asset_id: str) -> Artifact | None:
        return session.get(Artifact, asset_id)

    @staticmethod
    def list_assets(
        session: Session,
        asset_type: str | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Artifact], int]:
        """资产列表（类型 + 关键字 + 分页，按 id 倒序）。"""
        stmt = select(Artifact)
        count_stmt = select(func.count()).select_from(Artifact)
        if asset_type is not None:
            stmt = stmt.where(Artifact.type == asset_type)
            count_stmt = count_stmt.where(Artifact.type == asset_type)
        if keyword is not None:
            pattern = f"%{keyword}%"
            stmt = stmt.where(or_(Artifact.title.like(pattern), Artifact.asset_id.like(pattern)))
            count_stmt = count_stmt.where(or_(Artifact.title.like(pattern), Artifact.asset_id.like(pattern)))
        total = session.execute(count_stmt).scalar_one()
        items = list(
            session.execute(
                stmt.order_by(Artifact.create_time.desc()).offset((page - 1) * page_size).limit(page_size)
            ).scalars()
        )
        return items, total

    @staticmethod
    def get_versions(session: Session, asset_id: str) -> list[ArtifactVersion]:
        """版本列表（version 倒序）。"""
        return list(
            session.execute(
                select(ArtifactVersion)
                .where(ArtifactVersion.asset_id == asset_id)
                .order_by(ArtifactVersion.version.desc())
            ).scalars()
        )

    @staticmethod
    def get_version(session: Session, asset_id: str, version: int) -> ArtifactVersion | None:
        return session.execute(
            select(ArtifactVersion).where(
                ArtifactVersion.asset_id == asset_id, ArtifactVersion.version == version
            )
        ).scalar_one_or_none()

    @staticmethod
    def get_latest_version(session: Session, asset_id: str) -> ArtifactVersion | None:
        """最新版本（version 最大，active 优先）。"""
        return session.execute(
            select(ArtifactVersion)
            .where(ArtifactVersion.asset_id == asset_id)
            .order_by(ArtifactVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def count_by_type(session: Session) -> dict[str, int]:
        """资产按类型计数（大屏 assets_by_type）。"""
        rows = session.execute(select(Artifact.type, func.count()).group_by(Artifact.type)).all()
        return {asset_type: count for asset_type, count in rows}

    @staticmethod
    def get_latest_by_type(session: Session, asset_type: str) -> Artifact | None:
        """指定类型的最新资产（按 create_time 倒序）。"""
        return session.execute(
            select(Artifact).where(Artifact.type == asset_type).order_by(Artifact.create_time.desc()).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_latest_version_numbers(session: Session, asset_ids: list[str]) -> dict[str, int]:
        """批量取一批资产的最新版本号（一次查询，供列表页组装）。"""
        if not asset_ids:
            return {}
        rows = session.execute(
            select(ArtifactVersion.asset_id, func.max(ArtifactVersion.version))
            .where(ArtifactVersion.asset_id.in_(asset_ids))
            .group_by(ArtifactVersion.asset_id)
        ).all()
        return {asset_id: max_version for asset_id, max_version in rows}

    @staticmethod
    def get_dependencies(session: Session, asset_id: str, version: int) -> list[ArtifactDependency]:
        """资产 (asset_id, version) 的上游依赖（dep_asset_id 为上游）。"""
        return list(
            session.execute(
                select(ArtifactDependency).where(
                    ArtifactDependency.asset_id == asset_id, ArtifactDependency.version == version
                )
            ).scalars()
        )

    @staticmethod
    def next_version(session: Session, asset_id: str) -> int:
        """新资产版本号（无则 1）。"""
        latest = ArtifactRepo.get_latest_version(session, asset_id)
        return (latest.version + 1) if latest is not None else 1

    @staticmethod
    def publish(
        session: Session,
        artifact: Artifact,
        version: ArtifactVersion,
        dependencies: list[ArtifactDependency],
    ) -> ArtifactVersion:
        """单事务写 artifact（若不存在）+ artifact_version + artifact_dependency。"""
        if session.get(Artifact, artifact.asset_id) is None:
            session.add(artifact)
        session.add(version)
        for dep in dependencies:
            session.add(dep)
        session.commit()
        session.refresh(version)
        return version

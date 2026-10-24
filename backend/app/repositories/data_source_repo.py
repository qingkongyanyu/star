"""数据源数据访问（data_source）。

同步函数，服务层统一经 infra.db.to_thread 调用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.task_repo import DataSource


class DataSourceRepo:
    """DataSource 数据访问。"""

    @staticmethod
    def list_all(session: Session) -> list[DataSource]:
        return list(session.execute(select(DataSource).order_by(DataSource.id.desc())).scalars())

    @staticmethod
    def get_by_id(session: Session, ds_id: int) -> DataSource | None:
        return session.execute(select(DataSource).where(DataSource.id == ds_id)).scalar_one_or_none()

    @staticmethod
    def create(
        session: Session,
        *,
        name: str,
        ds_type: str,
        config: dict[str, Any],
        created_by: str | None,
        status: str = "unverified",
    ) -> DataSource:
        ds = DataSource(name=name, type=ds_type, config=config, created_by=created_by, status=status)
        session.add(ds)
        session.commit()
        session.refresh(ds)
        return ds

    @staticmethod
    def update_status(session: Session, ds_id: int, status: str, ok: bool) -> None:
        ds = DataSourceRepo.get_by_id(session, ds_id)
        if ds is None:
            return
        ds.status = "connected" if ok else "error"
        ds.last_tested_at = datetime.now()  # noqa: DTZ005  与 task_repo.py 时间处理一致
        session.commit()

    @staticmethod
    def delete(session: Session, ds_id: int) -> None:
        ds = DataSourceRepo.get_by_id(session, ds_id)
        if ds is None:
            return
        session.delete(ds)
        session.commit()

    @staticmethod
    def count(session: Session) -> int:
        return int(session.execute(select(func.count()).select_from(DataSource)).scalar_one())

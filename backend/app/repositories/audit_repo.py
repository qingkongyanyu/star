"""审计日志数据访问（追加写，checksum=SHA-256(detail) 防篡改）。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.task_repo import AuditLog


def _checksum(detail: dict[str, Any] | None) -> str:
    payload = (
        json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if detail is not None
        else ""
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditRepo:
    """audit_log 数据访问。"""

    @staticmethod
    def append(
        session: Session,
        *,
        actor: str,
        action: str,
        node_id: str | None = None,
        asset_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditLog:
        """追加一条审计日志（每次写操作后调用，契约 §十一）。"""
        entry = AuditLog(
            actor=actor,
            action=action,
            node_id=node_id,
            asset_id=asset_id,
            detail=detail,
            checksum=_checksum(detail),
        )
        session.add(entry)
        session.commit()
        return entry

    @staticmethod
    def list_by_node(session: Session, node_id: str) -> list[AuditLog]:
        """按节点回放日志（时间正序）。"""
        return list(
            session.execute(
                select(AuditLog).where(AuditLog.node_id == node_id).order_by(AuditLog.id)
            ).scalars()
        )

    @staticmethod
    def list(
        session: Session,
        *,
        actor: str | None = None,
        action: str | None = None,
        asset_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditLog], int]:
        """审计查询（筛选 + 分页，按 id 倒序；只读，应用层禁 UPDATE/DELETE）。"""
        stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)
        if actor is not None:
            stmt = stmt.where(AuditLog.actor == actor)
            count_stmt = count_stmt.where(AuditLog.actor == actor)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if asset_id is not None:
            stmt = stmt.where(AuditLog.asset_id == asset_id)
            count_stmt = count_stmt.where(AuditLog.asset_id == asset_id)
        if from_ts is not None:
            stmt = stmt.where(AuditLog.ts >= from_ts)
            count_stmt = count_stmt.where(AuditLog.ts >= from_ts)
        if to_ts is not None:
            stmt = stmt.where(AuditLog.ts <= to_ts)
            count_stmt = count_stmt.where(AuditLog.ts <= to_ts)
        total = session.execute(count_stmt).scalar_one()
        items = list(
            session.execute(
                stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
            ).scalars()
        )
        return items, total

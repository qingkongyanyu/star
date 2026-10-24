"""记忆数据访问（memory_entry，记忆权威存储）。"""
from __future__ import annotations

from sqlalchemy import String, or_, select
from sqlalchemy.orm import Session

from app.models.task_repo import MemoryEntry


class MemoryRepo:
    """memory_entry 数据访问。"""

    @staticmethod
    def create(
        session: Session,
        *,
        agent_id: str,
        scope: str,
        content: str,
        experience_type: str,
        source_task: str | None = None,
        task_type: str | None = None,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """插入记忆条目（仅 MemoryKeeper 经 ingest 写库，契约强约束）。"""
        entry = MemoryEntry(
            agent_id=agent_id,
            scope=scope,
            source_task=source_task,
            task_type=task_type,
            content=content,
            experience_type=experience_type,
            tags=tags,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry

    @staticmethod
    def search(
        session: Session,
        *,
        scope: str,
        agent_id: str | None,
        keyword: str | None,
        top_k: int,
        task_id: str | None = None,
    ) -> list[MemoryEntry]:
        """按 scope + 关键词（+ 任务）检索记忆（降级后端为 content LIKE + created_ts 倒序）。"""
        stmt = select(MemoryEntry)
        if scope == "private":
            stmt = stmt.where(MemoryEntry.scope == "private")
            if agent_id:
                stmt = stmt.where(MemoryEntry.agent_id == agent_id)
        elif scope == "collective":
            stmt = stmt.where(MemoryEntry.scope == "collective")
        # scope == "all"：不过滤
        if task_id:
            stmt = stmt.where(MemoryEntry.source_task == task_id)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(or_(MemoryEntry.content.like(pattern), MemoryEntry.tags.cast(String()).like(pattern)))
        return list(
            session.execute(stmt.order_by(MemoryEntry.created_ts.desc()).limit(top_k)).scalars()
        )

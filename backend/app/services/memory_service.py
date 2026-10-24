"""记忆用例：检索 / 复盘纪要列表（MemoryKeeper 写路径 ingest 属 Agent 阶段，本模块只读）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.infra.db import to_thread
from app.repositories.artifact_repo import ArtifactRepo
from app.repositories.memory_repo import MemoryRepo
from app.schemas.asset import AssetOut
from app.schemas.memory import MemoryHitOut


class MemoryService:
    """记忆服务：记忆权威存储在 MySQL（memory_entry），检索走关键词匹配（RAG-lite）。"""

    def __init__(self, task_session: Session, artifact_session: Session) -> None:
        self._task = task_session
        self._artifact = artifact_session

    async def search(
        self,
        *,
        query: str,
        scope: str,
        agent_id: str | None,
        top_k: int,
        task_id: str | None = None,
    ) -> list[MemoryHitOut]:
        rows = await to_thread(
            MemoryRepo.search,
            self._task,
            scope=scope,
            agent_id=agent_id,
            keyword=query or None,
            top_k=top_k,
            task_id=task_id,
        )
        return [MemoryHitOut.model_validate(row) for row in rows]

    async def retrospectives(self) -> list[AssetOut]:
        """复盘纪要列表（MemoryKeeper compose_retrospective_doc 交付物，type=retrospective）。"""
        items, _total = await to_thread(ArtifactRepo.list_assets, self._artifact, "retrospective", None, 1, 100)
        latest = await to_thread(
            ArtifactRepo.get_latest_version_numbers, self._artifact, [asset.asset_id for asset in items]
        )
        result: list[AssetOut] = []
        for asset in items:
            item = AssetOut.model_validate(asset)
            item.latest_version = latest.get(asset.asset_id)
            result.append(item)
        return result

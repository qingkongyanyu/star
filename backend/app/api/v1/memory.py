"""记忆接口：/memory（检索 / 复盘纪要列表）。

契约见 docs/02-后端/04-API接口契约.md §六（记忆）。v1 属主模型未接入：
scope=private 仅管理员；scope=collective/all 任意登录。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_role
from app.core.constants import Role
from app.infra.db import get_artifact_db, get_task_db
from app.models.task_repo import User
from app.schemas.asset import AssetOut
from app.schemas.memory import MemoryHitOut
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/search", response_model=list[MemoryHitOut], summary="记忆检索")
async def search(
    user: Annotated[User, Depends(get_current_user)],
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    scope: Annotated[str, Query(pattern="^(private|collective|all)$")] = "all",
    agent: Annotated[str | None, Query(max_length=64)] = None,
    top_k: Annotated[int, Query(ge=1, le=50)] = 5,
) -> list[MemoryHitOut]:
    """记忆检索（私有记忆仅管理员/属主；属主模型待接入）。"""
    # 非管理员访问 private/all 一律收敛为 collective，防止 scope=all 越权读到私有记忆
    if user.role != Role.ADMIN.value and scope in ("private", "all"):
        scope = "collective"
    return await MemoryService(task_session, artifact_session).search(
        query=q, scope=scope, agent_id=agent, top_k=top_k
    )


@router.get("/retrospectives", response_model=list[AssetOut], summary="复盘纪要列表")
async def retrospectives(
    _admin: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
) -> list[AssetOut]:
    """复盘纪要列表（管理员）。"""
    return await MemoryService(task_session, artifact_session).retrospectives()

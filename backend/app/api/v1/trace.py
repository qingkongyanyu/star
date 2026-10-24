"""溯源接口：/trace（血缘 + 归因报告 / 责任人清单）。

契约见 docs/02-后端/04-API接口契约.md §七（溯源），权限 分析师+。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import require_role
from app.core.constants import Role
from app.infra.db import get_artifact_db, get_task_db
from app.models.task_repo import User
from app.schemas.trace import ResponsibilityListOut, TraceReportOut
from app.services.trace_service import TraceService

router = APIRouter(prefix="/trace", tags=["trace"])

_User = Annotated[User, Depends(require_role(Role.ANALYST.value, Role.ADMIN.value))]


@router.get("/{asset_id}", response_model=TraceReportOut, summary="血缘 + 归因报告")
async def trace(
    asset_id: str,
    user: _User,
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
    depth: Annotated[int, Query(ge=1, le=10)] = 10,
) -> TraceReportOut:
    """血缘链 + 根因定位 + 责任人清单 + 归因报告。"""
    return await TraceService(task_session, artifact_session).trace(asset_id, depth)


@router.get("/{asset_id}/responsibility", response_model=ResponsibilityListOut, summary="责任人清单")
async def responsibility(
    asset_id: str,
    user: _User,
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
) -> ResponsibilityListOut:
    """责任人清单（第一责任人 + 协同责任人）。"""
    return await TraceService(task_session, artifact_session).responsibility(asset_id)

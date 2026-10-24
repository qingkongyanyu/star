"""大屏接口：/dashboard（KPI 总览 / 任务趋势 / 数据质量雷达）。

契约见 docs/02-后端/04-API接口契约.md §八（大屏），权限 登录。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.infra.db import get_artifact_db, get_task_db
from app.models.task_repo import User
from app.schemas.dashboard import QualityOut, SummaryOut, TrendsOut
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=SummaryOut, summary="KPI 总览")
async def summary(
    _user: Annotated[User, Depends(get_current_user)],
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
) -> SummaryOut:
    """KPI：任务总量/成功率/DQ阻断率/FE触发率/Agent健康/资产分布。"""
    return await DashboardService(task_session, artifact_session).summary()


@router.get("/trends", response_model=TrendsOut, summary="近 N 日任务趋势")
async def trends(
    _user: Annotated[User, Depends(get_current_user)],
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> TrendsOut:
    """近 N 日任务创建趋势。"""
    return await DashboardService(task_session, artifact_session).trends(days)


@router.get("/quality", response_model=QualityOut, summary="数据质量雷达")
async def quality(
    _user: Annotated[User, Depends(get_current_user)],
    task_session: Annotated[Session, Depends(get_task_db)],
    artifact_session: Annotated[Session, Depends(get_artifact_db)],
) -> QualityOut:
    """数据质量雷达（读最新 dq_report）。"""
    return await DashboardService(task_session, artifact_session).quality()

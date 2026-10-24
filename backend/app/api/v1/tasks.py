"""任务接口：/tasks（5 端点）+ /nodes/{node_id}（契约顶级路径）。

契约见 docs/02-后端/04-API接口契约.md §三（任务）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_role
from app.core.constants import Role, TaskStatus
from app.core.request_id import generate_request_id
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.schemas.asset import AssetOut
from app.schemas.memory import MemoryHitOut
from app.schemas.task import (
    DAGOut,
    NodeDetailOut,
    OverrideRequest,
    Page,
    TaskCreateRequest,
    TaskDetailOut,
    TaskOut,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
nodes_router = APIRouter(prefix="", tags=["tasks"])


@router.post("", response_model=DAGOut, status_code=status.HTTP_201_CREATED, summary="下发任务")
async def create_task(
    payload: TaskCreateRequest,
    user: Annotated[User, Depends(require_role(Role.ANALYST.value, Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DAGOut:
    """群聊需求 → 任务 + DAG 落库；Idempotency-Key 重复提交返回原任务。"""
    req_id = idempotency_key or generate_request_id()
    return await TaskService(session).create_task(
        message=payload.message,
        task_type=payload.task_type,
        priority=payload.priority,
        requester=user.username,
        req_id=req_id,
        data_source_id=payload.data_source_id,
    )


@router.get("", response_model=Page[TaskOut], summary="任务列表")
async def list_tasks(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TaskOut]:
    """任务列表（状态筛选 + 分页）。"""
    return await TaskService(session).list_tasks(
        status_filter.value if status_filter is not None else None,
        page,
        page_size,
    )


@router.get("/{task_id}", response_model=TaskDetailOut, summary="任务详情 + DAG")
async def get_task(
    task_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> TaskDetailOut:
    """任务详情 + DAG。"""
    return await TaskService(session).get_task_detail(task_id)


@router.get("/{task_id}/dag", response_model=DAGOut, summary="DAG 图数据")
async def get_task_dag(
    task_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> DAGOut:
    """DAG 图数据（节点/边/状态）。"""
    return await TaskService(session).get_dag(task_id)


@router.post("/{task_id}/override", status_code=status.HTTP_204_NO_CONTENT, summary="人工干预")
async def override(
    task_id: str,
    payload: OverrideRequest,
    user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> None:
    """人工干预：block / allow / rerun / abort（abort 为任务级）。"""
    await TaskService(session).override(task_id, payload.node_id, payload.action, user.username)


@router.get("/{task_id}/assets", response_model=list[AssetOut], summary="任务资产列表")
async def get_task_assets(
    task_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[AssetOut]:
    """任务工作台：该任务按 DAG 产出顺序的全部资产。"""
    return await TaskService(session).get_task_assets(task_id)


@router.get("/{task_id}/memory", response_model=list[MemoryHitOut], summary="任务记忆")
async def get_task_memory(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[MemoryHitOut]:
    """任务工作台：该任务产生的记忆条目（非管理员只见 collective）。"""
    return await TaskService(session).get_task_memory(task_id, role=user.role)


@nodes_router.get("/nodes/{node_id}", response_model=NodeDetailOut, summary="节点详情（含日志）")
async def get_node(
    node_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> NodeDetailOut:
    """节点详情（日志由审计记录回放）。"""
    return await TaskService(session).get_node_detail(node_id)

"""Agent 接口：/agents（列表/配置/质询）。

契约见 docs/02-后端/04-API接口契约.md §五（Agent）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_role
from app.core.constants import Role
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.schemas.agent import AgentConfigOut, AgentConfigUpdate, AgentKBOut, AgentOut, InquiryRequest, InquiryResponse
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut], summary="Agent 列表 + 实时状态")
async def list_agents(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[AgentOut]:
    """Agent 列表（12 个能力）+ 健康/配置/是否已实现。"""
    return await AgentService(session).list_agents()


@router.get("/kb", response_model=list[AgentKBOut], summary="Agent 知识库（职责 + 提示词）")
async def agent_knowledge(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[AgentKBOut]:
    """Agent 知识库：12 个 agent 的职责摘要、系统提示词、产出资产类型。"""
    return AgentService(session).knowledge()


@router.get("/{agent_id}/config", response_model=AgentConfigOut, summary="Agent 配置")
async def get_config(
    agent_id: str,
    _admin: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> AgentConfigOut:
    """某 Agent 配置（默认 + 覆盖合并）。"""
    return await AgentService(session).get_config(agent_id)


@router.put("/{agent_id}/config", response_model=AgentConfigOut, summary="更新配置")
async def update_config(
    agent_id: str,
    payload: AgentConfigUpdate,
    admin: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> AgentConfigOut:
    """更新配置（upsert + 审计）。"""
    return await AgentService(session).update_config(agent_id, payload.config, actor=admin.username)


@router.post("/{agent_id}/inquiry", response_model=InquiryResponse, summary="质询")
async def inquiry(
    agent_id: str,
    payload: InquiryRequest,
    user: Annotated[User, Depends(require_role(Role.ANALYST.value, Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> InquiryResponse:
    """质询（TraceNavigator @问责；v1 回执式应答）。"""
    return await AgentService(session).inquiry(agent_id, payload.message, actor=user.username)

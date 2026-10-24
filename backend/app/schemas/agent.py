"""Agent 相关 DTO（契约 docs/02-后端/04 §五；响应形状自设计）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentOut(BaseModel):
    """Agent 列表项 + 实时状态。"""

    id: str  # AGENT_CAPABILITY 的键，如 sql_agent
    name: str
    capability: str
    status: str  # healthy / warning / error / online
    health_percent: int
    implemented: bool
    config: dict[str, Any]


class AgentKBOut(BaseModel):
    """Agent 知识库 / 提示词库条目。"""

    id: str
    name: str
    capability: str
    role: str  # 职责摘要
    system_prompt: str  # 系统提示词
    asset_type: str  # 产出资产类型
    implemented: bool


class AgentConfigOut(BaseModel):
    """Agent 配置（默认 + 覆盖合并）。"""

    agent_id: str
    capability: str
    config: dict[str, Any]


class AgentConfigUpdate(BaseModel):
    """PUT /agents/{id}/config 请求体。"""

    config: dict[str, Any] = Field(default_factory=dict)


class InquiryRequest(BaseModel):
    """POST /agents/{id}/inquiry 请求体。"""

    message: str = Field(min_length=1, max_length=2000)


class InquiryResponse(BaseModel):
    """质询应答。"""

    reply: str

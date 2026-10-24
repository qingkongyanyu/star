"""溯源归因 DTO（契约 docs/02-后端/04 §七；响应形状自设计）。"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.asset import LineageOut


class RootCauseOut(BaseModel):
    """根因节点。"""

    node_id: str
    agent_type: str
    agent_name: str
    asset_id: str
    reason: str
    evidence: list[str]


class ResponsibilityOut(BaseModel):
    """责任人条目（第一 / 协同）。"""

    role: str  # primary / collaborative
    node_id: str
    agent_type: str
    agent_name: str
    asset_id: str
    reason: str


class ResponsibilityListOut(BaseModel):
    """责任人清单。"""

    asset_id: str
    items: list[ResponsibilityOut]


class TraceReportOut(BaseModel):
    """血缘 + 归因报告（溯源链路 + 责任人清单 + 依赖资产树）。"""

    asset_id: str
    version: int
    lineage: LineageOut
    root_cause: RootCauseOut | None = None
    responsibility: list[ResponsibilityOut] = []
    report: str

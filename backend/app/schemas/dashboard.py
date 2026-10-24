"""大屏仪表盘 DTO（契约 docs/02-后端/04 §八；聚合字段按前端原型自设计）。"""
from __future__ import annotations

from pydantic import BaseModel


class AgentHealthOut(BaseModel):
    """Agent 健康条目。"""

    capability: str
    name: str
    status: str  # healthy / warning / error / online(无执行记录)
    health_percent: int


class SummaryOut(BaseModel):
    """KPI 总览。"""

    total_tasks: int
    success_rate: float  # 百分数，一位小数
    dq_block_rate: float
    fe_trigger_rate: float
    agent_health: list[AgentHealthOut]
    assets_by_type: dict[str, int]


class TrendPoint(BaseModel):
    """近 N 日任务趋势单点。"""

    date: str  # YYYY-MM-DD
    created: int


class TrendsOut(BaseModel):
    """任务趋势。"""

    days: int
    items: list[TrendPoint]


class QualityDimension(BaseModel):
    """数据质量雷达维度。"""

    name: str
    value: int


class QualityOut(BaseModel):
    """数据质量雷达。"""

    score: int
    dimensions: list[QualityDimension]

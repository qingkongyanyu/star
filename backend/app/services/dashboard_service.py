"""大屏聚合用例：KPI 总览 / 任务趋势 / 数据质量雷达。

跨 task_repo + artifact_repo 两库聚合，服务层内存合并。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.constants import AGENT_CAPABILITY, AGENT_DISPLAY_NAMES, TaskStatus
from app.infra.db import to_thread
from app.repositories.artifact_repo import ArtifactRepo
from app.repositories.task_repo import TaskRepo
from app.schemas.dashboard import (
    AgentHealthOut,
    QualityDimension,
    QualityOut,
    SummaryOut,
    TrendPoint,
    TrendsOut,
)
from app.services.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

_QUALITY_DIMS = ["完整性", "唯一性", "一致性", "时效性", "准确性"]


class DashboardService:
    """大屏统计聚合服务。"""

    def __init__(self, task_session: Session, artifact_session: Session) -> None:
        self._task = task_session
        self._artifact = artifact_session

    async def summary(self) -> SummaryOut:
        task_status = await to_thread(TaskRepo.count_tasks_by_status, self._task)
        total_tasks = sum(task_status.values())
        success = task_status.get(TaskStatus.SUCCESS.value, 0)
        success_rate = round(success / total_tasks * 100, 1) if total_tasks else 0.0

        edge_counts = await to_thread(TaskRepo.count_edges_by_type, self._task)
        total_edges = sum(edge_counts.values())
        dq_block = edge_counts.get("quality_block", 0)
        dq_block_rate = round(dq_block / total_edges * 100, 1) if total_edges else 0.0

        node_stats = await to_thread(TaskRepo.count_nodes_by_agent_type, self._task)
        total_nodes = sum(stats["total"] for stats in node_stats.values())
        fe_nodes = node_stats.get("feature_engineer", {}).get("total", 0)
        fe_trigger_rate = round(fe_nodes / total_nodes * 100, 1) if total_nodes else 0.0

        assets_by_type = await to_thread(ArtifactRepo.count_by_type, self._artifact)

        return SummaryOut(
            total_tasks=total_tasks,
            success_rate=success_rate,
            dq_block_rate=dq_block_rate,
            fe_trigger_rate=fe_trigger_rate,
            agent_health=self._build_agent_health(node_stats),
            assets_by_type=assets_by_type,
        )

    async def trends(self, days: int) -> TrendsOut:
        rows = await to_thread(TaskRepo.task_trend, self._task, days)
        return TrendsOut(days=days, items=[TrendPoint(date=day, created=count) for day, count in rows])

    async def quality(self) -> QualityOut:
        latest = await to_thread(ArtifactRepo.get_latest_by_type, self._artifact, "dq_report")
        if latest is None:
            return QualityOut(score=0, dimensions=[QualityDimension(name=n, value=0) for n in _QUALITY_DIMS])
        version = await to_thread(ArtifactRepo.get_latest_version, self._artifact, latest.asset_id)
        if version is None:
            return QualityOut(score=0, dimensions=[QualityDimension(name=n, value=0) for n in _QUALITY_DIMS])
        try:
            content = await ArtifactService(self._artifact).fetch_content(latest.asset_id, version.version)
            parsed = json.loads(content)
            score = int(parsed.get("score", 0))
            dimensions = parsed.get("dimensions")
            if isinstance(dimensions, dict):
                values = [QualityDimension(name=n, value=int(dimensions.get(n, score))) for n in _QUALITY_DIMS]
            else:
                values = [QualityDimension(name=n, value=score) for n in _QUALITY_DIMS]
            return QualityOut(score=score, dimensions=values)
        except Exception:
            logger.warning("读取 dq_report 内容失败，质量雷达返回空", exc_info=True)
            return QualityOut(score=0, dimensions=[QualityDimension(name=n, value=0) for n in _QUALITY_DIMS])

    def _build_agent_health(self, node_stats: dict[str, dict[str, int]]) -> list[AgentHealthOut]:
        """遍历 AGENT_CAPABILITY（12 个）：health 由节点执行统计推导。"""
        out: list[AgentHealthOut] = []
        for agent_name, capability in AGENT_CAPABILITY.items():
            stats = node_stats.get(capability)
            display_name = AGENT_DISPLAY_NAMES.get(agent_name, agent_name)
            if not stats or stats["total"] == 0:
                out.append(AgentHealthOut(capability=capability, name=display_name, status="online", health_percent=100))
                continue
            # 健康度只看"实际执行成败"：BLOCKED（上游失败/人工阻断等结构性结果）不算该 Agent 的异常
            attempts = stats["success"] + stats["failed"]
            health = round(stats["success"] / attempts * 100) if attempts else 100
            status = "healthy" if health >= 90 else ("warning" if health >= 60 else "error")
            out.append(AgentHealthOut(capability=capability, name=display_name, status=status, health_percent=health))
        return out

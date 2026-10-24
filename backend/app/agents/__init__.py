"""Agent 包：注册表 + 工厂。

v3 实现 12 个 Agent（planner + metric_definition / metadata_profiling / sql_extract /
quality_check / feature_engineer / visualization / insight_analysis / report_writing /
review / memory_management / trace）；planner/orchestrator 角色由调度器承担。
"""
from __future__ import annotations

from app.agents.analyst_review import AnalystReviewAgent
from app.agents.base import AgentBase, AgentExecutionContext
from app.agents.data_profiler import DataProfilerAgent
from app.agents.data_quality import DataQualityAgent
from app.agents.feature_engineer import FeatureEngineerAgent
from app.agents.insight_analyst import InsightAnalystAgent
from app.agents.memory_keeper import MemoryKeeperAgent
from app.agents.metric_definition import MetricDefinitionAgent
from app.agents.report_scribe import ReportScribeAgent
from app.agents.sql_agent import SQLAgent
from app.agents.trace_navigator import TraceNavigatorAgent
from app.agents.visual_agent import VisualAgent

_AGENTS: tuple[type[AgentBase], ...] = (
    MetricDefinitionAgent,
    DataProfilerAgent,
    SQLAgent,
    DataQualityAgent,
    FeatureEngineerAgent,
    VisualAgent,
    InsightAnalystAgent,
    ReportScribeAgent,
    AnalystReviewAgent,
    MemoryKeeperAgent,
    TraceNavigatorAgent,
)

AGENT_REGISTRY: dict[str, type[AgentBase]] = {agent.CAPABILITY: agent for agent in _AGENTS}


def get_agent_for(capability: str) -> type[AgentBase] | None:
    """按能力标签取 Agent 类；未实现返回 None。"""
    return AGENT_REGISTRY.get(capability)


__all__ = ["AGENT_REGISTRY", "AgentBase", "AgentExecutionContext", "get_agent_for"]

"""Agent 用例：列表+实时状态 / 配置读取更新 / 质询。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents import AGENT_REGISTRY
from app.core.config import settings
from app.core.constants import (
    AGENT_CAPABILITY,
    AGENT_DEFAULT_MAX_RETRIES,
    AGENT_DEFAULT_TIMEOUT_SECONDS,
    AGENT_DISPLAY_NAMES,
)
from app.core.exceptions import NotFoundError
from app.infra.db import to_thread
from app.repositories.agent_config_repo import AgentConfigRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.task_repo import TaskRepo
from app.schemas.agent import AgentConfigOut, AgentKBOut, AgentOut, InquiryResponse

# Agent 职责摘要（知识库展示；与规格书·职责摘要一致）
ROLE_DESCRIPTIONS: dict[str, str] = {
    "planner": "全系统唯一调度中心：解析需求、召回记忆、拆解 DAG、任务认领仲裁、异常分支调度、终态判定、触发复盘。",
    "metric_definition": "全系统唯一指标口径来源：检索指标口径、术语对齐、冲突检测，输出指标规范。",
    "data_profiler": "探查数据源表结构、字段、类型、空值、主外键与表关联，输出数据资产结构文档。",
    "sql_agent": "依据指标口径与探查报告生成合规 SELECT，并对真实数据源执行取数产出数据集。",
    "data_quality": "检测缺失、重复、异常值、脏数据，输出质检报告并判定是否阻断。",
    "feature_engineer": "清洗与特征衍生，产出独立特征集，不改动原始数据源。",
    "visual_agent": "依据上游数据与特征工程建议选择并渲染图表（趋势/对比/构成/相关/归因等多图型），产出图表规格。",
    "insight_analyst": "把多数据集转成可复核的业务洞察：增长/集中度/贡献/异常/相关等分析算子，输出带证据与图表引用的关键发现。",
    "report_scribe": "整合全部上游资产，按意图动态组织章节，编撰图文表并茂的企业级分析报告（Markdown）。",
    "analyst_review": "对报告与指标口径做一致性、逻辑、幻觉检查，给出终审裁决。",
    "memory_keeper": "全系统唯一记忆管理者：11 套私有 + 1 套集体记忆向量库，任务后复盘提炼经验。",
    "trace_navigator": "溯源与问责：回放任务血缘、定位问题来源，支持审计追责。",
}

_ASSET_TYPE_LABEL: dict[str, str] = {
    "metric_spec": "指标口径",
    "profile": "探查报告",
    "sql": "SQL / 数据集",
    "dq_report": "质检报告",
    "feature_set": "特征集",
    "chart": "图表",
    "insight": "洞察分析",
    "report": "分析报告",
    "review": "评审意见",
    "retrospective": "复盘纪要",
    "trace": "溯源报告",
}


class AgentService:
    """Agent 服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_agents(self) -> list[AgentOut]:
        node_stats = await to_thread(TaskRepo.count_nodes_by_agent_type, self._session)
        out: list[AgentOut] = []
        for agent_id, capability in AGENT_CAPABILITY.items():
            stored = await to_thread(AgentConfigRepo.get_by_agent_id, self._session, agent_id)
            config = self._merged_config(capability, stored.config if stored is not None else None)
            health = self._health_for(node_stats.get(capability))
            out.append(
                AgentOut(
                    id=agent_id,
                    name=AGENT_DISPLAY_NAMES.get(agent_id, agent_id),
                    capability=capability,
                    status=health[0],
                    health_percent=health[1],
                    implemented=capability in AGENT_REGISTRY,
                    config=config,
                )
            )
        return out

    def knowledge(self) -> list[AgentKBOut]:
        """Agent 知识库：12 个 agent 的职责摘要 + 系统提示词 + 产出类型。"""
        out: list[AgentKBOut] = []
        for agent_id, capability in AGENT_CAPABILITY.items():
            cls = AGENT_REGISTRY.get(capability)
            if cls is not None:
                system_prompt = getattr(cls, "SYSTEM_PROMPT", "")
                asset_type = _ASSET_TYPE_LABEL.get(getattr(cls, "ASSET_TYPE", ""), "")
            else:
                # 调度角色（Planner）由 SchedulerService 承担，无独立 Agent 类
                system_prompt = "Planner 为全系统调度总指挥，由 SchedulerService 承担（非独立 Agent 实例）。职责：解析需求、拆解 DAG、认领仲裁、异常分支调度、终态判定。"
                asset_type = "DAG 编排"
            out.append(
                AgentKBOut(
                    id=agent_id,
                    name=AGENT_DISPLAY_NAMES.get(agent_id, agent_id),
                    capability=capability,
                    role=ROLE_DESCRIPTIONS.get(agent_id, ""),
                    system_prompt=system_prompt,
                    asset_type=asset_type,
                    implemented=capability in AGENT_REGISTRY,
                )
            )
        return out

    async def get_config(self, agent_id: str) -> AgentConfigOut:
        capability = self._ensure_agent(agent_id)
        stored = await to_thread(AgentConfigRepo.get_by_agent_id, self._session, agent_id)
        config = self._merged_config(capability, stored.config if stored is not None else None)
        return AgentConfigOut(agent_id=agent_id, capability=capability, config=config)

    async def update_config(self, agent_id: str, config: dict[str, Any], actor: str) -> AgentConfigOut:
        capability = self._ensure_agent(agent_id)
        row = await to_thread(
            AgentConfigRepo.upsert,
            self._session,
            agent_id=agent_id,
            capability=capability,
            config=config,
            updated_by=actor,
        )
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="agent.config.updated",
            detail={"agent_id": agent_id},
        )
        merged = self._merged_config(capability, row.config)
        return AgentConfigOut(agent_id=agent_id, capability=capability, config=merged)

    async def inquiry(self, agent_id: str, message: str, actor: str) -> InquiryResponse:
        """质询（TraceNavigator @问责）：落 agent_message 持久化队列（可回放/审计），不再空回执。"""
        self._ensure_agent(agent_id)
        name = AGENT_DISPLAY_NAMES.get(agent_id, agent_id)
        capability = AGENT_CAPABILITY[agent_id]
        from app.models.task_repo import AgentMessage

        def _persist() -> None:
            self._session.add(
                AgentMessage(
                    task_id=None,
                    from_role=actor,
                    to_role=capability,
                    subject="inquiry",
                    content=message,
                    status="pending",
                )
            )
            self._session.commit()

        await to_thread(_persist)
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="agent.inquiry",
            detail={"agent_id": agent_id, "capability": capability, "message": message},
        )
        return InquiryResponse(
            reply=f"已记录你对「{name}」的质询，进入问责队列（将随其下次执行/复盘回应）。消息：{message}"
        )

    def _ensure_agent(self, agent_id: str) -> str:
        if agent_id not in AGENT_CAPABILITY:
            raise NotFoundError(f"Agent 不存在: {agent_id}")
        return AGENT_CAPABILITY[agent_id]

    @staticmethod
    def _default_config(capability: str) -> dict[str, Any]:
        # 超时/重试与 base._load_config 运行时默认共用 constants 单一来源（显示=执行）
        return {
            "prompt_version": "v1",
            "model": settings.llm_model,  # 展示当前生效默认模型（运行时 None=用全局默认，等效）
            "enabled": True,
            "max_retries": AGENT_DEFAULT_MAX_RETRIES,
            "timeout_seconds": AGENT_DEFAULT_TIMEOUT_SECONDS,
        }

    @staticmethod
    def _merged_config(capability: str, override: dict[str, Any] | None) -> dict[str, Any]:
        defaults = AgentService._default_config(capability)
        if override:
            defaults.update(override)
        return defaults

    @staticmethod
    def _health_for(stats: dict[str, int] | None) -> tuple[str, int]:
        if not stats or stats["total"] == 0:
            return "online", 100
        # 健康度只看实际执行成败；BLOCKED（上游失败/人工阻断）不视为该 Agent 异常
        attempts = stats["success"] + stats["failed"]
        health = round(stats["success"] / attempts * 100) if attempts else 100
        status = "healthy" if health >= 90 else ("warning" if health >= 60 else "error")
        return status, health

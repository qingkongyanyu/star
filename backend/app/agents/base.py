"""Agent 基类与执行上下文。

主循环（docs/02-后端/02 §三）：claim → RUNNING → pull_inputs(上游) → execute(LLM)
→ publish_artifact → SUCCESS；异常重试一次，重试尽 FAILED。

v1：由调度器直接驱动 Agent 执行单个节点（DB CAS 认领，非 Redis pool）；
LLM 直出 JSON（不做函数调用循环）；经 ToolRegistry 调 publish/pull。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AGENT_CAPABILITY,
    AGENT_DEFAULT_MAX_RETRIES,
    AGENT_DEFAULT_TIMEOUT_SECONDS,
    ExperienceType,
    NodeStatus,
)
from app.infra.db import get_session_factory, to_thread
from app.infra.llm import get_llm
from app.models.task_repo import Task
from app.repositories.agent_config_repo import AgentConfigRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.data_source_repo import DataSourceRepo
from app.repositories.task_repo import TaskRepo
from app.repositories.user_repo import UserRepo
from app.services.artifact_service import ArtifactService
from app.services.intent import classify_intent, intent_summary
from app.tools import ToolDeps
from app.tools.artifact_tools import register_artifact_tools
from app.tools.memory_tools import register_memory_tools
from app.tools.registry import ToolRegistry
from app.tools.task_tools import register_task_tools

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = settings.llm_input_max_chars  # 上游内容拼入提示词的截断上限（可配置）

# capability → agent_id 反查（per-agent 配置按 agent_id 存表）
_AGENT_ID_BY_CAPABILITY: dict[str, str] = {cap: aid for aid, cap in AGENT_CAPABILITY.items()}


@dataclass
class AgentExecutionContext:
    """工具与子类共享的执行上下文（docs/02-后端/06 §七）。"""

    node_id: str
    task_id: str
    requester: str
    role: str
    capability: str
    token: str
    requirement: str
    instruction: str = ""  # Planner 拆解给该节点的定向指令（确定性 agent 作约束、LLM agent 拼入提示词）
    plan: dict[str, Any] | None = None  # Planner 结构化分析计划（意图槽位/指标/任务/过滤）
    inputs: dict[str, str] = field(default_factory=dict)  # asset_ref -> 内容文本
    data_source: dict[str, Any] | None = None  # 数据源接入 {id, type, config}（可选）
    model: str | None = None  # per-agent 配置覆盖的模型（None → 全局默认）
    timeout_seconds: int | None = None  # per-agent 执行超时（>0 施加 asyncio.wait_for）
    max_retries: int = 2  # per-agent 执行总尝试次数


def _extract_json(raw: str) -> str | None:
    """抽取首个平衡的 JSON 对象；无则返回 None（MockLLM 输出会落空）。"""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(raw)):
        ch = raw[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


class AgentBase:
    """Agent 基类：认领/执行/发布/上报 主循环。"""

    CAPABILITY: str = ""
    ASSET_TYPE: str = ""
    SYSTEM_PROMPT: str = ""

    async def run_node(self, node_id: str, task: Task) -> str:
        """执行单个节点，返回产出 asset_id；失败返回空串。

        执行后提交经验：成功 → success_pattern，失败 → pitfall（契约 submit_experience_summary）。
        """
        task_session = get_session_factory("task_repo")()
        artifact_session = get_session_factory("artifact_repo")()
        registry = self._build_registry()
        deps = ToolDeps(task_session=task_session, artifact_session=artifact_session)
        ctx: AgentExecutionContext | None = None
        try:
            # per-agent 配置生效（P0-4）：enabled=false 直接跳过并置 BLOCKED（不认领不执行）
            cfg = await self._load_config(task_session)
            if not cfg["enabled"]:
                await to_thread(TaskRepo.update_node_status, task_session, node_id, NodeStatus.BLOCKED.value)
                await to_thread(
                    AuditRepo.append,
                    task_session,
                    actor="scheduler",
                    action="agent.disabled",
                    node_id=node_id,
                    detail={"agent_type": self.CAPABILITY, "reason": "该 Agent 已被管理员停用（config.enabled=false）"},
                )
                logger.info("Agent %s 已停用，节点 %s 置 BLOCKED", self.CAPABILITY, node_id)
                return ""

            token = uuid.uuid4().hex
            claimed = await to_thread(TaskRepo.claim_node, task_session, node_id, token)
            if not claimed:
                return ""
            await to_thread(TaskRepo.update_node_status, task_session, node_id, NodeStatus.RUNNING.value)

            node = await to_thread(TaskRepo.get_node, task_session, node_id)
            if node is None:
                return ""

            # 真实角色：按发起人查用户表（ACL 校验基于真实角色，不再写死 admin）
            role = "readonly"
            if task.requester:
                try:
                    user = await UserRepo.get_by_username(task_session, task.requester)
                    role = user.role if user is not None else "readonly"
                except Exception:  # 查询失败回退只读
                    logger.warning("查询用户 %s 角色失败，按只读处理", task.requester, exc_info=True)
                    role = "readonly"

            ctx = AgentExecutionContext(
                node_id=node_id,
                task_id=task.task_id,
                requester=task.requester,
                role=role,
                capability=self.CAPABILITY,
                token=token,
                requirement=task.requirement or "",
                instruction=node.instruction or "",
                plan=task.plan_json if isinstance(task.plan_json, dict) else None,
                model=cfg["model"],
                timeout_seconds=cfg["timeout_seconds"],
                max_retries=cfg["max_retries"],
            )
            ctx.inputs = await self._pull_inputs(node.input_asset_ids or [])

            # 数据源接入：任务关联的数据源配置注入执行上下文（探查/取数用）
            if task.data_source_id:
                ds = await to_thread(DataSourceRepo.get_by_id, task_session, task.data_source_id)
                if ds is not None:
                    ctx.data_source = {"id": ds.id, "type": ds.type, "config": ds.config}

            result = await self._execute_with_retry(ctx, deps)
            asset_id = await self._publish(ctx, result, deps, registry)
            if asset_id:
                await to_thread(TaskRepo.set_node_success, task_session, node_id, asset_id)
                await self._submit_experience(
                    ctx, registry, deps, ExperienceType.SUCCESS_PATTERN.value,
                    str(result.get("title") or f"{self.CAPABILITY} 完成"),
                )
            else:
                await to_thread(TaskRepo.update_node_status, task_session, node_id, NodeStatus.FAILED.value)
                await self._submit_experience(
                    ctx, registry, deps, ExperienceType.PITFALL.value, "节点执行失败（无产出资产）"
                )
            return asset_id
        except Exception:
            logger.exception("Agent %s 执行节点 %s 失败", self.CAPABILITY, node_id)
            await to_thread(TaskRepo.update_node_status, task_session, node_id, NodeStatus.FAILED.value)
            if ctx is not None:
                await self._submit_experience(
                    ctx, registry, deps, ExperienceType.PITFALL.value, "节点执行异常"
                )
            return ""
        finally:
            task_session.close()
            artifact_session.close()

    async def _submit_experience(
        self,
        ctx: AgentExecutionContext,
        registry: ToolRegistry,
        deps: ToolDeps,
        experience_type: str,
        content: str,
    ) -> None:
        """经 submit_experience_summary 提交经验到 memory_entry（失败仅告警）。"""
        try:
            await registry.call(
                "submit_experience_summary",
                ctx,
                {
                    "task_id": ctx.task_id,
                    "agent_id": ctx.capability,
                    "experience_type": experience_type,
                    "content": content,
                    "tags": [ctx.capability],
                },
                deps,
            )
        except Exception:
            logger.warning("Agent %s 提交经验失败", self.CAPABILITY, exc_info=True)

    # ---- per-agent 配置（P0-4）----

    async def _load_config(self, session: Session) -> dict[str, Any]:
        """读取 per-agent 配置（按 agent_id 存表）；无配置/读失败回退默认：启用、全局模型、120s 超时、2 次尝试。

        默认 timeout 用共享常量（与 UI 面板显示一致）：LLM 节点（review/trace/planner 等）必须受
        asyncio.wait_for 约束，避免外部模型挂起拖死调度 worker。
        """
        agent_id = _AGENT_ID_BY_CAPABILITY.get(self.CAPABILITY, self.CAPABILITY)
        cfg: dict[str, Any] = {
            "enabled": True,
            "model": None,
            "timeout_seconds": AGENT_DEFAULT_TIMEOUT_SECONDS,
            "max_retries": AGENT_DEFAULT_MAX_RETRIES,
        }
        try:
            row = await to_thread(AgentConfigRepo.get_by_agent_id, session, agent_id)
            stored = dict(row.config or {}) if row is not None else {}
        except Exception:
            logger.warning("Agent %s 读取配置失败，按默认处理", self.CAPABILITY, exc_info=True)
            stored = {}
        if stored:
            if "enabled" in stored:
                cfg["enabled"] = bool(stored.get("enabled"))
            if stored.get("model"):
                cfg["model"] = str(stored["model"])
            if stored.get("timeout_seconds") is not None:
                try:
                    timeout = int(stored["timeout_seconds"])
                    # 非法/非正超时回退默认值（0/负数会关闭 wait_for 兜底，禁止）
                    cfg["timeout_seconds"] = timeout if timeout > 0 else AGENT_DEFAULT_TIMEOUT_SECONDS
                except (TypeError, ValueError):
                    cfg["timeout_seconds"] = AGENT_DEFAULT_TIMEOUT_SECONDS
            if stored.get("max_retries") is not None:
                try:
                    cfg["max_retries"] = int(stored["max_retries"])
                except (TypeError, ValueError):
                    cfg["max_retries"] = 2
        return cfg

    # ---- 子类接口 ----

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """调 LLM 生成结构化结果；子类覆写 build_prompt/parse_result/fallback。"""
        prompt = self.build_prompt(ctx)
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        llm = get_llm(model=ctx.model or None)  # per-agent model 覆盖（P0-4）
        raw = await llm.complete(messages)
        return self.parse_result(raw, ctx)

    def build_prompt(self, ctx: AgentExecutionContext) -> str:
        """构建 user 提示词：需求 + 已识别意图 + Planner 定向指令 + 相关上游内容。"""
        intent = classify_intent(ctx.requirement)
        parts = [f"任务需求：{ctx.requirement}"]
        parts.append(f"已识别分析意图：{intent_summary(intent)}")
        if ctx.plan:
            parts.append(f"分析计划：{json.dumps({k: ctx.plan.get(k) for k in ('metrics', 'tasks', 'time_range', 'notes')}, ensure_ascii=False)[:4000]}")
        if ctx.instruction:
            parts.append(f"Planner 定向指令：{ctx.instruction}")
        if ctx.inputs:
            parts.append("上游资产内容：")
            parts.extend(f"- {ref}: {content[: _MAX_INPUT_CHARS]}" for ref, content in ctx.inputs.items())
        return "\n".join(parts)

    def parse_result(self, raw: str, ctx: AgentExecutionContext) -> dict[str, Any]:
        """解析 LLM JSON；解析失败回退确定性默认（MockLLM 无 key 时生效）。"""
        text = _extract_json(raw)
        if text is not None:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return self.fallback(ctx)

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        """确定性默认结果（子类实现）。"""
        return {"title": f"{self.CAPABILITY} 产出"}

    # ---- 内部实现 ----

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        register_task_tools(registry)
        register_memory_tools(registry)
        register_artifact_tools(registry)
        return registry

    async def _pull_inputs(self, refs: list[str]) -> dict[str, str]:
        """并发拉取上游资产内容（每个引用独立会话，受配置上限约束）。

        上游资产可能位于 MinIO 或本地降级目录，逐个串行会放大节点准备耗时；
        这里用 gather + Semaphore 并发拉取，单个失败仅置空不影响其它输入。
        """
        if not refs:
            return {}
        semaphore = asyncio.Semaphore(max(1, settings.agent_input_fetch_concurrency))

        async def fetch(ref: str) -> tuple[str, str]:
            asset_id, _, ver = ref.partition("@")
            version = int(ver) if ver else None
            async with semaphore:
                # 同步 SQLAlchemy 会话不可跨线程共享，每个引用独立会话
                session = get_session_factory("artifact_repo")()
                try:
                    return ref, await ArtifactService(session).fetch_content(asset_id, version)
                except Exception:  # noqa: BLE001  上游拉取降级：失败置空
                    logger.warning("拉取上游资产 %s 失败，置空", ref)
                    return ref, ""
                finally:
                    session.close()

        pairs = await asyncio.gather(*(fetch(ref) for ref in refs))
        return dict(pairs)

    async def _execute_with_retry(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """执行 + 重试 + 确定性兜底；尝试次数与超时取自 per-agent 配置（P0-4）。

        超时由 asyncio.wait_for 施加（timeout_seconds>0 时），LLM/外部调用阻塞不拖死事件循环。
        """
        max_retries = max(1, ctx.max_retries)
        for attempt in range(1, max_retries + 1):
            try:
                coro = self.execute(ctx, deps)
                if ctx.timeout_seconds and ctx.timeout_seconds > 0:
                    coro = asyncio.wait_for(coro, timeout=ctx.timeout_seconds)
                return await coro
            except Exception:
                if attempt < max_retries:
                    logger.warning("Agent %s 第 %d 次执行失败，重试", self.CAPABILITY, attempt)
                else:
                    logger.warning("Agent %s 执行 %d 次仍失败，回退确定性默认", self.CAPABILITY, attempt, exc_info=True)
                    return self.fallback(ctx)
        return self.fallback(ctx)

    async def _publish(self, ctx: AgentExecutionContext, result: dict[str, Any], deps: ToolDeps, registry: ToolRegistry) -> str:
        """经 publish_artifact 工具发布资产，返回 asset_id。"""
        title = str(result.get("title") or f"{self.CAPABILITY} 产出")
        content = json.dumps(result, ensure_ascii=False)
        out = await registry.call(
            "publish_artifact",
            ctx,
            {
                "node_id": ctx.node_id,
                "type": self.ASSET_TYPE,
                "title": title,
                "content": content,
                "dep_asset_ids": list(ctx.inputs.keys()),
            },
            deps,
        )
        return str(out.get("asset_id") or "")

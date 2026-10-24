"""MemoryKeeper：记忆管家 Agent → retrospective。

任务终态（SUCCESS）后由调度器触发 `run_retrospective`（非 DAG 节点）：采集各
SUCCESS 节点产出 → Qwen 复盘（{summary, experience_list}）→ 发布 retrospective
资产 + 写 memory_entry（collective + 各 Agent private）。写路径唯一入口（契约强约束）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.agents.base import AgentBase, _extract_json
from app.core.constants import ExperienceType, NodeStatus
from app.infra.db import get_session_factory, to_thread
from app.infra.llm import get_llm
from app.models.task_repo import Task
from app.repositories.memory_repo import MemoryRepo
from app.repositories.task_repo import TaskRepo
from app.services.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

_SUMMARY_CHARS = 300  # 每个节点产出拼入提示词的截断上限
_RETRO_TIMEOUT = 60  # 复盘 LLM 超时（run_retrospective 不经 run_node，需显式 wait_for 防挂起）

_SYSTEM_PROMPT = """你是【MemoryKeeper 记忆管家 Agent】，负责任务终态复盘。
采集各 Agent 产出，输出结构化复盘纪要。请严格输出 JSON，格式：
{"summary": string, "experience_list": [{"agent_id": string, "experience_type": string, "content": string}]}。"""


class MemoryKeeperAgent(AgentBase):
    CAPABILITY = "memory_management"
    ASSET_TYPE = "retrospective"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def run_retrospective(self, task_id: str) -> str | None:
        """任务终态复盘：采集 → 总结 → 发布复盘资产 + 写记忆。返回复盘资产 id；异常不外抛。"""
        task_session = get_session_factory("task_repo")()
        artifact_session = get_session_factory("artifact_repo")()
        try:
            task = await to_thread(TaskRepo.get_by_task_id, task_session, task_id)
            if task is None:
                return None
            nodes = await to_thread(TaskRepo.get_nodes, task_session, task_id)
            outputs = [n for n in nodes if n.status == NodeStatus.SUCCESS.value and n.output_asset_id]
            if not outputs:
                return None

            summaries: list[str] = []
            dep_ids: list[str] = []
            for node in outputs:
                output_id = node.output_asset_id
                if output_id is None:
                    continue
                dep_ids.append(output_id)
                try:
                    content = await ArtifactService(artifact_session).fetch_content(output_id)
                    summaries.append(f"[{node.agent_type}] {content[:_SUMMARY_CHARS]}")
                except Exception:  # noqa: BLE001  单节点内容读取失败不阻断复盘
                    logger.warning("复盘读取节点 %s 产出失败，跳过", node.node_id)
                    summaries.append(f"[{node.agent_type}] （产出内容读取失败）")

            result = await self._compose_retrospective(task, summaries)
            version = await ArtifactService(artifact_session).publish(
                asset_type="retrospective",
                title=f"任务 {task_id} 复盘纪要",
                content=json.dumps(result, ensure_ascii=False),
                dep_ids=dep_ids,
                producer_agent=self.CAPABILITY,
            )

            # 只写 collective 记忆（各 Agent 私有经验由执行期 submit_experience_summary 提交）
            await to_thread(
                MemoryRepo.create,
                task_session,
                agent_id=self.CAPABILITY,
                scope="collective",
                source_task=task_id,
                task_type=task.task_type,
                content=result.get("summary", ""),
                experience_type=ExperienceType.SUCCESS_PATTERN.value,
                tags=["复盘"],
            )

            logger.info("任务 %s 复盘完成，复盘资产 %s，写 collective 记忆", task_id, version.asset_id)
            return version.asset_id
        except Exception:
            logger.exception("任务 %s 复盘失败（不影响任务状态）", task_id)
            return None
        finally:
            task_session.close()
            artifact_session.close()

    async def _compose_retrospective(self, task: Task, summaries: list[str]) -> dict[str, Any]:
        prompt = (
            f"任务 {task.task_id}（需求：{task.requirement or '-'}）执行完成，各节点产出：\n"
            + "\n".join(summaries)
            + "\n请输出复盘纪要（含整体总结与经验清单）。"
        )
        try:
            raw = await asyncio.wait_for(
                get_llm().complete(
                    [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
                ),
                timeout=_RETRO_TIMEOUT,
            )
            text = _extract_json(raw)
            if text is not None:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            logger.warning("复盘 LLM 生成失败，回退确定性文本", exc_info=True)
        return {
            "summary": f"任务 {task.task_id} 按 DAG 全链路完成，产出 {len(summaries)} 个资产",
            "experience_list": [
                {"agent_id": self.CAPABILITY, "experience_type": ExperienceType.SUCCESS_PATTERN.value, "content": "全链路协作完成"}
            ],
        }

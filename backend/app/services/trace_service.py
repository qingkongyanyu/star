"""溯源归因用例：血缘 + 归因报告 / 责任人清单（TraceNavigator 能力；Agent 本体待接入）。

归因（v1）：沿血缘从问题资产向上游逆推，首个异常节点（产出节点 status FAILED/BLOCKED
或审计含 dq.blocked/review.rejected）= 根因；无异常 → 直接产出节点。责任人：第一=根因
节点 Agent，协同=血缘链其他产出节点 Agent。报告经 Qwen 生成，失败回退确定性文本。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.core.constants import AGENT_CAPABILITY, AGENT_DISPLAY_NAMES, NodeStatus
from app.core.exceptions import NotFoundError
from app.infra.db import to_thread
from app.infra.llm import get_llm
from app.models.task_repo import DagNode
from app.repositories.artifact_repo import ArtifactRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.task_repo import TaskRepo
from app.schemas.asset import LineageOut
from app.schemas.trace import ResponsibilityListOut, ResponsibilityOut, RootCauseOut, TraceReportOut
from app.services.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

_ANOMALY_ACTIONS = {"dq.blocked", "review.rejected"}
_CAPABILITY_TO_NAME = {AGENT_CAPABILITY[key]: name for key, name in AGENT_DISPLAY_NAMES.items()}
_TRACE_LLM_TIMEOUT = 30  # 溯源文案 LLM 超时（防止外部模型挂起拖死请求）


class TraceService:
    """溯源归因服务。"""

    def __init__(self, task_session: Session, artifact_session: Session) -> None:
        self._task = task_session
        self._artifact = artifact_session

    async def trace(self, asset_id: str, depth: int) -> TraceReportOut:
        lineage = await self._get_lineage(asset_id, depth)
        root_cause, responsibilities = await self._attribute(asset_id, lineage)
        report = await self._compose_report(asset_id, lineage, root_cause, responsibilities)
        return TraceReportOut(
            asset_id=asset_id,
            version=lineage.version,
            lineage=lineage,
            root_cause=root_cause,
            responsibility=responsibilities,
            report=report,
        )

    async def responsibility(self, asset_id: str) -> ResponsibilityListOut:
        lineage = await self._get_lineage(asset_id, 10)
        _, responsibilities = await self._attribute(asset_id, lineage)
        return ResponsibilityListOut(asset_id=asset_id, items=responsibilities)

    async def grounded(self, asset_id: str, depth: int = 12) -> tuple[LineageOut, RootCauseOut | None, list[ResponsibilityOut]]:
        """确定性血缘 + 归因 + 责任人（不用 LLM，供 DAG 溯源节点产出可审计事实）。

        仅返回结构数据；文案由调用方渲染。真实 Agent 责任归属，绝不编造表/人。
        """
        lineage = await self._get_lineage(asset_id, depth)
        root_cause, responsibilities = await self._attribute(asset_id, lineage)
        return lineage, root_cause, responsibilities

    # ---- 内部 ----

    async def _get_lineage(self, asset_id: str, depth: int) -> LineageOut:
        asset = await to_thread(ArtifactRepo.get_by_asset_id, self._artifact, asset_id)
        if asset is None:
            raise NotFoundError(f"资产不存在: {asset_id}")
        return await ArtifactService(self._artifact).lineage(asset_id, depth)

    async def _attribute(self, asset_id: str, lineage: LineageOut) -> tuple[RootCauseOut | None, list[ResponsibilityOut]]:
        """归因 + 责任人清单（双库在 Python 侧 join）。"""
        producer_by_asset: dict[str, DagNode | None] = {}
        for node in lineage.nodes:
            producer_by_asset[node.asset_id] = await to_thread(
                TaskRepo.get_node_by_output_asset_id, self._task, node.asset_id
            )

        direct = producer_by_asset.get(asset_id)

        # 沿血缘从问题资产向上游逆推首个异常节点 = 根因
        root_node: DagNode | None = None
        root_asset_id = asset_id
        for node in lineage.nodes:
            producer = producer_by_asset.get(node.asset_id)
            if producer is not None and await self._node_has_anomaly(producer.node_id):
                root_node = producer
                root_asset_id = node.asset_id
                break

        primary = root_node if root_node is not None else direct
        responsibilities: list[ResponsibilityOut] = []
        root_cause: RootCauseOut | None = None
        if primary is not None:
            is_anomaly = root_node is not None
            reason = "根因节点（输出首次偏离预期）" if is_anomaly else "直接产出节点（无异常信号，输出正常）"
            responsibilities.append(
                ResponsibilityOut(
                    role="primary",
                    node_id=primary.node_id,
                    agent_type=primary.agent_type,
                    agent_name=_CAPABILITY_TO_NAME.get(primary.agent_type, primary.agent_type),
                    asset_id=root_asset_id,
                    reason=reason,
                )
            )
            evidence = [f"{n.asset_id}@{n.version}" for n in lineage.nodes]
            root_cause = RootCauseOut(
                node_id=primary.node_id,
                agent_type=primary.agent_type,
                agent_name=_CAPABILITY_TO_NAME.get(primary.agent_type, primary.agent_type),
                asset_id=root_asset_id,
                reason=reason,
                evidence=evidence,
            )

        # 协同责任人：血缘链上其他有产出节点的 Agent
        for node in lineage.nodes:
            if node.asset_id == root_asset_id:
                continue
            producer = producer_by_asset.get(node.asset_id)
            if producer is not None:
                responsibilities.append(
                    ResponsibilityOut(
                        role="collaborative",
                        node_id=producer.node_id,
                        agent_type=producer.agent_type,
                        agent_name=_CAPABILITY_TO_NAME.get(producer.agent_type, producer.agent_type),
                        asset_id=node.asset_id,
                        reason="执行链上游节点",
                    )
                )
        return root_cause, responsibilities

    async def _node_has_anomaly(self, node_id: str) -> bool:
        node = await to_thread(TaskRepo.get_node, self._task, node_id)
        if node is None:
            return False
        if node.status in {NodeStatus.FAILED.value, NodeStatus.BLOCKED.value}:
            return True
        logs = await to_thread(AuditRepo.list_by_node, self._task, node_id)
        return any(log.action in _ANOMALY_ACTIONS for log in logs)

    async def _compose_report(
        self,
        asset_id: str,
        lineage: LineageOut,
        root_cause: RootCauseOut | None,
        responsibilities: list[ResponsibilityOut],
    ) -> str:
        """给 /trace/{asset_id} API 用：LLM 依据确定性事实写文案；失败回退 render_report_md（事实版）。"""
        chain = "; ".join(f"{n.asset_id}@{n.version}（{n.type}）" for n in lineage.nodes)
        rc = f"{root_cause.agent_name}（{root_cause.node_id}）" if root_cause is not None else "未定位"
        resp = "; ".join(f"{r.role}={r.agent_name}" for r in responsibilities)
        prompt = (
            f"问题资产：{asset_id}（血缘 {len(lineage.nodes)} 节点：{chain}）\n"
            f"根因节点：{rc}\n责任人：{resp}\n"
            "请输出一份简洁的中文溯源归因报告（Markdown），含血缘链路、根因定位、责任人清单、证据链。"
            "约束：只能引用上面给出的事实（资产 ID/版本/类型、根因节点、责任 Agent），不得编造上游没有的表名/字段/人名。"
        )
        try:
            raw = await asyncio.wait_for(
                get_llm().complete(
                    [
                        {
                            "role": "system",
                            "content": "你是【TraceNavigator 溯源导航与问题定位 Agent】。只做溯源与定位，不代工修复；归因结论必须附完整证据链（资产 ID + 版本 + 依赖路径）；只允许引用给定事实，禁止编造表/字段/人名。",
                        },
                        {"role": "user", "content": prompt},
                    ]
                ),
                timeout=_TRACE_LLM_TIMEOUT,
            )
            if raw and "MockLLM" not in raw:
                return raw
        except Exception:
            logger.warning("Trace 报告 LLM 生成失败，回退确定性文本", exc_info=True)
        return TraceService.render_report_md(asset_id, lineage, root_cause, responsibilities)

    @staticmethod
    def render_report_md(
        asset_id: str,
        lineage: LineageOut,
        root_cause: RootCauseOut | None,
        responsibilities: list[ResponsibilityOut],
    ) -> str:
        """确定性溯源报告（无 LLM）：只陈述真实血缘/根因/责任 Agent，证据链=资产引用。"""
        lines = ["# 溯源归因报告", "", f"问题资产：{asset_id}", "", "## 血缘链路（证据链）"]
        lines.extend(f"- {n.asset_id}@{n.version}（{n.type}）" for n in lineage.nodes)
        if root_cause is not None:
            lines.extend(["", "## 根因定位"])
            lines.append(
                f"- {root_cause.agent_name}（{root_cause.node_id}）：{root_cause.reason}；证据："
                + "; ".join(root_cause.evidence or [])
            )
        lines.extend(["", "## 责任人（真实 Agent，按血缘归属）"])
        for r in responsibilities:
            lines.append(f"- {r.role}: {r.agent_name}（{r.agent_type} · {r.node_id}）— {r.reason}；产出 {r.asset_id}")
        lines.append("")
        lines.append("*溯源报告由系统对真实血缘/审计数据确定性生成。*")
        return "\n".join(lines)

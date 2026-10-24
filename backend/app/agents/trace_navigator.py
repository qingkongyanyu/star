"""TraceNavigator：溯源导航 Agent → trace（按需 DAG 节点，需求含溯源意图时追加）。

v2.2 修复（诚实性）：不再用裸 LLM 对着上游文本“自由发挥”——之前会编造源表名/责任人
（如把 pg_region_sales 说成 sales_transaction、@liwei 等）。现复用 TraceService 的
确定性血缘/归因（真实资产引用 + 真实 Agent 责任归属），产出纯事实的溯源报告。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.services.trace_service import TraceService

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "你是【TraceNavigator 溯源导航与问题定位 Agent】。只做溯源与定位，不代工修复。"


class TraceNavigatorAgent(AgentBase):
    CAPABILITY = "trace"
    ASSET_TYPE = "trace"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: Any) -> dict[str, Any]:
        """确定性溯源：沿真实血缘定位根因/责任 Agent，绝不用 LLM 编造表名/人名/链路。"""
        refs = [ref for ref in ctx.inputs if "@" in ref]
        target = self._pick_report_asset(ctx, refs)
        if not target:
            return self.fallback(ctx)
        try:
            svc = TraceService(deps.task_session, deps.artifact_session)
            lineage, root_cause, responsibilities = await svc.grounded(target, depth=12)
            report = TraceService.render_report_md(target, lineage, root_cause, responsibilities)
            payload: dict[str, Any] = {
                "report": report,
                "asset_manifest": list(ctx.inputs.keys()),
                "lineage": [f"{n.asset_id}@{n.version}" for n in lineage.nodes],
                "title": "溯源报告",
            }
            if root_cause is not None:
                payload["root_cause"] = {
                    "node_id": root_cause.node_id,
                    "agent_type": root_cause.agent_type,
                    "agent_name": root_cause.agent_name,
                    "reason": root_cause.reason,
                    "evidence": root_cause.evidence or [],
                }
            payload["responsibility"] = [
                {"role": r.role, "agent_name": r.agent_name, "node_id": r.node_id, "reason": r.reason}
                for r in responsibilities
            ]
            return payload
        except Exception:
            logger.exception("溯源节点失败，走确定性兜底")
            return self.fallback(ctx)

    @staticmethod
    def _pick_report_asset(ctx: AgentExecutionContext, refs: list[str]) -> str | None:
        """优先选“报告/终审”产出资产作为溯源根（其血缘覆盖全链）；其次任一 '@' 资产。"""
        for ref in refs:
            content = ctx.inputs.get(ref) or ""
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("report"), str):
                return ref.split("@", 1)[0]
        return refs[0].split("@", 1)[0] if refs else None

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        manifest = list(ctx.inputs.keys())
        return {
            "report": (
                f"溯源未定位到可核对的产出资产（上游 {len(manifest)} 项），未产出归因结论。"
                + ("证据链：" + "; ".join(manifest[:5]) if manifest else "无上游资产")
            ),
            "asset_manifest": manifest,
            "title": "溯源报告",
        }

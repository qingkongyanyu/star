"""InsightAnalyst（天机_洞察分析）→ insight。

职责（第 12 个 Agent，v3 新增分析层）：
- 消费可视化 Agent 的 datasets/charts + 指标口径 + 质检结论 + 分析计划；
- 用确定性分析算子（趋势/对比/构成/分布/相关/异常/归因）生成**带证据的关键发现**；
- 每条发现绑定数据集与图表 ID，供报告引用、终审复核、工作台展示；
- 可选调用 LLM 把结构化发现写成一段业务解读（数字仍以确定性发现为准）。

它不取数、不出图、不写最终报告——只负责"从数据到洞察"这一层，避免报告编撰
既算数又编故事、所有任务话术雷同。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.infra.llm import MockLLM, get_llm
from app.services.analysis_ops import build_findings
from app.services.chart_selector import chart_map_by_dataset

logger = logging.getLogger(__name__)

_NARRATIVE_TIMEOUT = 25

_SYSTEM_PROMPT = """你是【InsightAnalyst 洞察分析师】。你只基于给定的「结构化发现」写一段业务解读：
- 只能引用给定发现里的数字与结论，不得编造新数字、表名、人名；
- 指标名称必须使用「结构化发现」中的实际口径；若用户需求点名的指标不在发现里（如数据源缺该字段），
  不得把它当作已分析口径，应使用实际指标名或明确说明「该指标数据不可用」；
- 3–4 句话，先结论后依据，指出最值得关注的机会/风险与下一步建议；
- 不要复述所有发现，只抓最重要的 2–3 条。
只输出 JSON：{"narrative": string}。"""


class InsightAnalystAgent(AgentBase):
    """从多数据集提炼可复核的业务发现。"""

    CAPABILITY = "insight_analysis"
    ASSET_TYPE = "insight"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: Any) -> dict[str, Any]:
        """确定性算子产出 findings；LLM 仅润色一段 narrative。"""
        datasets = self._extract_datasets(ctx)
        charts = self._extract_charts(ctx)
        chart_map = chart_map_by_dataset(charts)
        findings = build_findings(datasets, chart_map)
        if not findings:
            findings = self._fallback_findings(datasets, chart_map)

        dq = self._extract_dq(ctx)
        quality_finding = self._quality_finding(dq)
        if quality_finding is not None:
            findings.append(quality_finding)

        summary = self._deterministic_summary(ctx, datasets, findings)
        narrative = await self._compose_narrative(ctx, findings)
        headline = [item["statement"] for item in self._rank_findings(findings)[:3]]
        return {
            "summary": summary,
            "narrative": narrative,
            "findings": findings,
            "headline": headline,
            "dataset_count": len(datasets),
            "chart_count": len(charts),
            "title": "关键洞察",
        }

    # ---- 输入解析 ----

    @staticmethod
    def _extract_datasets(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            datasets = payload.get("datasets")
            if isinstance(datasets, list) and datasets:
                return [item for item in datasets if isinstance(item, dict)]
            dataset = payload.get("dataset")
            if isinstance(dataset, dict) and isinstance(dataset.get("rows"), list):
                return [dataset]
        return []

    @staticmethod
    def _extract_charts(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            charts = payload.get("charts") if isinstance(payload, dict) else None
            if isinstance(charts, list):
                return [item for item in charts if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_dq(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            for candidate in (payload.get("dq"), payload):
                if isinstance(candidate, dict) and isinstance(candidate.get("score"), (int, float)) and "issues" in candidate:
                    return candidate
        return None

    # ---- 发现与摘要 ----

    @staticmethod
    def _rank_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weight = {"critical": 3, "warning": 2, "info": 1}
        return sorted(findings, key=lambda item: weight.get(str(item.get("severity")), 0), reverse=True)

    def _quality_finding(self, dq: dict[str, Any] | None) -> dict[str, Any] | None:
        if dq is None:
            return None
        issues = dq.get("issues") or []
        blocked = bool(dq.get("blocked"))
        severity = "critical" if blocked else ("warning" if issues else "info")
        detail = "；".join(str(issue.get("evidence")) for issue in issues[:3] if isinstance(issue, dict)) or "未发现明显问题"
        return {
            "id": "fquality",
            "dataset_id": None,
            "kind": "quality",
            "title": "数据质量结论",
            "statement": f"数据质量综合评分 {dq.get('score')}（{dq.get('level')}），{detail}。",
            "evidence": [f"score={dq.get('score')}", f"level={dq.get('level')}"],
            "severity": severity,
            "chart_ids": [],
            "metric": None,
            "unit": "",
            "value": dq.get("score"),
        }

    @staticmethod
    def _deterministic_summary(
        ctx: AgentExecutionContext, datasets: list[dict[str, Any]], findings: list[dict[str, Any]]
    ) -> str:
        plan = ctx.plan or {}
        metrics = plan.get("metrics") or []
        metric_names = "、".join(str(metric.get("name")) for metric in metrics[:3] if metric.get("name")) or "指标"
        kinds = "、".join(sorted({str(dataset.get("kind") or "") for dataset in datasets if dataset.get("kind")}))
        headline = "；".join(str(item.get("statement")) for item in findings[:2])
        return (
            f"围绕「{ctx.requirement}」，以{metric_names}为主线完成 {len(datasets)} 组分析（{kinds or '描述性'}），"
            f"产出 {len(findings)} 条可复核发现。{headline}"
        )

    async def _compose_narrative(self, ctx: AgentExecutionContext, findings: list[dict[str, Any]]) -> str:
        """LLM 业务解读（可选）；MockLLM/失败/超时 → 空串（报告回退到确定性发现）。"""
        if not findings:
            return ""
        llm = get_llm(model=ctx.model or None)
        if isinstance(llm, MockLLM):
            return ""
        material = "\n".join(
            f"- [{item.get('severity')}] {item.get('title')}：{item.get('statement')}" for item in findings[:8]
        )
        plan = ctx.plan or {}
        actual_metrics = "、".join(
            str(item.get("name") or "") for item in (plan.get("metrics") or []) if item.get("name")
        ) or "见结构化发现"
        unavailable_metrics = "、".join(
            str(item.get("name") or "") for item in (plan.get("unavailable") or []) if item.get("name")
        ) or "无"
        prompt = (
            f"用户需求：{ctx.requirement}\n"
            f"本次实际指标口径：{actual_metrics}\n"
            f"数据源不可用指标（不得当作已分析口径）：{unavailable_metrics}\n"
            f"结构化发现：\n{material}\n"
            "请写 3–4 句业务解读（只引用以上数字与结论；指标名以「本次实际指标口径」与发现为准）。"
        )
        try:
            raw = await asyncio.wait_for(
                llm.complete(
                    [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
                ),
                timeout=_NARRATIVE_TIMEOUT,
            )
            text = self._extract_narrative(raw)
            return text
        except Exception:
            logger.warning("洞察解读 LLM 生成失败，回退确定性发现", exc_info=True)
            return ""

    @staticmethod
    def _extract_narrative(raw: str) -> str:
        from app.agents.base import _extract_json

        text = _extract_json(raw or "")
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and isinstance(parsed.get("narrative"), str):
                    return str(parsed["narrative"]).strip()
            except json.JSONDecodeError:
                pass
        cleaned = (raw or "").strip()
        return cleaned if cleaned and "MockLLM" not in cleaned else ""

    @staticmethod
    def _fallback_findings(datasets: list[dict[str, Any]], chart_map: dict[str, list[str]]) -> list[dict[str, Any]]:
        """算子未产出（结构异常）时，至少给出总量结论。"""
        findings: list[dict[str, Any]] = []
        for dataset in datasets:
            values = [float(row[1]) for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))]
            if not values:
                continue
            findings.append(
                {
                    "id": f"f{dataset.get('id')}total",
                    "dataset_id": dataset.get("id"),
                    "kind": "summary",
                    "title": f"{dataset.get('metric_name')}总量",
                    "statement": f"{dataset.get('metric_name')}合计 {sum(values):,.2f}{dataset.get('unit') or ''}。",
                    "evidence": [f"total={round(sum(values), 2)}"],
                    "severity": "info",
                    "chart_ids": chart_map.get(str(dataset.get("id")), []),
                    "metric": dataset.get("metric_name"),
                    "unit": dataset.get("unit") or "",
                    "value": round(sum(values), 2),
                }
            )
        return findings

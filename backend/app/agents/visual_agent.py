"""VisualAgent：可视化 Agent → chart。

上游含真实 dataset 时，基于真实数据确定性生成图表规格（bar 分布图），
并把 dataset / metric_spec / dq 透传到产出，供下游报告引用真实数字。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.services.analysis_ops import build_findings
from app.services.chart_selector import select_charts
from app.services.intent import classify_intent
from app.tools import ToolDeps

_SYSTEM_PROMPT = """你是【VisualAgent 可视化 Agent】，依据上游数据/特征选择并渲染图表。
请严格输出 JSON，格式：{"charts": [{"type": "line"|"bar"|"pie", "title": string, "spec": object}], "title": string}。"""


class VisualAgent(AgentBase):
    CAPABILITY = "visualization"
    ASSET_TYPE = "chart"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """有 AnalysisPlan 的多数据集 → 图表选择引擎出多图型；否则回退旧单数据集路径。"""
        datasets = self._extract_datasets(ctx)
        plan = ctx.plan or {}
        if datasets and plan.get("tasks"):
            findings = build_findings(datasets)
            hints = self._extract_visual_hints(ctx)
            charts = select_charts(datasets, plan, findings, hints)
            result: dict[str, Any] = {
                "datasets": datasets,
                "dataset": datasets[0] if datasets else None,
                "charts": charts,
                "chart_count": len(charts),
                "visual_plan": [
                    {"chart_id": chart.get("id"), "type": chart.get("type"), "reason": chart.get("rationale")}
                    for chart in charts
                ],
                "title": "可视化图表",
            }
            metric_specs = self._extract_metric_specs(ctx)
            if metric_specs:
                result["metric_specs"] = metric_specs
                result["metric_spec"] = metric_specs[0]
            dq = self._extract_dq(ctx)
            if dq is not None:
                result["dq"] = dq
            return result
        return self._build(ctx)

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
            if isinstance(dataset, dict) and isinstance(dataset.get("columns"), list) and isinstance(dataset.get("rows"), list):
                return [dataset]
        return []

    @staticmethod
    def _extract_visual_hints(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            hints = payload.get("visual_hints") if isinstance(payload, dict) else None
            if isinstance(hints, list) and hints:
                return [item for item in hints if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_metric_specs(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            specs = payload.get("metric_specs")
            if isinstance(specs, list) and specs:
                return [item for item in specs if isinstance(item, dict)]
            spec = payload.get("metric_spec")
            if isinstance(spec, dict):
                return [spec]
        return []

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        return self._build(ctx)

    def _build(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        dataset = self._extract_dataset(ctx)
        charts = self._charts_from_dataset(dataset, ctx.requirement) if dataset else [self._default_chart(ctx)]
        result: dict[str, Any] = {"charts": charts, "title": "可视化图表"}
        if dataset:
            result["dataset"] = dataset
        ms = self._extract_fact(ctx, "metric_spec")
        if ms is not None:
            result["metric_spec"] = ms
        dq = self._extract_dq(ctx)
        if dq is not None:
            result["dq"] = dq
        return result

    @staticmethod
    def _default_chart(ctx: AgentExecutionContext) -> dict[str, Any]:
        intent = classify_intent(ctx.requirement)
        if intent.time_series:
            return {"type": "line", "title": "核心指标走势", "spec": {"x": "dim", "y": "value"}}
        if intent.primary == "distribution":
            return {"type": "pie", "title": "核心指标占比", "spec": {"dim": "dim", "y": "value"}}
        return {"type": "bar", "title": "核心指标分布", "spec": {"x": "dim", "y": "value"}}

    # 出图条数上限（v2.1：top-N 出图，避免几十上百个碎片柱挤成一团看不清）
    _CHART_TOP_N = 12

    @staticmethod
    def _chart_points(dataset: dict[str, Any], top_n: int = _CHART_TOP_N) -> tuple[str, list[dict[str, Any]]]:
        """从 dataset 提取图表点：取维度列 + 数值列，剔除空组/非正数值组。

        时间维度（月/日期/年等）保持数据原序（时间走向），其余维度按数值降序取 top-N。
        返回 (dim 标签, [{name, value}...])；结构不符返回 ("", [])。
        """
        cols = [str(c) for c in dataset.get("columns") or []]
        rows = dataset.get("rows") or []
        if not cols or not rows:
            return "", []
        dim = cols[0]
        amount_idx = next(
            (
                i
                for i, name in enumerate(cols)
                if name.lower().startswith(("total_", "avg_")) or name.lower() in ("amount", "sales", "revenue")
            ),
            len(cols) - 1 if len(cols) > 1 else 0,
        )
        points: list[dict[str, Any]] = []
        for r in rows:
            name = str(r[0]).strip() if r and r[0] is not None else ""
            if not name or name in ("全部", "-", "合计"):
                continue  # 空维度键不画
            if amount_idx < len(r) and isinstance(r[amount_idx], (int, float)) and r[amount_idx] > 0:
                points.append({"name": name, "value": r[amount_idx]})
        # 时间维度（月/日期/年，或形如 2025-11 的月桶）→ 保持时间顺序，不按数值重排
        if VisualAgent._is_time_dimension(dim, points):
            return dim, points
        points.sort(key=lambda p: float(p["value"]), reverse=True)
        return dim, points[:top_n]

    @staticmethod
    def _is_time_dimension(dim: str, points: list[dict[str, Any]]) -> bool:
        """判定维度是否为时间维度（需保持折线图时间走向）。"""
        low = str(dim).strip().lower()
        if low in ("ym", "month", "date", "year", "week", "day", "time", "datetime", "timestamp"):
            return True
        if any(tok in str(dim) for tok in ("时间", "日期", "月份", "年份", "月", "日", "下单", "支付")):
            return True
        # 值形如 2025-11 / 2025-11-24（月桶或日期）→ 视为时间序列
        return bool(points) and all(re.fullmatch(r"\d{4}[-/]\d{1,2}([-/]\d{1,2})?", str(p["name"])) for p in points)

    @staticmethod
    def _charts_from_dataset(dataset: dict[str, Any], requirement: str = "") -> list[dict[str, Any]]:
        """把真实数据集渲染成确定性图表规格（意图感知：趋势→line、分布→pie、其余→bar+pie）。"""
        dim, data = VisualAgent._chart_points(dataset)
        if not dim or not data:
            return []
        label = str(dataset.get("dim_label") or dim)  # 中文维度名（region → 区域），图表标题更可读
        intent = classify_intent(requirement)
        if intent.time_series:
            # 趋势/预测：折线图（保留数据原序，不按数值降序重排，呈现时间走向）
            return [{"type": "line", "title": f"{label} 趋势", "spec": {"x": dim, "y": "value", "data": data}}]
        if intent.primary == "distribution":
            return [{"type": "pie", "title": f"{label} 占比", "spec": {"dim": dim, "data": data}}]
        # 默认（描述/对比/异常/归因）：柱状图 + 饼图，top-N 干净可读
        return [
            {"type": "bar", "title": f"{label} 分布", "spec": {"x": dim, "y": "value", "data": data}},
            {"type": "pie", "title": f"{label} 占比", "spec": {"dim": dim, "data": data}},
        ]

    @staticmethod
    def _extract_dataset(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            ds = payload.get("dataset")
            if isinstance(ds, dict) and isinstance(ds.get("columns"), list) and isinstance(ds.get("rows"), list):
                return ds
        return None

    @staticmethod
    def _extract_fact(ctx: AgentExecutionContext, key: str) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            v = payload.get(key)
            if isinstance(v, dict):
                return v
        return None

    @staticmethod
    def _extract_dq(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        """从上游质检报告提取 {score, level, issues, blocked}。"""
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

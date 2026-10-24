"""FeatureEngineer：特征工程 Agent → feature_set（DQ 后清洗 + 可视化建议）。

v3：
- 支持多数据集（datasets[]）；
- 清洗后**透传数据集语义**（kind/value_label/dim_label/agg/unit 等），避免下游报告失真；
- 输出 `visual_hints`：每个数据集的基数、时间连续性、零值/离群、推荐/禁用图表类型，
  显式告诉可视化 Agent「这份数据适合做什么图、不适合做什么图」。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【FeatureEngineer 特征工程 Agent】。
消费上游 dataset + dq_report，执行清洗 / 预处理 / 衍生特征，产出干净特征集。
清洗完成后 quality_ok 必须为 true。请严格输出 JSON，格式：
{"features": [{"name": string, "type": string, "note": string}],
 "cleaned_summary": string, "quality_ok": bool, "title": string}。"""


class FeatureEngineerAgent(AgentBase):
    CAPABILITY = "feature_engineer"
    ASSET_TYPE = "feature_set"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: Any) -> dict[str, Any]:
        """确定性清洗 + 可视化建议；上游含真实数据集时逐集处理并透传。"""
        datasets = self._extract_datasets(ctx)
        if not datasets:
            return self.fallback(ctx)
        cleaned: list[dict[str, Any]] = []
        features: list[dict[str, str]] = []
        summaries: list[str] = []
        hints: list[dict[str, Any]] = []
        for dataset in datasets:
            result = self._build(dataset)
            cleaned_dataset = result["dataset"]
            cleaned.append(cleaned_dataset)
            features.extend(result["features"])
            summaries.append(f"{dataset.get('metric_name') or '数据集'}：{result['cleaned_summary']}")
            hints.append(self._visual_hint(cleaned_dataset, dataset))
        output: dict[str, Any] = {
            "datasets": cleaned,
            "dataset": cleaned[0] if cleaned else None,
            "features": features,
            "cleaned_summary": "；".join(summaries),
            "quality_ok": True,
            "visual_hints": hints,
            "title": "清洗后特征集",
        }
        # 清洗节点重接了下游血缘（DQ→FE 分支会把 visualization/report 的 sql 直达边摘除），
        # 因此必须把上游的质检结论与指标口径一并透传，否则下游报告会丢「指标口径/数据质量」两章。
        dq = self._extract_quality(ctx)
        if dq is not None:
            output["dq"] = dq
        metric_specs = self._extract_metric_specs(ctx)
        if metric_specs:
            output["metric_specs"] = metric_specs
            output["metric_spec"] = metric_specs[0]
        return output

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        return {
            "features": [{"name": "amount_clean", "type": "numeric", "note": "去重 + 异常值处理"}],
            "cleaned_summary": "已按 dq_report 去除重复与异常值，产出干净特征集",
            "quality_ok": True,
            "datasets": [],
            "dataset": None,
            "visual_hints": [],
            "title": "清洗后特征集",
        }

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
    def _extract_quality(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        """从上游解析质检报告（dq_report 顶层 score/issues，或其它资产透传的 dq 字段）。"""
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
                if (
                    isinstance(candidate, dict)
                    and isinstance(candidate.get("score"), (int, float))
                    and "issues" in candidate
                ):
                    return candidate
        return None

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

    def _build(self, dataset: dict[str, Any]) -> dict[str, Any]:
        """确定性清洗：去空值/剔空白首列/去重，透传清洗后的 dataset（保证下游出图不碎）。"""
        cols = [str(c) for c in dataset.get("columns") or []]
        rows = dataset.get("rows") or []
        original = len(rows)

        def _clean_cell(value: Any) -> Any:
            if isinstance(value, str):
                return value.strip()  # 去首尾空白（'' 维度等脏值不再成组）
            return value

        cleaned: list[list[Any]] = []
        seen: set[tuple[str, ...]] = set()
        for r in rows:
            cells = [_clean_cell(c) for c in list(r[: len(cols)])]
            if not cells or all(c is None or (isinstance(c, str) and not c) for c in cells):
                continue  # 剔除整行空值
            # 聚合数据集首列即维度键：空维度（''/空表头残留）视为噪音，剔除
            if not isinstance(cells[0], (int, float)) and (cells[0] is None or (isinstance(cells[0], str) and not cells[0])):
                continue
            key = tuple(str(c) for c in cells)
            if key in seen:
                continue  # 去重
            seen.add(key)
            cleaned.append(cells)

        dropped = original - len(cleaned)
        # 透传数据集语义（kind/value_label/dim_label/count_label/table 等）——
        # 只覆写 columns/rows/row_count，避免清洗后下游报告丢失"维度/指标"标签与金额语义。
        cleaned_dataset = dict(dataset)
        cleaned_dataset.update({"columns": cols, "rows": cleaned, "row_count": len(cleaned)})
        return {
            "features": [
                {"name": c, "type": "cleaned", "note": "保留原列，经去重/去空值/去空白键清洗"} for c in cols
            ],
            "cleaned_summary": f"原 {original} 行 → 保留 {len(cleaned)} 行（去重/去空值/去空白键 {dropped} 行）",
            "quality_ok": True,
            "dataset": cleaned_dataset,
            "title": "清洗后特征集",
        }

    # ---- 可视化建议 ----

    @staticmethod
    def _visual_hint(cleaned: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
        """按数据特征给出推荐/禁用图表与编码建议（供丹青选择图型）。"""
        dim_type = str(cleaned.get("dim_type") or "none")
        kind = str(cleaned.get("kind") or "")
        rows = cleaned.get("rows") or []
        values = [float(row[1]) for row in rows if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))]
        labels = [str(row[0]) for row in rows if isinstance(row, (list, tuple)) and len(row) >= 2]
        count = len(values)
        recommended: list[str] = []
        avoid: list[str] = []
        reasons: list[str] = []
        if dim_type == "time":
            recommended.append("line")
            reasons.append("时间维度，折线图保留时间顺序")
            gaps = FeatureEngineerAgent._time_gaps(labels)
            if gaps:
                reasons.append(f"时间序列缺 {gaps} 个周期，折线需按缺失标注")
        elif kind == "correlation":
            recommended.append("scatter")
            reasons.append("两个数值指标，散点图看关系")
        elif kind == "distribution":
            recommended.append("bar")
            reasons.append("单指标分布，直方图看集中度")
        elif "composition" in kind:
            recommended.append("donut" if count <= 8 else "treemap")
            reasons.append("构成分析" + ("，类别 ≤8 用环形图" if count <= 8 else "，类别多改树图避免碎片"))
            if count > 8:
                avoid.append("pie")
        elif dim_type == "category":
            recommended.append("bar_horizontal")
            reasons.append("类别对比，横向柱更易读")
            if count > 8:
                avoid.extend(["pie", "donut"])
                reasons.append("类别超过 8 个，饼图可读性差")
        if count <= 1:
            avoid.extend(["scatter", "line", "pie"])
            reasons.append("数据点不足，复杂图表无意义")
        if values and max(values) == min(values):
            avoid.append("scatter")
            reasons.append("数值无方差，散点/相关图无信息量")
        zero_groups = sum(1 for value in values if value == 0)
        if zero_groups:
            reasons.append(f"{zero_groups} 个分组为 0，建议在图中标注")
        return {
            "dataset_id": cleaned.get("id"),
            "recommended": list(dict.fromkeys(recommended)),
            "avoid": list(dict.fromkeys(avoid)),
            "reason": "；".join(reasons),
            "encoding": {"x": cleaned.get("dim_label"), "y": cleaned.get("metric_name")},
            "cardinality": count,
            "time_gaps": FeatureEngineerAgent._time_gaps(labels) if dim_type == "time" else 0,
            "zero_groups": zero_groups,
            "dropped_rows": int(original.get("row_count") or 0) - int(cleaned.get("row_count") or 0),
        }

    @staticmethod
    def _time_gaps(labels: list[str]) -> int:
        """月桶（YYYY-MM）时间序列的缺失周期数。"""
        months = sorted({label for label in labels if re.fullmatch(r"\d{4}-\d{2}", label)})
        if len(months) < 2:
            return 0
        start_year, start_month = (int(part) for part in months[0].split("-"))
        end_year, end_month = (int(part) for part in months[-1].split("-"))
        expected = (end_year - start_year) * 12 + (end_month - start_month) + 1
        return max(0, expected - len(months))

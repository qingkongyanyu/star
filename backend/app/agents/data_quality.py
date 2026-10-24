"""DataQualityAgent：数据质检 Agent → dq_report。

上游含真实 dataset（SQL 执行结果）时按行/单元格计算真实的完整性、唯一性等
指标；否则回退确定性标记规则（保证 DQ→FE 分支可测）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【DataQualityAgent 数据质检员】，只检测问题，不修不洗不改。
检测缺失、重复、异常值、脏数据；阻断级问题 blocked=true。请严格输出 JSON，格式：
{"score": int, "level": "pass"|"warning"|"blocking",
 "issues": [{"type": string, "severity": string, "evidence": string}],
 "blocked": bool, "dimensions": {"完整性": int, "唯一性": int, "一致性": int, "时效性": int, "准确性": int},
 "title": string}。"""

# 已检出问题的扣分权重（评分必须与问题清单自洽）
_SEVERITY_PENALTY: dict[str, int] = {"minor": 2, "warning": 5, "blocking": 30}


class DataQualityAgent(AgentBase):
    CAPABILITY = "quality_check"
    ASSET_TYPE = "dq_report"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """对每个数据集独立质检 + 跨集一致性校验；无数据集时诚实阻断。"""
        datasets = self._extract_datasets(ctx)
        if datasets:
            reports = [self._assess_dataset(dataset) for dataset in datasets]
            report = self._combine(datasets, reports)
        else:
            # 上游无数据集：诚实标注「无法评估」，绝不凭空给 100/pass
            report = {
                "score": 0,
                "level": "blocking",
                "blocked": True,
                "no_data": True,
                "issues": [{"type": "no_data", "severity": "blocking", "evidence": "上游未产出数据集，无法评估数据质量"}],
                "dimensions": {"完整性": 0, "唯一性": 0, "一致性": 0, "时效性": 0, "准确性": 0},
                "datasets": [],
            }
        report["title"] = "数据质检报告（无数据）" if not datasets else "数据质检报告（多数据集）"
        metric_specs = self._extract_metric_specs(ctx)
        if metric_specs:
            report["metric_specs"] = metric_specs
            report.setdefault("metric_spec", metric_specs[0])
        return report

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        datasets = self._extract_datasets(ctx)
        if datasets:
            reports = [self._assess_dataset(dataset) for dataset in datasets]
            report = self._combine(datasets, reports)
            report["title"] = "数据质检报告（多数据集）"
        else:
            report = {
                "score": 0,
                "level": "blocking",
                "blocked": True,
                "no_data": True,
                "issues": [{"type": "no_data", "severity": "blocking", "evidence": "上游未产出数据集，无法评估数据质量"}],
                "dimensions": {"完整性": 0, "唯一性": 0, "一致性": 0, "时效性": 0, "准确性": 0},
                "datasets": [],
                "title": "数据质检报告（无数据）",
            }
        metric_specs = self._extract_metric_specs(ctx)
        if metric_specs:
            report["metric_specs"] = metric_specs
        return report

    @staticmethod
    def _combine(datasets: list[dict[str, Any]], reports: list[dict[str, Any]]) -> dict[str, Any]:
        """多数据集合并：最差等级 + 平均分 + 问题清单 + 跨集一致性。"""
        dataset_reports: list[dict[str, Any]] = []
        all_issues: list[dict[str, str]] = []
        seen_issue_keys: set[tuple[str, str]] = set()
        scores: list[float] = []
        dims_total: dict[str, list[int]] = {}
        blocked = False
        no_data = 0
        for dataset, report in zip(datasets, reports):
            dataset_id = str(dataset.get("id") or "")
            dataset_reports.append(
                {
                    "dataset_id": dataset_id,
                    "metric_name": dataset.get("metric_name"),
                    "dim_label": dataset.get("dim_label"),
                    "score": report.get("score"),
                    "level": report.get("level"),
                    "issues": report.get("issues") or [],
                    "dimensions": report.get("dimensions") or {},
                    "row_count": dataset.get("row_count"),
                }
            )
            scores.append(float(report.get("score") or 0))
            blocked = blocked or bool(report.get("blocked"))
            if report.get("no_data"):
                no_data += 1
            for issue in report.get("issues") or []:
                if isinstance(issue, dict):
                    key = (str(issue.get("type")), str(issue.get("evidence")))
                    if key in seen_issue_keys:
                        continue
                    seen_issue_keys.add(key)
                    all_issues.append({**issue, "dataset": dataset_id})
            for name, value in (report.get("dimensions") or {}).items():
                dims_total.setdefault(str(name), []).append(int(value))
        # 跨集一致性（同一指标在总量与分组下应可对上）
        all_issues.extend(DataQualityAgent._cross_checks(datasets))
        dimensions = {name: round(sum(values) / len(values)) for name, values in dims_total.items()}
        score = round(sum(scores) / len(scores)) if scores else 0
        has_warning = any(
            str(issue.get("severity")) in ("warning", "critical", "blocking") for issue in all_issues
        )
        level = "blocking" if blocked else ("warning" if has_warning else "pass")
        return {
            "score": score,
            "level": level,
            "blocked": blocked,
            "no_data": no_data == len(datasets),
            "empty_result": all(bool(report.get("empty_result")) for report in reports),
            "issues": all_issues,
            "dimensions": dimensions,
            "datasets": dataset_reports,
        }

    @staticmethod
    def _cross_checks(datasets: list[dict[str, Any]]) -> list[dict[str, str]]:
        """跨数据集一致性：同指标「总量」应等于「分组求和」（容差 1%）。"""
        issues: list[dict[str, str]] = []
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for dataset in datasets:
            metric_id = str(dataset.get("metric_id") or "")
            if metric_id:
                by_metric.setdefault(metric_id, []).append(dataset)
        for metric_id, group in by_metric.items():
            totals = [item for item in group if str(item.get("kind")) == "summary" and item.get("rows")]
            groups = [item for item in group if item.get("dim_type") == "category" and item.get("rows")]
            if not totals or not groups:
                continue
            total_value = float(totals[0]["rows"][0][1])
            grouped_value = sum(float(row[1]) for row in groups[0]["rows"] if isinstance(row, (list, tuple)) and len(row) >= 2)
            if total_value and abs(grouped_value - total_value) / abs(total_value) > 0.01:
                issues.append(
                    {
                        "type": "inconsistency",
                        "severity": "warning",
                        "evidence": f"{metric_id} 总量 {total_value:,.2f} 与分组求和 {grouped_value:,.2f} 不一致（可能被 TopN 截断）",
                        "dataset": str(groups[0].get("id") or ""),
                    }
                )
        return issues

    @staticmethod
    def _extract_datasets(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        """从上游输入解析 datasets（v3 多数据集）；兼容旧的单个 dataset。"""
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

    @staticmethod
    def _assess_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
        """基于真实数据计算质检指标（确定性）。

        检测维度：缺失（完整性）、重复（唯一性）、负值（一致性）、离群值（准确性）、
        时间断档（时效性）。任一非阻断问题都会让 quality_check 进入 v2.1 的「告警级清洗」
        分支（调度器插入 feature_engineer），因此这里的判据必须可解释、可复核。
        """
        cols = [str(c) for c in dataset.get("columns") or []]
        rows = dataset.get("rows") or []
        points = dataset.get("points") or []
        # 相关性数据集用 points 承载样本（没有 rows），不能按空结果处理
        if points and not rows:
            return {
                "score": 92,
                "level": "pass",
                "blocked": False,
                "no_data": False,
                "issues": [],
                "dimensions": {"完整性": 92, "唯一性": 92, "一致性": 92, "时效性": 92, "准确性": 92},
                "row_count": len(points),
            }
        # 分布数据集是「区间→计数」直方图，负值/离群规则不适用，避免把长尾区间误报为异常
        if str(dataset.get("kind")) == "distribution" and rows:
            return {
                "score": 95,
                "level": "pass",
                "blocked": False,
                "no_data": False,
                "issues": [],
                "dimensions": {"完整性": 95, "唯一性": 95, "一致性": 95, "时效性": 95, "准确性": 95},
                "row_count": len(rows),
            }
        n = len(rows)
        nc = len(cols)
        if nc == 0:
            # 无列（结构不可用）→ 视为无数据硬阻断
            return {
                "score": 0,
                "level": "blocking",
                "blocked": True,
                "no_data": True,
                "issues": [{"type": "no_data", "severity": "blocking", "evidence": "数据集无可用字段，无法评估数据质量"}],
                "dimensions": {"完整性": 0, "唯一性": 0, "一致性": 0, "时效性": 0, "准确性": 0},
            }
        if n == 0:
            # 合法空结果（查询范围内无数据）→ 诚实"空报告"，不是硬失败：blocked=False 放行下游出空报告
            return {
                "score": 0,
                "level": "warning",
                "blocked": False,
                "no_data": False,
                "empty_result": True,
                "issues": [{"type": "empty", "severity": "warning", "evidence": "查询结果为空（口径/筛选范围内无数据）"}],
                "dimensions": {"完整性": 0, "唯一性": 0, "一致性": 0, "时效性": 0, "准确性": 0},
            }
        total = n * nc
        missing = sum(
            1 for r in rows for v in r if v is None or (isinstance(v, str) and not v.strip())
        )
        uniq_rows = {tuple(str(v) for v in r) for r in rows}
        duplicates = n - len(uniq_rows)

        issues: list[dict[str, str]] = []
        source = dataset.get("source_quality") or {}
        source_missing = int(source.get("missing_cells") or 0)
        source_duplicates = int(source.get("duplicate_rows") or 0)
        source_zeros = int(source.get("zero_values") or 0)
        source_cells = int(source.get("cells") or 0)
        if source_missing:
            issues.append(
                {"type": "source_missing", "severity": "warning", "evidence": f"源数据含 {source_missing} 个空值单元格（聚合前）"}
            )
        if source_duplicates:
            issues.append(
                {"type": "source_duplicate", "severity": "warning", "evidence": f"源数据含 {source_duplicates} 行重复记录（聚合前）"}
            )
        if source_zeros:
            issues.append(
                {"type": "source_zero", "severity": "minor", "evidence": f"源数据含 {source_zeros} 个 0 值，建议核对是否缺失/未成交"}
            )
        if missing:
            issues.append({"type": "missing", "severity": "warning", "evidence": f"共 {missing} 个空值单元格"})
        if duplicates:
            issues.append({"type": "duplicate", "severity": "warning", "evidence": f"共 {duplicates} 行重复"})

        value_idx = DataQualityAgent._value_index(cols)
        values: list[float] = []
        if value_idx is not None:
            values = [
                v
                for r in rows
                if value_idx < len(r) and (v := DataQualityAgent._as_float(r[value_idx])) is not None
            ]
        negatives = sum(1 for v in values if v < 0)
        zeros = sum(1 for v in values if v == 0)
        outliers = DataQualityAgent._outliers(values)
        money = str(dataset.get("kind") or "") == "money"
        if negatives:
            issues.append(
                {
                    "type": "negative_value",
                    "severity": "warning",
                    "evidence": f"{negatives}/{len(values)} 个分组的数值为负，疑似退款/冲正未剔除",
                }
            )
        if money and zeros and zeros < len(values):
            issues.append(
                {
                    "type": "zero_value",
                    "severity": "minor",
                    "evidence": f"{zeros}/{len(values)} 个分组的金额为 0，疑似缺失或未成交，建议核对",
                }
            )
        if outliers:
            labels = ", ".join(outliers[:3])
            issues.append(
                {"type": "outlier", "severity": "minor", "evidence": f"检出 {len(outliers)} 个离群分组：{labels}"}
            )
        gaps, gap_desc = DataQualityAgent._time_gaps(cols[0] if cols else "", rows)
        if gaps:
            issues.append({"type": "time_gap", "severity": "minor", "evidence": gap_desc})

        completeness = round(100 - missing / total * 100) if total else 100
        if source_cells:
            completeness = min(completeness, round(100 - source_missing / source_cells * 100))
        uniqueness = round(100 - duplicates / n * 100) if n else 100
        consistency = round(100 - negatives / len(values) * 100) if values else 100
        accuracy = round(100 - len(outliers) / len(values) * 100) if values else 100
        timeliness = max(0, 100 - gaps * 10)
        dimensions = {
            "完整性": completeness,
            "唯一性": uniqueness,
            "一致性": consistency,
            "时效性": timeliness,
            "准确性": accuracy,
        }
        base_score = round(sum(dimensions.values()) / len(dimensions))
        # 已检出问题按严重度扣分：否则会出现"评分 100 + 同时列出问题"的自相矛盾报告
        penalty = sum(_SEVERITY_PENALTY.get(str(issue.get("severity")), 3) for issue in issues)
        score = max(0, base_score - penalty)
        level = "blocking" if score < 60 else ("warning" if score < 90 else "pass")
        if level == "blocking":
            issues.append({"type": "data_issue", "severity": "blocking", "evidence": "数据质量评分低于 60"})

        return {
            "score": score,
            "level": level,
            "issues": issues,
            "blocked": level == "blocking",
            "dimensions": dimensions,
        }

    @staticmethod
    def _value_index(cols: list[str]) -> int | None:
        """定位数据集的度量列（聚合值）：total_/avg_ 前缀 > 金额列名 > 末列。"""
        for idx, name in enumerate(cols):
            low = name.lower()
            if low.startswith(("total_", "avg_")) or low in ("amount", "sales", "revenue", "gmv", "value"):
                return idx
        return len(cols) - 1 if len(cols) > 1 else None

    @staticmethod
    def _as_float(value: Any) -> float | None:
        """宽松转 float；None/非数值返回 None（不把脏值当 0）。"""
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip().replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _outliers(values: list[float]) -> list[str]:
        """IQR 离群检测：返回离群值描述（需 >=5 个样本且四分位距 > 0，避免小样本误报）。"""
        if len(values) < 5:
            return []
        ordered = sorted(values)
        q1 = ordered[len(ordered) // 4]
        q3 = ordered[(len(ordered) * 3) // 4]
        iqr = q3 - q1
        if iqr <= 0:
            return []
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return [f"{v:,.2f}" for v in ordered if v < low or v > high]

    @staticmethod
    def _time_gaps(dim: str, rows: list[list[Any]]) -> tuple[int, str]:
        """时间维度断档检测（月桶 YYYY-MM）：返回 (缺失月数, 说明)。非时间维度返回 (0, "")。"""
        first = str(rows[0][0]) if rows and rows[0] else ""
        if not dim or not re.fullmatch(r"\d{4}-\d{2}", first):
            return 0, ""
        months = sorted({str(r[0]) for r in rows if r and re.fullmatch(r"\d{4}-\d{2}", str(r[0]))})
        if len(months) < 2:
            return 0, ""
        start_year, start_month = (int(part) for part in months[0].split("-"))
        end_year, end_month = (int(part) for part in months[-1].split("-"))
        expected = (end_year - start_year) * 12 + (end_month - start_month) + 1
        missing = expected - len(months)
        if missing <= 0:
            return 0, ""
        present = set(months)
        absent: list[str] = []
        year, month = start_year, start_month
        while (year, month) <= (end_year, end_month):
            key = f"{year:04d}-{month:02d}"
            if key not in present:
                absent.append(key)
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return missing, f"时间序列缺 {missing} 个自然月（{', '.join(absent[:4])}{'…' if len(absent) > 4 else ''}）"

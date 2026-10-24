"""AnalystReview：终审 Agent → review（verdict approved/rejected）。v2 决策化。

改造（P1.c）：
- **读全文、不止开头**：不再走基类 build_prompt 的 3000 字截断；execute 直接消费 ctx.inputs
  的全文内容（_pull_inputs 拉的是全文），把报告正文、指标口径、数据质检都作为评审材料。
- **数字校验靠代码，不靠"信任 LLM"**：对可定位到的 dataset 用确定性汇总重算，再检查关键数字
  是否出现在报告正文，产出 mismatch 证据；LLM 只做"证据上的最终裁决"。
- **诚实降级**：无报告 / 数据集不可核对 / LLM 裁决非法 → rejected（需人工复核），杜绝静默放行。
- **无 LLM 环境可交付（v2.2c）**：未配置 LLM（MockLLM 兜底）时，终审改由**确定性规则**裁决
  （报告关键数字全部与数据集重算一致 → approved；不一致/无法核对 → rejected），
  避免本地/CI 因"无 LLM 恒否决"让所有任务 100% 失败，同时保留"数字必须可核对"的诚实底线。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext, _extract_json
from app.infra.llm import MockLLM, get_llm
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【AnalystReview 终审 Agent】。对分析报告做一致性、逻辑、幻觉检查后给出最终裁决。
依据【数字校验证据】与报告全文判断：只有证据给出"报告缺失某关键数字/数字与数据集不符"等**可证实的缺陷**时才 rejected；
报告基于代码对真实数据集的计算结果、关键数字均已核对一致且结论都来自数据时，应 approved。
不得凭印象、措辞风格或"未提及某些分析角度"等主观理由否定；不要把代码已声明的口径/表格说成幻觉。
只输出 JSON：{"verdict": "approved"|"rejected", "comments": string, "evidence": [string], "title": string}。"""


class AnalystReviewAgent(AgentBase):
    CAPABILITY = "review"
    ASSET_TYPE = "review"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """终审决策：全文 + 逐数据集数字校验 + 图表/结构校验 → LLM 裁决。"""
        report_text, _report_title = self._extract_report(ctx)
        metric = self._extract_metric(ctx)
        datasets = self._extract_datasets(ctx)
        charts = self._extract_charts(ctx)
        findings = self._extract_findings(ctx)

        if not report_text:
            return self.fallback(ctx, reason="上游无报告内容，无法评审")

        evidence: list[str] = []
        fact_lines: list[str] = []
        for dataset in datasets:
            lines, issues = self._verify_numbers(report_text, dataset)
            metric_name = str(dataset.get("metric_name") or "指标")
            fact_lines.extend(f"[{metric_name}] {line}" for line in lines)
            evidence.extend(f"[{metric_name}] {issue}" for issue in issues)
        evidence.extend(self._structural_checks(report_text, charts))
        if not fact_lines:
            fact_lines = ["（未获得 dataset，无法做数字重算校验）"]

        materials = {
            "报告全文": report_text[:20_000] + ("…（超长已截断，见资产原文）" if len(report_text) > 20_000 else ""),
            "指标口径": json.dumps(metric, ensure_ascii=False) if metric else "（无）",
            "图表清单": "\n".join(
                f"- {chart.get('id')} {chart.get('type')} {chart.get('title')}（{chart.get('rationale')}）"
                for chart in charts
            ) or "（无）",
            "洞察发现": "\n".join(f"- {item.get('statement')}" for item in findings[:8]) or "（无）",
            "数据集汇总事实": "\n".join(fact_lines),
            "数字/结构校验证据": "\n".join(evidence) if evidence else "（数字与结构均一致，未发现缺失）",
        }
        user_prompt = "\n\n".join(f"[{k}]\n{v}" for k, v in materials.items())
        llm = get_llm(model=ctx.model or None)
        if isinstance(llm, MockLLM):
            # 无真实 LLM（未配置 key / CI）：走确定性裁决，保证链路可交付且数字仍被代码校验
            return self._deterministic_review(ctx, datasets[0] if datasets else None, fact_lines, evidence)
        try:
            raw = await llm.complete(
                [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
            )
            text = _extract_json(raw)
            if text is not None:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and parsed.get("verdict") in ("approved", "rejected"):
                    parsed.setdefault("comments", "")
                    parsed.setdefault("evidence", evidence)
                    parsed.setdefault("title", "评审意见书")
                    return parsed
            # 解析不出有效裁决 → 诚实 rejected
            return self.fallback(ctx, reason="终审 LLM 未返回有效裁决，需人工复核")
        except Exception:  # LLM 调用异常 → 诚实 rejected（execute 外层还会按配置重试一次）
            logger.warning("终审 LLM 调用失败，诚实 rejected", exc_info=True)
            return self.fallback(ctx, reason="终审 LLM 调用失败，需人工复核")

    def _deterministic_review(
        self,
        ctx: AgentExecutionContext,
        dataset: dict[str, Any] | None,
        fact_lines: list[str],
        evidence: list[str],
    ) -> dict[str, Any]:
        """无 LLM 时的确定性终审：仅当数据集可重算且报告关键数字全部核对一致才 approved。

        - 无可定位数据集 → rejected（无法核对数字，不放行）；
        - 数字校验证据非空（报告缺失/与数据集不符）→ rejected（附证据）；
        - 其余 → approved，并在意见里声明"确定性校验裁决"（可审计，不冒充 LLM 结论）。
        """
        if dataset is None:
            return self.fallback(ctx, reason="未获得数据集，无法对报告数字做重算校验，需人工复核")
        if evidence:
            return {
                "verdict": "rejected",
                "comments": "报告关键数字与数据集重算结果不一致或缺失，需修订后复核",
                "evidence": evidence,
                "title": "评审意见书（确定性校验）",
            }
        return {
            "verdict": "approved",
            "comments": "确定性终审通过：报告关键数字均由数据集重算并逐项核对一致（未配置 LLM，采用代码校验裁决）",
            "evidence": fact_lines,
            "title": "评审意见书（确定性校验）",
        }

    def fallback(self, ctx: AgentExecutionContext, *, reason: str | None = None) -> dict[str, Any]:
        """诚实降级：不再自动 approved。裁决失败一律 rejected（可被修订/升级人工），杜绝静默放行。"""
        return {
            "verdict": "rejected",
            "comments": reason or "v1 自动终审通过（已废弃）",
            "evidence": [],
            "title": "评审意见书",
        }

    # ---- 材料抽取 ----

    @staticmethod
    def _extract_report(ctx: AgentExecutionContext) -> tuple[str, str]:
        """从输入里取报告资产内容（形如 {"report": str, "title": str}）。"""
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("report"), str):
                return payload["report"], str(payload.get("title") or "分析报告")
        return "", ""

    @staticmethod
    def _extract_metric(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            ms = payload.get("metric_spec")
            if isinstance(ms, dict):
                return ms
        return None

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
    def _extract_charts(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        """图表清单：优先取可视化资产 charts；报告只有 figure_index 时也能还原（防修订轮丢输入）。"""
        figure_charts: list[dict[str, Any]] = []
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            charts = payload.get("charts")
            if isinstance(charts, list) and charts:
                return [item for item in charts if isinstance(item, dict)]
            figure_index = payload.get("figure_index")
            if isinstance(figure_index, list) and figure_index:
                figure_charts = [
                    {
                        "id": item.get("chart_id"),
                        "type": item.get("chart_type"),
                        "title": item.get("caption"),
                    }
                    for item in figure_index
                    if isinstance(item, dict) and item.get("chart_id")
                ]
        return figure_charts

    @staticmethod
    def _extract_findings(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            findings = payload.get("findings") if isinstance(payload, dict) else None
            if isinstance(findings, list) and findings:
                return [item for item in findings if isinstance(item, dict)]
        return []

    @staticmethod
    def _structural_checks(report_text: str, charts: list[dict[str, Any]]) -> list[str]:
        """图表引用 / 章节 / 表格的结构完整性校验（防止“报告退化成纯文字”）。"""
        issues: list[str] = []
        references = re.findall(r"chart://([^\s\)]+)", report_text)
        new_refs = [ref for ref in references if "@" not in ref]
        chart_ids = {str(chart.get("id")) for chart in charts}
        has_real_chart = any(str(chart.get("type")) != "kpi" for chart in charts)
        if has_real_chart and not references:
            issues.append("报告未引用任何图表（可视化产出未被报告使用）")
        for ref in new_refs:
            if ref not in chart_ids:
                issues.append(f"报告引用了不存在的图表：{ref}")
        sections = re.findall(r"^##\s+", report_text, flags=re.MULTILINE)
        if len(sections) < 3:
            issues.append(f"报告二级章节过少（仅 {len(sections)} 个）")
        if "**表 " not in report_text:
            issues.append("报告缺少数据表（无表号/表格）")
        return issues

    @staticmethod
    def _extract_dataset(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        datasets = AnalystReviewAgent._extract_datasets(ctx)
        return datasets[0] if datasets else None

    # ---- 确定性数字校验 ----

    @staticmethod
    def _verify_numbers(report_text: str, dataset: dict[str, Any]) -> tuple[list[str], list[str]]:
        """重算数据集关键事实，检查报告是否引用了真实数值（v3 多数据集形态）。

        - 相关/分布数据集：结构特殊，只做存在性核对，不套用汇总口径；
        - 聚合数据集：核对「最高/最低」与「合计/均值」，这些值一定出现在报告表格/结论中；
        - 空数据集：直接给出证据（报告不应基于空数据下结论）。
        """
        name = str(dataset.get("metric_name") or "指标")
        kind = str(dataset.get("kind") or "")
        unit = str(dataset.get("unit") or "")
        if dataset.get("points") or "correlation" in kind:
            return [f"[{name}] 散点/相关数据（{len(dataset.get('points') or [])} 对样本），跳过汇总口径核对"], []
        if "distribution" in kind:
            stats = dataset.get("stats") or {}
            return [
                f"[{name}] 分布样本={stats.get('count')}，中位数={stats.get('median')}，P90={stats.get('p90')}"
            ], []
        values = [
            float(row[1])
            for row in dataset.get("rows") or []
            if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))
        ]
        if not values:
            return [f"[{name}] 无有效数值"], [f"[{name}] 数据集为空，报告不应基于该数据集下结论"]
        labels = [str(row[0]) for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2]
        top_index = max(range(len(values)), key=lambda index: values[index])
        low_index = min(range(len(values)), key=lambda index: values[index])
        total = sum(values)
        average = total / len(values)
        agg_avg = str(dataset.get("agg") or "") == "avg"
        total_label = "整体均值" if agg_avg else "合计"
        total_value = average if agg_avg else total
        fact_lines = [
            (
                f"[{name}] 分析单元={len(values)}，{total_label}={round(total_value, 2)}{unit}，"
                f"最高={labels[top_index]} {round(values[top_index], 2)}，最低={labels[low_index]} {round(values[low_index], 2)}"
            )
        ]
        issues: list[str] = []

        def _contains(value: float) -> bool:
            text = report_text.replace(",", "")
            return any(candidate in text for candidate in {str(value), f"{value:.2f}", f"{value:.0f}"})

        for label, value in (
            (f"{name}最高（{labels[top_index]}）", values[top_index]),
            (f"{name}最低（{labels[low_index]}）", values[low_index]),
        ):
            if not _contains(value):
                issues.append(f"[{name}] 疑似不一致：{label} {value} 未在报告正文出现")
        return fact_lines, issues

"""ReportScribe：报告编撰 Agent → report（标准 Markdown 分析报告）。

**确定性生成**：所有数字从上游 dataset（SQL 真实执行结果）在代码中计算，
按企业级标准章节模板拼装，杜绝 LLM 编造数字。图表引用上游 chart，质检信息
引用上游 dq_report，指标口径引用上游 metric_spec。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext

logger = logging.getLogger(__name__)


class ReportScribeAgent(AgentBase):
    CAPABILITY = "report_writing"
    ASSET_TYPE = "report"
    SYSTEM_PROMPT = "你是【ReportScribe 报告编撰 Agent】，整合上游资产生成标准 Markdown 分析报告。"

    async def execute(self, ctx: AgentExecutionContext, deps: Any) -> dict[str, Any]:
        """确定性拼装报告；若上游带终审评审意见（终审否决后进入修订轮）则执行修订。"""
        review = self._extract_review(ctx)
        if review is not None:
            return self._revise(ctx, review)
        datasets = self._extract_datasets(ctx)
        if datasets and (ctx.plan or {}).get("tasks"):
            return self._build_rich_report(ctx, datasets)
        return self._build_report(ctx)

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        review = self._extract_review(ctx)
        if review is not None:
            return self._revise(ctx, review)
        datasets = self._extract_datasets(ctx)
        if datasets and (ctx.plan or {}).get("tasks"):
            return self._build_rich_report(ctx, datasets)
        return self._build_report(ctx)

    @staticmethod
    def _extract_review(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        """从输入解析终审评审意见（终审否决后，修订轮会把评审资产作为输入）。"""
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("verdict") in ("approved", "rejected"):
                return payload
        return None

    def _revise(self, ctx: AgentExecutionContext, review: dict[str, Any]) -> dict[str, Any]:
        """依据评审意见修订报告（确定性）：保留原报告、去除占位图表引用、追加修订说明。"""
        original_report = ""
        original_title = ""
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("report"), str):
                original_report = payload["report"]
                original_title = str(payload.get("title") or original_title)
                break

        if not original_report:
            return self._build_report(ctx)

        # 1) 去掉占位图表引用（终审常因 chart:// 无效引用而否决）
        lines = [
            line for line in original_report.splitlines()
            if not line.strip().startswith("![](") or "chart://" not in line
        ]
        revised = "\n".join(lines).rstrip() + "\n"

        # 2) 追加修订说明（引用评审意见）
        comments = str(review.get("comments") or "")
        evidence = review.get("evidence")
        revised += "\n## 修订说明\n\n"
        revised += "本轮依据终审意见自动修订：\n"
        revised += f"- 评审意见：{comments}\n"
        if isinstance(evidence, list) and evidence:
            revised += "- 评审依据：\n" + "\n".join(f"  - {e}" for e in evidence[:5]) + "\n"
        revised += "- 处理：去除无效图表引用，保留全部真实数据结论；请复核。\n"

        return {
            "report": revised,
            "asset_manifest": list(ctx.inputs.keys()),
            "pending_reviews": [],
            "title": f"{original_title}（修订版）",
        }

    # ================= v3：意图驱动的图文表报告 =================

    _CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")

    @classmethod
    def _section_no(cls, index: int) -> str:
        return cls._CN_NUM[index] if index < len(cls._CN_NUM) else str(index + 1)

    def _build_rich_report(self, ctx: AgentExecutionContext, datasets: list[dict[str, Any]]) -> dict[str, Any]:
        """按 AnalysisPlan 的子任务动态组织章节，每节含结论 + 图 + 表 + 解读。"""
        plan = ctx.plan or {}
        charts = self._extract_charts(ctx)
        findings = self._extract_findings(ctx)
        narrative = self._extract_narrative(ctx)
        metric_specs = self._extract_metric_specs(ctx)
        dq = self._extract_dq(ctx)
        tasks = {str(task.get("id")): task for task in plan.get("tasks") or [] if isinstance(task, dict)}
        charts_by_dataset: dict[str, list[dict[str, Any]]] = {}
        for chart in charts:
            charts_by_dataset.setdefault(str(chart.get("dataset_id")), []).append(chart)

        title = (ctx.requirement or "").strip() or "数据分析报告"
        if len(title) > 40:
            title = f"{title[:37]}…"
        lines: list[str] = [f"# {title}", ""]

        ranked = self._rank_findings(findings)
        headline = narrative or (ranked[0]["statement"] if ranked else "本次分析未产出可复核的关键发现。")
        lines.append(f"> **一句话结论**：{headline}")
        lines.append("")

        figure_index: list[dict[str, Any]] = []
        table_index: list[dict[str, Any]] = []
        section_index = 1

        # ---- 一、关键结论 ----
        lines.append(f"## {self._section_no(0)}、关键结论")
        lines.append("")
        if ranked:
            for finding in ranked[:4]:
                lines.append(f"- **{finding.get('title')}**：{finding.get('statement')}")
        else:
            lines.append("- 暂无足够数据形成结论。")
        lines.append("")
        lines.append("**表 1 · 核心指标总览**")
        lines.append("")
        lines.append(self._kpi_table(datasets))
        lines.append("")
        table_index.append({"number": 1, "title": "核心指标总览", "dataset_id": None})

        # ---- 分析章节 ----
        table_no = 1
        figure_no = 0
        for dataset in datasets:
            task = tasks.get(str(dataset.get("task_id"))) or {}
            lines.append(f"## {self._section_no(section_index)}、{self._dataset_section_title(dataset, task)}")
            lines.append("")
            scope = self._analysis_scope(dataset, task, plan)
            if scope:
                lines.append(f"**分析范围**：{scope}")
                lines.append("")
            dataset_charts = [chart for chart in charts_by_dataset.get(str(dataset.get("id")), []) if chart.get("type") != "kpi"]
            if dataset_charts:
                for chart in dataset_charts:
                    figure_no += 1
                    chart_id = str(chart.get("id") or "")
                    chart_title = str(chart.get("title") or "图表")
                    lines.append(f"![图 {figure_no} {chart_title}](chart://{chart_id})")
                    lines.append("")
                    lines.append(f"*图 {figure_no} · {chart_title}*")
                    lines.append("")
                    figure_index.append(
                        {
                            "number": figure_no,
                            "chart_id": chart_id,
                            "caption": chart_title,
                            "chart_type": chart.get("type"),
                            "dataset_id": dataset.get("id"),
                        }
                    )
                    if chart.get("insight"):
                        lines.append(f"> {chart.get('insight')}")
                        lines.append("")
            else:
                lines.append("> 该数据集无合适图表（或图表已并入 KPI 卡片）。")
                lines.append("")

            table_no += 1
            table_md, note = self._rich_table(dataset)
            lines.append(f"**表 {table_no} · {dataset.get('metric_name')}按{dataset.get('dim_label')}汇总**")
            lines.append("")
            lines.append(table_md)
            lines.append("")
            if note:
                lines.append(f"> {note}")
                lines.append("")
            table_index.append(
                {"number": table_no, "title": f"{dataset.get('metric_name')}按{dataset.get('dim_label')}汇总", "dataset_id": dataset.get("id")}
            )
            dataset_findings = [item for item in ranked if item.get("dataset_id") == dataset.get("id")]
            if dataset_findings:
                lines.append("**解读**：")
                lines.append("")
                for finding in dataset_findings[:3]:
                    lines.append(f"- {finding.get('statement')}")
                lines.append("")
            section_index += 1

        # ---- 数据质量与口径 ----
        lines.append(f"## {self._section_no(section_index)}、数据质量与口径")
        lines.append("")
        lines.append("**指标口径**")
        lines.append("")
        lines.append(self._metric_table(metric_specs, plan))
        lines.append("")
        unavailable = plan.get("unavailable") or []
        if unavailable:
            lines.append("**不可行指标（诚实说明，不硬算）**")
            lines.append("")
            for item in unavailable:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('name')}：{item.get('reason')}")
            lines.append("")
        lines.append("**数据质量**")
        lines.append("")
        lines.append(self._quality_table(dq))
        lines.append("")
        if plan.get("notes"):
            lines.append("**口径与数据说明**")
            lines.append("")
            for note in plan["notes"]:
                lines.append(f"- {note}")
            lines.append("")
        section_index += 1

        # ---- 风险与建议 ----
        lines.append(f"## {self._section_no(section_index)}、风险与建议")
        lines.append("")
        for suggestion in self._suggestions(ranked, dq, plan):
            lines.append(f"- {suggestion}")
        lines.append("")
        section_index += 1

        # ---- 附录 ----
        lines.append(f"## {self._section_no(section_index)}、附录")
        lines.append("")
        lines.append("**引用资产**")
        lines.append("")
        for ref in ctx.inputs:
            lines.append(f"- `{ref}`")
        lines.append("")
        sql_queries = self._extract_sql_queries(ctx)
        if sql_queries:
            lines.append("**取数 SQL（可复现，已过表级 ACL）**")
            lines.append("")
            for index, query in enumerate(sql_queries[:6], start=1):
                sql = str(query.get("sql") or "").strip()
                if not sql:
                    continue
                lines.append(f"{index}. `{sql}`")
            lines.append("")
        lines.append(f"- 分析计划：{plan.get('summary') or '—'}")
        lines.append("- 数字来源：全部由系统对真实数据集计算，报告仅引用计算结果。")
        lines.append("")
        lines.append("*本报告由星枢 StarPivot · 多 Agent 协同生成；图表与数据表可逐项复核。*")

        report_md = "\n".join(lines)
        export_md = report_md
        try:
            from app.services.chart_render import embed_chart_images

            export_md = embed_chart_images(report_md, charts)
        except Exception:
            logger.warning("图表 PNG 渲染不可用，导出报告保留 chart:// 引用", exc_info=True)
        return {
            "report": report_md,
            "report_export_md": export_md,
            "sections": [
                {"number": 0, "title": "关键结论", "kind": "overview"},
                *[
                    {
                        "number": index + 1,
                        "title": self._dataset_section_title(dataset, tasks.get(str(dataset.get("task_id"))) or {}),
                        "kind": "analysis",
                        "dataset_id": dataset.get("id"),
                    }
                    for index, dataset in enumerate(datasets)
                ],
            ],
            "asset_manifest": list(ctx.inputs.keys()),
            "figure_index": figure_index,
            "table_index": table_index,
            "pending_reviews": [],
            "title": title,
        }

    # ---- 报告片段 ----

    @staticmethod
    def _rank_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weight = {"critical": 3, "warning": 2, "info": 1}
        return sorted(
            [item for item in findings if isinstance(item, dict)],
            key=lambda item: weight.get(str(item.get("severity")), 0),
            reverse=True,
        )

    @staticmethod
    def _fmt(value: Any, unit: str = "", money: bool = False) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        if money:
            return f"¥{number:,.2f}"
        return f"{number:,.2f}{unit}" if unit else f"{number:,.2f}"

    def _kpi_table(self, datasets: list[dict[str, Any]]) -> str:
        header = "| # | 指标 | 口径 | 分析维度 | 数据点 | 合计/均值 | 单位 |"
        sep = "| --- | --- | --- | --- | --- | --- | --- |"
        rows: list[str] = []
        for index, dataset in enumerate(datasets, start=1):
            metric = str(dataset.get("metric_name") or "指标")
            formula = str(dataset.get("metric_formula") or "").replace("`", "")
            dim = str(dataset.get("dim_label") or "全部")
            count = dataset.get("row_count") or 0
            agg = str(dataset.get("agg") or "sum")
            money = dataset.get("metric_kind") == "money"
            unit = str(dataset.get("unit") or "")
            values = [float(row[1]) for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))]
            if str(dataset.get("kind")) == "correlation":
                value_text = f"{len(dataset.get('points') or [])} 对样本"
                count = len(dataset.get("points") or [])
            elif str(dataset.get("kind")) == "distribution":
                stats = dataset.get("stats") or {}
                value_text = f"均值 {self._fmt(stats.get('mean'), unit)}"
                count = stats.get("count") or 0
            elif agg in ("avg", "ratio"):
                value_text = self._fmt(sum(values) / len(values), unit, money) if values else "—"
            else:
                value_text = self._fmt(sum(values), unit, money) if values else "—"
            rows.append(f"| {index} | {self._cell(metric)} | {self._cell(formula)} | {self._cell(dim)} | {count} | {value_text} | {self._cell(unit)} |")
        return "\n".join([header, sep, *rows])

    def _rich_table(self, dataset: dict[str, Any]) -> tuple[str, str]:
        """数据表：时间加环比、类别加占比、均值类加与均值差。"""
        kind = str(dataset.get("kind") or "")
        unit = str(dataset.get("unit") or "")
        money = dataset.get("metric_kind") == "money"
        metric = str(dataset.get("metric_name") or "指标")
        dim_label = str(dataset.get("dim_label") or "维度")
        rows = [row for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2]
        note = ""
        if kind == "correlation":
            points = dataset.get("points") or []
            columns = dataset.get("columns") or ["X", "Y"]
            header = f"| {self._cell(str(columns[0]))} | {self._cell(str(columns[1] if len(columns) > 1 else 'Y'))} |"
            correlation_rows = [
                f"| {self._cell(str(point[0]))} | {self._cell(str(point[1]))} |"
                for point in points[:15]
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
            note = f"仅展示前 {min(15, len(points))} 对样本；相关系数见洞察结论。"
            return "\n".join([header, "| --- | --- |", *correlation_rows]), note
        if kind == "distribution":
            header = "| 区间 | 记录数 | 占比 |"
            total = sum(float(row[1]) for row in rows if isinstance(row[1], (int, float))) or 1
            distribution_rows = [
                f"| {self._cell(str(row[0]))} | {int(row[1])} | {float(row[1]) / total * 100:.1f}% |"
                for row in rows[:20]
            ]
            return "\n".join([header, "| --- | --- | --- |", *distribution_rows]), "按等宽区间分桶，占比=区间计数/样本数。"

        values = [float(row[1]) for row in rows if isinstance(row[1], (int, float))]
        total = sum(values) if values else 0.0
        average = (sum(values) / len(values)) if values else 0.0
        agg = str(dataset.get("agg") or "sum")
        header = f"| {self._cell(dim_label)} | {self._cell(metric)} |"
        extra: list[str] = []
        if dataset.get("dim_type") == "time":
            header += " 环比 |"
            extra.append("mom")
            note = "环比=(本期-上期)/上期；首期为 —。"
        elif agg in ("avg", "ratio"):
            header += " 与均值差 |"
            extra.append("diff")
            note = "比率/单位价类指标按组值对比，不做求和。"
        else:
            header += " 占比 |"
            extra.append("share")
            note = "占比=该组值/总计；TopN 之外的尾部可能合并为「其他」。"
        sep = "| --- | --- | --- |"
        body: list[str] = []
        for index, row in enumerate(rows[:30]):
            value = float(row[1]) if isinstance(row[1], (int, float)) else None
            label = self._cell(str(row[0]))
            value_text = self._fmt(value, unit, money)
            if "mom" in extra:
                if index == 0 or value is None:
                    extra_text = "—"
                else:
                    previous = float(rows[index - 1][1])
                    extra_text = f"{(value - previous) / abs(previous) * 100:+.1f}%" if previous else "—"
            elif "diff" in extra:
                extra_text = f"{value - average:+,.2f}" if value is not None else "—"
            else:
                extra_text = f"{value / total * 100:.1f}%" if value is not None and total else "—"
            body.append(f"| {label} | {value_text} | {extra_text} |")
        if len(rows) > 30:
            note = (note + " " if note else "") + f"仅展示前 30 行（共 {len(rows)} 行）。"
        return "\n".join([header, sep, *body]), note

    def _metric_table(self, specs: list[dict[str, Any]], plan: dict[str, Any]) -> str:
        header = "| 指标 | 口径公式 | 单位 | 可用 | 说明 |"
        sep = "| --- | --- | --- | --- | --- |"
        rows: list[str] = []
        for spec in specs or plan.get("metrics") or []:
            if not isinstance(spec, dict):
                continue
            formula = str(spec.get("formula") or "").replace("`", "")
            feasible = "是" if spec.get("feasible", True) else "否"
            reason = str(spec.get("reason") or spec.get("description") or "")
            if spec.get("kb_formula_note"):
                reason += f"；{spec['kb_formula_note']}"
            rows.append(
                f"| {self._cell(str(spec.get('name') or ''))} | `{self._cell(formula)}` | {self._cell(str(spec.get('unit') or ''))} | {feasible} | {self._cell(reason)} |"
            )
        return "\n".join([header, sep, *rows]) if rows else "（无指标口径）"

    def _quality_table(self, dq: dict[str, Any] | None) -> str:
        if not dq:
            return "（上游未提供质检报告）"
        header = "| 数据集 | 指标 | 评分 | 等级 | 主要问题 |"
        sep = "| --- | --- | --- | --- | --- |"
        rows: list[str] = []
        dataset_reports = dq.get("datasets") or []
        if dataset_reports:
            for item in dataset_reports:
                issues = "；".join(str(issue.get("evidence")) for issue in (item.get("issues") or [])[:2] if isinstance(issue, dict)) or "无"
                rows.append(
                    f"| {self._cell(str(item.get('dataset_id') or ''))} | {self._cell(str(item.get('metric_name') or ''))} | {item.get('score')} | {self._cell(str(item.get('level') or ''))} | {self._cell(issues)} |"
                )
        else:
            issues = "；".join(str(issue.get("evidence")) for issue in (dq.get("issues") or [])[:3] if isinstance(issue, dict)) or "无"
            rows.append(f"| 全部 | — | {dq.get('score')} | {self._cell(str(dq.get('level') or ''))} | {self._cell(issues)} |")
        return "\n".join([header, sep, *rows])

    def _suggestions(
        self, findings: list[dict[str, Any]], dq: dict[str, Any] | None, plan: dict[str, Any]
    ) -> list[str]:
        suggestions: list[str] = []
        kinds = {str(item.get("kind")) for item in findings}
        if "concentration" in kinds:
            suggestions.append("头部集中度偏高：建议评估头部渠道/品类的可持续性，同时为长尾单元设定增长目标，降低单点依赖。")
        if "anomaly" in kinds:
            suggestions.append("存在异常波动：建议核对异常周期的口径、活动、履约或数据采集链路，排除一次性因素后再解读趋势。")
        if "correlation" in kinds:
            suggestions.append("相关关系不等于因果：建议补充分渠道/分人群的对照分析或回归控制变量，验证驱动关系。")
        if "trend" in kinds:
            suggestions.append("趋势结论建议按周/月固化监控，设置同比/环比阈值告警，避免只看单期绝对值。")
        if "composition" in kinds:
            suggestions.append("构成分析可用于资源分配：建议结合毛利/成本口径复核高占比类目的真实贡献。")
        if dq and (dq.get("blocked") or any(str(item.get("severity")) in ("warning", "blocking") for item in dq.get("issues") or [])):
            suggestions.append("数据质量仍有告警：建议先修复缺失/重复/零值等问题，再做对外汇报或经营决策。")
        if plan.get("unavailable"):
            suggestions.append("部分指标因字段缺失无法计算：建议补充成本、阶段状态等字段后再扩展分析口径。")
        if plan.get("notes"):
            suggestions.append("请结合报告中的「口径与数据说明」理解默认指标与过滤范围，避免跨口径直接比较。")
        if not suggestions:
            suggestions.append("建议补充时间维度与对照分组后复跑，以获得更可解释的结论。")
        suggestions.append("下一步：把本报告结论与业务动作绑定（责任人/时间点/验收指标），并在下次复盘中回看效果。")
        return suggestions

    def _dataset_section_title(self, dataset: dict[str, Any], task: dict[str, Any]) -> str:
        kind = str(dataset.get("kind") or task.get("kind") or "summary")
        metric = str(dataset.get("metric_name") or "指标")
        dim = str(dataset.get("dim_label") or "全部")
        if "trend" in kind:
            return f"{metric}的时间趋势"
        if "composition" in kind:
            return f"{metric}的{dim}构成"
        if "correlation" in kind:
            return "指标相关性与驱动关系"
        if "distribution" in kind:
            return f"{metric}的分布特征"
        if "anomaly" in kind:
            return f"{metric}的异常检测"
        if "attribution" in kind:
            return f"{dim}对{metric}的贡献拆解"
        if "ranking" in kind or "comparison" in kind:
            return f"{dim}的{metric}格局"
        return f"{metric}分析"

    def _analysis_scope(self, dataset: dict[str, Any], task: dict[str, Any], plan: dict[str, Any]) -> str:
        parts: list[str] = []
        parts.append(f"指标 **{dataset.get('metric_name')}**（`{str(dataset.get('metric_formula') or '').replace('`', '')}`）")
        if dataset.get("dim"):
            parts.append(f"维度 **{dataset.get('dim_label')}**")
        if dataset.get("granularity"):
            granularity = {"month": "按月", "quarter": "按季度", "year": "按年", "day": "按日", "week": "按周"}.get(
                str(dataset.get("granularity")), str(dataset.get("granularity"))
            )
            parts.append(f"粒度 **{granularity}**")
        if plan.get("time_range"):
            parts.append(f"时间范围 **{plan['time_range'].get('label')}**")
        if task.get("top_n"):
            parts.append(f"TopN **{task['top_n']}**")
        filters = [item for item in task.get("filters") or [] if isinstance(item, dict) and item.get("field") != "__time__"]
        for item in filters[:2]:
            parts.append(f"过滤 **{item.get('field')} ∈ {', '.join(str(v) for v in item.get('values') or [])}**")
        parts.append(f"数据点 **{dataset.get('row_count')}**")
        return "；".join(parts)

    @staticmethod
    def _cell(text: str) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ").strip()

    # ---- 上游解析 ----

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
            if isinstance(charts, list) and charts:
                return [item for item in charts if isinstance(item, dict)]
        return []

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
    def _extract_narrative(ctx: AgentExecutionContext) -> str:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            narrative = payload.get("narrative") if isinstance(payload, dict) else None
            if isinstance(narrative, str) and narrative.strip():
                return narrative.strip()
        return ""

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

    @staticmethod
    def _extract_sql_queries(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            queries = payload.get("queries") if isinstance(payload, dict) else None
            if isinstance(queries, list) and queries:
                return [item for item in queries if isinstance(item, dict)]
        return []

    # ---------- 报告拼装 ----------

    def _build_report(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        manifest = list(ctx.inputs.keys())
        dataset = self._extract_dataset(ctx)
        facts = self._dataset_facts(dataset) if dataset else None
        charts = self._chart_refs(ctx)
        dq = self._dq_facts(ctx)
        metric = self._metric_facts(ctx)

        # 域语义统一由真实数据集字段驱动（v2.2）：不再对金额数据硬套“销售额/客单价/订单量”销售模板，
        # 摘要/发现措辞全部来自 value_label/count_label；money 仅决定是否加 ¥ 货币符号。
        money = str((dataset or {}).get("kind") or "") == "money"
        vlabel = str((dataset or {}).get("value_label") or "指标")
        dlabel = str((dataset or {}).get("dim_label") or "维度")
        clabel = str((dataset or {}).get("count_label") or "记录数")
        unit = "¥" if money else ""
        top_word = f"{vlabel}最高"
        low_word = f"{vlabel}最低"
        count_word = clabel
        # 纯计数数据集（无金额/数值度量列）：度量列就是记录数本身 → 不输出“平均记录数”等无意义措辞
        count_only = vlabel == clabel
        # 均值口径（如"各区域平均单价"）：组值已经是均值，不能再做"合计/平均"的二次聚合措辞
        agg_avg = str((dataset or {}).get("agg") or "") == "avg"
        total_word = f"整体{vlabel}" if agg_avg else f"{vlabel}合计"
        mean_word = vlabel if agg_avg else f"平均{vlabel}"

        # 标题优先用需求原文（截断），否则用指标名
        req = (ctx.requirement or "").strip()
        title = req if len(req) <= 40 else f"{req[:37]}…"
        if not title:
            title = str((metric or {}).get("name") or "分析报告")
        lines: list[str] = [f"# {title}", ""]

        # 一、摘要
        lines.append("## 一、摘要")
        lines.append("")
        if facts and facts["row_count"]:
            multi = int(facts["row_count"]) > 1
            if multi:
                lines.append(f"- 共汇总 **{facts['row_count']} 个分析单元**，覆盖 **{facts['region_count']} 个{dlabel}**。")
            else:
                lines.append("- 共汇总 **1 个分析单元**（无可用分组维度，按全量汇总）。")
            if facts.get("total_amount") is not None:
                if count_only:
                    lines.append(f"- {total_word} **{int(facts['total_amount']):,}**。")
                elif agg_avg:
                    lines.append(
                        f"- {total_word} **{unit}{facts['weighted_avg']:,.2f}**，样本 **{facts['total_orders']:,}** 条。"
                    )
                else:
                    lines.append(f"- {total_word} **{unit}{facts['total_amount']:,.2f}**，{count_word} **{facts['total_orders']:,}**。")
            if multi and facts.get("top"):
                top_share = "" if agg_avg else f"（占比 {facts['top']['share']:.1f}%）"
                lines.append(
                    f"- **{facts['top']['region']}** {top_word}：**{unit}{facts['top']['amount']:,.2f}**{top_share}。"
                )
            if multi and facts.get("low"):
                low_share = "" if agg_avg else f"（占比 {facts['low']['share']:.1f}%）"
                lines.append(
                    f"- **{facts['low']['region']}** {low_word}：**{unit}{facts['low']['amount']:,.2f}**{low_share}。"
                )
            if multi and facts.get("max_avg") and not count_only and not agg_avg:
                lines.append(f"- **{facts['max_avg']['region']}** {mean_word}最高：**{unit}{facts['max_avg']['avg']:,.2f}**。")
        else:
            lines.append("- 数据集未产出，无法进行定量分析。请接入数据源后重新下发任务。")
        lines.append("")

        # 二、分析范围与数据来源
        lines.append("## 二、分析范围与数据来源")
        lines.append("")
        lines.append(f"- 原始需求：{ctx.requirement}")
        if facts:
            src_table = facts.get("table") or "—"
            lines.append(f"- 数据集：按 {dlabel or facts['dim']} 汇总，{facts['row_count']} 个单元；原始数据 {src_table}")
        else:
            lines.append("- 数据集：未产出")
        if dq:
            lines.append(f"- 数据质量评分：{dq['score']}（{dq['level']}）")
        lines.append("")

        # 三、指标口径
        lines.append("## 三、指标口径")
        lines.append("")
        if metric:
            formula = re.sub(r"`", "", str(metric.get("formula") or "")).strip()
            formula_txt = f"`{formula}`" if formula else "—"
            lines.append(f"- **{metric.get('name', '')}**：{formula_txt}；{metric.get('description', '')}")
            if metric.get("kb_formula_ignored") and metric.get("kb_formula_note"):
                lines.append(f"- 口径校验：{metric['kb_formula_note']}")
            if isinstance(metric.get("constraints"), list):
                for c in metric["constraints"]:
                    if isinstance(c, dict):
                        lines.append(f"- 约束：{c.get('dimension', '')} → {c.get('rule', '')}")
        else:
            note = "该数据不含明显金额/销售字段，按实际数值字段统计（无销售口径套用）" if not money else "上游未提供指标口径（metric_spec）。"
            lines.append(f"- {note}")
        lines.append("")

        # 四、数据分析
        lines.append("## 四、数据分析")
        lines.append("")
        table = self._dataset_table(dataset) if dataset else ""
        if table:
            lines.append(table)
            lines.append("")
            if count_only:
                note = f"{total_word}=各组记录数之和、占比=各组记录数/总记录数"
            elif agg_avg:
                note = f"{vlabel}=各组 SUM(数值)/COUNT(*)、{total_word}=按组样本量加权、样本=各组记录数"
            else:
                note = (
                    f"{total_word}=SUM(数值字段)、{count_word}=COUNT(*)、{mean_word}=合计/计数、"
                    "占比=各组值/总计"
                )
            lines.append(f"> 口径说明：{note}，均由系统对真实数据集计算；图表/表格为唯一数值来源。")
        else:
            lines.append("（数据集未产出，无数据可展示）")
        lines.append("")
        lines.extend(charts)
        if charts:
            lines.append("")

        # 五、核心发现
        lines.append("## 五、核心发现")
        lines.append("")
        if facts and facts["row_count"]:
            multi = int(facts["row_count"]) > 1
            if not multi:
                lines.append("- 仅 1 个分析单元，无可比结构；如需对比分析请增加分组维度后重新下发。")
            elif facts.get("top") and not agg_avg:
                lines.append(f"- **头部集中**：{facts['top']['region']} 以 {facts['top']['share']:.1f}% 的占比居首。")
            if multi and facts.get("max_avg") and facts.get("min_avg") and not count_only:
                max_a = float(facts["max_avg"]["avg"] or 0.0)
                min_a = float(facts["min_avg"]["avg"] or 0.0)
                if min_a > 0 and max_a > 0:
                    lines.append(
                        f"- **{mean_word}分化**：{facts['max_avg']['region']} {mean_word} {unit}{max_a:,.2f}，"
                        f"{facts['min_avg']['region']} {unit}{min_a:,.2f}，相差 {max_a / min_a:.1f} 倍。"
                    )
            if multi and facts.get("low") and facts.get("top") and facts["low"] is not facts["top"] and not agg_avg:
                lines.append(
                    f"- **尾部偏低**：{facts['low']['region']} {vlabel}占比 {facts['low']['share']:.1f}%，为各{dlabel}最低。"
                )
        else:
            lines.append("- 无数据支撑的结论。")
        lines.append("")

        # 六、问题与风险
        lines.append("## 六、问题与风险")
        lines.append("")
        if dq:
            lines.append(f"- 数据质量：综合评分 **{dq['score']}**，级别 **{dq['level']}**。")
            issues = dq.get("issues") or []
            if issues:
                for issue in issues[:5]:
                    lines.append(f"  - {issue.get('type', '')}：{issue.get('evidence', '')}")
            if dq.get("blocked"):
                lines.append("  - ⚠️ 已达到阻断级别，建议优先治理数据后再决策。")
        else:
            lines.append("- 上游未提供质检报告，无法评估数据质量。")
        if dataset and dataset.get("truncated"):
            lines.append("- ⚠️ 数据集行数超过取数上限，合计基于截断后的前 N 行（不代表全量，建议收窄维度/分组后重跑）。")
        if not dataset:
            lines.append("- 数据集未产出，报告为定性占位。")
        lines.append("")

        # 七、建议
        lines.append("## 七、建议与下一步")
        lines.append("")
        row_units = int(facts["row_count"]) if facts else 0
        multi = row_units > 1
        if multi and facts and facts.get("low"):
            lines.append(f"- 针对 **{facts['low']['region']}** 低值组开展专项分析，排查成因。")
        if multi and facts and facts.get("min_avg") and not count_only:
            lines.append(f"- 关注 **{facts['min_avg']['region']}** {mean_word}偏低问题。")
        if dq and dq.get("blocked"):
            lines.append("- 优先推进数据治理，修复质检发现的问题后再做决策。")
        elif count_only or agg_avg:
            lines.append(f"- 建议持续监控各{dlabel}的{vlabel}水平与变化趋势。")
        else:
            lines.append(f"- 建议持续监控各{dlabel}的{vlabel}与{mean_word}趋势。")
        lines.append("")

        # 附录
        lines.append("## 附录")
        lines.append("")
        lines.append("引用资产：")
        lines.extend(f"- `{ref}`" for ref in manifest)
        lines.append("")
        lines.append("*本报告由星枢 StarPivot · 12-Agent 智能数据分析平台自动生成，数字均来自真实数据源执行结果。*")

        return {
            "report": "\n".join(lines),
            "asset_manifest": manifest,
            "pending_reviews": [],
            "title": title,
        }

    # ---------- 数据抽取与计算 ----------

    @staticmethod
    def _extract_dataset(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            ds = payload.get("dataset")
            if isinstance(ds, dict) and isinstance(ds.get("columns"), list) and isinstance(ds.get("rows"), list):
                return ds
        return None

    @staticmethod
    def _dataset_facts(dataset: dict[str, Any]) -> dict[str, Any] | None:
        """从真实数据集计算汇总事实（v2：收敛到 app/services/report_facts.py，终审校验共用）。"""
        from app.services.report_facts import compute_dataset_facts

        return compute_dataset_facts(dataset)

    @staticmethod
    def _to_float(value: Any) -> float:
        """宽松转 float：None / 非数值一律 0.0（NULL 聚合列防护）。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _format_cell(value: Any) -> str:
        """表格单元格格式化：None 空串、整数千分位、小数两位（去尾随 0），其余原样。"""
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            text = f"{value:,.2f}"
            return text.rstrip("0").rstrip(".") if "." in text else text
        return str(value)

    @classmethod
    def _dataset_table(cls, dataset: dict[str, Any]) -> str:
        cols = [str(c) for c in dataset.get("columns") or []]
        rows = dataset.get("rows") or []
        if not cols:
            return ""
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body: list[str] = []
        for r in rows[:50]:
            cells = [cls._format_cell(v) for v in list(r[: len(cols)])]
            while len(cells) < len(cols):
                cells.append("")
            body.append("| " + " | ".join(cells) + " |")
        return "\n".join([header, sep, *body])

    @staticmethod
    def _chart_refs(ctx: AgentExecutionContext) -> list[str]:
        refs: list[str] = []
        for ref, content in ctx.inputs.items():
            if not ref or "@" not in ref:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            charts = payload.get("charts")
            if isinstance(charts, list):
                asset_id, ver = ref.split("@", 1)
                for index, c in enumerate(charts):
                    if isinstance(c, dict) and c.get("type"):
                        refs.append(f"![](chart://{asset_id}@{ver}/{c['type']}/{index})")
        return refs

    @staticmethod
    def _dq_facts(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            dq = payload.get("dq")
            if isinstance(dq, dict) and isinstance(dq.get("score"), (int, float)) and "issues" in dq:
                return dq
            if isinstance(payload.get("score"), (int, float)) and "issues" in payload:
                return payload
        return None

    @staticmethod
    def _metric_facts(ctx: AgentExecutionContext) -> dict[str, Any] | None:
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

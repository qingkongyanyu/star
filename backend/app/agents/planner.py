"""Planner（天枢，v3）：需求 → 意图槽位 → 分析任务 → DAG。

v3 变化（对应「分析不能千篇一律」）：
- 不再只做「节点剪枝 + per-node instruction」，而是先产出结构化 **AnalysisPlan**
  （分析类型多标签 / 指标 / 维度 / 时间粒度 / 时间范围 / 过滤 / TopN / 子任务）；
- 规则 NLU 打底（无 LLM 也可用），LLM 只补充规则未识别的槽位（且必须过 schema 校验）；
- 计划随任务落库 `task.plan_json`，供指标定义/取数/可视化/洞察/报告/终审全链消费；
- DAG 固定保留完整分析链（含新增的洞察分析层），差异化体现在任务、图表与报告结构。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import _extract_json
from app.infra.db import to_thread
from app.infra.llm import MockLLM, get_llm
from app.models.task_repo import Task
from app.repositories.kb_repo import KbRepo
from app.repositories.memory_repo import MemoryRepo
from app.services.analysis_plan import AnalysisPlan, plan_analysis, plan_instruction
from app.services.dag_service import build_dag, decompose
from app.services.intent import classify_intent
from app.services.metric_catalog import build_column_profiles, build_metric_catalog
from app.services.schema_service import load_schema

logger = logging.getLogger(__name__)

_PLAN_TIMEOUT = 20  # LLM 槽位抽取超时秒数

_SYSTEM_PROMPT = """你是【Planner 天枢_调度】，把数据分析需求拆成结构化槽位。
只输出 JSON：
{"analysis_types": ["trend"|"comparison"|"ranking"|"composition"|"distribution"|"anomaly"|"attribution"|"correlation"|"prediction"|"quality"|"descriptive"],
 "metrics": ["需求里出现的业务指标名"],
 "dimensions": ["需求里出现、且真实存在的字段名"],
 "time_range": {"kind": "year|month|relative|quarter|month_offset", "year": 2025, "month": 1, "amount": 6, "unit": "月", "offset": 0, "label": "..."},
 "filters": [{"field": "字段名", "values": ["值"]}],
 "top_n": 10,
 "chart_preference": ["line"|"bar"|"pie"|"donut"|"scatter"|"heatmap"|"waterfall"|"treemap"],
 "plan_summary": "一句话拆解说明"}
规则：
1. 只能使用给定字段与能力，不得发明字段/指标；
2. metrics/dimensions 用中文业务词（如「销售额」「大区」），系统会映射到真实列；
3. 不确定的槽位留空，不要编造；time_range/filters 只填需求里明确出现的；
4. 不要输出 DAG/节点，只输出槽位。"""


class PlannerAgent:
    """任务拆解器（非 DAG 节点）。"""

    @staticmethod
    async def plan(session: Session, *, task: Task, data_source: dict[str, Any] | None) -> dict[str, Any]:
        """为任务产出 AnalysisPlan + DAG。返回 {nodes, edges, plan_source, plan_summary, intent, analysis_plan}。"""
        requirement = (task.requirement or "").strip() or "通用数据分析"
        intent = classify_intent(requirement)
        schema = await load_schema(data_source, requirement)
        base_plan = plan_analysis(requirement, schema)

        hints: dict[str, Any] | None = None
        llm_summary = ""
        try:
            hints = await PlannerAgent._llm_slots(session, requirement, schema, base_plan, task)
            if hints:
                llm_summary = str(hints.get("plan_summary") or "")
        except Exception:  # LLM 仅做补充，失败不影响规则计划
            logger.warning("Planner LLM 槽位抽取失败，使用规则计划", exc_info=True)
            hints = None

        plan: AnalysisPlan = base_plan
        plan_source = "template"
        if hints:
            # 规则打底 + LLM 补充 → hybrid（规则优先，LLM 只补未识别槽位）
            plan = plan_analysis(requirement, schema, llm_hints=hints, plan_source="hybrid")
            plan_source = "hybrid"

        plan_summary = llm_summary or plan.summary
        specs = PlannerAgent._node_specs(plan)
        nodes, edges = build_dag(task.task_id, specs)
        if not nodes:  # 兜底：任何异常都退回标准链路
            nodes, edges = decompose(requirement, task.task_id, (data_source or {}).get("type"))
            plan_source = "template"
        return {
            "nodes": nodes,
            "edges": edges,
            "plan_source": plan_source,
            "plan_summary": plan_summary,
            "intent": intent.to_dict(),
            "analysis_plan": plan.to_dict(),
        }

    # ---- DAG 节点与指令 ----

    @staticmethod
    def _node_specs(plan: AnalysisPlan) -> list[tuple[str, str | None]]:
        """完整分析链 + 按意图追加 trace；每个节点带面向职责的指令。"""
        base = plan_instruction(plan)
        tasks = "；".join(f"{t['kind']}·{t['metric_name']}·{t['dim_label']}" for t in plan.tasks[:4]) or "无"
        metrics = "、".join(f"{m['name']}" for m in plan.metrics[:6]) or "无"
        dims = "、".join(d["name"] for d in plan.dimensions[:4]) or "无"
        specs: list[tuple[str, str | None]] = [
            ("metric_definition", f"按计划定义指标口径：{metrics} | {base}"),
            ("metadata_profiling", f"探查数据源真实结构，供指标/取数列名对齐：{base}"),
            ("sql_extract", f"按子任务取数：{tasks} | 维度：{dims} | {base}"),
            ("quality_check", f"对每个数据集做质检与跨集一致性校验：{base}"),
            ("visualization", f"按数据集特征与特征工程建议选择图表（时间→折线/类别→横向柱/构成→环图或树图/相关→散点/归因→瀑布）：{base}"),
            ("insight_analysis", f"对 datasets + charts 做趋势/对比/构成/异常/相关/归因分析，产出带证据的关键发现：{base}"),
            ("report_writing", f"按意图动态组织章节，引用 findings/charts/datasets 写图文表并茂报告：{base}"),
            ("review", f"逐数据集核对报告数字、图表引用与口径一致性：{base}"),
        ]
        if any(word in plan.requirement for word in ("溯源", "追责", "根因", "为什么", "血缘")):
            specs.append(("trace", "对本次分析做血缘溯源与问题归因"))
        return specs

    # ---- LLM 槽位（可失败，返回 None） ----

    @staticmethod
    async def _llm_slots(
        session: Session,
        requirement: str,
        schema: dict[str, Any],
        base_plan: AnalysisPlan,
        task: Task,
    ) -> dict[str, Any] | None:
        llm = get_llm()
        if isinstance(llm, MockLLM):
            return None
        kb_rows = await to_thread(KbRepo.list_all, session, 100)
        memories = await to_thread(
            MemoryRepo.search,
            session,
            scope="collective",
            agent_id=None,
            keyword=requirement if len(requirement) >= 2 else None,
            top_k=5,
        )
        columns = build_column_profiles(schema)
        catalog = build_metric_catalog(columns)
        column_lines = "\n".join(
            f"- {column.name}（{column.type}；语义={column.role}；样本={', '.join(column.samples[:3]) or '无'}）"
            for column in columns[:40]
        )
        metric_lines = "\n".join(
            f"- {metric.name}（{metric.formula}；可用={metric.feasible}）" for metric in catalog[:20]
        )
        kb_lines = "\n".join(
            f"- {kb.metric_name}：{(kb.formula or kb.description or '')[:120]}" for kb in kb_rows[:10] if getattr(kb, "metric_name", None)
        )
        memory_lines = "\n".join(f"- {(m.content or '')[:120]}" for m in memories[:5] if getattr(m, "content", None))
        user = (
            f"需求：{requirement}\n"
            f"数据源类型：{schema.get('source') or 'unknown'}\n"
            f"表：{schema.get('table') or '无'}\n"
            f"真实字段：\n{column_lines or '（无）'}\n"
            f"可用指标候选：\n{metric_lines or '（无）'}\n"
            f"规则初步判断：{base_plan.summary}\n"
            f"指标知识库口径：\n{kb_lines or '（无）'}\n"
            f"历史协作经验：\n{memory_lines or '（无）'}\n"
            "请只输出槽位 JSON。"
        )
        raw = await asyncio.wait_for(
            llm.complete(
                [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}]
            ),
            timeout=_PLAN_TIMEOUT,
        )
        text = _extract_json(raw)
        if text is None:
            return None
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        allowed = {"analysis_types", "metrics", "dimensions", "time_range", "filters", "top_n", "chart_preference", "plan_summary"}
        slots = {key: parsed[key] for key in allowed if key in parsed}
        # 校验维度/指标必须来自真实字段或候选（防止 LLM 发明列）
        known_columns = {column.name for column in columns}
        if isinstance(slots.get("dimensions"), list):
            slots["dimensions"] = [name for name in slots["dimensions"] if name in known_columns]
        # 时间范围/过滤必须能在需求原文里找到依据，否则视为 LLM 幻觉（曾把"趋势"误判成"2025-01"）
        time_range = slots.get("time_range")
        if isinstance(time_range, dict):
            label = str(time_range.get("label") or "")
            year = str(time_range.get("year") or "")
            month_value = time_range.get("month")
            month = f"{int(month_value):02d}" if isinstance(month_value, int) else ""
            if not any(token and token in requirement for token in (label, year, month)):
                slots.pop("time_range", None)
        filters = slots.get("filters")
        if isinstance(filters, list):
            cleaned_filters: list[dict[str, Any]] = []
            for item in filters:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field") or "")
                values = [str(value) for value in item.get("values") or []]
                if field in known_columns and values and all(value in requirement for value in values):
                    cleaned_filters.append(item)
            if cleaned_filters:
                slots["filters"] = cleaned_filters
            else:
                slots.pop("filters", None)
        return slots or None

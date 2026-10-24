"""Planner（天枢）v3 测试：结构化槽位 → AnalysisPlan → DAG。

覆盖：规则兜底（无 LLM）、LLM 槽位补充、指标目录映射、trace 追加、计划落库给下游。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agents import planner
from app.agents.planner import PlannerAgent
from app.models.task_repo import Task


def _task(requirement: str) -> Task:
    return Task(
        task_id="t-plan-test",
        req_id="r1",
        requester="pytest",
        task_type="analysis",
        requirement=requirement,
        status="PENDING",
    )


def _schema() -> dict[str, Any]:
    return {
        "table": "电商数据.csv",
        "tables": [],
        "columns": [
            {"name": "订单号", "type": "string"},
            {"name": "下单时间", "type": "date"},
            {"name": "地区", "type": "string"},
            {"name": "商品类目", "type": "string"},
            {"name": "实付金额", "type": "float"},
            {"name": "成本", "type": "float"},
        ],
        "sample_rows": [
            ["A1", "2025-01-05 10:00:00", "华东", "数码", "100", "60"],
            ["A2", "2025-02-08 11:00:00", "华南", "食品", "200", "120"],
            ["A3", "2025-03-12 12:00:00", "华东", "数码", "150", "90"],
            ["A4", "2025-04-20 13:00:00", "华北", "食品", "300", "180"],
        ],
        "row_count": 4,
        "notes": "test",
        "source": "file",
    }


def test_plan_template_without_llm(monkeypatch: Any) -> None:
    async def _no_hints(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(PlannerAgent, "_llm_slots", staticmethod(_no_hints))
    monkeypatch.setattr(planner, "load_schema", lambda *args, **kwargs: _async_value(_schema()))
    result = asyncio.run(PlannerAgent.plan(object(), task=_task("对比各大区的销售额"), data_source={"type": "file"}))
    assert result["plan_source"] == "template"
    types = [node.agent_type for node in result["nodes"]]
    assert types == [
        "metric_definition", "metadata_profiling", "sql_extract", "quality_check",
        "visualization", "insight_analysis", "report_writing", "review",
    ]
    assert all(node.instruction for node in result["nodes"])  # v3：模板路径也有定向指令
    plan = result["analysis_plan"]
    assert plan["metrics"][0]["name"] in ("实付金额", "销售额")
    assert any(task["dim"] == "地区" for task in plan["tasks"])


def test_plan_uses_llm_slots(monkeypatch: Any) -> None:
    async def _hints(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "analysis_types": ["trend", "anomaly"],
            "metrics": ["实付金额"],
            "dimensions": ["地区"],
            "plan_summary": "LLM 识别为趋势+异常",
        }

    monkeypatch.setattr(PlannerAgent, "_llm_slots", staticmethod(_hints))
    monkeypatch.setattr(planner, "load_schema", lambda *args, **kwargs: _async_value(_schema()))
    result = asyncio.run(PlannerAgent.plan(object(), task=_task("分析各月实付金额走势并看异常"), data_source={"type": "file"}))
    assert result["plan_source"] == "hybrid"
    assert result["plan_summary"] == "LLM 识别为趋势+异常"
    labels = result["analysis_plan"]["intent"]["labels"]
    assert "trend" in labels and "anomaly" in labels
    assert any("trend" in task["kind"] for task in result["analysis_plan"]["tasks"])


def test_plan_appends_trace_on_trace_intent(monkeypatch: Any) -> None:
    async def _no_hints(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(PlannerAgent, "_llm_slots", staticmethod(_no_hints))
    monkeypatch.setattr(planner, "load_schema", lambda *args, **kwargs: _async_value(_schema()))
    result = asyncio.run(PlannerAgent.plan(object(), task=_task("按区域分析并溯源追责"), data_source={"type": "file"}))
    assert [node.agent_type for node in result["nodes"]][-1] == "trace"


def test_plan_handles_missing_schema(monkeypatch: Any) -> None:
    async def _no_hints(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(PlannerAgent, "_llm_slots", staticmethod(_no_hints))
    result = asyncio.run(PlannerAgent.plan(object(), task=_task("随便分析一下"), data_source=None))
    assert result["plan_source"] == "template"
    assert [node.agent_type for node in result["nodes"]][:2] == ["metric_definition", "metadata_profiling"]


async def _async_value(value: Any) -> Any:
    return value

"""InsightAnalyst（天机）单元测试：从 datasets/charts 提炼带证据的发现。"""
from __future__ import annotations

import asyncio
import json

from app.agents.base import AgentExecutionContext
from app.agents.insight_analyst import InsightAnalystAgent


def _ctx(inputs: dict[str, str]) -> AgentExecutionContext:
    return AgentExecutionContext(
        node_id="n",
        task_id="t",
        requester="u",
        role="admin",
        capability="insight_analysis",
        token="x",
        requirement="对比各大区销售额",
        inputs=inputs,
        plan={"tasks": [{"id": "a1"}], "metrics": [{"name": "销售额"}]},
    )


def test_execute_produces_findings_and_chart_refs() -> None:
    dataset = {
        "id": "da1",
        "kind": "comparison",
        "metric_name": "销售额",
        "metric_kind": "money",
        "unit": "元",
        "dim_label": "地区",
        "dim_type": "category",
        "agg": "sum",
        "columns": ["地区", "销售额"],
        "rows": [["华东", 60.0], ["华南", 30.0], ["华北", 10.0]],
        "row_count": 3,
    }
    charts = [{"id": "c1", "type": "bar_horizontal", "dataset_id": "da1", "title": "地区对比"}]
    ctx = _ctx({"sql@1": json.dumps({"dataset": dataset}), "viz@1": json.dumps({"charts": charts})})
    result = asyncio.run(InsightAnalystAgent().execute(ctx, deps=None))
    assert result["findings"]
    assert result["findings"][0]["dataset_id"] == "da1"
    assert result["findings"][0]["chart_ids"] == ["c1"]
    assert result["summary"]
    assert result["headline"]


def test_execute_handles_multiple_datasets() -> None:
    first = {
        "id": "da1",
        "kind": "trend",
        "metric_name": "销售额",
        "dim_label": "月份",
        "dim_type": "time",
        "agg": "sum",
        "rows": [["2025-01", 100.0], ["2025-02", 120.0], ["2025-03", 90.0]],
        "row_count": 3,
    }
    second = {
        "id": "da2",
        "kind": "composition",
        "metric_name": "销售额",
        "dim_label": "品类",
        "dim_type": "category",
        "agg": "sum",
        "rows": [["A", 80.0], ["B", 20.0]],
        "row_count": 2,
    }
    ctx = _ctx({"sql@1": json.dumps({"datasets": [first, second]})})
    result = asyncio.run(InsightAnalystAgent().execute(ctx, deps=None))
    kinds = {item["kind"] for item in result["findings"]}
    assert "trend" in kinds and "composition" in kinds
    assert result["dataset_count"] == 2


def test_empty_inputs_returns_empty_findings() -> None:
    ctx = _ctx({})
    result = asyncio.run(InsightAnalystAgent().execute(ctx, deps=None))
    assert result["findings"] == []
    assert result["summary"]

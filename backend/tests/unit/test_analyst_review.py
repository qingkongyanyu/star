"""AnalystReview / report_facts 测试（P1.c：诚实降级 + 数字校验）。"""
from __future__ import annotations

from app.agents.analyst_review import AnalystReviewAgent
from app.agents.base import AgentExecutionContext


def _ctx(inputs: dict[str, str]) -> AgentExecutionContext:
    return AgentExecutionContext(
        node_id="n",
        task_id="t",
        requester="u",
        role="admin",
        capability="review",
        token="x",
        requirement="按区域分析销售额",
        inputs=inputs,
    )


def test_fallback_is_honest_rejected() -> None:
    agent = AnalystReviewAgent()
    out = agent.fallback(_ctx({}))
    assert out["verdict"] == "rejected"  # 不再静默 approved


def test_execute_without_report_rejects() -> None:
    import asyncio

    agent = AnalystReviewAgent()
    out = asyncio.run(agent.execute(_ctx({}), deps=None))  # 无报告 → 拒绝（不可评审）
    assert out["verdict"] == "rejected"
    assert "无报告" in out["comments"]


def test_execute_with_report_but_no_dataset_rejects_honestly() -> None:
    import asyncio

    agent = AnalystReviewAgent()
    ctx = _ctx(
        {
            "report@1": '{"report": "# 报告\\n总销售额 9999", "title": "分析报告"}',
        }
    )
    out = asyncio.run(agent.execute(ctx, deps=None))  # 无可重算数据集 → 无法核对数字 → 诚实 rejected
    assert out["verdict"] == "rejected"
    assert "数据集" in out["comments"]


def test_execute_with_dataset_and_mockllm_uses_deterministic_review() -> None:
    """未配置 LLM 时，报告数字与数据集一致且结构完整 → 确定性终审通过。"""
    import asyncio

    agent = AnalystReviewAgent()
    report = (
        "# 报告\\n\\n## 一、关键结论\\n\\n- 华东 100.0，总销售额 150.0，订单量 15\\n\\n"
        "**表 1 · 核心指标总览**\\n\\n| 指标 | 合计 |\\n| --- | --- |\\n| 销售额 | 150.0 |\\n\\n"
        "## 二、地区格局\\n\\n- 华东领先\\n\\n## 三、建议\\n\\n- 保持监控"
    )
    ctx = _ctx(
        {
            "sql@1": '{"dataset": {"columns": ["region", "order_count", "total_amount"],'
            ' "rows": [["华东", 10, 100.0], ["华北", 5, 50.0]]}}',
            "report@1": '{"report": "' + report + '", "title": "分析报告"}',
        }
    )
    out = asyncio.run(agent.execute(ctx, deps=None))
    assert out["verdict"] == "approved"
    assert "确定性" in out["comments"]


def test_verify_numbers_detects_missing_total() -> None:
    dataset = {
        "metric_name": "销售额",
        "unit": "元",
        "agg": "sum",
        "kind": "comparison",
        "dim_type": "category",
        "columns": ["地区", "销售额"],
        "rows": [["华东", 100.0], ["华北", 50.0]],
        "row_count": 2,
    }
    report_ok = "华东销售额最高 100.00，华北 50.00"
    _facts, issues = AnalystReviewAgent._verify_numbers(report_ok, dataset)
    assert issues == []  # 数字在正文出现 → 无 mismatch

    report_missing = "华东领跑，但报告没写具体数字"
    _facts, issues = AnalystReviewAgent._verify_numbers(report_missing, dataset)
    assert any("最高" in issue for issue in issues)  # 数字缺失被检出

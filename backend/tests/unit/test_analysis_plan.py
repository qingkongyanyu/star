"""AnalysisPlan（v3 意图拆解）单元测试：维度顺序、构成维度、相关任务。"""
from __future__ import annotations

from app.services.analysis_plan import plan_analysis


def _schema(columns: list[tuple[str, str]], rows: list[list[str]]) -> dict:
    return {
        "table": "t",
        "columns": [{"name": name, "type": kind} for name, kind in columns],
        "sample_rows": rows,
        "row_count": len(rows),
    }


ECOMMERCE = _schema(
    [
        ("订单号", "string"),
        ("下单时间", "datetime"),
        ("地区", "string"),
        ("商品类目", "string"),
        ("实付金额", "float"),
        ("应付金额", "float"),
        ("成本", "float"),
    ],
    [
        ["A1", "2025-01-05 10:00:00", "华东", "数码家电", "100", "120", "60"],
        ["A2", "2025-02-05 10:00:00", "华南", "食品生鲜", "200", "220", "120"],
        ["A3", "2025-03-05 10:00:00", "华东", "数码家电", "150", "160", "90"],
    ],
)

MYSQL = _schema(
    [
        ("order_no", "string"),
        ("ym", "string"),
        ("region", "string"),
        ("channel", "string"),
        ("amount", "float"),
    ],
    [
        ["SO1", "2025-01", "华东", "线上", "1000"],
        ["SO2", "2025-02", "华南", "线下", "2000"],
    ],
)


def test_composition_uses_nearest_dimension_and_comparison_uses_other() -> None:
    plan = plan_analysis("各商品类目的销售额占比，找出贡献最高的类目，并分析地区差异", ECOMMERCE)
    metrics = [item["name"] for item in plan.metrics]
    assert "实付金额" in metrics
    composition = [task for task in plan.tasks if "composition" in task["kind"]]
    assert composition and composition[0]["dim"] == "商品类目"
    comparison = [task for task in plan.tasks if "comparison" in task["kind"] or "ranking" in task["kind"]]
    assert any(task["dim"] == "地区" for task in comparison)


def test_english_column_composition_dimension() -> None:
    plan = plan_analysis("按地区看销售额趋势，并分析渠道构成", MYSQL)
    assert plan.metrics and plan.metrics[0]["name"] == "销售额"
    composition = [task for task in plan.tasks if "composition" in task["kind"]]
    assert composition and composition[0]["dim"] == "channel"
    # 点名了地区 + 趋势 → 额外补一个地区对比
    assert any(task["dim"] == "region" for task in plan.tasks)


def test_correlation_uses_time_bucket_when_time_available() -> None:
    plan = plan_analysis("销售额和订单量有什么关系", ECOMMERCE)
    assert plan.metrics[0]["name"] == "订单量"
    correlation = [task for task in plan.tasks if "correlation" in task["kind"]]
    assert len(correlation) == 1
    assert correlation[0]["dim_type"] == "time"
    assert correlation[0]["granularity"] == "month"
    assert correlation[0]["secondary_metric_id"]


def test_correlation_only_does_not_add_noise_comparisons() -> None:
    plan = plan_analysis("销售额和订单量有什么关系", ECOMMERCE)
    assert len(plan.tasks) == 1


def test_time_trend_without_dimension_has_no_extra_comparison() -> None:
    plan = plan_analysis("分析 2025 年各月实付金额的走势", ECOMMERCE)
    assert [task["kind"] for task in plan.tasks] == ["trend"]
    # 「2025 年各月」是月粒度：年份是范围，不能把「年」当成聚合粒度
    assert plan.tasks[0]["granularity"] == "month"
    assert plan.time_range == {"kind": "year", "year": 2025, "label": "2025 年"}
    # 字段真实存在 → 不应出现「实付金额不可用」的误报
    assert all(item["name"] != "实付金额" for item in plan.unavailable)


def test_missing_requested_metric_is_reported_honestly() -> None:
    plan = plan_analysis("分析 2025 年各月实付金额的走势", UNIT_PRICE)
    assert any(item["name"] == "实付金额" and item["feasible"] is False for item in plan.unavailable)


def test_attribution_intent_adds_contribution_task() -> None:
    plan = plan_analysis("对比各大区的销售额，并溯源根因", ECOMMERCE)
    kinds = [task["kind"] for task in plan.tasks]
    assert "attribution" in kinds
    assert "comparison" in kinds or "ranking" in kinds
    assert plan.intent["evidence"], "意图应记录原文证据词（可解释性）"


UNIT_PRICE = _schema(
    [
        ("区域", "string"),
        ("单价", "float"),
    ],
    [["华东", "12000"], ["华南", "15000"]],
)


def test_unit_price_does_not_get_meaningless_sum_task() -> None:
    plan = plan_analysis("各区域平均单价对比，找出最高与最低", UNIT_PRICE)
    metric_names = [metric["name"] for metric in plan.metrics]
    assert "平均单价" in metric_names
    assert "单价合计" not in metric_names
    assert all("单价合计" not in task["metric_name"] for task in plan.tasks)


def test_money_columns_are_not_dimensions() -> None:
    plan = plan_analysis("对比各商品类目的销售额和毛利率，找出销售额高但毛利率低的类目", ECOMMERCE)
    assert all(task["dim"] != "实付金额" for task in plan.tasks)
    assert all(task["dim"] != "销售额" for task in plan.tasks)
    assert any(task["dim"] == "商品类目" for task in plan.tasks)
    assert any(metric["name"] == "毛利率" for metric in plan.metrics)

"""取数口径 / 质检 / 报告事实 / 终审 的回归测试（纯函数，无 DB/LLM）。

覆盖本轮修复：
1. CSV 金额列识别（中文合计语义优先于单价）与聚合语义（平均 → AVG）；
2. CSV 维度识别（大区/战区 → 地区）；
3. 质检新增判据（零值/离群）与评分扣分自洽；
4. 报告事实在 AVG 口径下不再二次除计数；
5. 无 LLM 时终审的确定性裁决（数字核对一致才 approved）；
6. 特征工程透传数据集语义（kind/value_label/agg 不丢）；
7. 指标知识库公式的列存在性校验；
8. 指标口径与取数列对齐（需求点名 + AVG 语义 + 维度中文名）。
"""
from __future__ import annotations

import pytest
from app.agents.analyst_review import AnalystReviewAgent
from app.agents.base import AgentExecutionContext
from app.agents.data_quality import DataQualityAgent
from app.agents.feature_engineer import FeatureEngineerAgent
from app.agents.metric_definition import MetricDefinitionAgent, _formula_columns
from app.agents.sql_agent import SQLAgent
from app.services.report_facts import compute_dataset_facts


def _ctx(requirement: str = "") -> AgentExecutionContext:
    return AgentExecutionContext(
        node_id="n",
        task_id="t",
        requester="u",
        role="admin",
        capability="sql_extract",
        token="x",
        requirement=requirement,
    )


# ---------- 1. CSV 金额列 / 聚合语义 ----------


def test_pick_csv_amount_prefers_total_semantics_over_unit_price() -> None:
    cols = ["单价", "实付金额", "应付金额"]
    assert SQLAgent._pick_csv_amount(cols, cols, "统计各区域销售额") == "实付金额"


def test_pick_csv_amount_respects_requirement_named_column() -> None:
    cols = ["单价", "总价", "建筑面积"]
    assert SQLAgent._pick_csv_amount(cols, cols, "各区域平均单价对比") == "单价"


def test_pick_csv_amount_chinese_total_before_unit_price() -> None:
    cols = ["建筑面积", "单价", "总价"]
    assert SQLAgent._pick_csv_amount(cols, cols, "分析一下") == "总价"


def test_wants_average() -> None:
    assert SQLAgent._wants_average("各区域平均单价对比")
    assert SQLAgent._wants_average("平均销售额是多少")
    assert not SQLAgent._wants_average("各区域销售额合计")


def test_aggregate_records_avg_mode_uses_group_mean() -> None:
    data = {
        "columns": ["区域", "单价"],
        "rows": [["A", "10000"], ["A", "20000"], ["B", "5000"]],
    }
    built = SQLAgent()._aggregate_records(
        _ctx("各区域平均单价对比"),
        source_label="CSV:t",
        source_note="test",
        data=data,
        title="t",
    )
    dataset = built["dataset"]
    assert dataset["agg"] == "avg"
    assert dataset["columns"] == ["区域", "order_count", "avg_单价"]
    assert dataset["value_label"] == "平均单价"
    assert dataset["count_label"] == "记录数"  # 单价是单位价，不能叫"订单量"
    assert dataset["rows"] == [["A", 2, 15000.0], ["B", 1, 5000.0]]


def test_aggregate_records_sum_mode_keeps_total_column() -> None:
    data = {"columns": ["区域", "销售额"], "rows": [["A", "10"], ["A", "20"], ["B", "5"]]}
    built = SQLAgent()._aggregate_records(
        _ctx("各区域销售额合计"), source_label="CSV:t", source_note="test", data=data, title="t"
    )
    dataset = built["dataset"]
    assert dataset["agg"] == "sum"
    assert dataset["columns"] == ["区域", "order_count", "total_销售额"]
    assert dataset["rows"] == [["A", 2, 30.0], ["B", 1, 5.0]]


# ---------- 2. CSV 维度识别 ----------


def test_pick_csv_dim_maps_colloquial_region_words() -> None:
    cols = ["订单号", "地区", "商品类目", "单价", "实付金额"]
    rows = [
        ["1", "华东", "数码", "10", "20"],
        ["2", "华南", "食品", "30", "40"],
        ["3", "华东", "数码", "50", "60"],
    ]
    assert SQLAgent._pick_csv_dim(cols, rows, "对比各大区的销售额") == "地区"
    assert SQLAgent._pick_csv_dim(cols, rows, "对比各战区的销售额") == "地区"
    assert SQLAgent._pick_csv_dim(cols, rows, "对比各商品类目的销售额") == "商品类目"


# ---------- 3. 质检判据与评分 ----------


def test_quality_flags_zero_group_and_penalizes_score() -> None:
    dataset = {
        "columns": ["渠道", "order_count", "total_销售额"],
        "rows": [["APP", 2, 20000.5], ["门店", 2, 9800.0], ["客服", 2, 0.0]],
        "kind": "money",
    }
    report = DataQualityAgent._assess_dataset(dataset)
    issue_types = {issue["type"] for issue in report["issues"]}
    assert "zero_value" in issue_types
    assert report["score"] < 100
    assert report["blocked"] is False
    assert set(report["dimensions"]) == {"完整性", "唯一性", "一致性", "时效性", "准确性"}


def test_quality_flags_outlier_group() -> None:
    dataset = {
        "columns": ["区域", "order_count", "total_amount"],
        "rows": [["A", 1, 99.0], ["B", 1, 100.0], ["C", 1, 101.0], ["D", 1, 102.0], ["E", 1, 100.0], ["F", 1, 1000.0]],
        "kind": "money",
    }
    report = DataQualityAgent._assess_dataset(dataset)
    assert any(issue["type"] == "outlier" for issue in report["issues"])
    assert report["dimensions"]["准确性"] < 100


def test_quality_empty_result_is_not_blocking() -> None:
    report = DataQualityAgent._assess_dataset({"columns": ["region", "total_amount"], "rows": []})
    assert report["blocked"] is False
    assert report["empty_result"] is True


# ---------- 4. 报告事实（AVG 口径） ----------


def test_report_facts_avg_does_not_divide_count_again() -> None:
    dataset = {
        "columns": ["区域", "order_count", "avg_单价"],
        "rows": [["A", 2, 15000.0], ["B", 1, 5000.0]],
        "agg": "avg",
        "kind": "money",
        "value_label": "平均单价",
    }
    facts = compute_dataset_facts(dataset)
    assert facts is not None
    assert facts["max_avg"]["avg"] == 15000.0
    assert facts["min_avg"]["avg"] == 5000.0
    assert facts["weighted_avg"] == pytest.approx((15000.0 * 2 + 5000.0) / 3)


def test_report_facts_sum_mode_average_is_total_over_count() -> None:
    dataset = {"columns": ["区域", "order_count", "total_amount"], "rows": [["A", 2, 100.0], ["B", 1, 50.0]]}
    facts = compute_dataset_facts(dataset)
    assert facts is not None
    assert facts["max_avg"]["avg"] == 50.0
    assert facts["weighted_avg"] == pytest.approx(50.0)


# ---------- 5. 无 LLM 的确定性终审 ----------


def test_deterministic_review_approves_when_numbers_match() -> None:
    agent = AnalystReviewAgent()
    dataset = {"columns": ["region", "order_count", "total_amount"], "rows": [["华东", 10, 100.0], ["华北", 5, 50.0]]}
    report = "# 报告\n华东 100.0，总销售额 150.0，订单量 15"
    fact_lines, evidence = agent._verify_numbers(report, dataset)
    verdict = agent._deterministic_review(_ctx(), dataset, fact_lines, evidence)
    assert verdict["verdict"] == "approved"
    assert "确定性" in verdict["comments"]


def test_deterministic_review_rejects_on_mismatch_or_missing_dataset() -> None:
    agent = AnalystReviewAgent()
    dataset = {"columns": ["region", "order_count", "total_amount"], "rows": [["华东", 10, 100.0], ["华北", 5, 50.0]]}
    rejected = agent._deterministic_review(_ctx(), dataset, ["facts"], ["疑似不一致"])
    assert rejected["verdict"] == "rejected"
    assert rejected["evidence"] == ["疑似不一致"]
    no_dataset = agent._deterministic_review(_ctx(), None, [], [])
    assert no_dataset["verdict"] == "rejected"


# ---------- 6. 特征工程透传数据集语义 ----------


def test_feature_engineer_preserves_dataset_semantics() -> None:
    dataset = {
        "columns": ["区域", "order_count", "total_销售额"],
        "rows": [["A", 1, 10.0], ["A", 1, 10.0]],
        "kind": "money",
        "value_label": "销售额",
        "dim_label": "区域",
        "count_label": "订单量",
        "table": "orders",
        "agg": "sum",
    }
    result = FeatureEngineerAgent()._build(dataset)
    cleaned = result["dataset"]
    assert cleaned["rows"] == [["A", 1, 10.0]]  # 去重
    assert cleaned["kind"] == "money"
    assert cleaned["value_label"] == "销售额"
    assert cleaned["dim_label"] == "区域"
    assert cleaned["agg"] == "sum"


# ---------- 7. 知识库口径公式校验 ----------


def test_formula_columns_extraction() -> None:
    assert _formula_columns("SUM(`amount`)") == ["amount"]
    assert _formula_columns("COUNT(*)") == ["*"]
    assert _formula_columns("SUM(实付金额)") == ["实付金额"]
    assert _formula_columns("") == []


# ---------- 8. 指标口径与取数列对齐（需求点名 + AVG 语义） ----------


def test_metric_spec_follows_requirement_named_column_and_avg() -> None:
    profile = {
        "table": "湛江房源数据.csv",
        "columns": [
            {"name": "区域", "type": "string"},
            {"name": "建筑面积", "type": "string"},
            {"name": "单价", "type": "string"},
            {"name": "总价", "type": "string"},
        ],
    }
    spec = MetricDefinitionAgent._build_spec(profile, "各区域平均单价对比，找出最高与最低")
    assert spec["name"] == "平均单价"
    assert spec["formula"] == "AVG(单价)"  # 需求点名的"单价"优先于默认的"总价"，且平均语义用 AVG
    sum_spec = MetricDefinitionAgent._build_spec(profile, "各区域总价对比")
    assert sum_spec["formula"] == "SUM(总价)"


def test_metric_spec_ignores_dimension_named_in_requirement() -> None:
    profile = {
        "table": "t",
        "columns": [{"name": "区域", "type": "string"}, {"name": "实付金额", "type": "float"}],
    }
    spec = MetricDefinitionAgent._build_spec(profile, "各区域平均实付金额")
    assert spec["formula"] == "AVG(实付金额)"
    assert spec["name"] == "平均实付金额"


def test_dim_display_maps_english_business_columns() -> None:
    assert SQLAgent._dim_display("region") == "区域"
    assert SQLAgent._dim_display("channel") == "渠道"
    assert SQLAgent._dim_display("地区") == "地区"
    assert SQLAgent._dim_display(None) == "维度"

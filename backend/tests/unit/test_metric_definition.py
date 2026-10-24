"""MetricDefinition 中文金额列识别（纯函数，无 DB）。

覆盖：中文金额列（实付金额/总价/单价）能正确识别为 SUM 口径而非退化 COUNT；
合计语义列优先于单位价列；无金额列退化为记录数。
"""
from __future__ import annotations

from app.agents.metric_definition import MetricDefinitionAgent, _is_amount_column


def test_build_spec_prefers_total_semantic_amount_col() -> None:
    """合计语义（实付金额）优先于单位价（单价）。"""
    report = {
        "table": "电商数据",
        "columns": [
            {"name": "订单号", "type": "string"},
            {"name": "单价", "type": "float"},
            {"name": "实付金额", "type": "float"},
        ],
    }
    spec = MetricDefinitionAgent._build_spec(report)
    assert spec["name"] == "实付金额"
    assert spec["formula"] == "SUM(实付金额)"


def test_build_spec_english_amount_col() -> None:
    report = {"table": "orders", "columns": [{"name": "amount", "type": "float"}, {"name": "region", "type": "string"}]}
    spec = MetricDefinitionAgent._build_spec(report)
    assert spec["name"] == "销售额"
    assert spec["formula"] == "SUM(amount)"


def test_build_spec_no_amount_falls_back_to_count() -> None:
    report = {"table": "users", "columns": [{"name": "user_id", "type": "int"}, {"name": "age", "type": "int"}]}
    spec = MetricDefinitionAgent._build_spec(report)
    assert spec["name"] == "记录数"
    assert spec["formula"] == "COUNT(user_id)"


def test_is_amount_column_cn_and_en() -> None:
    assert _is_amount_column("实付金额") == "实付金额"
    assert _is_amount_column("总价") == "总价"
    assert _is_amount_column("单价") == "单价"
    assert _is_amount_column("amount") == "销售额"
    assert _is_amount_column("商品类目") is None
    assert _is_amount_column("订单号") is None

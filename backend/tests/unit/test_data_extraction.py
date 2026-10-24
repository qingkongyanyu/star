"""data_extraction（v3 取数/聚合）单元测试：不连数据库。"""
from __future__ import annotations

from app.services.data_extraction import (
    GroupState,
    aggregate_records,
    attach_metrics,
    build_sql,
    histogram,
    metric_value,
    sql_dim_expression,
    sql_metric_expression,
    time_range_bounds,
)

SCHEMA = {
    "table": "orders",
    "columns": [
        {"name": "订单号", "type": "string"},
        {"name": "下单时间", "type": "datetime"},
        {"name": "地区", "type": "string"},
        {"name": "实付金额", "type": "float"},
        {"name": "数量", "type": "int"},
    ],
    "sample_rows": [
        ["A1", "2025-01-05 10:00:00", "华东", "100", "2"],
        ["A2", "2025-01-15 10:00:00", "华南", "200", "3"],
        ["A3", "2025-02-05 10:00:00", "华东", "150", "1"],
        ["A4", "2025-02-15 10:00:00", "华北", "50", "4"],
    ],
    "row_count": 4,
}

METRICS = [
    {"id": "sum:实付金额", "name": "实付金额", "formula": "SUM(实付金额)", "agg": "sum", "column": "实付金额", "kind": "money", "unit": "元"},
    {"id": "count_distinct:订单号", "name": "订单量", "formula": "COUNT(DISTINCT 订单号)", "agg": "count_distinct", "column": "订单号", "kind": "count", "unit": "单"},
]


def _schema() -> dict:
    schema = dict(SCHEMA)
    return attach_metrics(schema, METRICS)


def test_aggregate_comparison_and_share() -> None:
    tasks = [
        {
            "id": "a1",
            "kind": "ranking",
            "metric_id": "sum:实付金额",
            "dim": "地区",
            "dim_type": "category",
            "top_n": 10,
        }
    ]
    datasets = aggregate_records(tasks, _schema(), SCHEMA["sample_rows"])
    assert len(datasets) == 1
    dataset = datasets[0]
    assert dataset["rows"] == [["华东", 250.0], ["华南", 200.0], ["华北", 50.0]]
    assert dataset["metric_name"] == "实付金额"
    assert dataset["source_quality"]["missing_cells"] == 0


def test_aggregate_time_trend_buckets() -> None:
    tasks = [
        {
            "id": "a1",
            "kind": "trend",
            "metric_id": "sum:实付金额",
            "dim": "下单时间",
            "dim_type": "time",
            "granularity": "month",
        }
    ]
    dataset = aggregate_records(tasks, _schema(), SCHEMA["sample_rows"])[0]
    assert dataset["rows"] == [["2025-01", 300.0], ["2025-02", 200.0]]
    assert dataset["row_count"] == 2


def test_count_distinct_metric_tracks_string_ids() -> None:
    state = GroupState()
    indexes = {name: index for index, name in enumerate(["订单号", "实付金额"])}
    state.add(["A1", "100"], indexes, METRICS[1])
    state.add(["A2", "200"], indexes, METRICS[1])
    state.add(["A1", "300"], indexes, METRICS[1])
    assert metric_value(state, METRICS[1]) == 2.0


def test_top_n_merges_tail_into_other() -> None:
    tasks = [
        {
            "id": "a1",
            "kind": "comparison",
            "metric_id": "sum:实付金额",
            "dim": "地区",
            "dim_type": "category",
            "top_n": 2,
        }
    ]
    dataset = aggregate_records(tasks, _schema(), SCHEMA["sample_rows"])[0]
    assert len(dataset["rows"]) == 2
    assert dataset["rows"][0] == ["华东", 250.0]
    assert dataset["rows"][-1][0] == "其他"
    assert dataset["rows"][-1][1] == 250.0


def test_sql_expressions_not_null_and_time_substring() -> None:
    metric = METRICS[0]
    assert sql_metric_expression(metric, "mysql") == "SUM(`实付金额`)"
    assert sql_dim_expression("下单时间", "time", "month", "mysql") == "SUBSTRING(CAST(`下单时间` AS CHAR), 1, 7)"
    task = {
        "id": "a1",
        "kind": "trend",
        "metric_id": metric["id"],
        "dim": "下单时间",
        "dim_type": "time",
        "granularity": "month",
        "filters": [{"field": "__time__", "value": {"kind": "year", "year": 2025}}],
    }
    sql = build_sql(task, _schema(), metric, "mysql")
    assert "SUM(`实付金额`) AS metric_value" in sql
    assert "BETWEEN '2025' AND '2025'" in sql
    assert "GROUP BY SUBSTRING" in sql


def test_time_range_bounds_year() -> None:
    assert time_range_bounds({"kind": "year", "year": 2024}, None) == ((2024, 1, 1), (2024, 12, 31))


def test_histogram_stats_and_bins() -> None:
    rows, stats = histogram([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert len(rows) == 8
    assert stats["min"] == 1 and stats["max"] == 10
    assert stats["median"] == 5.5


def test_ratio_metric_is_percent() -> None:
    metric = {
        "id": "ratio:毛利率",
        "name": "毛利率",
        "formula": "(SUM(销售额)-SUM(成本))/SUM(销售额)",
        "agg": "ratio",
        "kind": "ratio",
        "unit": "%",
        "numerator": {"op": "diff", "left": {"op": "sum", "column": "销售额"}, "right": {"op": "sum", "column": "成本"}},
        "denominator": {"op": "sum", "column": "销售额"},
    }
    state = GroupState()
    indexes = {"销售额": 0, "成本": 1}
    state.add([100.0, 60.0], indexes, metric)
    assert metric_value(state, metric) == 40.0
    assert "* 100" in sql_metric_expression(metric, "mysql")

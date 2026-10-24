"""图表选择引擎与 ECharts option 单元测试。"""
from __future__ import annotations

from app.services.chart_selector import chart_map_by_dataset, select_charts


def _dataset(dataset_id: str, kind: str, dim_type: str, rows: list[list], **extra: object) -> dict:
    base = {
        "id": dataset_id,
        "kind": kind,
        "dim_type": dim_type,
        "dim_label": "地区" if dim_type == "category" else "月份",
        "metric_name": "销售额",
        "metric_kind": "money",
        "unit": "元",
        "agg": "sum",
        "columns": ["维度", "销售额"],
        "rows": rows,
        "row_count": len(rows),
    }
    base.update(extra)
    return base


PLAN = {"tasks": [{"id": "a1"}], "chart_preference": []}


def test_trend_uses_line() -> None:
    dataset = _dataset("da1", "trend", "time", [["2025-01", 100.0], ["2025-02", 120.0]])
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] == "line"
    assert charts[0]["option"]["series"][0]["type"] == "line"
    assert "时间" in charts[0]["rationale"]


def test_composition_uses_donut_or_treemap() -> None:
    dataset = _dataset("da1", "composition", "category", [["A", 60.0], ["B", 40.0]])
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] in ("donut", "pie", "treemap")
    assert charts[0]["option"]["series"][0]["type"] in ("pie", "treemap")


def test_correlation_uses_scatter_with_regression() -> None:
    dataset = _dataset(
        "da1",
        "correlation",
        "none",
        [],
        points=[[1, 2], [2, 4], [3, 6], [4, 8]],
        columns=["订单量", "销售额"],
    )
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] == "scatter"
    assert len(charts[0]["option"]["series"]) == 2  # 散点 + 回归线


def test_attribution_uses_waterfall() -> None:
    dataset = _dataset("da1", "attribution", "category", [["A", 120.0], ["B", 80.0], ["C", 100.0]])
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] == "waterfall"


def test_two_category_charts_do_not_repeat_type() -> None:
    first = _dataset("da1", "comparison", "category", [["A", 100.0], ["B", 80.0]])
    second = _dataset("da2", "comparison", "category", [["A", 90.0], ["B", 70.0]])
    charts = select_charts([first, second], PLAN)
    # 每个数据集产出「对比图 + 结构图」两类互补视图，且全任务图型不重复
    assert len(charts) == 4
    types = [chart["type"] for chart in charts]
    assert len(set(types)) == len(types)


def test_category_comparison_gets_complementary_share_chart() -> None:
    dataset = _dataset("da1", "comparison", "category", [["A", 100.0], ["B", 80.0], ["C", 60.0]])
    charts = select_charts([dataset], PLAN)
    assert [chart["type"] for chart in charts][:2] == ["bar_horizontal", "donut"]
    assert "占比" in charts[1]["title"]


def test_trend_gets_complementary_bar_chart() -> None:
    dataset = _dataset("da1", "trend", "time", [["2025-01", 100.0], ["2025-02", 120.0]])
    charts = select_charts([dataset], PLAN)
    assert [chart["type"] for chart in charts] == ["line", "bar"]
    assert "对比" in charts[1]["title"]


def test_attribution_gets_waterfall_and_comparison() -> None:
    dataset = _dataset("da1", "attribution", "category", [["A", 120.0], ["B", 80.0], ["C", 100.0]])
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] == "waterfall"
    assert charts[1]["type"] == "bar_horizontal"
    assert charts[1]["title"].startswith("各")


def test_negative_values_do_not_get_pie_chart() -> None:
    dataset = _dataset("da1", "comparison", "category", [["A", 100.0], ["B", -20.0]])
    charts = select_charts([dataset], PLAN)
    assert len(charts) == 1
    assert charts[0]["type"] == "bar_horizontal"


def test_kpi_for_no_dimension() -> None:
    dataset = _dataset("da1", "summary", "none", [["全部", 1000.0]])
    charts = select_charts([dataset], PLAN)
    assert charts[0]["type"] == "kpi"


def test_chart_map_by_dataset() -> None:
    charts = [{"id": "c1", "dataset_id": "da1"}, {"id": "c2", "dataset_id": "da1"}]
    assert chart_map_by_dataset(charts) == {"da1": ["c1", "c2"]}

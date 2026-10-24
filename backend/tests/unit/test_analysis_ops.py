"""分析算子（洞察依据）单元测试：趋势/对比/构成/相关/异常。"""
from __future__ import annotations

from app.services.analysis_ops import build_findings


def _dataset(kind: str, rows: list[list], **extra: object) -> dict:
    base = {
        "id": "da1",
        "kind": kind,
        "metric_name": "销售额",
        "metric_kind": "money",
        "unit": "元",
        "dim_label": "地区",
        "dim_type": "category",
        "agg": "sum",
        "rows": rows,
        "row_count": len(rows),
    }
    base.update(extra)
    return base


def test_comparison_finding_has_share_and_evidence() -> None:
    findings = build_findings([_dataset("comparison", [["华东", 60.0], ["华南", 30.0], ["华北", 10.0]])])
    comparison = next(item for item in findings if item["kind"] == "comparison")
    assert "华东" in comparison["statement"]
    assert "60.0%" in comparison["statement"]
    assert any("CR3" in line for line in comparison["evidence"])


def test_trend_finding_growth_and_peak() -> None:
    findings = build_findings(
        [_dataset("trend", [["2025-01", 100.0], ["2025-02", 120.0], ["2025-03", 90.0]], dim_type="time")]
    )
    trend = next(item for item in findings if item["kind"] == "trend")
    assert "下降 10.0%" in trend["statement"]
    assert "2025-02" in trend["statement"]


def test_composition_hhi() -> None:
    findings = build_findings([_dataset("composition", [["A", 80.0], ["B", 20.0]])])
    composition = next(item for item in findings if item["kind"] == "composition")
    assert "HHI 0.68" in composition["statement"]


def test_correlation_pearson() -> None:
    dataset = _dataset(
        "correlation",
        [],
        points=[[1, 2], [2, 4], [3, 6], [4, 8]],
        columns=["订单量", "销售额"],
        dim_type="none",
    )
    findings = build_findings([dataset])
    correlation = next(item for item in findings if item["kind"] == "correlation")
    assert "Pearson 1.00" in correlation["statement"]
    assert "强正相关" in correlation["statement"]


def test_anomaly_finding_on_outlier() -> None:
    rows = [[f"g{i}", float(i)] for i in range(1, 10)] + [["g-outlier", 100.0]]
    findings = build_findings([_dataset("anomaly", rows)])
    anomaly = next(item for item in findings if item["kind"] == "anomaly")
    assert "g-outlier" in anomaly["statement"]


def test_chart_ids_are_attached() -> None:
    findings = build_findings([_dataset("comparison", [["A", 60.0], ["B", 40.0]])], {"da1": ["c1"]})
    assert findings[0]["chart_ids"] == ["c1"]


def test_cross_metric_high_sales_low_margin() -> None:
    sales = {
        "id": "da1",
        "kind": "comparison",
        "metric_name": "销售额",
        "metric_kind": "money",
        "unit": "元",
        "dim": "商品类目",
        "dim_label": "商品类目",
        "dim_type": "category",
        "agg": "sum",
        "rows": [["运动户外", 293642.0], ["数码家电", 280000.0], ["食品生鲜", 240000.0], ["美妆个护", 230000.0], ["家居日用", 220000.0]],
        "row_count": 5,
    }
    margin = {
        "id": "da2",
        "kind": "comparison",
        "metric_name": "毛利率",
        "metric_kind": "ratio",
        "unit": "%",
        "dim": "商品类目",
        "dim_label": "商品类目",
        "dim_type": "category",
        "agg": "ratio",
        "rows": [["家居日用", 36.4], ["数码家电", 36.1], ["食品生鲜", 35.5], ["运动户外", 35.0], ["美妆个护", 33.4]],
        "row_count": 5,
    }
    findings = build_findings([sales, margin])
    cross = next(item for item in findings if item["kind"] == "attribution")
    assert "高销售" in cross["statement"]
    assert "运动户外" in cross["statement"]
    assert "第 4" in cross["statement"]
    assert cross["severity"] == "warning"

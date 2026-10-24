"""图表 PNG 渲染与 Markdown 内嵌测试（matplotlib Agg）。"""
from __future__ import annotations

from app.services.chart_render import chart_data_uri, embed_chart_images, render_chart


def _chart(chart_type: str, **extra: object) -> dict:
    base = {
        "id": "c1",
        "type": chart_type,
        "title": "测试图表",
        "data": {"categories": ["A", "B", "C"], "series": [{"name": "值", "values": [10.0, 20.0, 30.0]}]},
        "money": False,
    }
    base.update(extra)
    return base


def test_render_line_bar_pie_scatter() -> None:
    for chart_type in ("line", "bar", "bar_horizontal", "donut"):
        data = render_chart(_chart(chart_type))
        assert data is not None and len(data) > 5000, chart_type
    scatter = render_chart(
        _chart("scatter", points=[[1, 2], [2, 4], [3, 6]], data={}, columns=["X", "Y"])
    )
    assert scatter is not None and len(scatter) > 5000


def test_kpi_chart_not_rendered() -> None:
    assert render_chart(_chart("kpi")) is None


def test_embed_chart_images_to_data_uri() -> None:
    markdown = "![图 1 测试](chart://c1)"
    embedded = embed_chart_images(markdown, [_chart("bar")])
    assert embedded.startswith("![图 1 测试](data:image/png;base64,")
    assert chart_data_uri(_chart("bar")) is not None


def test_embed_keeps_legacy_refs() -> None:
    markdown = "![旧图](chart://asset@1/chart/0)"
    assert embed_chart_images(markdown, []) == markdown

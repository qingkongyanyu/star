"""图表选择引擎 + ECharts option 构建（纯函数，无 DB/LLM）。

输入：datasets（真实数据）× AnalysisPlan 意图 × 特征工程 visual_hints × 用户图表偏好
输出：charts[]（type/title/encoding/data/option/rationale/insight）

选择原则（可解释）：
- 时间维度 + 趋势/异常 → 折线（保留时间顺序，标注峰谷/异常）；
- 类别 + 对比/排名 → 横向柱（Top-N 排序，类目多时可读）；
- 构成/占比 → 环形图（≤8 类）或树图（>8 类）；
- 分布 → 直方图；相关 → 散点 + 回归线；归因 → 瀑布图；
- 同任务内图表类型去重，避免"所有任务都是柱状图"。
"""
from __future__ import annotations

from typing import Any

_CHART_KEYS = {
    "line", "bar", "bar_horizontal", "bar_stacked", "bar_grouped", "pie", "donut", "rose",
    "scatter", "heatmap", "radar", "funnel", "treemap", "boxplot", "waterfall", "kpi",
}

COLORS = ["#d97706", "#ea580c", "#b45309", "#f59e0b", "#9a3412", "#ca8a04", "#7c2d12", "#a16207", "#dc2626", "#0f766e"]

_MAX_CHARTS = 6


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float, unit: str, money: bool) -> str:
    if money:
        return f"¥{value:,.2f}"
    return f"{value:,.2f}{unit}" if unit else f"{value:,.2f}"


def _categories_values(dataset: dict[str, Any]) -> tuple[list[str], list[float]]:
    categories: list[str] = []
    values: list[float] = []
    for row in dataset.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        number = _num(row[1])
        if number is None:
            continue
        categories.append(str(row[0]))
        values.append(number)
    return categories, values


def _base_option(title: str) -> dict[str, Any]:
    return {
        "animation": True,
        "animationDuration": 700,
        "color": COLORS,
        "title": {"text": title, "left": 8, "top": 4, "textStyle": {"fontSize": 13, "fontWeight": 600, "color": "#4a3420"}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": {"left": 64, "right": 26, "top": 48, "bottom": 56, "containLabel": True},
    }


def _bar_option(
    categories: list[str],
    values: list[float],
    title: str,
    *,
    horizontal: bool = False,
    unit: str = "",
    money: bool = False,
    highlight: set[int] | None = None,
) -> dict[str, Any]:
    option = _base_option(title)
    highlight = highlight or set()
    data = [
        {
            "value": value,
            "itemStyle": {"color": COLORS[i % len(COLORS)] if i not in highlight else "#dc2626"},
        }
        for i, value in enumerate(values)
    ]
    label = {"show": True, "position": "right" if horizontal else "top", "fontSize": 10, "formatter": "{c}%" if unit == "%" else "{c}"}
    if horizontal:
        option["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}}
        option["xAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
        option["yAxis"] = {
            "type": "category",
            "data": categories,
            "inverse": True,
            "axisLabel": {"fontSize": 11, "color": "#6b5540"},
            "axisLine": {"lineStyle": {"color": "rgba(180,130,70,0.35)"}},
        }
        option["grid"]["left"] = 110
        option["series"] = [
            {
                "type": "bar",
                "data": data,
                "barMaxWidth": 26,
                "itemStyle": {"borderRadius": [0, 6, 6, 0]},
                "label": label,
            }
        ]
    else:
        option["xAxis"] = {
            "type": "category",
            "data": categories,
            "axisLabel": {"fontSize": 10, "color": "#6b5540", "rotate": 30 if len(categories) > 6 else 0, "interval": 0},
            "axisLine": {"lineStyle": {"color": "rgba(180,130,70,0.35)"}},
        }
        option["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
        option["series"] = [
            {"type": "bar", "data": data, "barMaxWidth": 34, "itemStyle": {"borderRadius": [6, 6, 0, 0]}, "label": label}
        ]
    return option


def _line_option(
    categories: list[str],
    values: list[float],
    title: str,
    *,
    unit: str = "",
    money: bool = False,
    highlight: set[int] | None = None,
) -> dict[str, Any]:
    option = _base_option(title)
    highlight = highlight or set()
    mark_points: list[dict[str, Any]] = []
    if values:
        mark_points.append({"type": "max", "name": "峰值", "label": {"fontSize": 10}})
        mark_points.append({"type": "min", "name": "谷值", "label": {"fontSize": 10}})
        for index in sorted(highlight):
            if 0 <= index < len(categories):
                mark_points.append(
                    {
                        "coord": [categories[index], values[index]],
                        "name": "异常",
                        "itemStyle": {"color": "#dc2626"},
                        "label": {"fontSize": 10, "color": "#dc2626"},
                    }
                )
    option["xAxis"] = {
        "type": "category",
        "data": categories,
        "boundaryGap": False,
        "axisLabel": {"fontSize": 10, "color": "#6b5540", "rotate": 30 if len(categories) > 8 else 0, "interval": "auto"},
        "axisLine": {"lineStyle": {"color": "rgba(180,130,70,0.35)"}},
    }
    option["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
    option["series"] = [
        {
            "type": "line",
            "data": values,
            "smooth": False,
            "symbolSize": 7,
            "lineStyle": {"width": 2.5, "color": "#d97706"},
            "itemStyle": {"color": "#ea580c"},
            "areaStyle": {"color": "rgba(217,119,6,0.12)"},
            "markPoint": {"symbolSize": 42, "data": mark_points},
        }
    ]
    return option


def _pie_option(
    categories: list[str], values: list[float], title: str, *, donut: bool = True, unit: str = "", money: bool = False
) -> dict[str, Any]:
    option = _base_option(title)
    option["tooltip"] = {"trigger": "item", "formatter": "{b}<br/>{c}（{d}%）"}
    option["legend"] = {"type": "scroll", "orient": "vertical", "right": 4, "top": "middle", "textStyle": {"fontSize": 10}}
    option["series"] = [
        {
            "type": "pie",
            "radius": ["42%", "68%"] if donut else ["0%", "66%"],
            "center": ["38%", "56%"],
            "data": [{"name": name, "value": value} for name, value in zip(categories, values)],
            "label": {"fontSize": 10, "formatter": "{b}\n{d}%"},
            "labelLine": {"length": 8, "length2": 6},
            "itemStyle": {"borderColor": "#fffdf8", "borderWidth": 2, "borderRadius": 4},
        }
    ]
    return option


def _treemap_option(categories: list[str], values: list[float], title: str, *, unit: str = "", money: bool = False) -> dict[str, Any]:
    option = _base_option(title)
    option["tooltip"] = {"trigger": "item", "formatter": "{b}<br/>{c}（{d}%）"}
    option["series"] = [
        {
            "type": "treemap",
            "roam": False,
            "nodeClick": False,
            "breadcrumb": {"show": False},
            "label": {"show": True, "fontSize": 11, "formatter": "{b}\n{c}"},
            "upperLabel": {"show": False},
            "data": [{"name": name, "value": value} for name, value in zip(categories, values)],
        }
    ]
    return option


def _scatter_option(
    points: list[list[float]],
    title: str,
    *,
    x_name: str,
    y_name: str,
    unit: str = "",
    money: bool = False,
) -> dict[str, Any]:
    option = _base_option(title)
    option["tooltip"] = {"trigger": "item", "formatter": f"{x_name}: {{c}}"}
    option["xAxis"] = {"type": "value", "name": x_name, "nameTextStyle": {"fontSize": 10}, "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
    option["yAxis"] = {"type": "value", "name": y_name, "nameTextStyle": {"fontSize": 10}, "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
    series: list[dict[str, Any]] = [
        {"type": "scatter", "data": points, "symbolSize": 8, "itemStyle": {"color": "rgba(217,119,6,0.65)", "borderColor": "#b45309"}}
    ]
    # 回归线：最小二乘（可选，样本 >= 3）
    if len(points) >= 3:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom > 0:
            slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
            intercept = mean_y - slope * mean_x
            low, high = min(xs), max(xs)
            series.append(
                {
                    "type": "line",
                    "data": [[low, slope * low + intercept], [high, slope * high + intercept]],
                    "showSymbol": False,
                    "lineStyle": {"color": "#dc2626", "width": 1.5, "type": "dashed"},
                    "tooltip": {"show": False},
                }
            )
    option["series"] = series
    return option


def _waterfall_option(categories: list[str], values: list[float], title: str, *, unit: str = "", money: bool = False) -> dict[str, Any]:
    option = _base_option(title)
    if not values:
        return option
    average = sum(values) / len(values)
    deltas = [value - average for value in values]
    base: list[float] = []
    cumulative = 0.0
    for delta in deltas:
        if delta >= 0:
            base.append(cumulative)
            cumulative += delta
        else:
            cumulative += delta
            base.append(cumulative)
    option["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}}
    option["xAxis"] = {
        "type": "category",
        "data": categories,
        "axisLabel": {"fontSize": 10, "rotate": 30 if len(categories) > 6 else 0, "interval": 0, "color": "#6b5540"},
    }
    option["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "rgba(180,130,70,0.12)"}}}
    option["series"] = [
        {"type": "bar", "stack": "waterfall", "itemStyle": {"color": "transparent"}, "data": base, "silent": True, "tooltip": {"show": False}},
        {
            "type": "bar",
            "stack": "waterfall",
            "data": [
                {
                    "value": delta,
                    "itemStyle": {"color": "#15803d" if delta >= 0 else "#dc2626", "borderRadius": [4, 4, 0, 0]},
                }
                for delta in deltas
            ],
            "label": {"show": True, "position": "top", "fontSize": 9, "formatter": "{c}"},
        },
    ]
    return option


def _histogram_option(dataset: dict[str, Any], title: str) -> dict[str, Any]:
    categories = [str(row[0]) for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2]
    values = [float(row[1]) for row in dataset.get("rows") or [] if isinstance(row, (list, tuple)) and len(row) >= 2 and _num(row[1]) is not None]
    option = _bar_option(categories, values, title, unit="条", money=False)
    option["xAxis"]["axisLabel"]["rotate"] = 35
    option["xAxis"]["axisLabel"]["fontSize"] = 9
    return option


def _kpi_chart(dataset: dict[str, Any], title: str, chart_id: str) -> dict[str, Any]:
    labels, values = _categories_values(dataset)
    total = sum(values)
    return {
        "id": chart_id,
        "type": "kpi",
        "title": title,
        "subtitle": dataset.get("metric_name") or "",
        "dataset_id": dataset.get("id"),
        "encoding": {},
        "data": {"labels": labels, "values": values},
        "option": {},
        "rationale": "无分组维度的总量指标：用 KPI 卡片呈现，不强行作图",
        "insight": f"{dataset.get('metric_name')}合计 {total:,.2f}{dataset.get('unit') or ''}",
        "unit": dataset.get("unit") or "",
        "money": dataset.get("metric_kind") == "money",
    }


def _variant_for(base: str, used: set[str]) -> str:
    """同任务图表类型去重：首选被占用时选择语义相近的替代图型。"""
    if base not in used:
        return base
    alternatives: dict[str, tuple[str, ...]] = {
        "bar_horizontal": ("bar", "treemap"),
        "bar": ("bar_horizontal", "treemap"),
        "donut": ("pie", "treemap"),
        "pie": ("donut", "treemap"),
        "line": ("bar",),
        "waterfall": ("bar_horizontal",),
    }
    for candidate in alternatives.get(base, ()):
        if candidate not in used:
            return candidate
    return base


def select_charts(
    datasets: list[dict[str, Any]],
    plan: dict[str, Any],
    findings: list[dict[str, Any]] | None = None,
    visual_hints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """为每个数据集选择 1 张图（必要时补第二张），并构建可渲染 option。"""
    if findings is None:
        from app.services.analysis_ops import build_findings

        findings = build_findings(datasets)
    hints_by_dataset = {str(h.get("dataset_id")): h for h in (visual_hints or []) if isinstance(h, dict)}
    preference = [str(item) for item in (plan.get("chart_preference") or []) if item in _CHART_KEYS]
    charts: list[dict[str, Any]] = []
    used_types: set[str] = set()
    for dataset in datasets:
        if len(charts) >= _MAX_CHARTS:
            break
        kind = str(dataset.get("kind") or "summary")
        kind_tokens = set(kind.split("+"))
        dim_type = str(dataset.get("dim_type") or "none")
        categories, values = _categories_values(dataset)
        unit = str(dataset.get("unit") or "")
        money = dataset.get("metric_kind") == "money"
        dim_label = str(dataset.get("dim_label") or "维度")
        metric = str(dataset.get("metric_name") or "指标")
        hint = hints_by_dataset.get(str(dataset.get("id"))) or {}
        chart_type = ""
        title = ""
        option: dict[str, Any] = {}
        rationale = ""

        if "distribution" in kind_tokens and dim_type == "none":
            title = f"{metric}分布"
            chart_type = "bar" if "bar" not in used_types else "bar_horizontal"
            option = _histogram_option(dataset, title)
            if chart_type == "bar_horizontal":
                option = _bar_option(categories, values, title, horizontal=True, unit="条")
                option["yAxis"]["axisLabel"]["fontSize"] = 9
            rationale = "单指标分布：用直方图看集中趋势与长尾"
        elif "correlation" in kind_tokens:
            points = dataset.get("points") or []
            title = "指标相关性"
            columns = dataset.get("columns") or ["X", "Y"]
            option = _scatter_option(points, title, x_name=str(columns[0]), y_name=str(columns[1] if len(columns) > 1 else "Y"))
            chart_type, rationale = "scatter", "两个数值指标：散点 + 回归线看相关方向与强度"
        elif "composition" in kind_tokens:
            title = f"{dim_label}{metric}构成"
            target = preference[0] if preference and preference[0] in ("pie", "donut", "treemap") else ""
            chart_type = _variant_for(target or ("treemap" if len(categories) > 8 else "donut"), used_types)
            if chart_type in ("pie", "donut", "rose"):
                option = _pie_option(categories, values, title, donut=chart_type != "pie", unit=unit, money=money)
            else:
                option = _treemap_option(categories, values, title, unit=unit, money=money)
            rationale = f"构成/占比分析：{len(categories)} 个类别" + (
                "，类别较多用树图避免饼图碎片" if chart_type == "treemap" else "，用环形图看份额"
            )
        elif "attribution" in kind_tokens:
            title = f"{dim_label}{metric}贡献"
            chart_type = _variant_for("waterfall", used_types)
            if chart_type == "waterfall":
                option = _waterfall_option(categories, values, title, unit=unit, money=money)
            else:
                option = _bar_option(categories, values, title, horizontal=chart_type == "bar_horizontal", unit=unit, money=money)
            rationale = "归因分析：以均值为基准拆解各维度贡献（绿增红减）"
        elif dim_type == "time":
            title = f"{metric}走势"
            highlight: set[int] = set()
            if "anomaly" in kind_tokens and len(values) >= 5:
                ordered = sorted(values)
                q1 = ordered[len(ordered) // 4]
                q3 = ordered[(len(ordered) * 3) // 4]
                iqr = q3 - q1
                if iqr > 0:
                    highlight = {i for i, value in enumerate(values) if value < q1 - 1.5 * iqr or value > q3 + 1.5 * iqr}
            option = _line_option(categories, values, title, unit=unit, money=money, highlight=highlight)
            chart_type = "line"
            rationale = "时间维度：折线图保留时间顺序" + ("，并标注异常点" if highlight else "，标注峰谷")
        elif dim_type == "category":
            title = f"各{dim_label}{metric}对比"
            target = next((item for item in preference if item in ("bar", "bar_horizontal")), "")
            chart_type = _variant_for(target or "bar_horizontal", used_types)
            option = _bar_like_option(chart_type, categories, values, title, unit=unit, money=money)
            if chart_type == "bar_horizontal":
                rationale = "类别对比：横向柱状图对中文类目更友好"
            elif chart_type == "treemap":
                rationale = "类别对比：类别较多且柱状图类型已占用，改用树图对照面积"
            else:
                rationale = "类别对比：柱状图"
        else:
            # 无维度总量 → KPI（不进图表区）
            charts.append(_kpi_chart(dataset, f"{metric}总量", f"c{len(charts) + 1}"))
            continue

        dataset_findings = [f for f in findings if f.get("dataset_id") == dataset.get("id")]
        insight = str(dataset_findings[0].get("statement")) if dataset_findings else ""
        if hint.get("reason"):
            rationale += f"；特征工程建议：{hint['reason']}"
        chart_id = f"c{len(charts) + 1}"
        charts.append(
            {
                "id": chart_id,
                "type": chart_type,
                "title": title,
                "subtitle": f"{dataset.get('task_kind') or kind} · {dataset.get('row_count')} 个数据点",
                "dataset_id": dataset.get("id"),
                "encoding": {"x": dim_label, "y": metric, "series": None},
                "data": {"categories": categories, "series": [{"name": metric, "values": values}]},
                "option": option,
                "rationale": rationale,
                "insight": insight,
                "unit": unit,
                "money": money,
            }
        )
        used_types.add(chart_type)

        # 互补视图：同一数据集补 1 张不同语义的图（结构图 + 对比/趋势图），
        # 满足「图文表并茂」且避免全篇只有一种图型（类型仍强制去重）。
        secondary = _secondary_chart(
            kind_tokens=kind_tokens,
            dim_type=dim_type,
            categories=categories,
            values=values,
            dim_label=dim_label,
            metric=metric,
            used_types=used_types,
            unit=unit,
            money=money,
        )
        if secondary is not None and len(charts) < _MAX_CHARTS:
            second_type, second_title, second_option, second_rationale = secondary
            charts.append(
                {
                    "id": f"c{len(charts) + 1}",
                    "type": second_type,
                    "title": second_title,
                    "subtitle": f"补充视图 · {dataset.get('row_count')} 个数据点",
                    "dataset_id": dataset.get("id"),
                    "encoding": {"x": dim_label, "y": metric, "series": None},
                    "data": {"categories": categories, "series": [{"name": metric, "values": values}]},
                    "option": second_option,
                    "rationale": second_rationale,
                    "insight": insight,
                    "unit": unit,
                    "money": money,
                }
            )
            used_types.add(second_type)
    return charts


def _bar_like_option(
    chart_type: str,
    categories: list[str],
    values: list[float],
    title: str,
    *,
    unit: str,
    money: bool,
) -> dict[str, Any]:
    """按已选类型构建柱/横向柱/树图 option（类型与 option 必须一致）。"""
    if chart_type == "treemap":
        return _treemap_option(categories, values, title, unit=unit, money=money)
    return _bar_option(
        categories, values, title, horizontal=chart_type == "bar_horizontal", unit=unit, money=money
    )


def _secondary_chart(
    *,
    kind_tokens: set[str],
    dim_type: str,
    categories: list[str],
    values: list[float],
    dim_label: str,
    metric: str,
    used_types: set[str],
    unit: str,
    money: bool,
) -> tuple[str, str, dict[str, Any], str] | None:
    """为同一数据集选择互补图型；数据不满足时返回 None（诚实降级，不硬作图）。"""
    if len(categories) < 2 or len(values) < 2:
        return None
    non_negative = all(value >= 0 for value in values)
    if "attribution" in kind_tokens:
        chart_type = _variant_for("bar_horizontal", used_types)
        title = f"各{dim_label}{metric}对比"
        option = _bar_like_option(chart_type, categories, values, title, unit=unit, money=money)
        return chart_type, title, option, "补充对比视图：用排序柱状图对照各维度绝对量，与瀑布贡献拆解互补"
    if "composition" in kind_tokens:
        chart_type = _variant_for("bar_horizontal", used_types)
        title = f"各{dim_label}{metric}对比"
        option = _bar_like_option(chart_type, categories, values, title, unit=unit, money=money)
        return chart_type, title, option, "补充对比视图：份额之外再看绝对量差异"
    if dim_type == "time":
        chart_type = _variant_for("bar", used_types)
        title = f"{metric}分时段对比"
        option = _bar_like_option(chart_type, categories, values, title, unit=unit, money=money)
        return chart_type, title, option, "补充柱状对比：逐时段绝对值更易读出量级与峰谷"
    if dim_type == "category" and len(categories) <= 12 and non_negative:
        chart_type = _variant_for("donut", used_types)
        title = f"各{dim_label}{metric}占比"
        if chart_type in ("pie", "donut"):
            option = _pie_option(categories, values, title, donut=chart_type != "pie", unit=unit, money=money)
        else:
            option = _treemap_option(categories, values, title, unit=unit, money=money)
        return chart_type, title, option, "补充结构视图：看各类别份额与集中度，与对比柱互补"
    return None


def chart_map_by_dataset(charts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """dataset_id → chart ids（供洞察 Agent 引用图表证据）。"""
    mapping: dict[str, list[str]] = {}
    for chart in charts:
        dataset_id = str(chart.get("dataset_id") or "")
        if not dataset_id:
            continue
        mapping.setdefault(dataset_id, []).append(str(chart.get("id")))
    return mapping


def regression(points: list[list[float]]) -> tuple[float, float] | None:
    """最小二乘回归（供单测/外部复用）。"""
    clean = [(float(p[0]), float(p[1])) for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(clean) < 2:
        return None
    mean_x = sum(p[0] for p in clean) / len(clean)
    mean_y = sum(p[1] for p in clean) / len(clean)
    denom = sum((p[0] - mean_x) ** 2 for p in clean)
    if denom <= 0:
        return None
    slope = sum((p[0] - mean_x) * (p[1] - mean_y) for p in clean) / denom
    return slope, mean_y - slope * mean_x

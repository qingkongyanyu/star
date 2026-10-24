"""图表 PNG 渲染：把图表 spec 画成图片并内嵌到 Markdown 报告（图文表并茂）。

- 使用 matplotlib（Agg 后端，无显示依赖）；中文字体优先 Microsoft YaHei / Noto Sans CJK；
- 支持 bar / bar_horizontal / line / pie / donut / scatter / waterfall / treemap / 直方图；
- `embed_chart_images(report_md, charts)` 把 `![图 N ...](chart://c1)` 替换为
  `data:image/png;base64,...`，导出的 .md 离线也能看到图；
- 应用内仍保留 `chart://` 由前端 ECharts 交互渲染（report 字段），导出用 `report_export_md`。
"""
from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_COLORS = ["#d97706", "#ea580c", "#b45309", "#f59e0b", "#9a3412", "#ca8a04", "#7c2d12", "#a16207", "#dc2626", "#0f766e"]
_FONTS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Zen Hei", "DejaVu Sans"]

_configured = False


def _setup() -> None:
    """惰性初始化 matplotlib（避免无渲染场景也加载）。"""
    global _configured
    if _configured:
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = _FONTS
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "#fffdf8"
    plt.rcParams["axes.facecolor"] = "#fffdf8"
    plt.rcParams["axes.edgecolor"] = "#e6d7bd"
    plt.rcParams["axes.labelcolor"] = "#5a4328"
    plt.rcParams["xtick.color"] = "#6b5540"
    plt.rcParams["ytick.color"] = "#6b5540"
    plt.rcParams["text.color"] = "#4a3420"
    _configured = True


def _values(chart: dict[str, Any]) -> tuple[list[str], list[float]]:
    data = chart.get("data") or {}
    categories = [str(item) for item in data.get("categories") or []]
    series = data.get("series") or []
    values = [float(value) for value in (series[0].get("values") if series else []) or []]
    if len(categories) != len(values):
        pairs = [(str(c), float(v)) for c, v in zip(categories, values) if isinstance(v, (int, float))]
        categories = [pair[0] for pair in pairs]
        values = [pair[1] for pair in pairs]
    return categories, values


def _label(value: float, money: bool) -> str:
    if money:
        return f"{value:,.0f}"
    if abs(value) >= 10000:
        return f"{value / 10000:.1f}万"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"


def _bar(ax: Any, categories: list[str], values: list[float], horizontal: bool, money: bool) -> None:
    if horizontal:
        ordered = list(zip(categories, values))[::-1]
        labels = [item[0] for item in ordered]
        nums = [item[1] for item in ordered]
        bars = ax.barh(labels, nums, color=[_COLORS[i % len(_COLORS)] for i in range(len(nums))], height=0.62)
        for bar, value in zip(bars, nums):
            ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f" {_label(value, money)}", va="center", fontsize=8)
        ax.grid(axis="x", color="#b4824626", linestyle="--", linewidth=0.6)
    else:
        bars = ax.bar(categories, values, color=[_COLORS[i % len(_COLORS)] for i in range(len(values))], width=0.62)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), _label(value, money), ha="center", va="bottom", fontsize=8)
        ax.grid(axis="y", color="#b4824626", linestyle="--", linewidth=0.6)
        if len(categories) > 6:
            ax.tick_params(axis="x", rotation=28, labelsize=8)


def _line(ax: Any, categories: list[str], values: list[float], money: bool) -> None:
    ax.plot(range(len(categories)), values, color="#d97706", linewidth=2, marker="o", markersize=4, markerfacecolor="#ea580c")
    ax.fill_between(range(len(categories)), values, color="#f59e0b", alpha=0.12)
    peak = max(range(len(values)), key=lambda i: values[i]) if values else 0
    trough = min(range(len(values)), key=lambda i: values[i]) if values else 0
    for index, tag in ((peak, "峰值"), (trough, "谷值")):
        if values:
            ax.annotate(f"{tag} {_label(values[index], money)}", (index, values[index]), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8, color="#b45309")
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=30 if len(categories) > 8 else 0, fontsize=8)
    ax.grid(axis="y", color="#b4824626", linestyle="--", linewidth=0.6)


def _pie(ax: Any, categories: list[str], values: list[float], donut: bool) -> None:
    _wedges, _texts, autotexts = ax.pie(
        values,
        labels=categories,
        autopct="%1.1f%%",
        startangle=90,
        colors=[_COLORS[i % len(_COLORS)] for i in range(len(values))],
        wedgeprops={"width": 0.45 if donut else 1.0, "edgecolor": "#fffdf8", "linewidth": 1.5},
        textprops={"fontsize": 8},
        pctdistance=0.78,
    )
    for text in autotexts:
        text.set_fontsize(7)
        text.set_color("#4a3420")


def _scatter(ax: Any, chart: dict[str, Any]) -> None:
    points = [point for point in chart.get("points") or [] if isinstance(point, (list, tuple)) and len(point) >= 2]
    if not points:
        return
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    ax.scatter(xs, ys, s=16, color="#d97706", alpha=0.65, edgecolors="#b45309", linewidths=0.3)
    if len(points) >= 3:
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom > 0:
            slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
            intercept = mean_y - slope * mean_x
            line_x = [min(xs), max(xs)]
            ax.plot(line_x, [slope * x + intercept for x in line_x], color="#dc2626", linestyle="--", linewidth=1.2)
    columns = chart.get("columns") or ["X", "Y"]
    ax.set_xlabel(str(columns[0]), fontsize=9)
    ax.set_ylabel(str(columns[1] if len(columns) > 1 else "Y"), fontsize=9)
    ax.grid(color="#b4824626", linestyle="--", linewidth=0.6)


def _waterfall(ax: Any, categories: list[str], values: list[float], money: bool) -> None:
    if not values:
        return
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
    ax.bar(categories, base, color="none", width=0.62)
    colors = ["#15803d" if delta >= 0 else "#dc2626" for delta in deltas]
    ax.bar(categories, deltas, bottom=base, color=colors, width=0.62)
    for index, delta in enumerate(deltas):
        ax.text(index, base[index] + delta, f"{delta:+,.0f}", ha="center", va="bottom", fontsize=8)
    ax.axhline(0, color="#b09a7a", linewidth=0.8)
    if len(categories) > 6:
        ax.tick_params(axis="x", rotation=28, labelsize=8)
    ax.grid(axis="y", color="#b4824626", linestyle="--", linewidth=0.6)


def _treemap(ax: Any, categories: list[str], values: list[float]) -> None:
    """简易 treemap（slice-and-dice 递归），保证导出报告里也能看到结构。"""
    from matplotlib.patches import Rectangle

    pairs = sorted(zip(categories, values), key=lambda item: item[1], reverse=True)[:20]
    total = sum(value for _name, value in pairs) or 1.0

    def _subtotal(items: list[tuple[str, float]]) -> float:
        return sum(value for _name, value in items) or 1.0

    def _draw(items: list[tuple[str, float]], x: float, y: float, w: float, h: float, vertical: bool) -> None:
        if not items:
            return
        if len(items) == 1:
            name, value = items[0]
            color = _COLORS[sum(ord(ch) for ch in name) % len(_COLORS)]
            ax.add_patch(Rectangle((x, y), w, h, facecolor=color, edgecolor="#fffdf8", linewidth=1.2, alpha=0.92))
            if w * h > 0.025:
                ax.text(
                    x + w / 2,
                    y + h / 2,
                    f"{name}\n{value / total * 100:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#fffdf8",
                    wrap=True,
                )
            return
        half = _subtotal(items) / 2
        running = 0.0
        mid = 1
        for index, (_name, value) in enumerate(items, start=1):
            running += value
            if running >= half:
                mid = index
                break
        mid = max(1, min(len(items) - 1, mid))
        first, second = items[:mid], items[mid:]
        ratio = _subtotal(first) / _subtotal(items)
        if vertical:
            _draw(first, x, y, w * ratio, h, False)
            _draw(second, x + w * ratio, y, w * (1 - ratio), h, False)
        else:
            _draw(first, x, y, w, h * ratio, True)
            _draw(second, x, y + h * ratio, w, h * (1 - ratio), True)

    _draw(pairs, 0.0, 0.0, 1.0, 1.0, True)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")


def render_chart(chart: dict[str, Any]) -> bytes | None:
    """把图表 spec 渲染成 PNG 字节；KPI/无数据/不支持类型返回 None。"""
    chart_type = str(chart.get("type") or "")
    if chart_type in ("kpi", ""):
        return None
    try:
        _setup()
        import matplotlib.pyplot as plt

        categories, values = _values(chart)
        money = bool(chart.get("money"))
        if chart_type in ("scatter",) and not values:
            figure, ax = plt.subplots(figsize=(7.2, 4.2))
            _scatter(ax, chart)
        elif chart_type in ("pie", "donut", "rose"):
            if not values:
                return None
            figure, ax = plt.subplots(figsize=(6.6, 4.2))
            _pie(ax, categories, values, donut=chart_type != "pie")
        elif chart_type == "treemap":
            if not values:
                return None
            figure, ax = plt.subplots(figsize=(7.2, 4.2))
            _treemap(ax, categories, values)
        elif chart_type == "waterfall":
            if not values:
                return None
            figure, ax = plt.subplots(figsize=(7.2, 4.2))
            _waterfall(ax, categories, values, money)
        elif not values:
            return None
        else:
            figure, ax = plt.subplots(figsize=(7.2, 4.2))
            if chart_type == "line":
                _line(ax, categories, values, money)
            elif chart_type == "bar_horizontal":
                _bar(ax, categories, values, True, money)
            else:
                _bar(ax, categories, values, False, money)
        ax.set_title(str(chart.get("title") or ""), fontsize=12, pad=10, loc="left")
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=150, facecolor="#fffdf8")
        plt.close(figure)
        return buffer.getvalue()
    except Exception:
        logger.warning("图表渲染失败（type=%s）", chart_type, exc_info=True)
        return None


def chart_data_uri(chart: dict[str, Any]) -> str | None:
    data = render_chart(chart)
    if data is None:
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def embed_chart_images(markdown: str, charts: list[dict[str, Any]]) -> str:
    """把 Markdown 里的 `chart://<chart_id>` 替换为 PNG data URI（导出的 .md 可离线看图）。"""
    by_id = {str(chart.get("id")): chart for chart in charts if chart.get("id")}
    cache: dict[str, str | None] = {}

    def _replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        ref = match.group(2).strip()
        if "@" in ref or ref not in by_id:
            return match.group(0)
        if ref not in cache:
            cache[ref] = chart_data_uri(by_id[ref])
        uri = cache[ref]
        return f"![{alt}]({uri})" if uri else match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(chart://([^)]+)\)", _replace, markdown)

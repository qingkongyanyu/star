"""分析计划（AnalysisPlan）：把自然语言需求拆成结构化槽位 + 可执行子任务。

天枢（Planner）的「意图识别」在这里落地，纯规则、可解释、可单测；LLM 只作为可选补充
（`llm_hints` 仅在规则未识别时采用，且所有取值都要通过 schema/catalog 校验）。

槽位：分析类型（可多标签）× 指标（可多个）× 维度 × 时间粒度 × 时间范围 × 过滤 × TopN × 图表偏好。
子任务：每个 task = (kind, metric, dim, granularity, top_n, sort)，由取数 Agent 执行成独立数据集。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.intent import Intent, classify_intent
from app.services.metric_catalog import (
    ColumnProfile,
    MetricCandidate,
    build_column_profiles,
    build_metric_catalog,
    default_metrics,
    resolve_metrics,
)

# 分析类型优先级（primary 取最具体）
_ANALYSIS_ORDER = (
    "quality",
    "correlation",
    "attribution",
    "anomaly",
    "prediction",
    "trend",
    "ranking",
    "comparison",
    "distribution",
    "composition",
    "descriptive",
)

_EXTRA_PATTERNS: dict[str, tuple[str, ...]] = {
    "ranking": ("排名", "排行", "top", "前几", "前三", "前五", "前十", "最高", "最低", "榜首", "垫底"),
    "composition": ("占比", "构成", "结构", "份额", "比例", "组成"),
    "correlation": ("相关", "相关性", "关系", "关联", "影响"),
    "quality": ("数据质量", "缺失", "重复", "脏数据", "异常值", "完整率", "质量"),
    "distribution": ("分布", "直方", "区间", "频次", "集中度"),
    "anomaly": ("异常", "波动", "突增", "突降", "离群", "骤降", "骤增", "告警"),
    "trend": ("趋势", "走势", "变化", "增长", "下滑", "上升", "同比", "环比", "逐月", "月度", "季度"),
    "attribution": ("为什么", "原因", "归因", "贡献", "驱动", "根因"),
    "prediction": ("预测", "预估", "外推", "未来", "预计", "展望"),
}

_TIME_KEYWORDS = ("月", "月份", "季度", "年", "年度", "日", "天", "周", "时间", "日期")

_DIM_ALIASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("大区", "战区", "区域", "地区", "地域"), ("地区", "区域", "大区", "战区", "region", "area")),
    (("省份", "省"), ("省份", "省", "province")),
    (("城市", "市"), ("城市", "city")),
    (("品类", "类目", "分类", "类别"), ("商品类目", "品类", "类目", "类别", "分类", "category")),
    (("渠道",), ("渠道", "channel")),
    (("品牌",), ("品牌", "brand")),
    (("状态",), ("订单状态", "状态", "status")),
    (("支付方式", "支付"), ("支付方式", "payment")),
    (("会员",), ("是否会员", "会员", "member")),
    (("物流",), ("物流方式", "物流")),
    (("户型",), ("户型",)),
    (("朝向",), ("朝向",)),
    (("装修",), ("装修",)),
    (("楼层",), ("楼层",)),
    (("小区",), ("小区", "小区名字")),
    (("性别",), ("性别", "gender")),
)

_CHART_WORDS: dict[str, tuple[str, ...]] = {
    "pie": ("饼图", "饼状图", "环形图", "环图", "甜甜圈"),
    "bar": ("柱状图", "柱形图", "条形图", "横向柱"),
    "line": ("折线图", "趋势图", "曲线图", "面积图"),
    "scatter": ("散点图", "相关图"),
    "heatmap": ("热力图", "热图"),
    "radar": ("雷达图",),
    "funnel": ("漏斗图", "漏斗"),
    "treemap": ("树图", "矩形树图"),
    "waterfall": ("瀑布图",),
    "boxplot": ("箱线图", "箱型图"),
}

_METRIC_REQUEST_TOKENS = (
    "销售额", "销售金额", "销售", "收入", "营收", "利润", "成本", "金额", "实付金额", "应付金额",
    "成交额", "GMV", "gmv", "客单价", "毛利率", "单价", "均价", "总价", "订单量", "订单数", "客户数",
    "用户数", "记录数", "数量", "销量", "同比", "环比", "占比", "转化率", "平均",
)

# 需要做「字段可用性诚实校验」的具体业务指标（不含同比/环比/占比等分析算子）
_FIELD_METRIC_TOKENS = (
    "实付金额", "应付金额", "销售额", "销售金额", "成交额", "GMV", "gmv", "客单价", "毛利率", "利润率",
    "转化率", "退款金额", "成本", "利润", "单价", "均价", "总价", "订单量", "订单数", "客户数", "用户数",
    "销量", "数量",
)


@dataclass
class AnalysisTask:
    """一个可执行的分析子任务（对应一个数据集/一张或多张图）。"""

    id: str
    kind: str
    metric_id: str
    metric_name: str
    dim: str | None
    dim_type: str  # time | category | none
    dim_label: str
    granularity: str | None = None
    top_n: int | None = None
    sort: str = "desc"
    filters: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    secondary_metric_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "dim": self.dim,
            "dim_type": self.dim_type,
            "dim_label": self.dim_label,
            "granularity": self.granularity,
            "top_n": self.top_n,
            "sort": self.sort,
            "filters": self.filters,
            "title": self.title,
            "secondary_metric_id": self.secondary_metric_id,
        }


@dataclass
class AnalysisPlan:
    """Planner 的结构化产出（落库 task.plan_json，供全链 Agent 消费）。"""

    requirement: str
    intent: dict[str, Any]
    metrics: list[dict[str, Any]]
    unavailable: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]
    filters: list[dict[str, Any]]
    time_range: dict[str, Any] | None
    top_n: int | None
    tasks: list[dict[str, Any]]
    notes: list[str]
    summary: str
    plan_source: str
    schema: dict[str, Any] = field(default_factory=dict)
    chart_preference: list[str] = field(default_factory=list)
    llm_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "intent": self.intent,
            "metrics": self.metrics,
            "unavailable": self.unavailable,
            "dimensions": self.dimensions,
            "filters": self.filters,
            "time_range": self.time_range,
            "top_n": self.top_n,
            "tasks": self.tasks,
            "notes": self.notes,
            "summary": self.summary,
            "plan_source": self.plan_source,
            "schema": self.schema,
            "chart_preference": self.chart_preference,
            "llm_used": self.llm_used,
        }

    @property
    def primary_metric(self) -> dict[str, Any] | None:
        return self.metrics[0] if self.metrics else None


# ---------- 槽位抽取 ----------


_EVIDENCE_EXTRA: dict[str, tuple[str, ...]] = {
    "comparison": ("对比", "比较", "相比", "差异", "高于", "低于", "vs", "pk"),
    "ranking": ("排名", "排行", "最高", "最低", "榜首", "垫底", "top", "前几"),
}


def detect_analysis_evidence(requirement: str) -> list[str]:
    """意图证据词：记录「为什么判成这个意图」的原文命中词（可解释/可审计）。"""
    req = (requirement or "").lower()
    hits: list[str] = []
    for patterns in (*_EXTRA_PATTERNS.values(), *_EVIDENCE_EXTRA.values()):
        for pattern in patterns:
            low = pattern.lower()
            if low in req and pattern not in hits:
                hits.append(pattern)
    return hits[:8]


def detect_analysis_types(requirement: str) -> list[str]:
    """多标签分析类型（规则命中，按优先级排序）。"""
    req = (requirement or "").lower()
    labels: list[str] = []
    for label, patterns in _EXTRA_PATTERNS.items():
        if any(pattern.lower() in req for pattern in patterns):
            labels.append(label)
    intent: Intent = classify_intent(requirement)
    for label in intent.intents:
        if label == "descriptive":
            continue
        mapped = "comparison" if label == "comparison" else label
        if mapped not in labels:
            labels.append(mapped)
    if not labels:
        labels = ["descriptive"]
    labels.sort(key=lambda name: _ANALYSIS_ORDER.index(name) if name in _ANALYSIS_ORDER else 99)
    return labels


def extract_time_range(requirement: str) -> dict[str, Any] | None:
    """时间范围槽位：2025 年 / 2025-03 / 近 6 个月 / 本季度 等。"""
    req = (requirement or "").strip()
    if not req:
        return None
    match = re.search(r"(20\d{2})\s*年", req)
    if match:
        year = int(match.group(1))
        return {"kind": "year", "year": year, "label": f"{year} 年"}
    match = re.search(r"(20\d{2})[-/](\d{1,2})", req)
    if match:
        return {
            "kind": "month",
            "year": int(match.group(1)),
            "month": int(match.group(2)),
            "label": f"{match.group(1)}-{int(match.group(2)):02d}",
        }
    match = re.search(r"(?:近|最近)\s*(\d+)\s*(天|日|周|个月|月|年)", req)
    if match:
        return {"kind": "relative", "amount": int(match.group(1)), "unit": match.group(2), "label": match.group(0)}
    if "本季度" in req or "这季度" in req:
        return {"kind": "quarter", "offset": 0, "label": "本季度"}
    if "上季度" in req:
        return {"kind": "quarter", "offset": -1, "label": "上季度"}
    if "本月" in req:
        return {"kind": "month_offset", "offset": 0, "label": "本月"}
    if "上月" in req:
        return {"kind": "month_offset", "offset": -1, "label": "上月"}
    if "全年" in req:
        return {"kind": "year", "year": None, "label": "全年"}
    return None


def extract_top_n(requirement: str) -> int | None:
    """TopN 槽位：top10 / 前 5 / 前五。"""
    req = (requirement or "").lower()
    match = re.search(r"top\s*(\d+)", req)
    if match:
        return max(1, min(50, int(match.group(1))))
    match = re.search(r"前\s*(\d+)", req)
    if match:
        return max(1, min(50, int(match.group(1))))
    cn_numbers = {"三": 3, "五": 5, "十": 10, "二十": 20}
    match = re.search(r"前([一二三四五六七八九十]+)", req)
    if match:
        return cn_numbers.get(match.group(1), 10)
    if any(word in req for word in ("排名", "排行", "榜首", "垫底")):
        return 10
    return None


def extract_chart_preference(requirement: str) -> list[str]:
    """用户点名的图表类型（优先级高于自动选择）。"""
    req = requirement or ""
    found: list[str] = []
    for chart_type, words in _CHART_WORDS.items():
        if any(word in req for word in words) and chart_type not in found:
            found.append(chart_type)
    return found


def _match_dimension_mentions(requirement: str, columns: list[ColumnProfile]) -> list[ColumnProfile]:
    """需求点名的维度：按「在需求中出现的先后」排序（列名直接命中 > 同义词映射）。"""
    req = (requirement or "").lower()
    scored: list[tuple[int, int, int, ColumnProfile]] = []
    for column in columns:
        if column.role not in ("category", "text"):
            continue
        name = column.name.lower()
        position = req.find(name) if name else -1
        if position >= 0:
            scored.append((100, position, column.cardinality or 0, column))
            continue
        for keywords, aliases in _DIM_ALIASES:
            if not any(keyword in req for keyword in keywords):
                continue
            if any(alias.lower() == name for alias in aliases):
                alias_position = min((req.find(keyword) for keyword in keywords if keyword in req), default=len(req))
                scored.append((80, alias_position, column.cardinality or 0, column))
                break
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    seen: set[str] = set()
    out: list[ColumnProfile] = []
    for _score, _position, _cardinality, column in scored:
        if column.name not in seen:
            seen.add(column.name)
            out.append(column)
    return out


def _best_category(columns: list[ColumnProfile], *, max_cardinality: int = 60) -> ColumnProfile | None:
    candidates = [
        c
        for c in columns
        if c.role == "category" and (c.cardinality is None or 1 < c.cardinality <= max_cardinality)
    ]
    if not candidates:
        candidates = [c for c in columns if c.role == "category"]
    return min(candidates, key=lambda c: c.cardinality if c.cardinality is not None else 999, default=None)


def _best_time(columns: list[ColumnProfile]) -> ColumnProfile | None:
    times = [c for c in columns if c.role == "time"]
    return times[0] if times else None


_COMPOSITION_WORDS = ("占比", "构成", "结构", "份额", "比例", "组成")


def _dimension_position(requirement: str, column: ColumnProfile) -> int:
    """维度在需求中的位置：列名直查，英文列则回退到同义词关键词位置。"""
    req = requirement or ""
    position = req.find(column.name)
    if position >= 0:
        return position
    for keywords, aliases in _DIM_ALIASES:
        if any(alias.lower() == column.name.lower() for alias in aliases):
            positions = [req.find(keyword) for keyword in keywords if keyword in req]
            if positions:
                return min(positions)
    return -1


def _composition_dimension(
    requirement: str, mentioned: list[ColumnProfile], fallback: ColumnProfile | None
) -> ColumnProfile | None:
    """构成分析取「离占比/构成关键词最近」的维度（如「渠道构成」→渠道，「商品类目占比」→商品类目）。"""
    req = requirement or ""
    keyword_positions = [req.find(word) for word in _COMPOSITION_WORDS if word in req]
    if not keyword_positions:
        return fallback
    keyword_position = min(keyword_positions)
    best: ColumnProfile | None = None
    best_distance = 10**6
    for column in mentioned:
        position = _dimension_position(req, column)
        if position < 0:
            continue
        distance = abs(position - keyword_position)
        if distance < best_distance:
            best_distance = distance
            best = column
    return best or fallback


def _infer_granularity(requirement: str, time_column: ColumnProfile | None) -> str:
    req = requirement or ""
    if "季度" in req or "季" in req:
        return "quarter"
    if "日" in req or "天" in req:
        return "day"
    if "周" in req:
        return "week"
    # 「月」必须先于「年」判断：否则「2025 年各月」会被「年」误判为年粒度。
    if "月" in req:
        return "month"
    if "年度" in req or "每年" in req or "逐年" in req or "按年" in req or "年" in req:
        return "year"
    # 数据本身是月桶（2025-11）→ 月；否则默认月（趋势的可读性最好）
    sample = next((s for s in (time_column.samples if time_column else []) if s), "")
    if re.fullmatch(r"\d{4}-\d{2}", sample):
        return "month"
    return "month"


def _extract_filters(
    requirement: str, columns: list[ColumnProfile], time_range: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """维度值过滤：需求里出现的真实类别值（如「华东」「已完成」）。"""
    filters: list[dict[str, Any]] = []
    req = (requirement or "").strip()
    if not req:
        return filters
    for column in columns:
        if column.role not in ("category", "text"):
            continue
        values = [value for value in column.samples if value and len(value) >= 2 and value in req]
        if values:
            filters.append({"field": column.name, "op": "in", "values": list(dict.fromkeys(values))})
    if time_range:
        filters.append({"field": "__time__", "op": "range", "value": time_range})
    return filters


def _task_title(kind: str, dim_label: str, metric_name: str) -> str:
    if kind == "trend":
        return f"{metric_name}的{dim_label}趋势"
    if kind in ("comparison", "ranking"):
        return f"各{dim_label}的{metric_name}对比"
    if kind == "composition":
        return f"{metric_name}的{dim_label}构成"
    if kind == "distribution":
        return f"{metric_name}的分布"
    if kind == "correlation":
        return f"{metric_name}与其他指标的关系"
    if kind == "attribution":
        return f"{metric_name}的{dim_label}贡献拆解"
    if kind == "anomaly":
        return f"{metric_name}的异常检测"
    return f"{metric_name}分析"


def _plan_tasks(
    requirement: str,
    labels: list[str],
    metrics: list[MetricCandidate],
    columns: list[ColumnProfile],
    time_range: dict[str, Any] | None,
    top_n: int | None,
    filters: list[dict[str, Any]],
    chart_preference: list[str],
    notes: list[str],
    dim_hints: list[str] | None = None,
) -> list[AnalysisTask]:
    if not metrics:
        return []
    primary = metrics[0]
    mentioned_dims = _match_dimension_mentions(requirement, columns)
    if not mentioned_dims and dim_hints:
        mentioned_dims = [
            c for c in columns if c.name in dim_hints and c.role in ("category", "time")
        ]
    primary_dim = mentioned_dims[0] if mentioned_dims else _best_category(columns)
    composition_dim = _composition_dimension(requirement, mentioned_dims, primary_dim)
    secondary_dim = next(
        (dim for dim in mentioned_dims[1:] if dim.role != "time" and dim.name != (composition_dim.name if composition_dim else None)),
        None,
    )
    comparison_dim = secondary_dim if secondary_dim is not None else primary_dim
    time_column = _best_time(columns)
    if time_column is not None and any(keyword in (requirement or "") for keyword in _TIME_KEYWORDS):
        mentioned_time = next((c for c in columns if c.role == "time" and c.name in (requirement or "")), None)
        time_column = mentioned_time or time_column

    tasks: list[AnalysisTask] = []
    seen: dict[tuple[str, str | None, str], AnalysisTask] = {}

    def _add(
        kind: str,
        metric: MetricCandidate,
        dim: ColumnProfile | None,
        *,
        granularity: str | None = None,
        task_top_n: int | None = None,
        sort: str = "desc",
        secondary: MetricCandidate | None = None,
    ) -> None:
        if dim is None:
            dim_name, dim_type, dim_label = None, "none", "全部"
        elif dim.role == "time":
            dim_name, dim_type, dim_label = dim.name, "time", dim.name
        else:
            dim_name, dim_type, dim_label = dim.name, "category", dim.name
        # 归因与对比共用同一维度但语义不同（对比=排序，归因=贡献拆解），保留为两个数据集；
        # 其余同指标同维度的子任务合并，避免重复取数。
        key_kind = kind if kind in ("attribution", "composition", "distribution") else ""
        key = (metric.id, dim_name if kind != "correlation" else f"corr:{dim_name}", key_kind)
        if key in seen:
            merged = seen[key]
            if kind not in merged.kind:
                merged.kind = f"{merged.kind}+{kind}" if merged.kind else kind
            return
        task = AnalysisTask(
            id=f"a{len(tasks) + 1}",
            kind=kind,
            metric_id=metric.id,
            metric_name=metric.name,
            dim=dim_name,
            dim_type=dim_type,
            dim_label=dim_label,
            granularity=granularity,
            top_n=task_top_n,
            sort=sort,
            filters=filters,
            title=_task_title(kind, dim_label, metric.name),
            secondary_metric_id=secondary.id if secondary else None,
        )
        tasks.append(task)
        seen[key] = task

    wants_time = any(label in ("trend", "prediction") for label in labels) or "趋势" in (requirement or "")
    wants_compare = any(label in ("comparison", "ranking", "attribution") for label in labels)
    wants_attribution = "attribution" in labels or "waterfall" in chart_preference
    wants_composition = "composition" in labels
    wants_distribution = "distribution" in labels
    wants_correlation = "correlation" in labels
    wants_anomaly = "anomaly" in labels

    if wants_time:
        if time_column is not None:
            _add(
                "trend",
                primary,
                time_column,
                granularity=_infer_granularity(requirement, time_column),
                task_top_n=None,
                sort="asc",
            )
        else:
            notes.append("需求含趋势意图，但数据源没有可解析的时间字段，已跳过时间序列分析")

    if wants_compare and comparison_dim is not None:
        _add(
            "ranking" if "ranking" in labels else "comparison",
            primary,
            comparison_dim,
            task_top_n=top_n or 10,
            sort="asc" if any(word in (requirement or "") for word in ("最低", "垫底", "升序")) else "desc",
        )
    # 归因/贡献意图：单独产出一个归因数据集，图表选择器据此出瀑布图（与对比柱状图互补）
    if wants_attribution and comparison_dim is not None:
        _add("attribution", primary, comparison_dim, task_top_n=top_n or 10)
    # 「按地区看趋势」这类需求：时间趋势之外再补一个点名维度的对比，避免只给总体走势
    if (
        wants_time
        and mentioned_dims
        and primary_dim is not None
        and (composition_dim is None or primary_dim.name != composition_dim.name)
    ):
        _add("comparison", primary, primary_dim, task_top_n=top_n or 10)
    if wants_composition:
        target_dim = composition_dim
        if target_dim is not None and target_dim.cardinality and target_dim.cardinality > 8:
            target_dim = _best_category(columns, max_cardinality=8) or target_dim
        if target_dim is not None:
            _add("composition", primary, target_dim, task_top_n=12)
    if wants_distribution:
        _add("distribution", primary, None, task_top_n=None)
    if wants_anomaly:
        anomaly_dim = time_column if time_column is not None else primary_dim
        if anomaly_dim is not None:
            _add("anomaly", primary, anomaly_dim, granularity=_infer_granularity(requirement, anomaly_dim) if anomaly_dim.role == "time" else None)
    if wants_correlation and len(metrics) >= 2:
        # 有时间维 → 按时间桶聚合两个指标再做相关（销售额 vs 订单量这类跨量纲指标需要对齐周期）；
        # 无时间维 → 退化为明细散点（要求两个数值列）。
        if time_column is not None:
            _add(
                "correlation",
                primary,
                time_column,
                granularity="month",
                secondary=metrics[1],
            )
        else:
            _add("correlation", primary, None, secondary=metrics[1])

    # 无明确分析类型（描述性）→ 有维度就对比，没有维度就总量
    if not tasks:
        if primary_dim is not None:
            _add("comparison", primary, primary_dim, task_top_n=top_n or 10)
        else:
            _add("summary", primary, None)

    # 多指标：为第二个指标补一个同维度对比（仅对比/构成/无任务时；纯趋势/相关不硬凑）
    if len(metrics) >= 2 and len(tasks) < 4 and (wants_compare or wants_composition or not tasks):
        _add("comparison", metrics[1], comparison_dim, task_top_n=top_n or 10)

    # 用户点名图表：没有对应类型时补一个构成/对比任务
    if chart_preference and len(tasks) < 4:
        if any(chart in ("pie", "treemap") for chart in chart_preference) and composition_dim is not None:
            _add("composition", primary, composition_dim, task_top_n=12)
        if (
            any(chart in ("scatter", "heatmap") for chart in chart_preference)
            and len(metrics) >= 2
            and not any("correlation" in task.kind for task in tasks)
        ):
            _add("correlation", primary, None, secondary=metrics[1])

    return tasks[:4]


def _missing_requested_metrics(
    requirement: str,
    metrics: list[MetricCandidate],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """需求点名但数据源没有对应字段/口径的指标（诚实说明，不静默替换）。"""
    if not metrics:
        return []
    known_names = {str(item.get("name") or "") for item in existing}
    out: list[dict[str, Any]] = []
    for token in _FIELD_METRIC_TOKENS:
        if token not in (requirement or "") or token in known_names:
            continue
        matched = any(
            token in (metric.name or "")
            or token in (metric.column or "")
            or any(token in alias for alias in metric.aliases)
            for metric in metrics
        )
        if matched:
            continue
        out.append(
            {
                "name": token,
                "feasible": False,
                "reason": f"数据源中没有可计算的「{token}」字段/口径，已改用可执行指标",
            }
        )
        known_names.add(token)
    return out


def plan_analysis(
    requirement: str,
    schema: dict[str, Any],
    *,
    llm_hints: dict[str, Any] | None = None,
    plan_source: str = "rules",
) -> AnalysisPlan:
    """生成结构化分析计划。`llm_hints` 只补规则未识别的槽位，且全部要过 schema 校验。"""
    columns = build_column_profiles(schema)
    catalog = build_metric_catalog(columns)
    labels = detect_analysis_types(requirement)
    hints = llm_hints or {}

    # 规则优先；规则没识别出指标时，才用 LLM 给的指标名去目录里找
    metrics, unavailable = resolve_metrics(requirement, catalog)
    defaulted_metrics = False
    if not metrics and isinstance(hints.get("metrics"), list):
        guessed = " ".join(str(item) for item in hints["metrics"] if item)
        metrics, extra_unavailable = resolve_metrics(guessed, catalog)
        unavailable.extend(extra_unavailable)
    if not metrics:
        metrics = default_metrics(catalog, limit=2)
        defaulted_metrics = bool(metrics)
    elif len(metrics) == 1:
        # 补一个默认指标丰富分析；但同一列的 SUM/AVG 不能并存（如「平均单价」不应再补「单价合计」）
        primary_metric = metrics[0]
        fallback = [
            item
            for item in default_metrics(catalog, limit=4)
            if item.id != primary_metric.id
            and not (item.agg == "sum" and item.column == primary_metric.column)
        ]
        if fallback:
            metrics.append(fallback[0])

    # 需求点名但真实字段/口径不支持的指标 → 诚实说明（不静默用别的指标冒充）
    unavailable.extend(_missing_requested_metrics(requirement, metrics, unavailable))

    # LLM 补充分析类型（规则没有识别到的）
    if isinstance(hints.get("analysis_types"), list):
        for label in hints["analysis_types"]:
            if isinstance(label, str) and label in _ANALYSIS_ORDER and label not in labels:
                labels.append(label)
        labels.sort(key=lambda name: _ANALYSIS_ORDER.index(name) if name in _ANALYSIS_ORDER else 99)

    # LLM 补充维度通过 dim_hints 传给 _plan_tasks（必须真实存在且是类别/时间列）
    time_range = extract_time_range(requirement)
    if time_range is None and isinstance(hints.get("time_range"), dict):
        time_range = hints["time_range"] if hints["time_range"].get("kind") else None
    top_n = extract_top_n(requirement)
    if top_n is None and isinstance(hints.get("top_n"), int):
        top_n = max(1, min(50, hints["top_n"]))
    chart_preference = extract_chart_preference(requirement)
    if isinstance(hints.get("chart_preference"), list):
        for chart in hints["chart_preference"]:
            if chart in _CHART_KEYS and chart not in chart_preference:
                chart_preference.append(chart)

    filters = _extract_filters(requirement, columns, time_range)
    notes: list[str] = []
    dim_hints = [str(name) for name in (hints.get("dimensions") or []) if name]
    if defaulted_metrics and metrics:
        extra = f"，并补充「{metrics[1].name}」" if len(metrics) > 1 else ""
        notes.append(f"需求未点名可用指标，已按数据默认选择「{metrics[0].name}」{extra}")
    if not metrics:
        notes.append("数据源没有任何可计算指标（缺少数值/标识字段），只能做结构探查")
    tasks = _plan_tasks(
        requirement,
        labels,
        metrics,
        columns,
        time_range,
        top_n,
        filters,
        chart_preference,
        notes,
        dim_hints=dim_hints,
    )
    if not tasks and metrics:
        tasks = [
            AnalysisTask(
                id="a1",
                kind="summary",
                metric_id=metrics[0].id,
                metric_name=metrics[0].name,
                dim=None,
                dim_type="none",
                dim_label="全部",
                title=f"{metrics[0].name}总量",
                filters=filters,
            )
        ]
    if not tasks:
        notes.append("没有可执行的取数任务：请检查数据源字段与需求描述")

    # 维度槽位：以实际子任务使用的维度为准（含默认维度/时间维度）
    dims: list[dict[str, Any]] = []
    seen_dim: set[str] = set()
    for task in tasks:
        if not task.dim or task.dim in seen_dim:
            continue
        seen_dim.add(task.dim)
        column = next((c for c in columns if c.name == task.dim), None)
        dims.append(
            {
                "name": task.dim,
                "role": task.dim_type,
                "cardinality": column.cardinality if column is not None else None,
            }
        )

    intent_summary = " · ".join(labels[:3])
    summary = f"识别意图：{intent_summary}；指标：{('、'.join(m.name for m in metrics) or '无')}"
    if tasks:
        summary += f"；子任务：{len(tasks)} 个（{'、'.join(t.kind for t in tasks)}）"
    return AnalysisPlan(
        requirement=requirement,
        intent={
            "primary": labels[0] if labels else "descriptive",
            "labels": labels,
            "time_series": any(label in ("trend", "prediction", "anomaly") for label in labels),
            "evidence": detect_analysis_evidence(requirement),
        },
        metrics=[metric.to_dict() for metric in metrics],
        unavailable=unavailable,
        dimensions=dims,
        filters=filters,
        time_range=time_range,
        top_n=top_n,
        tasks=[task.to_dict() for task in tasks],
        notes=notes,
        summary=summary,
        plan_source=plan_source,
        schema=schema,
        chart_preference=chart_preference,
        llm_used=plan_source in ("llm", "hybrid"),
    )


# 允许的图表键（与 chart_selector 对齐）
_CHART_KEYS = {
    "line", "bar", "bar_horizontal", "bar_stacked", "bar_grouped", "pie", "donut", "rose",
    "scatter", "heatmap", "radar", "funnel", "treemap", "boxplot", "waterfall", "kpi",
}


def plan_instruction(plan: AnalysisPlan) -> str:
    """把计划压缩成 per-node 指令文本（UI/日志可读；结构化数据在 task.plan_json）。"""
    metrics = "、".join(f"{m['name']}[{m['formula']}]" for m in plan.metrics[:4]) or "无"
    dims = "、".join(d["name"] for d in plan.dimensions[:3]) or "无"
    parts = [
        f"意图：{plan.intent.get('primary', 'descriptive')}（{'/'.join(plan.intent.get('labels', []))}）",
        f"指标：{metrics}",
        f"维度：{dims}",
    ]
    if plan.time_range:
        parts.append(f"时间：{plan.time_range.get('label')}")
    if plan.top_n:
        parts.append(f"TopN：{plan.top_n}")
    if plan.tasks:
        parts.append("子任务：" + "；".join(f"{t['kind']}·{t['metric_name']}·{t['dim_label']}" for t in plan.tasks[:4]))
    return " | ".join(parts)

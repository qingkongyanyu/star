"""意图识别引擎（v3）：把自然语言需求分类为结构化分析意图，驱动 DAG 剪枝与执行策略。

对比旧实现（v2.x 仅 `has_metric_intent` 布尔判断，且只影响"是否保留 metric 链"）：
本模块输出多维度结构化 `Intent`，覆盖常见数据分析意图，供 Planner（DAG 剪枝）与
各执行 Agent（取数维度/聚合方式/报告结构/可视化类型）复用，实现"先识别意图，再决定怎么做"。

设计原则（诚实 + 确定性 + 可单测）：
- **纯关键词/正则规则**，不依赖 LLM，保证可解释、可单测、可兜底；
- 每个意图维度独立判定、可叠加（一条需求可同时是"趋势 + 金额"）；
- 未命中任何规则 → 默认 `descriptive`（描述性统计），绝不空返回；
- 只做"识别"，不做"执行"——识别结果由 Planner/Scheduler 消费。
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- 分析意图主类别 ----
ANALYSIS_INTENTS = (
    "trend",        # 趋势分析：随时间变化（近N月/同比/环比/走势/增长）
    "comparison",   # 对比分析：A vs B、排行、对比、差异
    "anomaly",      # 异常检测：异常/突增/突降/波动/离群/告警
    "attribution",  # 归因分析：为什么/原因/驱动/根因/影响
    "distribution", # 分布分析：分布/占比/构成/结构/集中度
    "descriptive",  # 描述性统计（默认兜底）：总数/平均/最大/最小/汇总
    "prediction",   # 预测/预警：预测/预估/趋势外推/预警
)

# 金额/指标口径意图（独立于分析意图，决定是否保留 metric_definition 链）
_MONEY_WORDS = (
    "金额", "销售额", "销售", "成交", "收入", "营收", "利润", "营业额", "客单", "房价",
    "单价", "总价", "成本", "gmv", "sales", "amount", "revenue", "price", "cost", "money",
)
_INDICATOR_WORDS = ("指标", "口径", "度量", "metric", "kpi")

# 溯源意图（追加 trace 节点）
_TRACE_WORDS = ("溯源", "追责", "根因", "为什么", "来源", "血统", "血缘")

# ---- 各意图关键词/正则 ----
_TREND_PATTERNS = (
    "趋势", "走势", "变化", "增长", "下降", "下滑", "上升", "同比", "环比", "近几",
    "近几个月", "逐月", "逐日", "随时间", "时间序列", "月度", "季度", "年度", "趋势图",
)
_COMPARISON_PATTERNS = (
    "对比", "比较", "排行", "排名", "差异", "谁更", "哪个", "最高", "最低", "前几", "top",
    "top n", "榜首", "垫底", "pk", "对比图", "横向比较",
)
_ANOMALY_PATTERNS = (
    "异常", "突增", "突降", "飙升", "暴跌", "波动", "离群", "异常值", "告警", "预警",
    "异常检测", "突变", "骤降", "骤增", "反常",
)
_ATTRIBUTION_PATTERNS = (
    "为什么", "原因", "归因", "驱动", "根因", "影响", "导致", "因素", "诱因", "贡献",
)
_DISTRIBUTION_PATTERNS = (
    "分布", "占比", "构成", "结构", "集中度", "份额", "比例", "组成", "分布图", "饼图",
)
_PREDICTION_PATTERNS = (
    "预测", "预估", "外推", "未来", "预计", "展望", "forecast", "预测值", "预判",
)

# 时间/趋势维度词（供 SQLAgent 选维度时优先匹配时间列）
_TIME_DIM_WORDS = ("月", "月份", "季度", "年", "年度", "日期", "周", "ym", "month", "date", "year", "week")


@dataclass(frozen=True)
class Intent:
    """结构化分析意图。"""

    primary: str = "descriptive"          # 主分析意图（ANALYSIS_INTENTS 之一）
    intents: tuple[str, ...] = ("descriptive",)  # 命中的全部意图（可叠加，按优先级排序）
    has_metric: bool = False              # 是否需要指标口径（金额/指标语义）
    has_trace: bool = False               # 是否溯源（追加 trace 节点）
    time_series: bool = False             # 是否时间序列（趋势/预测）
    needs_visualization: bool = True      # 是否需要可视化（本系统恒保留图表，保留字段供扩展）
    keywords: tuple[str, ...] = ()        # 命中的关键词（供指令透传/审计）

    def to_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary,
            "intents": list(self.intents),
            "has_metric": self.has_metric,
            "has_trace": self.has_trace,
            "time_series": self.time_series,
            "keywords": list(self.keywords),
        }


# 意图优先级（决定 primary）：高优先级意图更"具体"，应优先作为主意图
_INTENT_PRIORITY = {
    "prediction": 100,
    "anomaly": 90,
    "attribution": 80,
    "trend": 70,
    "comparison": 60,
    "distribution": 50,
    "descriptive": 0,
}


def _match_keywords(requirement: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
    """返回 requirement 中命中的关键词（去重、保序）。"""
    req = (requirement or "").lower()
    return tuple(p for p in patterns if p in req)


def classify_intent(requirement: str) -> Intent:
    """识别一条需求的分析意图（纯规则，确定性）。

    - 主意图按优先级取"最具体"命中项，未命中任何分析意图 → descriptive；
    - has_metric：金额/指标词命中；
    - has_trace：溯源词命中；
    - time_series：趋势/预测命中（决定时间维度优先）。
    """
    req = (requirement or "").strip()
    if not req:
        return Intent()

    matched: dict[str, tuple[str, ...]] = {
        "trend": _match_keywords(req, _TREND_PATTERNS),
        "comparison": _match_keywords(req, _COMPARISON_PATTERNS),
        "anomaly": _match_keywords(req, _ANOMALY_PATTERNS),
        "attribution": _match_keywords(req, _ATTRIBUTION_PATTERNS),
        "distribution": _match_keywords(req, _DISTRIBUTION_PATTERNS),
        "prediction": _match_keywords(req, _PREDICTION_PATTERNS),
    }
    hit = {k: v for k, v in matched.items() if v}
    if not hit:
        return Intent(has_metric=has_metric_intent(req), has_trace=has_trace_intent(req))

    # 优先级排序（命中多意图时，primary 取最具体者）
    ordered = sorted(hit.items(), key=lambda kv: _INTENT_PRIORITY[kv[0]], reverse=True)
    intents = tuple(k for k, _ in ordered)
    primary = intents[0]
    keywords = tuple(kw for _, kws in ordered for kw in kws)

    return Intent(
        primary=primary,
        intents=intents,
        has_metric=has_metric_intent(req),
        has_trace=has_trace_intent(req),
        time_series=primary in ("trend", "prediction"),
        keywords=keywords,
    )


def has_metric_intent(requirement: str) -> bool:
    """需求是否含金额/销售/指标口径语义（决定是否保留指标定义链）。"""
    req = (requirement or "").lower()
    return any(w in req for w in _MONEY_WORDS) or any(w in req for w in _INDICATOR_WORDS)


def has_trace_intent(requirement: str) -> bool:
    """需求是否含溯源语义（追加 trace 节点）。"""
    return any(w in (requirement or "").lower() for w in _TRACE_WORDS)


def intent_summary(intent: Intent) -> str:
    """生成一句话意图摘要（供审计/指令透传）。"""
    label = {
        "trend": "趋势分析", "comparison": "对比分析", "anomaly": "异常检测",
        "attribution": "归因分析", "distribution": "分布分析", "descriptive": "描述性统计",
        "prediction": "预测预警",
    }
    parts = [label.get(intent.primary, intent.primary)]
    if intent.has_metric:
        parts.append("含指标口径")
    if intent.has_trace:
        parts.append("含溯源")
    return " · ".join(parts)

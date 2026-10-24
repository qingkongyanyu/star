"""意图识别引擎测试（app.services.intent）：多意图分类、金额/溯源判定、边界兜底。"""
from __future__ import annotations

from app.services.intent import (
    classify_intent,
    has_metric_intent,
    has_trace_intent,
    intent_summary,
)


def test_trend_intent() -> None:
    intent = classify_intent("分析近6个月销售趋势变化")
    assert intent.primary == "trend"
    assert intent.time_series is True
    assert intent.has_metric is True  # 含"销售"


def test_comparison_intent() -> None:
    intent = classify_intent("对比各渠道销售额排名")
    assert intent.primary == "comparison"
    assert intent.has_metric is True


def test_anomaly_intent() -> None:
    intent = classify_intent("检测订单量的异常波动并告警")
    assert intent.primary == "anomaly"


def test_attribution_intent() -> None:
    intent = classify_intent("为什么本月销售额下滑了")
    assert intent.primary == "attribution"
    assert intent.has_trace is True  # "为什么"命中溯源词


def test_distribution_intent() -> None:
    intent = classify_intent("分析各品类占比分布")
    assert intent.primary == "distribution"


def test_prediction_intent() -> None:
    intent = classify_intent("预测下季度销售额走势")
    assert intent.primary == "prediction"
    assert intent.time_series is True


def test_descriptive_fallback() -> None:
    intent = classify_intent("统计一下记录总数")
    assert intent.primary == "descriptive"
    assert intent.has_metric is False


def test_empty_requirement_falls_back_to_descriptive() -> None:
    intent = classify_intent("")
    assert intent.primary == "descriptive"
    assert intent.intents == ("descriptive",)


def test_multi_intent_priority() -> None:
    """趋势 + 异常 + 金额 同时命中 → primary 取优先级最高（anomaly）。"""
    intent = classify_intent("销售趋势异常波动预警")
    # anomaly 优先级(90) > trend(70)，primary 应为 anomaly
    assert intent.primary == "anomaly"
    assert "trend" in intent.intents


def test_has_metric_intent_variants() -> None:
    assert has_metric_intent("分析销售额") is True
    assert has_metric_intent("看GMV指标") is True
    assert has_metric_intent("统计记录数") is False


def test_has_trace_intent_variants() -> None:
    assert has_trace_intent("做数据溯源") is True
    assert has_trace_intent("追责是谁的锅") is True
    assert has_trace_intent("为什么下降") is True
    assert has_trace_intent("普通趋势分析") is False


def test_intent_summary() -> None:
    intent = classify_intent("近3月销售趋势")
    assert "趋势" in intent_summary(intent)


def test_intent_to_dict_roundtrip() -> None:
    intent = classify_intent("预测销售额并溯源")
    d = intent.to_dict()
    assert d["primary"] == "prediction"
    assert d["has_trace"] is True
    assert isinstance(d["intents"], list)

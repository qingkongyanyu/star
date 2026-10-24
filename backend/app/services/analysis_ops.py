"""分析算子库：把 datasets 转成「可复核的业务发现」（纯函数，无 DB/LLM）。

洞察 Agent（天机）与报告编撰共用：
- 每个发现都带 `statement + evidence + dataset_id + chart_ids`，数字全部来自数据集；
- 不同分析类型走不同算子（趋势/对比/构成/分布/相关/异常/归因），避免所有任务一套话术。
"""
from __future__ import annotations

import hashlib
import logging
import math
import statistics
from typing import Any

logger = logging.getLogger(__name__)

_MAX_FINDINGS = 14


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None, unit: str = "", *, money: bool = False) -> str:
    if value is None:
        return "—"
    if money:
        return f"¥{value:,.2f}"
    if abs(value) >= 10000:
        return f"{value:,.2f}{unit}"
    return f"{value:,.2f}{unit}".rstrip("0").rstrip(".") if unit else f"{value:,.2f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _labels_values(dataset: dict[str, Any]) -> tuple[list[str], list[float]]:
    labels: list[str] = []
    values: list[float] = []
    for row in dataset.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        number = _num(row[1])
        if number is None:
            continue
        labels.append(str(row[0]))
        values.append(number)
    return labels, values


def _finding(
    dataset: dict[str, Any],
    kind: str,
    title: str,
    statement: str,
    evidence: list[str],
    *,
    severity: str = "info",
    chart_ids: list[str] | None = None,
    value: float | None = None,
) -> dict[str, Any]:
    digest = hashlib.md5(f"{dataset.get('id')}|{kind}|{title}".encode()).hexdigest()[:6]
    return {
        "id": f"f{digest}",
        "dataset_id": dataset.get("id"),
        "kind": kind,
        "title": title,
        "statement": statement,
        "evidence": evidence,
        "severity": severity,
        "chart_ids": chart_ids or [],
        "metric": dataset.get("metric_name"),
        "unit": dataset.get("unit") or "",
        "value": value,
    }


def _share(value: float, total: float) -> float:
    return (value / total * 100) if total else 0.0


def _quartiles(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    if len(ordered) < 4:
        return ordered[0], ordered[-1]
    q1 = ordered[len(ordered) // 4]
    q3 = ordered[(len(ordered) * 3) // 4]
    return q1, q3


def _std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None

    def _rank(values: list[float]) -> list[float]:
        ordered = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        for position, index in enumerate(ordered, start=1):
            ranks[index] = float(position)
        return ranks

    return _pearson(_rank(xs), _rank(ys))


def _correlation_label(value: float | None) -> str:
    if value is None:
        return "样本不足，无法判断"
    strength = abs(value)
    if strength >= 0.8:
        level = "强"
    elif strength >= 0.5:
        level = "中等"
    elif strength >= 0.3:
        level = "弱"
    else:
        level = "几乎无"
    direction = "正" if value >= 0 else "负"
    return f"{level}{direction}相关"


# ---------- 各分析类型 ----------


def summary_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if not values:
        return []
    total = sum(values)
    if dataset.get("agg") == "ratio":
        total = total / len(values)
    money = dataset.get("metric_kind") == "money"
    unit = dataset.get("unit") or ""
    metric = dataset.get("metric_name") or "指标"
    statement = (
        f"{metric}{'平均' if dataset.get('agg') == 'ratio' else '合计'} {_fmt(total, unit, money=money)}，覆盖 {len(values)} 个分组；"
        f"均值 {_fmt(total / len(values) if dataset.get('agg') != 'ratio' else total, unit, money=money)}，"
        f"最高 {labels[values.index(max(values))]}，最低 {labels[values.index(min(values))]}。"
    )
    return [
        _finding(
            dataset,
            "summary",
            f"{metric}总量",
            statement,
            [f"合计={round(total, 2)}", f"分组数={len(values)}", f"均值={round(total / len(values), 2)}"],
            chart_ids=chart_ids,
            value=round(total, 2),
        )
    ]


def comparison_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if len(values) < 2:
        return summary_findings(dataset, chart_ids)
    money = dataset.get("metric_kind") == "money"
    unit = dataset.get("unit") or ""
    metric = dataset.get("metric_name") or "指标"
    dim = dataset.get("dim_label") or "维度"
    total = sum(values)
    pairs = sorted(zip(labels, values), key=lambda item: item[1], reverse=True)
    top_label, top_value = pairs[0]
    low_label, low_value = pairs[-1]
    top_share = _share(top_value, total)
    top3_share = _share(sum(value for _label, value in pairs[:3]), total)
    gap = top_value / low_value if low_value > 0 else None
    avg_mode = dataset.get("agg") in ("avg", "ratio")
    findings: list[dict[str, Any]] = []
    if avg_mode:
        statement = (
            f"{dim}中 {top_label} 的{metric}最高（{_fmt(top_value, unit, money=money)}），"
            f"{low_label} 最低（{_fmt(low_value, unit, money=money)}）"
            + (f"，相差 {gap:.1f} 倍。" if gap else "。")
        )
        evidence = [f"{top_label}={top_value}", f"{low_label}={low_value}"]
    else:
        statement = (
            f"{top_label} 以 {_fmt(top_value, unit, money=money)}（{_pct(top_share)}）居首，"
            f"{low_label} 最低（{_fmt(low_value, unit, money=money)}，{_pct(_share(low_value, total))}）；"
            f"头部 3 个{dim}合计占 {_pct(top3_share)}。"
        )
        evidence = [
            f"{top_label}占比={round(top_share, 1)}%",
            f"CR3={round(top3_share, 1)}%",
            f"首尾差={round(top_value - low_value, 2)}",
        ]
    findings.append(
        _finding(
            dataset,
            "comparison",
            f"{dim}的{metric}格局",
            statement,
            evidence,
            severity="info" if top3_share < 70 else "warning",
            chart_ids=chart_ids,
            value=round(top_share, 1),
        )
    )
    if top3_share >= 70:
        findings.append(
            _finding(
                dataset,
                "concentration",
                "头部集中度偏高",
                f"前 3 个{dim}贡献了 {_pct(top3_share)} 的{metric}，结构偏集中；需关注长尾{dim}的经营质量与头部依赖风险。",
                [f"CR3={round(top3_share, 1)}%", f"分组数={len(values)}"],
                severity="warning",
                chart_ids=chart_ids,
            )
        )
    return findings


def trend_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if len(values) < 2:
        return summary_findings(dataset, chart_ids)
    money = dataset.get("metric_kind") == "money"
    unit = dataset.get("unit") or ""
    metric = dataset.get("metric_name") or "指标"
    ordered = sorted(zip(labels, values), key=lambda item: item[0])
    labels = [item[0] for item in ordered]
    values = [item[1] for item in ordered]
    first, last = values[0], values[-1]
    growth = ((last - first) / abs(first) * 100) if first else None
    peak_index = max(range(len(values)), key=lambda i: values[i])
    trough_index = min(range(len(values)), key=lambda i: values[i])
    changes = [
        ((values[i] - values[i - 1]) / abs(values[i - 1]) * 100) if values[i - 1] else 0.0
        for i in range(1, len(values))
    ]
    volatility = _std(changes) if changes else 0.0
    statement = (
        f"{metric}从 {labels[0]} 的 {_fmt(first, unit, money=money)} 变化到 {labels[-1]} 的 {_fmt(last, unit, money=money)}"
        + (f"，累计{'增长' if (growth or 0) >= 0 else '下降'} {abs(growth):.1f}%" if growth is not None else "")
        + f"；峰值在 {labels[peak_index]}（{_fmt(values[peak_index], unit, money=money)}），"
        f"谷值在 {labels[trough_index]}（{_fmt(values[trough_index], unit, money=money)}），期间变动率标准差 {volatility:.1f}%。"
    )
    findings = [
        _finding(
            dataset,
            "trend",
            f"{metric}的时间走势",
            statement,
            [f"{labels[0]}={first}", f"{labels[-1]}={last}", f"波动率={round(volatility, 2)}%"],
            chart_ids=chart_ids,
            value=round(growth, 1) if growth is not None else None,
        )
    ]
    # 异常点（IQR）
    q1, q3 = _quartiles(values)
    iqr = q3 - q1
    if iqr > 0:
        outliers = [(labels[i], values[i]) for i in range(len(values)) if values[i] > q3 + 1.5 * iqr or values[i] < q1 - 1.5 * iqr]
        if outliers:
            detail = "、".join(f"{label}（{_fmt(value, unit, money=money)}）" for label, value in outliers[:3])
            findings.append(
                _finding(
                    dataset,
                    "anomaly",
                    "时间序列异常点",
                    f"按 IQR 规则检出 {len(outliers)} 个异常时间点：{detail}；建议核对同期口径或活动/故障因素。",
                    [f"{label}={value}" for label, value in outliers[:5]],
                    severity="warning",
                    chart_ids=chart_ids,
                )
            )
    return findings


def composition_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if len(values) < 2:
        return summary_findings(dataset, chart_ids)
    metric = dataset.get("metric_name") or "指标"
    dim = dataset.get("dim_label") or "维度"
    total = sum(values)
    pairs = sorted(zip(labels, values), key=lambda item: item[1], reverse=True)
    shares = [_share(value, total) for _label, value in pairs]
    hhi = sum((value / total) ** 2 for _label, value in pairs) if total else 0.0
    top3 = sum(shares[:3])
    tail = max(0.0, 100 - top3)
    statement = (
        f"{metric}由 {len(pairs)} 个{dim}构成：{pairs[0][0]} 占 {_pct(shares[0])}，"
        f"前 3 类合计 {_pct(top3)}，HHI {hhi:.2f}；其余 {len(pairs) - 3 if len(pairs) > 3 else 0} 个长尾{dim}合计 {_pct(tail)}。"
    )
    return [
        _finding(
            dataset,
            "composition",
            f"{dim}的{metric}构成",
            statement,
            [f"{label}={round(share, 1)}%" for label, share in zip(labels, shares)][:5] + [f"HHI={round(hhi, 3)}"],
            severity="warning" if hhi >= 0.3 else "info",
            chart_ids=chart_ids,
            value=round(hhi, 3),
        )
    ]


def distribution_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    stats = dataset.get("stats") or {}
    metric = dataset.get("metric_name") or "指标"
    unit = dataset.get("unit") or ""
    if not stats:
        return summary_findings(dataset, chart_ids)
    mean = _num(stats.get("mean"))
    median = _num(stats.get("median"))
    p90 = _num(stats.get("p90"))
    skew = "右偏（长尾高值）" if mean is not None and median is not None and mean > median * 1.05 else (
        "左偏（长尾低值）" if mean is not None and median is not None and mean < median * 0.95 else "近似对称"
    )
    statement = (
        f"{metric}的分布：中位数 {_fmt(median, unit)}，均值 {_fmt(mean, unit)}，P90 {_fmt(p90, unit)}，"
        f"最小值 {_fmt(_num(stats.get('min')), unit)}，最大值 {_fmt(_num(stats.get('max')), unit)}；形态{skew}。"
    )
    return [
        _finding(
            dataset,
            "distribution",
            f"{metric}分布特征",
            statement,
            [f"median={median}", f"mean={mean}", f"p90={p90}", f"count={stats.get('count')}"],
            chart_ids=chart_ids,
            value=mean,
        )
    ]


def correlation_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    points = dataset.get("points") or []
    raw_xs = [_num(point[0]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
    raw_ys = [_num(point[1]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
    pairs = [(float(x), float(y)) for x, y in zip(raw_xs, raw_ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return []
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    pearson = _pearson(xs, ys)
    spearman = _spearman(xs, ys)
    columns = dataset.get("columns") or ["X", "Y"]
    label = _correlation_label(pearson)
    statement = (
        f"{columns[0]} 与 {columns[1]} 呈{label}（Pearson {pearson:.2f}，Spearman {spearman:.2f}，样本 {len(pairs)} 条）"
        if pearson is not None and spearman is not None
        else f"{columns[0]} 与 {columns[1]} 的样本离散度不足，无法计算有效相关系数。"
    )
    return [
        _finding(
            dataset,
            "correlation",
            f"{columns[0]} 与 {columns[1]} 的关系",
            statement,
            [f"pearson={round(pearson, 3) if pearson is not None else None}", f"spearman={round(spearman, 3) if spearman is not None else None}"],
            chart_ids=chart_ids,
            value=round(pearson, 3) if pearson is not None else None,
        )
    ]


def anomaly_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if len(values) < 5:
        return comparison_findings(dataset, chart_ids)
    money = dataset.get("metric_kind") == "money"
    unit = dataset.get("unit") or ""
    metric = dataset.get("metric_name") or "指标"
    q1, q3 = _quartiles(values)
    iqr = q3 - q1
    if iqr <= 0:
        return comparison_findings(dataset, chart_ids)
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = [(labels[i], values[i]) for i in range(len(values)) if values[i] < low or values[i] > high]
    if not outliers:
        return [
            _finding(
                dataset,
                "anomaly",
                f"{metric}未发现显著异常",
                f"按 IQR 规则检查 {len(values)} 个分组，未发现显著离群值（正常区间 {_fmt(low, unit, money=money)} ~ {_fmt(high, unit, money=money)}）。",
                [f"q1={q1}", f"q3={q3}", f"groups={len(values)}"],
                chart_ids=chart_ids,
            )
        ]
    detail = "、".join(f"{label}（{_fmt(value, unit, money=money)}）" for label, value in outliers[:3])
    return [
        _finding(
            dataset,
            "anomaly",
            f"{metric}异常分组",
            f"检出 {len(outliers)} 个离群分组：{detail}；正常区间约 {_fmt(low, unit, money=money)} ~ {_fmt(high, unit, money=money)}，建议逐一核对口径与业务事件。",
            [f"{label}={value}" for label, value in outliers[:5]],
            severity="warning",
            chart_ids=chart_ids,
        )
    ]


def attribution_findings(dataset: dict[str, Any], chart_ids: list[str]) -> list[dict[str, Any]]:
    labels, values = _labels_values(dataset)
    if len(values) < 2:
        return summary_findings(dataset, chart_ids)
    money = dataset.get("metric_kind") == "money"
    unit = dataset.get("unit") or ""
    metric = dataset.get("metric_name") or "指标"
    dim = dataset.get("dim_label") or "维度"
    total = sum(values)
    average = total / len(values)
    pairs = sorted(zip(labels, values), key=lambda item: item[1], reverse=True)
    contributors = [(label, value - average) for label, value in pairs if value > average]
    drags = [(label, average - value) for label, value in pairs if value < average]
    top_text = "、".join(f"{label}（+{_fmt(delta, unit, money=money)}）" for label, delta in contributors[:3]) or "无"
    drag_text = "、".join(f"{label}（-{_fmt(delta, unit, money=money)}）" for label, delta in drags[:3]) or "无"
    statement = (
        f"以各{dim}均值为基准，正向贡献最大的是 {top_text}；低于均值的拖累项为 {drag_text}。"
        f"{metric}合计 {_fmt(total, unit, money=money)}，均值 {_fmt(average, unit, money=money)}。"
    )
    return [
        _finding(
            dataset,
            "attribution",
            f"{dim}对{metric}的贡献拆解",
            statement,
            [f"{label}={value}" for label, value in pairs[:6]],
            chart_ids=chart_ids,
        )
    ]


_OP_BY_KIND = {
    "trend": trend_findings,
    "comparison": comparison_findings,
    "ranking": comparison_findings,
    "composition": composition_findings,
    "distribution": distribution_findings,
    "correlation": correlation_findings,
    "anomaly": anomaly_findings,
    "attribution": attribution_findings,
    "summary": summary_findings,
}


def cross_metric_findings(
    datasets: list[dict[str, Any]], chart_map: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """同一维度上的双指标交叉（如「销售额高但毛利率低」的类目）。

    单独的对比算子只能各说各话；把两个同一维度、不同指标的数据集按维度值对齐，
    才能回答用户真正关心的「高销低毛」「高流量低转化」这类组合问题。
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for dataset in datasets:
        if dataset.get("dim_type") != "category" or not dataset.get("rows"):
            continue
        key = (str(dataset.get("dim") or ""), str(dataset.get("dim_label") or ""))
        groups.setdefault(key, []).append(dataset)
    findings: list[dict[str, Any]] = []
    for (dim, dim_label), group in groups.items():
        if len(group) < 2:
            continue
        primary = next(
            (item for item in group if item.get("agg") == "sum" and item.get("metric_kind") == "money"),
            None,
        )
        secondary = next(
            (item for item in group if item.get("agg") in ("ratio", "avg") and item is not primary),
            None,
        )
        if primary is None or secondary is None:
            continue
        primary_values = {
            str(row[0]): float(row[1])
            for row in primary.get("rows") or []
            if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))
        }
        secondary_values = {
            str(row[0]): float(row[1])
            for row in secondary.get("rows") or []
            if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], (int, float))
        }
        common = [label for label in primary_values if label in secondary_values]
        if len(common) < 3:
            continue
        primary_rank = sorted(common, key=lambda label: primary_values[label], reverse=True)
        secondary_rank = sorted(common, key=lambda label: secondary_values[label], reverse=True)
        secondary_average = sum(secondary_values.values()) / len(secondary_values)
        top_label = primary_rank[0]
        top_rank = secondary_rank.index(top_label) + 1
        unit = str(secondary.get("unit") or "")
        primary_unit = str(primary.get("unit") or "")
        money = primary.get("metric_kind") == "money"
        low_margin = secondary_values[top_label] < secondary_average and top_rank > len(common) / 2
        if low_margin:
            statement = (
                f"{top_label} 的{primary.get('metric_name')}最高（{_fmt(primary_values[top_label], primary_unit, money=money)}），"
                f"但{secondary.get('metric_name')}仅 {_fmt(secondary_values[top_label], unit)}，"
                f"在 {len(common)} 个{dim_label}中排第 {top_rank}，低于均值 {_fmt(secondary_average, unit)}；"
                f"属于典型的高销售、低盈利单元，建议优先复核定价/成本结构。"
            )
            severity = "warning"
        else:
            statement = (
                f"{top_label} 的{primary.get('metric_name')}最高（{_fmt(primary_values[top_label], primary_unit, money=money)}），"
                f"{secondary.get('metric_name')}为 {_fmt(secondary_values[top_label], unit)}，排第 {top_rank}/{len(common)}；"
                f"高销售单元的整体盈利质量尚可，但仍需结合成本与退款口径复核。"
            )
            severity = "info"
        findings.append(
            {
                "id": f"fcross{dim}",
                "dataset_id": primary.get("id"),
                "kind": "attribution",
                "title": f"{dim_label}双指标交叉：高销售是否低毛利",
                "statement": statement,
                "evidence": [
                    f"{top_label}.{primary.get('metric_name')}={round(primary_values[top_label], 2)}",
                    f"{top_label}.{secondary.get('metric_name')}={round(secondary_values[top_label], 2)}",
                    f"排名={top_rank}/{len(common)}",
                    f"{secondary.get('metric_name')}均值={round(secondary_average, 2)}",
                ],
                "severity": severity,
                "chart_ids": chart_map.get(str(primary.get("id")), []) + chart_map.get(str(secondary.get("id")), []),
                "metric": primary.get("metric_name"),
                "unit": primary_unit,
                "value": round(primary_values[top_label], 2),
            }
        )
    return findings


def build_findings(
    datasets: list[dict[str, Any]],
    chart_map: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """按数据集类型调用对应算子，返回去重后的发现列表。"""
    chart_map = chart_map or {}
    findings: list[dict[str, Any]] = []
    for dataset in datasets:
        kind = str(dataset.get("kind") or "summary")
        # 组合类型（如 comparison+composition）取第一个可识别算子
        op = None
        for token in kind.split("+"):
            op = _OP_BY_KIND.get(token)
            if op is not None:
                break
        op = op or summary_findings
        chart_ids = chart_map.get(str(dataset.get("id")), [])
        try:
            findings.extend(op(dataset, chart_ids))
        except Exception:
            logger.warning("数据集 %s 分析算子失败，已跳过", dataset.get("id"), exc_info=True)
            continue
    findings.extend(cross_metric_findings(datasets, chart_map))
    # 去重（同 dataset + 同标题）+ 限制条数
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for finding in findings:
        key = (str(finding.get("dataset_id")), str(finding.get("title")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique[:_MAX_FINDINGS]

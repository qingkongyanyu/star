"""报告数据事实计算（纯函数，v2 从 ReportScribe 抽出，供编撰与终审数字校验共用）。

保证"报告里的数字 = 代码对真实 dataset 的重算结果"——防幻觉不靠信任 LLM，
而是同一份汇总函数在编撰端与终审端各自独立调用再比对。
"""
from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float:
    """宽松转 float：None / 非数值一律 0.0（NULL 聚合列防护）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_dataset_facts(dataset: dict[str, Any]) -> dict[str, Any] | None:
    """从真实数据集（{columns, rows}）计算汇总事实；结构不符返回 None。"""
    cols = [str(c) for c in dataset.get("columns") or []]
    rows = dataset.get("rows") or []
    if not cols or not rows:
        return None
    dim = cols[0]
    idx = {name: i for i, name in enumerate(cols)}
    amount_idx = next(
        (
            i
            for n, i in idx.items()
            if n.lower().startswith(("total_", "avg_")) or n.lower() in ("amount", "sales", "revenue", "gmv")
        ),
        None,
    )
    count_idx = next((i for n, i in idx.items() if n.lower() in ("order_count", "count", "qty", "quantity", "cnt")), None)
    if amount_idx is None:
        numeric_cols = [i for i, name in enumerate(cols) if i > 0]
        amount_idx = numeric_cols[-1] if numeric_cols else None

    parsed: list[dict[str, Any]] = []
    for r in rows:
        amount = _to_float(r[amount_idx]) if amount_idx is not None and amount_idx < len(r) else 0.0
        count = _to_float(r[count_idx]) if count_idx is not None and count_idx < len(r) else 0.0
        if amount_idx is None and count_idx is None:
            continue
        parsed.append({"region": str(r[0]), "amount": amount, "count": count})

    if not parsed:
        return None

    total_amount = sum(p["amount"] for p in parsed)
    total_orders = int(sum(p["count"] for p in parsed))
    agg = str(dataset.get("agg") or "sum")
    for p in parsed:
        p["share"] = (p["amount"] / total_amount * 100) if total_amount else 0.0
        # 组均值：SUM 数据集需 amount/count 还原；AVG 数据集的 amount 本身已经是组均值
        p["avg"] = p["amount"] if agg == "avg" else ((p["amount"] / p["count"]) if p["count"] else 0.0)
    # 整体均值：SUM 数据集 = 合计/计数；AVG 数据集按各组样本数加权还原
    weighted_avg = (
        (sum(p["amount"] * p["count"] for p in parsed) / total_orders)
        if agg == "avg" and total_orders
        else ((total_amount / total_orders) if total_orders else 0.0)
    )

    parsed_sorted = sorted(parsed, key=lambda p: p["amount"], reverse=True)
    return {
        "dim": dim,
        "table": dataset.get("table") or None,
        "row_count": len(parsed),
        "region_count": len(parsed),
        "total_amount": total_amount,
        "total_orders": total_orders,
        "avg_amount": (total_amount / total_orders) if total_orders else 0.0,
        "weighted_avg": weighted_avg,
        "agg": agg,
        "top": parsed_sorted[0],
        "low": parsed_sorted[-1],
        "max_avg": max(parsed, key=lambda p: p["avg"]),
        "min_avg": min(parsed, key=lambda p: p["avg"]),
    }

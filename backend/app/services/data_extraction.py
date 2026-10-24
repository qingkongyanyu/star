"""多任务取数：把 AnalysisPlan 的 tasks 执行成 datasets。

两种执行路径（同一套数据集契约）：
- 记录型（CSV/API/Kafka）：把记录读入内存，按任务做过滤 → 分组 → 指标计算 → 排序/TopN；
- SQL 型（MySQL/PostgreSQL/OLAP）：按任务生成只读聚合 SQL，执行后组装数据集。

数据集统一形态（下游 DQ/FE/可视化/洞察/报告共用）：
{
  id, task_id, kind, metric_id, metric_name, metric_formula, unit, agg, metric_kind,
  dim, dim_type, dim_label, granularity, columns, rows, row_count, source_row_count,
  table, sql, truncated, error, stats, points, filters, notes
}
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.metric_catalog import to_number

logger = logging.getLogger(__name__)

_DIM_DISPLAY = {
    "region": "区域",
    "area": "区域",
    "channel": "渠道",
    "city": "城市",
    "category": "品类",
    "status": "状态",
    "ym": "月份",
    "month": "月份",
    "date": "日期",
    "year": "年份",
    "customer_id": "客户",
    "product": "商品",
}


def dim_display(name: str | None) -> str:
    """维度列中文展示名（英文业务列归一为中文，报告措辞更自然）。"""
    if not name:
        return "全部"
    return _DIM_DISPLAY.get(str(name).strip().lower(), str(name))


# ---------- 时间处理 ----------


def time_parts(value: Any) -> tuple[int, int, int] | None:
    """把各种时间值解析成 (年, 月, 日)；无法解析返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        try:
            stamp = dt.datetime.fromtimestamp(int(text[:10]), tz=dt.UTC)
            return (stamp.year, stamp.month, stamp.day)
        except (ValueError, OSError, OverflowError):
            return None
    match = re.match(r"^(\d{4})[-/年](\d{1,2})(?:[-/月](\d{1,2}))?", text)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
    match = re.match(r"^(\d{4})$", text)
    if match:
        return (int(match.group(1)), 1, 1)
    return None


def bucket_time(value: Any, granularity: str | None) -> str | None:
    """按粒度把时间值归一到可比较的时间桶标签。"""
    parts = time_parts(value)
    if parts is None:
        return None
    year, month, day = parts
    granularity = granularity or "month"
    if granularity == "year":
        return f"{year:04d}"
    if granularity == "quarter":
        return f"{year:04d}-Q{(month - 1) // 3 + 1}"
    if granularity == "day":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if granularity == "week":
        try:
            iso = dt.date(year, month, min(day, 28)).isocalendar()
            return f"{iso.year:04d}-W{iso.week:02d}"
        except ValueError:
            return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def time_range_bounds(
    time_range: dict[str, Any] | None, max_parts: tuple[int, int, int] | None
) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """把计划的时间范围换算成闭区间 (start, end)。"""
    if not time_range:
        return None
    kind = time_range.get("kind")
    if kind == "year":
        year = time_range.get("year") or (max_parts[0] if max_parts else None)
        if not year:
            return None
        return (int(year), 1, 1), (int(year), 12, 31)
    if kind == "month":
        year, month = int(time_range.get("year") or 0), int(time_range.get("month") or 1)
        return (year, month, 1), (year, month, 28)
    if kind in ("relative", "month_offset", "quarter"):
        end = max_parts or (dt.datetime.now(tz=dt.UTC).date().year, dt.datetime.now(tz=dt.UTC).date().month, 1)
        if kind == "relative":
            amount = int(time_range.get("amount") or 1)
            unit = str(time_range.get("unit") or "月")
            if unit in ("天", "日"):
                end_date = dt.date(*end)
                start_date = end_date - dt.timedelta(days=amount)
                return (start_date.year, start_date.month, start_date.day), end
            if unit == "周":
                end_date = dt.date(*end)
                start_date = end_date - dt.timedelta(weeks=amount)
                return (start_date.year, start_date.month, start_date.day), end
            months = amount * 12 if unit == "年" else amount
            start_year, start_month = _shift_month(end[0], end[1], -months + 1)
            return (start_year, start_month, 1), end
        if kind == "month_offset":
            offset = int(time_range.get("offset") or 0)
            year, month = _shift_month(end[0], end[1], offset)
            return (year, month, 1), (year, month, 28)
        # quarter
        offset = int(time_range.get("offset") or 0)
        quarter = (end[1] - 1) // 3
        total_quarter = end[0] * 4 + quarter + offset
        year, q = total_quarter // 4, total_quarter % 4
        start_month = q * 3 + 1
        return (year, start_month, 1), (year, start_month + 2, 28)
    return None


def _in_range(parts: tuple[int, int, int] | None, bounds: tuple[tuple[int, int, int], tuple[int, int, int]] | None) -> bool:
    if bounds is None:
        return True
    if parts is None:
        return False
    return bounds[0] <= parts <= bounds[1]


# ---------- 指标计算 ----------


@dataclass
class GroupState:
    """一个分组内的聚合中间状态。"""

    count: int = 0
    sums: dict[str, float] = field(default_factory=dict)
    avg_sums: dict[str, float] = field(default_factory=dict)
    avg_counts: dict[str, int] = field(default_factory=dict)
    distinct: dict[str, set[str]] = field(default_factory=dict)

    def add(self, row: list[Any], column_indexes: dict[str, int], metric: dict[str, Any]) -> None:
        self.count += 1
        for column in _metric_columns(metric):
            index = column_indexes.get(column)
            if index is None or index >= len(row):
                continue
            raw = row[index]
            if raw is not None and str(raw).strip() != "":
                # 去重计数可能统计字符串列（订单号/客户ID），必须先记录原始值再判断数值
                self.distinct.setdefault(column, set()).add(str(raw).strip())
            number = to_number(raw)
            if number is None:
                continue
            self.sums[column] = self.sums.get(column, 0.0) + number
            self.avg_sums[column] = self.avg_sums.get(column, 0.0) + number
            self.avg_counts[column] = self.avg_counts.get(column, 0) + 1


def _metric_columns(metric: dict[str, Any]) -> list[str]:
    columns: list[str] = []

    def _walk(expr: dict[str, Any] | None) -> None:
        if not isinstance(expr, dict):
            return
        column = expr.get("column")
        if column:
            columns.append(str(column))
        for key in ("left", "right"):
            _walk(expr.get(key))

    if metric.get("column"):
        columns.append(str(metric["column"]))
    _walk(metric.get("numerator"))
    _walk(metric.get("denominator"))
    seen: set[str] = set()
    out: list[str] = []
    for column in columns:
        if column not in seen:
            seen.add(column)
            out.append(column)
    return out


def _eval_expr(state: GroupState, expr: dict[str, Any] | None) -> float | None:
    if not isinstance(expr, dict):
        return None
    op = expr.get("op")
    column = str(expr.get("column") or "")
    if op == "count":
        return float(state.count)
    if op == "sum":
        return state.sums.get(column, 0.0)
    if op == "avg":
        count = state.avg_counts.get(column, 0)
        return (state.avg_sums.get(column, 0.0) / count) if count else None
    if op == "count_distinct":
        return float(len(state.distinct.get(column, set())))
    if op == "diff":
        left = _eval_expr(state, expr.get("left"))
        right = _eval_expr(state, expr.get("right"))
        if left is None or right is None:
            return None
        return left - right
    return None


def metric_value(state: GroupState, metric: dict[str, Any]) -> float | None:
    """按指标定义计算分组值（含派生指标）。"""
    agg = str(metric.get("agg") or "sum")
    if agg == "ratio":
        numerator = _eval_expr(state, metric.get("numerator"))
        denominator = _eval_expr(state, metric.get("denominator"))
        if numerator is None or denominator is None or denominator == 0:
            return None
        # 比率类指标统一以百分数存储（unit=%），图表/报告/洞察不必再各自换算
        return numerator / denominator * 100
    if agg == "count":
        return float(state.count)
    if agg == "count_distinct":
        return float(len(state.distinct.get(str(metric.get("column") or ""), set())))
    column = str(metric.get("column") or "")
    if agg == "sum":
        return state.sums.get(column, 0.0)
    if agg == "avg":
        count = state.avg_counts.get(column, 0)
        return (state.avg_sums.get(column, 0.0) / count) if count else None
    return None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _fmt_value(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


# ---------- 记录型取数 ----------


def filter_records(
    rows: list[list[Any]],
    columns: list[str],
    task: dict[str, Any],
    time_column: str | None,
) -> list[list[Any]]:
    """按任务过滤记录（时间范围 + 维度值）。供 SQLAgent/单测复用。"""
    return _filter_records(rows, columns, task, time_column)


def first_column(metric: dict[str, Any]) -> str | None:
    """取指标依赖的第一个真实列（派生指标沿分子递归）。"""
    return _first_column(metric)


def _filter_records(
    rows: list[list[Any]],
    columns: list[str],
    task: dict[str, Any],
    time_column: str | None,
) -> list[list[Any]]:
    filters = [f for f in task.get("filters") or [] if isinstance(f, dict)]
    value_filters = [f for f in filters if f.get("field") not in ("__time__", None)]
    time_filter = next((f for f in filters if f.get("field") == "__time__"), None)
    bounds = None
    if time_filter and time_column:
        parts_list = [time_parts(row[columns.index(time_column)]) for row in rows if time_column in columns]
        max_parts = max((p for p in parts_list if p is not None), default=None)
        bounds = time_range_bounds(time_filter.get("value"), max_parts)
    time_index = columns.index(time_column) if time_column and time_column in columns else None
    indexes = {f["field"]: columns.index(f["field"]) for f in value_filters if f.get("field") in columns}
    out: list[list[Any]] = []
    for row in rows:
        ok = True
        for f in value_filters:
            index = indexes.get(str(f.get("field")))
            if index is None or index >= len(row):
                continue
            value = str(row[index] or "").strip()
            if value not in [str(v) for v in f.get("values") or []]:
                ok = False
                break
        if not ok:
            continue
        if time_filter and time_index is not None and time_index < len(row) and not _in_range(
            time_parts(row[time_index]), bounds
        ):
            continue
        out.append(row)
    return out


def _dim_index(columns: list[str], dim: str | None) -> int | None:
    if not dim:
        return None
    try:
        return columns.index(dim)
    except ValueError:
        return None


def _build_dataset_meta(
    task: dict[str, Any],
    metric: dict[str, Any],
    schema: dict[str, Any],
    *,
    dim_label: str,
) -> dict[str, Any]:
    return {
        "id": f"d{task.get('id', 'a1')}",
        "task_id": task.get("id"),
        "kind": task.get("kind"),
        "metric_id": metric.get("id"),
        "metric_name": metric.get("name"),
        "metric_formula": metric.get("formula"),
        "unit": metric.get("unit") or "",
        "agg": metric.get("agg") or "sum",
        "metric_kind": metric.get("kind") or "numeric",
        "dim": task.get("dim"),
        "dim_type": task.get("dim_type") or "none",
        "dim_label": dim_label,
        "granularity": task.get("granularity"),
        "columns": [dim_label, str(metric.get("name") or "指标")],
        "rows": [],
        "row_count": 0,
        "source_row_count": len(schema.get("sample_rows") or []),
        "table": schema.get("table") or "",
        "sql": "",
        "truncated": False,
        "error": None,
        "stats": {},
        "points": None,
        "filters": task.get("filters") or [],
        "notes": [],
    }


def _source_quality(rows: list[list[Any]], columns: list[str]) -> dict[str, int]:
    """源记录级质量画像（缺失/重复/零值），供 DQ 在聚合后仍能发现上游脏数据。"""
    if not rows or not columns:
        return {}
    cells = len(rows) * len(columns)
    missing = sum(
        1 for row in rows for value in row if value is None or (isinstance(value, str) and not value.strip())
    )
    unique = {tuple(str(value) for value in row) for row in rows}
    zero_values = 0
    zero_skip_tokens = ("退款", "折扣", "refund", "discount", "成本")
    for row in rows:
        for column, value in zip(columns, row):
            if any(token in str(column) for token in zero_skip_tokens):
                continue  # 退款/折扣/成本为 0 是正常业务语义，不算脏数据
            number = to_number(value)
            if number == 0:
                zero_values += 1
    return {
        "rows": len(rows),
        "cells": cells,
        "missing_cells": missing,
        "duplicate_rows": len(rows) - len(unique),
        "zero_values": zero_values,
    }


def aggregate_records(
    tasks: list[dict[str, Any]],
    schema: dict[str, Any],
    records: list[list[Any]],
) -> list[dict[str, Any]]:
    """记录型数据源：按任务聚合出 datasets（纯函数，便于单测）。"""
    columns = [str(c.get("name")) for c in schema.get("columns") or [] if isinstance(c, dict)]
    datasets: list[dict[str, Any]] = []
    source_quality = _source_quality(records, columns)
    for task in tasks:
        metric = _metric_def(task, schema)
        if metric is None:
            continue
        dim = task.get("dim")
        dim_label = dim_display(dim) if dim else "全部"
        dataset = _build_dataset_meta(task, metric, schema, dim_label=dim_label)
        dataset["source_row_count"] = len(records)
        filtered = _filter_records(records, columns, task, dim if task.get("dim_type") == "time" else None)
        if dim and dim not in columns:
            dataset["error"] = f"维度字段不存在：{dim}"
            datasets.append(dataset)
            continue
        di = _dim_index(columns, dim)
        column_indexes = {name: idx for idx, name in enumerate(columns)}
        groups: dict[str, GroupState] = {}
        skipped = 0
        for row in filtered:
            if di is not None:
                raw_dim = row[di] if di < len(row) else None
                if task.get("dim_type") == "time":
                    label = bucket_time(raw_dim, task.get("granularity"))
                else:
                    label = str(raw_dim).strip() if raw_dim is not None else ""
                if not label:
                    skipped += 1
                    continue
            else:
                label = "全部"
            state = groups.setdefault(label, GroupState())
            state.add(row, column_indexes, metric)
        rows: list[list[Any]] = []
        for label, state in groups.items():
            value = _round(metric_value(state, metric))
            if value is None:
                skipped += 1
                continue
            rows.append([label, value])
        sort_desc = task.get("sort") != "asc"
        if task.get("dim_type") == "time":
            rows.sort(key=lambda item: str(item[0]))
        else:
            rows.sort(key=lambda item: float(item[1]), reverse=sort_desc)
        top_n = task.get("top_n")
        if top_n and len(rows) > int(top_n):
            rows = _apply_top_n(rows, int(top_n), metric)
        dataset["rows"] = rows
        dataset["row_count"] = len(rows)
        if skipped:
            dataset["notes"].append(f"跳过 {skipped} 条空维度/无法计算指标的行")
        if metric.get("agg") == "avg":
            dataset["notes"].append("单位价类指标按组均值计算，未做求和")
        datasets.append(dataset)
        if source_quality:
            dataset["source_quality"] = source_quality
    return datasets


def _apply_top_n(rows: list[list[Any]], top_n: int, metric: dict[str, Any]) -> list[list[Any]]:
    """TopN：可加总的指标把尾部合并为「其他」；均值/比率类只保留前 N。"""
    if metric.get("agg") in ("sum", "count", "count_distinct"):
        head = rows[: top_n - 1]
        rest = rows[top_n - 1 :]
        if rest:
            head.append(["其他", round(sum(float(item[1]) for item in rest), 2)])
        return head
    return rows[:top_n]


def _metric_def(task: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any] | None:
    """从计划 schema 的指标缓存/任务里取指标定义。"""
    metrics = schema.get("_metrics") or []
    metric_id = task.get("metric_id")
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("id") == metric_id:
            return metric
    return None


def attach_metrics(schema: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """把指标定义挂到 schema 上（内部用，避免每次重新构建目录）。"""
    schema["_metrics"] = metrics
    return schema


# ---------- SQL 型取数 ----------


def quote_ident(name: str, ds_type: str) -> str:
    """按方言引用标识符并去掉引号字符（防注入）。"""
    clean = str(name).replace("`", "").replace('"', "").replace("'", "")
    return f'"{clean}"' if ds_type == "postgres" else f"`{clean}`"


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_dim_expression(dim: str, dim_type: str, granularity: str | None, ds_type: str) -> str:
    ident = quote_ident(dim, ds_type)
    if dim_type != "time":
        return ident
    # 时间列可能是 DATE/DATETIME，也可能是 CHAR('2025-01')；统一转文本再截取，
    # 避免 DATE_FORMAT('2025-01') 在 MySQL 直接返回 NULL。
    text_col = f"CAST({ident} AS TEXT)" if ds_type == "postgres" else f"CAST({ident} AS CHAR)"
    if granularity == "year":
        return f"SUBSTRING({text_col}, 1, 4)"
    if granularity == "day":
        return f"SUBSTRING({text_col}, 1, 10)"
    if granularity == "quarter":
        month_cast = f"CAST(SUBSTRING({text_col}, 6, 2) AS INTEGER)" if ds_type == "postgres" else f"CAST(SUBSTRING({text_col}, 6, 2) AS UNSIGNED)"
        return f"CONCAT(SUBSTRING({text_col}, 1, 4), '-Q', CEIL({month_cast} / 3))"
    return f"SUBSTRING({text_col}, 1, 7)"


def sql_metric_expression(metric: dict[str, Any], ds_type: str) -> str:
    """生成指标聚合 SQL 表达式。"""
    if metric.get("agg") == "ratio":
        numerator = _sql_expr(metric.get("numerator"), ds_type)
        denominator = _sql_expr(metric.get("denominator"), ds_type)
        return f"(({numerator}) / NULLIF(({denominator}), 0)) * 100"
    return _sql_expr(metric, ds_type)


def _sql_expr(expr: dict[str, Any] | None, ds_type: str) -> str:
    if not isinstance(expr, dict):
        return "NULL"
    op = expr.get("op") or expr.get("agg")
    column = quote_ident(str(expr.get("column") or ""), ds_type) if expr.get("column") else ""
    if op == "count":
        return "COUNT(*)"
    if op == "sum":
        return f"SUM({column})"
    if op == "avg":
        return f"AVG({column})"
    if op == "count_distinct":
        return f"COUNT(DISTINCT {column})"
    if op == "diff":
        return f"({_sql_expr(expr.get('left'), ds_type)} - {_sql_expr(expr.get('right'), ds_type)})"
    return "NULL"


def sql_where(task: dict[str, Any], schema: dict[str, Any], ds_type: str) -> str:
    """根据任务过滤生成 WHERE 子句（时间范围 + 维度值）。"""
    columns = {str(c.get("name")): str(c.get("type") or "") for c in schema.get("columns") or [] if isinstance(c, dict)}
    clauses: list[str] = []
    for item in task.get("filters") or []:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if field == "__time__":
            time_column = _time_column_for_sql(task, schema)
            if not time_column:
                continue
            range_value = item.get("value") or {}
            bounds = time_range_bounds(range_value, _sql_max_time(task, schema, ds_type))
            if bounds is None:
                continue
            ident = quote_ident(time_column, ds_type)
            start, end = bounds
            width = 4 if range_value.get("kind") == "year" else (10 if task.get("granularity") == "day" else 7)
            text_col = f"CAST({ident} AS TEXT)" if ds_type == "postgres" else f"CAST({ident} AS CHAR)"
            expr = f"SUBSTRING({text_col}, 1, {width})"
            start_text = f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}"[:width]
            end_text = f"{end[0]:04d}-{end[1]:02d}-{end[2]:02d}"[:width]
            clauses.append(f"{expr} BETWEEN {_sql_literal(start_text)} AND {_sql_literal(end_text)}")
            continue
        if field in columns:
            values = [str(v) for v in item.get("values") or []]
            if values:
                ident = quote_ident(str(field), ds_type)
                clauses.append(f"{ident} IN ({', '.join(_sql_literal(v) for v in values)})")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _time_column_for_sql(task: dict[str, Any], schema: dict[str, Any]) -> str | None:
    if task.get("dim_type") == "time" and task.get("dim"):
        return str(task["dim"])
    for column in schema.get("columns") or []:
        name = str(column.get("name") or "")
        if any(token in name for token in ("时间", "日期", "date", "time", "ym", "month")):
            return name
    return None


def _sql_max_time(task: dict[str, Any], schema: dict[str, Any], ds_type: str) -> tuple[int, int, int] | None:
    """SQL 侧时间范围用样本值推算（避免额外查询）；样本为空则由数据库当前时间兜底。"""
    time_column = _time_column_for_sql(task, schema)
    columns = [str(c.get("name")) for c in schema.get("columns") or [] if isinstance(c, dict)]
    if not time_column or time_column not in columns:
        return None
    index = columns.index(time_column)
    parts = [time_parts(row[index]) for row in schema.get("sample_rows") or [] if isinstance(row, (list, tuple)) and index < len(row)]
    return max((p for p in parts if p is not None), default=None)


def build_sql(task: dict[str, Any], schema: dict[str, Any], metric: dict[str, Any], ds_type: str) -> str:
    """为单个任务生成聚合 SQL。"""
    table = quote_ident(str(schema.get("table") or ""), ds_type)
    metric_expr = sql_metric_expression(metric, ds_type)
    dim = task.get("dim")
    if dim:
        dim_expr = sql_dim_expression(str(dim), str(task.get("dim_type") or "category"), task.get("granularity"), ds_type)
        select = f"SELECT {dim_expr} AS dim_value, {metric_expr} AS metric_value, COUNT(*) AS row_count"
        group_by = f" GROUP BY {dim_expr}"
    else:
        select = f"SELECT '全部' AS dim_value, {metric_expr} AS metric_value, COUNT(*) AS row_count"
        group_by = ""
    where = sql_where(task, schema, ds_type)
    order = ""
    limit = ""
    if task.get("dim_type") == "time":
        order = " ORDER BY dim_value ASC"
    else:
        order = " ORDER BY metric_value " + ("ASC" if task.get("sort") == "asc" else "DESC")
        limit = f" LIMIT {int(task.get('top_n') or 200)}"
    return f"{select} FROM {table}{where}{group_by}{order}{limit}"


def sql_correlation_series(
    task: dict[str, Any], schema: dict[str, Any], metrics: list[dict[str, Any]], ds_type: str
) -> str | None:
    """时间桶相关：按时间粒度聚合两个指标，再在桶级别计算相关。"""
    if not task.get("dim") or task.get("dim_type") != "time" or len(metrics) < 2:
        return None
    dim_expr = sql_dim_expression(str(task["dim"]), "time", task.get("granularity"), ds_type)
    first = sql_metric_expression(metrics[0], ds_type)
    second = sql_metric_expression(metrics[1], ds_type)
    table = quote_ident(str(schema.get("table") or ""), ds_type)
    where = sql_where(task, schema, ds_type)
    return (
        f"SELECT {dim_expr} AS dim_value, {first} AS x, {second} AS y "
        f"FROM {table}{where} GROUP BY {dim_expr} ORDER BY dim_value ASC"
    )


def sql_points_query(task: dict[str, Any], schema: dict[str, Any], metrics: list[dict[str, Any]], ds_type: str) -> str | None:
    """相关性任务：取两个数值字段的明细对（LIMIT 3000）。"""
    first = _first_column(metrics[0]) if metrics else None
    second = _first_column(metrics[1]) if len(metrics) > 1 else None
    if not first or not second:
        return None
    table = quote_ident(str(schema.get("table") or ""), ds_type)
    where = sql_where(task, schema, ds_type)
    return f"SELECT {quote_ident(first, ds_type)} AS x, {quote_ident(second, ds_type)} AS y FROM {table}{where} LIMIT 3000"


def sql_distribution_query(task: dict[str, Any], schema: dict[str, Any], metric: dict[str, Any], ds_type: str) -> str | None:
    """分布任务：取指标原始列的明细值（LIMIT 3000）。"""
    column = _first_column(metric)
    if not column:
        return None
    table = quote_ident(str(schema.get("table") or ""), ds_type)
    where = sql_where(task, schema, ds_type)
    return f"SELECT {quote_ident(column, ds_type)} AS value FROM {table}{where} LIMIT 3000"


def _first_column(metric: dict[str, Any]) -> str | None:
    if metric.get("column"):
        return str(metric["column"])
    numerator = metric.get("numerator")
    if isinstance(numerator, dict):
        return _first_column(numerator)
    return None


def dataset_from_sql(
    task: dict[str, Any],
    metric: dict[str, Any],
    schema: dict[str, Any],
    rows: list[list[Any]],
    sql: str,
) -> dict[str, Any]:
    """把 SQL 聚合结果组装成统一数据集。"""
    dim = task.get("dim")
    dim_label = dim_display(dim) if dim else "全部"
    dataset = _build_dataset_meta(task, metric, schema, dim_label=dim_label)
    dataset["sql"] = sql
    parsed: list[list[Any]] = []
    truncated = bool(schema.get("_sql_truncated"))
    for row in rows:
        label = str(row[0]) if row and row[0] is not None else ""
        value = to_number(row[1]) if len(row) > 1 else None
        if not label or value is None:
            continue
        parsed.append([label, round(value, 2)])
    if task.get("dim_type") == "time":
        parsed.sort(key=lambda item: str(item[0]))
    dataset["rows"] = parsed
    dataset["row_count"] = len(parsed)
    dataset["truncated"] = truncated
    return dataset


def dataset_from_points(
    task: dict[str, Any],
    metrics: list[dict[str, Any]],
    schema: dict[str, Any],
    points: list[tuple[float, float]],
    sql: str = "",
) -> dict[str, Any]:
    first = metrics[0] if metrics else {"id": "unknown", "name": "指标"}
    dataset = _build_dataset_meta(task, first, schema, dim_label="明细")
    dataset["kind"] = "correlation"
    dataset["columns"] = [first.get("name") or "X", (metrics[1].get("name") if len(metrics) > 1 else "Y")]
    dataset["points"] = [[round(x, 4), round(y, 4)] for x, y in points[:3000]]
    dataset["row_count"] = len(dataset["points"])
    dataset["sql"] = sql
    dataset["secondary_metric_id"] = metrics[1].get("id") if len(metrics) > 1 else None
    return dataset


def dataset_from_values(
    task: dict[str, Any],
    metric: dict[str, Any],
    schema: dict[str, Any],
    values: list[float],
    sql: str = "",
) -> dict[str, Any]:
    """分布任务：把原始值分桶成直方图数据集。"""
    dataset = _build_dataset_meta(task, metric, schema, dim_label=f"{metric.get('name')}区间")
    dataset["kind"] = "distribution"
    dataset["columns"] = ["区间", "记录数"]
    dataset["rows"], stats = histogram(values)
    dataset["row_count"] = len(dataset["rows"])
    dataset["stats"] = stats
    dataset["sql"] = sql
    return dataset


def histogram(values: list[float], bins: int = 8) -> tuple[list[list[Any]], dict[str, Any]]:
    """等宽直方图（纯函数）：返回 ([区间标签, 计数], 统计量)。"""
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return [], {}
    low, high = clean[0], clean[-1]
    if high == low:
        return [[f"{low:g}", len(clean)]], {
            "mean": round(low, 2),
            "median": round(low, 2),
            "min": low,
            "max": high,
            "p90": low,
            "count": len(clean),
        }
    width = (high - low) / bins
    counts = [0] * bins
    for value in clean:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    rows = [
        [f"{low + width * i:,.2f} ~ {low + width * (i + 1):,.2f}", counts[i]]
        for i in range(bins)
    ]
    def _pct(percent: float) -> float:
        position = (len(clean) - 1) * percent
        lower = int(position)
        upper = min(lower + 1, len(clean) - 1)
        return round(clean[lower] + (clean[upper] - clean[lower]) * (position - lower), 2)

    stats = {
        "mean": round(sum(clean) / len(clean), 2),
        "median": _pct(0.5),
        "min": round(low, 2),
        "max": round(high, 2),
        "p90": _pct(0.9),
        "count": len(clean),
    }
    return rows, stats

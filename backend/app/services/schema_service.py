"""数据源 Schema 装载：给 Planner 的意图/指标识别提供真实字段与样本。

与 DataProfiler 的区别：Profiler 是 DAG 节点（负责产出探查报告）；本服务只做"规划所需的
轻量结构读取"，Planner 在任务创建时调用一次，结果随 AnalysisPlan 落库，供下游复用，
避免同一任务里重复连库/重复读文件。
"""
from __future__ import annotations

import logging
from typing import Any

from app.infra import datasource
from app.infra.db import to_thread
from app.services.metric_catalog import to_number

logger = logging.getLogger(__name__)

_SQL_TYPES = ("mysql", "postgres", "olap")


def _infer_type(values: list[Any]) -> str:
    """由样本值推断字段类型（int/float/date/string）。"""
    non_empty = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return "string"
    numbers = [to_number(v) for v in non_empty]
    if all(n is not None for n in numbers):
        return "int" if all(float(n).is_integer() for n in numbers if n is not None) else "float"
    import re

    if all(re.match(r"^\d{4}[-/年]\d{1,2}", str(v).strip()) for v in non_empty[:20]):
        return "date"
    return "string"


def _infer_columns(columns: list[str], rows: list[list[Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for index, name in enumerate(columns):
        values = [row[index] if index < len(row) else None for row in rows[:80]]
        out.append({"name": str(name), "type": _infer_type(values)})
    return out


def pick_table(tables: list[str], requirement: str | None) -> str:
    """按需求关键词选表（订单/销售→orders，客户→customers，商品→products）。"""
    req = (requirement or "").lower()
    preferred: list[tuple[tuple[str, ...], list[str]]] = [
        (("订单", "销售", "成交", "order", "sales", "金额", "gmv"), ["orders", "monthly_sales", "order_items", "sales"]),
        (("客户", "用户", "会员", "customer", "user"), ["customers", "users"]),
        (("商品", "产品", "品类", "product", "item"), ["products", "items"]),
    ]
    for keywords, candidates in preferred:
        if any(keyword in req for keyword in keywords):
            for candidate in candidates:
                if candidate in tables:
                    return candidate
    return tables[0] if tables else ""


async def load_schema(data_source: dict[str, Any] | None, requirement: str | None = None) -> dict[str, Any]:
    """装载 schema：{table, tables, columns, sample_rows, row_count, notes, source}。失败不抛异常。"""
    if not data_source:
        return {"table": "", "tables": [], "columns": [], "sample_rows": [], "row_count": None, "notes": "未接入数据源", "source": ""}
    ds_type = str(data_source.get("type") or "")
    config = data_source.get("config") or {}
    try:
        if ds_type in _SQL_TYPES:
            return await _load_sql(ds_type, config, requirement)
        if ds_type == "file":
            return await _load_file(config)
        if ds_type == "api":
            return await _load_api(config)
        if ds_type == "stream":
            return await _load_stream(config)
    except Exception:
        logger.warning("装载数据源 schema 失败（type=%s）", ds_type, exc_info=True)
    return {
        "table": "",
        "tables": [],
        "columns": [{"name": str(c.get("name")), "type": str(c.get("type") or "string")} for c in config.get("columns") or [] if isinstance(c, dict)],
        "sample_rows": config.get("sample_rows") or [],
        "row_count": config.get("row_count"),
        "notes": "结构读取失败，使用数据源注册时的字段快照",
        "source": ds_type,
    }


async def _load_sql(ds_type: str, config: dict[str, Any], requirement: str | None) -> dict[str, Any]:
    url = datasource.build_sqlalchemy_url(config)
    tables = await to_thread(datasource.sql_list_tables, url, 30)
    if not tables:
        return {
            "table": "",
            "tables": [],
            "columns": [],
            "sample_rows": [],
            "row_count": None,
            "notes": "数据源没有可用表（或无权限）",
            "source": ds_type,
        }
    table = pick_table(tables, requirement)
    info = await to_thread(datasource.sql_schema, url, table)
    sample = await to_thread(datasource.sql_sample_rows, url, table, 50)
    columns = [{"name": c.name, "type": c.type} for c in info.columns]
    if not columns:
        columns = _infer_columns(sample["columns"], sample["rows"])
    return {
        "table": info.name,
        "tables": tables,
        "columns": columns,
        "sample_rows": sample["rows"],
        "row_count": info.row_count,
        "notes": f"真实结构探查（{ds_type}，表 {info.name}）",
        "source": ds_type,
    }


async def _load_file(config: dict[str, Any]) -> dict[str, Any]:
    object_key = str(config.get("object_key") or "")
    data = await datasource.load_uploaded_csv(object_key)
    if data is None or not data.get("columns"):
        columns = [{"name": str(c.get("name")), "type": str(c.get("type") or "string")} for c in config.get("columns") or [] if isinstance(c, dict)]
        return {
            "table": str(config.get("file_name") or ""),
            "tables": [],
            "columns": columns,
            "sample_rows": config.get("sample_rows") or [],
            "row_count": config.get("row_count"),
            "notes": "CSV 文件不可读，使用注册时字段快照",
            "source": "file",
        }
    rows = data["rows"][:60]
    return {
        "table": str(config.get("file_name") or ""),
        "tables": [],
        "columns": _infer_columns([str(c) for c in data["columns"]], rows),
        "sample_rows": rows,
        "row_count": data.get("row_count"),
        "notes": f"CSV 真实解析（{data.get('row_count')} 行）",
        "source": "file",
    }


async def _load_api(config: dict[str, Any]) -> dict[str, Any]:
    data = await datasource.api_fetch_records(config, limit=60)
    if data is None or not data.get("columns"):
        columns = [{"name": str(c.get("name")), "type": str(c.get("type") or "string")} for c in config.get("columns") or [] if isinstance(c, dict)]
        return {
            "table": str(config.get("url") or ""),
            "tables": [],
            "columns": columns,
            "sample_rows": [],
            "row_count": None,
            "notes": "API 未返回结构化记录，使用注册时字段快照",
            "source": "api",
        }
    rows = data["rows"][:60]
    return {
        "table": str(config.get("url") or ""),
        "tables": [],
        "columns": _infer_columns([str(c) for c in data["columns"]], rows),
        "sample_rows": rows,
        "row_count": data.get("row_count"),
        "notes": f"API 真实请求解析（{data.get('row_count')} 条记录）",
        "source": "api",
    }


async def _load_stream(config: dict[str, Any]) -> dict[str, Any]:
    data = await datasource.kafka_fetch_records(config, limit=50)
    if data is not None and data.get("columns"):
        rows = data["rows"][:50]
        return {
            "table": str(config.get("topic") or ""),
            "tables": [],
            "columns": _infer_columns([str(c) for c in data["columns"]], rows),
            "sample_rows": rows,
            "row_count": data.get("row_count"),
            "notes": f"Kafka 采样（{data.get('row_count')} 条消息）",
            "source": "stream",
        }
    columns = [{"name": str(c.get("name")), "type": str(c.get("type") or "string")} for c in config.get("columns") or [] if isinstance(c, dict)]
    return {
        "table": str(config.get("topic") or ""),
        "tables": [],
        "columns": columns,
        "sample_rows": [],
        "row_count": None,
        "notes": "Kafka 暂无可用消息，使用注册时字段快照",
        "source": "stream",
    }

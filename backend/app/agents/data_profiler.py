"""DataProfiler：数据探查 Agent → profile。

接入数据源后执行真实结构探查（SQL 取表/字段/行数，file/api 用缓存结构）；
未接入时回退 LLM / 确定性占位报告（v1 原行为）。
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.infra import datasource
from app.infra.db import to_thread
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【DataProfiler 数据探查 Agent】，负责探查数据源结构并输出探查报告。
只输出结构事实：表名、行数估计、字段清单与类型、主键、明显质量问题。
请严格输出 JSON，格式：{"report": {"table": string, "row_count": int, "columns": [{"name": string, "type": string}], "notes": string}, "title": string}。"""

_SQL_TYPES = ("mysql", "postgres", "olap")


class DataProfilerAgent(AgentBase):
    CAPABILITY = "metadata_profiling"
    ASSET_TYPE = "profile"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """数据源接入时走真实探查；无数据源诚实标注，绝不调用 LLM 编造表结构。"""
        if ctx.data_source:
            report = await self._inspect(ctx.data_source, ctx.requirement)
            if report is not None:
                return report
        return {
            "report": {
                "table": "",
                "row_count": 0,
                "columns": [],
                "notes": "未接入数据源，无法探查真实表结构；请先接入数据源后重新下发任务",
                "no_data": True,
            },
            "title": "数据探查报告（无数据源）",
        }

    async def _inspect(self, ds: dict[str, Any], requirement: str = "") -> dict[str, Any] | None:
        """按数据源类型真实读取结构；失败返回 None（回退）。"""
        ds_id = ds["id"]
        ds_type = ds["type"]
        cfg = ds["config"]
        try:
            if ds_type in _SQL_TYPES:
                url = datasource.build_sqlalchemy_url(cfg)
                # 同步 DB 阻塞调用必须经 to_thread 包裹，避免卡住事件循环
                tables = await to_thread(datasource.sql_list_tables, url, 8)
                if not tables:
                    return {"report": {"table": "", "row_count": 0, "columns": [], "notes": "数据源无可用表"}, "title": "数据探查报告"}
                table = self._pick_table(tables, requirement)
                info = await to_thread(datasource.sql_schema, url, table)
                return {
                    "report": {
                        "data_source_id": ds_id,
                        "table": info.name,
                        "row_count": info.row_count,
                        "columns": [{"name": c.name, "type": c.type} for c in info.columns],
                        "notes": f"数据源类型：{ds_type}；表清单：{', '.join(tables)}",
                    },
                    "title": f"数据探查报告 · {info.name}",
                }
            if ds_type in ("file", "api"):
                columns = cfg.get("columns") or []
                table = cfg.get("file_name") or cfg.get("url") or ""
                return {
                    "report": {
                        "data_source_id": ds_id,
                        "table": table,
                        "row_count": cfg.get("row_count"),
                        "columns": columns,
                        "notes": f"数据源类型：{ds_type}",
                    },
                    "title": f"数据探查报告 · {table}",
                }
        except Exception:  # 探查失败回退占位
            logger.warning("数据源 %s 真实探查失败，回退占位", ds_id, exc_info=True)
        return None

    @staticmethod
    def _pick_table(tables: list[str], requirement: str) -> str:
        """按需求关键词选表（订单/销售→orders，客户→customers，商品→products）。"""
        req = (requirement or "").lower()
        preferred: list[tuple[tuple[str, ...], list[str]]] = [
            (("订单", "销售", "成交", "order", "sales", "金额"), ["orders", "monthly_sales", "order_items"]),
            (("客户", "用户", "会员", "customer"), ["customers"]),
            (("商品", "产品", "品类", "product"), ["products"]),
        ]
        for keywords, candidates in preferred:
            if any(k in req for k in keywords):
                for cand in candidates:
                    if cand in tables:
                        return cand
        return tables[0]

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        return {
            "report": {
                "table": "",
                "row_count": 0,
                "columns": [],
                "notes": f"探查失败（需求：{ctx.requirement}），无可用数据",
                "no_data": True,
            },
            "title": "数据探查报告（无数据）",
        }

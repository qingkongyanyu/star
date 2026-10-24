"""SQLAgent：数据抽取 Agent → sql + dataset。

接入数据源且上游含真实探查报告时，基于真实表/列确定性生成合规 SELECT，
并**真实执行取数**，把结果 dataset(columns/rows) 附到产出供下游质检/报告使用；
否则回退 LLM（v1 生成 SQL + 说明，不真实取数）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.infra import datasource
from app.infra.db import to_thread
from app.repositories.acl_repo import AclRepo
from app.services.data_extraction import (
    GroupState,
    aggregate_records,
    attach_metrics,
    bucket_time,
    build_sql,
    dataset_from_points,
    dataset_from_sql,
    dataset_from_values,
    dim_display,
    filter_records,
    first_column,
    metric_value,
    sql_correlation_series,
    sql_distribution_query,
    sql_points_query,
)
from app.services.intent import classify_intent
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【SQLAgent 数据抽取 Agent】。
依据上游指标口径（metric_spec）与探查报告（profile）生成合规 SELECT SQL。
只读、逐条可解释、附权限校验记录。请严格输出 JSON，格式：
{"sql": string, "dataset_object_key": string, "acl_check_record": {"allowed": bool, "note": string}, "title": string}。"""

# 英文业务维度列 → 中文展示名（报告/图表标题用；未命中保持原列名）
_DIM_DISPLAY: dict[str, str] = {
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


class SQLAgent(AgentBase):
    CAPABILITY = "sql_extract"
    ASSET_TYPE = "sql"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """优先按 AnalysisPlan 执行多任务取数；无计划时回退单口径 legacy 路径。"""
        plan = ctx.plan or {}
        tasks = [task for task in plan.get("tasks") or [] if isinstance(task, dict)]
        metrics = [metric for metric in plan.get("metrics") or [] if isinstance(metric, dict)]
        schema = plan.get("schema") or {}
        if tasks and metrics and schema.get("columns") and ctx.data_source:
            try:
                return await self._execute_plan(ctx, deps, plan, schema, metrics, tasks)
            except Exception:
                logger.exception("计划取数失败，回退单口径取数")
        return await self._execute_legacy(ctx, deps)

    async def _execute_plan(
        self,
        ctx: AgentExecutionContext,
        deps: ToolDeps,
        plan: dict[str, Any],
        schema: dict[str, Any],
        metrics: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """按计划的多任务取数：SQL 源走聚合 SQL，记录源走内存聚合。"""
        data_source = ctx.data_source or {}
        ds_type = str(data_source.get("type") or "")
        schema = attach_metrics(dict(schema), metrics)
        if ds_type in ("mysql", "postgres", "olap"):
            return await self._execute_plan_sql(ctx, deps, data_source, ds_type, plan, schema, metrics, tasks)
        return await self._execute_plan_records(ctx, data_source, ds_type, plan, schema, metrics, tasks)

    async def _execute_plan_records(
        self,
        ctx: AgentExecutionContext,
        data_source: dict[str, Any],
        ds_type: str,
        plan: dict[str, Any],
        schema: dict[str, Any],
        metrics: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        config = data_source.get("config") or {}
        if ds_type == "file":
            data = await datasource.load_uploaded_csv(str(config.get("object_key") or ""))
        elif ds_type == "api":
            data = await datasource.api_fetch_records(config)
        elif ds_type == "stream":
            data = await datasource.kafka_fetch_records(config)
        else:
            data = None
        if data is None or not data.get("columns") or not data.get("rows"):
            return self._no_data_payload(
                ctx,
                error=f"记录型数据源无法读取（type={ds_type or 'unknown'}，无可用记录）",
                title="取数（无记录）",
            )
        records = data["rows"]
        columns = [str(c.get("name")) for c in schema.get("columns") or [] if isinstance(c, dict)]
        source_label = str(schema.get("table") or config.get("file_name") or ds_type)
        special = [task for task in tasks if task.get("kind") in ("correlation", "distribution")]
        normal = [task for task in tasks if task not in special]
        datasets = aggregate_records(normal, schema, records)
        for task in special:
            dataset = self._special_record_dataset(task, schema, records, columns, metrics, source_label)
            if dataset is not None:
                datasets.append(dataset)
        for dataset in datasets:
            dataset["source_row_count"] = len(records)
            dataset["table"] = dataset.get("table") or source_label
        return self._plan_payload(ctx, datasets, [], [{"allowed": True, "note": "记录型数据源无需表级 ACL"}], metrics)

    def _special_record_dataset(
        self,
        task: dict[str, Any],
        schema: dict[str, Any],
        records: list[list[Any]],
        columns: list[str],
        metrics: list[dict[str, Any]],
        source_label: str,
    ) -> dict[str, Any] | None:
        """相关/分布任务：从原始记录计算散点或直方图。"""
        metric = next((m for m in metrics if m.get("id") == task.get("metric_id")), None)
        if metric is None:
            return None
        time_column = None
        for column in schema.get("columns") or []:
            name = str(column.get("name") or "")
            if any(token in name for token in ("时间", "日期", "date", "time", "ym", "month")):
                time_column = name
                break
        filtered = filter_records(records, columns, task, time_column)
        if "correlation" in str(task.get("kind")):
            second = next((m for m in metrics if m.get("id") == task.get("secondary_metric_id")), None)
            if second is None:
                return None
            if task.get("dim_type") == "time" and task.get("dim") in columns:
                # 时间桶相关：先按周期聚合两个指标，再在桶级别算相关（销售额 vs 订单量）
                dim_index = columns.index(str(task["dim"]))
                first_states: dict[str, GroupState] = {}
                second_states: dict[str, GroupState] = {}
                for row in filtered:
                    label = bucket_time(row[dim_index] if dim_index < len(row) else None, task.get("granularity"))
                    if not label:
                        continue
                    first_states.setdefault(label, GroupState()).add(row, {name: idx for idx, name in enumerate(columns)}, metric)
                    second_states.setdefault(label, GroupState()).add(row, {name: idx for idx, name in enumerate(columns)}, second)
                points: list[tuple[float, float]] = []
                for label in sorted(first_states):
                    x = metric_value(first_states[label], metric)
                    y = metric_value(second_states[label], second)
                    if x is not None and y is not None:
                        points.append((x, y))
                if len(points) < 3:
                    return None
                dataset = dataset_from_points(task, [metric, second], schema, points)
                dataset["columns"] = [str(metric.get("name")), str(second.get("name"))]
                dataset["dim_label"] = dim_display(str(task["dim"]))
                dataset["granularity"] = task.get("granularity")
                dataset["kind"] = "correlation"
            else:
                first_col = first_column(metric)
                second_col = first_column(second)
                if not first_col or not second_col or first_col not in columns or second_col not in columns:
                    return None
                xi, yi = columns.index(first_col), columns.index(second_col)
                points = []
                for row in filtered[:3000]:
                    x = self._to_number(row[xi] if xi < len(row) else None)
                    y = self._to_number(row[yi] if yi < len(row) else None)
                    if x is not None and y is not None:
                        points.append((x, y))
                if not points:
                    return None
                dataset = dataset_from_points(task, [metric, second], schema, points)
        else:
            column = first_column(metric)
            if not column or column not in columns:
                return None
            index = columns.index(column)
            values = [
                value
                for row in filtered[:5000]
                if index < len(row) and (value := self._to_number(row[index])) is not None
            ]
            if not values:
                return None
            dataset = dataset_from_values(task, metric, schema, values)
        dataset["table"] = source_label
        return dataset

    async def _execute_plan_sql(
        self,
        ctx: AgentExecutionContext,
        deps: ToolDeps,
        data_source: dict[str, Any],
        ds_type: str,
        plan: dict[str, Any],
        schema: dict[str, Any],
        metrics: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        table = str(schema.get("table") or "")
        allowed, reason = await to_thread(
            AclRepo.check,
            deps.task_session,
            role=ctx.role,
            user=ctx.requester,
            table=table,
            action="select",
        )
        acl_records = [{"table": table, "allowed": allowed, "note": reason}]
        if not allowed:
            dataset = dataset_from_sql(tasks[0], metrics[0], schema, [], "")
            dataset["error"] = reason
            return self._plan_payload(ctx, [dataset], [], acl_records, metrics)
        url = datasource.build_sqlalchemy_url(data_source.get("config") or {})
        datasets: list[dict[str, Any]] = []
        queries: list[dict[str, Any]] = []
        for task in tasks:
            dataset = await self._run_sql_task(url, ds_type, task, schema, metrics)
            datasets.append(dataset)
            queries.append(
                {
                    "task_id": task.get("id"),
                    "sql": dataset.get("sql") or "",
                    "ok": dataset.get("error") is None and not dataset.get("empty_result"),
                    "error": dataset.get("error"),
                }
            )
        return self._plan_payload(ctx, datasets, queries, acl_records, metrics)

    async def _run_sql_task(
        self,
        url: str,
        ds_type: str,
        task: dict[str, Any],
        schema: dict[str, Any],
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        metric = next((m for m in metrics if m.get("id") == task.get("metric_id")), metrics[0])
        kind = str(task.get("kind") or "")
        if "correlation" in kind:
            second = next((m for m in metrics if m.get("id") == task.get("secondary_metric_id")), None)
            if second is None:
                dataset = dataset_from_sql(task, metric, schema, [], "")
                dataset["error"] = "相关性分析缺少第二指标"
                return dataset
            series_sql = sql_correlation_series(task, schema, [metric, second], ds_type)
            if series_sql is not None:
                data = await asyncio.to_thread(datasource.sql_execute, url, series_sql, 500)
                if data is None:
                    dataset = dataset_from_sql(task, metric, schema, [], series_sql)
                    dataset["error"] = "SQL 执行失败（时间桶相关取数）"
                    return dataset
                points = []
                for row in data.get("rows") or []:
                    x = self._to_number(row[1] if len(row) > 1 else None)
                    y = self._to_number(row[2] if len(row) > 2 else None)
                    if x is not None and y is not None:
                        points.append((x, y))
                dataset = dataset_from_points(task, [metric, second], schema, points, series_sql)
                dataset["columns"] = [str(metric.get("name")), str(second.get("name"))]
                dataset["dim_label"] = dim_display(str(task.get("dim")))
                dataset["granularity"] = task.get("granularity")
                dataset["kind"] = "correlation"
                dataset["source_row_count"] = data.get("row_count")
                return dataset
            sql = sql_points_query(task, schema, [metric, second], ds_type)
            if sql is None:
                dataset = dataset_from_sql(task, metric, schema, [], "")
                dataset["error"] = "相关性分析需要两个数值字段，当前指标缺少可配对列"
                return dataset
            data = await asyncio.to_thread(datasource.sql_execute, url, sql, 3100)
            if data is None:
                dataset = dataset_from_sql(task, metric, schema, [], sql)
                dataset["error"] = "SQL 执行失败（相关分析取数）"
                return dataset
            points = []
            for row in data.get("rows") or []:
                x = self._to_number(row[0] if len(row) > 0 else None)
                y = self._to_number(row[1] if len(row) > 1 else None)
                if x is not None and y is not None:
                    points.append((x, y))
            dataset = dataset_from_points(task, [metric, second], schema, points, sql)
            dataset["source_row_count"] = data.get("row_count")
            return dataset
        if kind == "distribution":
            sql = sql_distribution_query(task, schema, metric, ds_type)
            if sql is None:
                dataset = dataset_from_sql(task, metric, schema, [], "")
                dataset["error"] = "分布分析需要可枚举的数值字段"
                return dataset
            data = await asyncio.to_thread(datasource.sql_execute, url, sql, 3100)
            if data is None:
                dataset = dataset_from_sql(task, metric, schema, [], sql)
                dataset["error"] = "SQL 执行失败（分布取数）"
                return dataset
            values = [
                value
                for row in data.get("rows") or []
                if (value := self._to_number(row[0] if row else None)) is not None
            ]
            dataset = dataset_from_values(task, metric, schema, values, sql)
            dataset["source_row_count"] = data.get("row_count")
            return dataset
        sql = build_sql(task, schema, metric, ds_type)
        data = await asyncio.to_thread(datasource.sql_execute, url, sql, 200)
        if data is None:
            dataset = dataset_from_sql(task, metric, schema, [], sql)
            dataset["error"] = "SQL 执行失败（连接/语法/权限异常）"
            return dataset
        dataset = dataset_from_sql(task, metric, schema, data.get("rows") or [], sql)
        dataset["source_row_count"] = data.get("row_count")
        dataset["truncated"] = bool(data.get("truncated"))
        if not dataset.get("rows"):
            dataset["empty_result"] = True
        return dataset

    def _plan_payload(
        self,
        ctx: AgentExecutionContext,
        datasets: list[dict[str, Any]],
        queries: list[dict[str, Any]],
        acl_records: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        valid = [dataset for dataset in datasets if dataset.get("rows") or dataset.get("points")]
        primary = valid[0] if valid else (datasets[0] if datasets else None)
        payload: dict[str, Any] = {
            "datasets": datasets,
            "dataset": primary,
            "queries": queries,
            "acl_check_records": acl_records,
            "acl_check_record": acl_records[0] if acl_records else {"allowed": True, "note": "记录型数据源无需表级 ACL"},
            "no_data": not valid,
            "error": None if valid else "取数失败或查询范围内无数据",
            "title": f"{metrics[0].get('name') if metrics else '指标'} 取数（{len(datasets)} 个数据集）",
        }
        metric_spec = self._extract_metric_spec(ctx)
        if metric_spec:
            payload["metric_spec"] = metric_spec
        return payload

    async def _execute_legacy(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """v2 单口径取数（无结构化计划时的向后兼容路径）。"""
        ds = ctx.data_source
        if ds:
            ds_type = ds.get("type")
            # 记录型数据源（CSV/API/Kafka）：真实拉取记录 → 聚合 → 与 SQL 同构的 dataset（v2.2 全类型取数）
            if ds_type == "file":
                return await self._execute_file(ctx)
            if ds_type == "api":
                return await self._execute_api(ctx)
            if ds_type == "stream":
                return await self._execute_stream(ctx)
            built = self._build_from_profile(ctx)
            if built is not None:
                # 表级 ACL 校验：按目标表判定，拒绝则不执行取数（记录校验结果，可审计）
                allowed, reason = await to_thread(
                    AclRepo.check,
                    deps.task_session,
                    role=ctx.role,
                    user=ctx.requester,
                    table=built.get("table", ""),
                    action="select",
                )
                built["acl_check_record"] = {"allowed": allowed, "note": reason}
                if allowed and ds_type in ("mysql", "postgres", "olap"):
                    url = datasource.build_sqlalchemy_url(ds["config"])
                    data = await asyncio.to_thread(datasource.sql_execute, url, built["sql"])
                    if data is not None:
                        data = datasource.mask_dataset(data)  # 敏感字段自动脱敏（手机号/身份证/邮箱）
                        data["table"] = built.get("table", "")  # 表名透传，供报告“原始表”展示
                        # 域语义按真实聚合列判定：有 total_ 金额/销售列才算 money（否则纯计数表会误标成“总销售额”）
                        _ds_cols = [str(c) for c in (data.get("columns") or [])]
                        _agg_col = next(
                            (c for c in _ds_cols if c.lower().startswith(("total_", "avg_"))), None
                        )
                        _is_avg = _agg_col is not None and _agg_col.lower().startswith("avg_")
                        _amt = _agg_col.split("_", 1)[1] if _agg_col else None
                        _money = bool(_amt) and SQLAgent._is_money_col(_amt)
                        data["kind"] = "money" if _money else "numeric"
                        data["agg"] = "avg" if _is_avg else "sum"
                        data["value_label"] = (
                            f"平均{SQLAgent._col_display(_amt)}" if _is_avg else SQLAgent._col_display(_amt)
                        ) if _amt else "记录数"
                        data["dim_label"] = SQLAgent._dim_display(_ds_cols[0]) if _ds_cols else "维度"
                        data["count_label"] = (
                            "订单量" if _money and not SQLAgent._is_unit_price(_amt) else "记录数"
                        )
                        built["dataset"] = data
                        built["acl_check_record"] = {"allowed": True, "note": f"真实数据源接入并执行取数（{data['row_count']} 行）"}
                    else:
                        built["error"] = "真实数据源执行取数失败（SQL 或连接异常）"
                        built["acl_check_record"] = {"allowed": True, "note": "已过 ACL，但真实执行取数失败"}
                # 透传上游指标口径，供下游报告引用
                ms = self._extract_metric_spec(ctx)
                if ms:
                    built["metric_spec"] = ms
                return built
            # 数据源已接入但没拿到真实表结构（连接失败/无表/探查节点无产出）：诚实说明原因，
            # 不能笼统说"未接入数据源"（会误导用户去重复接入）。
            return {
                "sql": "",
                "dataset_object_key": "",
                "acl_check_record": {"allowed": False, "note": "数据源已接入，但未取得真实表结构，未执行取数"},
                "error": "上游探查报告不含真实表结构（数据源连接失败或无可分析表），无法生成取数 SQL",
                "no_data": True,
                "title": "取数 SQL（无表结构）",
            }
        return {
            "sql": "",
            "dataset_object_key": "",
            "acl_check_record": {"allowed": False, "note": "未接入数据源，未执行取数"},
            "error": "未接入数据源，无法生成并执行取数 SQL",
            "no_data": True,
            "title": "取数 SQL（无数据源）",
        }

    @staticmethod
    def _extract_metric_spec(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            ms = payload.get("metric_spec")
            if isinstance(ms, dict):
                return ms
        return None

    # ---- file(CSV) 数据源：真实读取 → 聚合（v2 接回 file 分析链路）----

    _FILE_AMOUNT_COLS = ("sales", "amount", "revenue", "price", "total", "cost", "gmv")
    # 中文/英文金额列优先级（组内任一 token 命中即采用；合计语义优先于单位价，
    # 避免把「单价」当「销售额」汇总——电商/房源类 CSV 的常见口径错误）
    _FILE_AMOUNT_CN: tuple[tuple[str, ...], ...] = (
        ("实付金额", "实付", "actual_amount", "paid_amount"),
        ("应付金额", "应付", "order_amount"),
        ("成交额", "成交金额", "销售额", "销售金额", "gmv"),
        ("总金额", "总价", "金额", "amount", "sales", "revenue", "total"),
        ("收入", "营收", "利润", "成本", "cost", "profit"),
        ("单价", "均价", "price", "unit_price"),
    )
    _FILE_DIM_COLS = ("region", "area", "channel", "city", "category", "status", "product", "ym", "month", "date")
    # 常见中文类别维度词（湛江房源等记录型 CSV 用得上）
    _FILE_DIM_CN = ("区域", "地区", "城市", "渠道", "品类", "类别", "类型", "月份", "年份", "日期", "户型", "朝向", "装修", "楼层", "小区", "状态")
    # 时间列识别：英文约定名 + 中文时间语义词（值唯一、需聚合到时间桶做趋势）
    _TIME_DIM_EN = ("ym", "month", "date", "year", "week", "day", "time", "datetime", "timestamp", "created", "updated")
    _TIME_DIM_CN = ("时间", "日期", "月份", "年份", "日", "下单", "支付", "成交", "创建", "更新", "订单时间", "交易时间")
    # 需求关键词 → 期望维度列（英文列名 / 中文类别词），命中即作为首选
    _DIM_ALIASES = (
        ("大区", ("region", "area", "大区", "区域", "地区")),
        ("战区", ("战区", "region", "区域", "地区")),
        ("区域", ("region", "area", "区域", "地区")), ("城市", ("city", "城市")), ("渠道", ("channel", "渠道")),
        ("省份", ("province", "省份", "省")),
        ("品类", ("category", "品类", "类别", "分类")), ("状态", ("status", "状态")), ("月份", ("ym", "month", "月份", "月")),
        ("户型", ("户型",)), ("朝向", ("朝向",)), ("装修", ("装修",)), ("楼层", ("楼层",)), ("小区", ("小区",)),
        ("品牌", ("品牌", "brand")), ("支付方式", ("支付方式", "payment")),
        ("时间", ("ym", "month", "date", "year", "时间", "日期", "月份", "下单时间", "支付时间")),
    )

    async def _execute_file(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        """读取上传 CSV 全量并聚合（维度 × 记录数 × 数值合计），产出与 SQL 同构的 dataset。"""
        cfg = (ctx.data_source or {}).get("config") or {}
        object_key = str(cfg.get("object_key") or "")
        data = await datasource.load_uploaded_csv(object_key)
        if data is None or not data.get("columns") or not data.get("rows"):
            return SQLAgent._no_data_payload(
                ctx, error=f"文件数据源无法读取（object_key={object_key[:40] or '空'}）", title="取数（文件不可读）"
            )
        fname = str(cfg.get("file_name") or "upload")
        return self._aggregate_records(
            ctx,
            source_label=f"CSV:{fname}",
            source_note="文件数据源真实读取聚合",
            data=data,
            title=f"{fname} 聚合数据",
        )

    async def _execute_api(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        """API 真实取数：请求返回的记录 JSON → 聚合 → dataset（v2.2，与 SQL 同构）。"""
        cfg = (ctx.data_source or {}).get("config") or {}
        url = str(cfg.get("url") or "")
        data = await datasource.api_fetch_records(cfg)
        if data is None or not data.get("columns") or not data.get("rows"):
            return SQLAgent._no_data_payload(
                ctx, error=f"API 数据源无法取数（url={url[:60] or '空'}，无结构化 JSON 记录）", title="取数（API 无记录）"
            )
        host = url.split("/", 3)[-1] if url else "api"
        return self._aggregate_records(
            ctx,
            source_label=f"API:{url[:80]}",
            source_note="API 数据源真实请求解析聚合",
            data=data,
            title=f"API {host[:40]} 聚合数据",
        )

    async def _execute_stream(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        """Kafka 真实取数：读取 topic 内保留的 JSON 记录 → 聚合 → dataset（v2.2，与 SQL 同构）。"""
        cfg = (ctx.data_source or {}).get("config") or {}
        topic = str(cfg.get("topic") or "")
        data = await datasource.kafka_fetch_records(cfg)
        if data is None or not data.get("columns") or not data.get("rows"):
            return SQLAgent._no_data_payload(
                ctx, error=f"Kafka 数据源无法取数（topic={topic[:40] or '空'}，无可用 JSON 消息）", title="取数（Kafka 无记录）"
            )
        return self._aggregate_records(
            ctx,
            source_label=f"KAFKA:{topic}",
            source_note="Kafka 数据源真实消费解析聚合",
            data=data,
            title=f"Kafka {topic[:40]} 聚合数据",
        )

    @staticmethod
    def _no_data_payload(ctx: AgentExecutionContext, *, error: str, title: str) -> dict[str, Any]:
        """取数失败的诚实 no_data 载荷（与 SQL/CSV 失败形态一致）。"""
        return {
            "sql": "",
            "dataset_object_key": "",
            "datasets": [],
            "dataset": None,
            "queries": [],
            "acl_check_record": {"allowed": False, "note": "记录型数据源取数失败，未产出数据集"},
            "acl_check_records": [],
            "error": error,
            "no_data": True,
            "title": title,
        }

    def _aggregate_records(
        self, ctx: AgentExecutionContext, *, source_label: str, source_note: str, data: dict[str, Any], title: str
    ) -> dict[str, Any]:
        """把记录矩阵（CSV/API/Kafka 统一形态 {columns, rows}）聚合成与 SQL 同构的 dataset。

        维度智能选取 + 数值列按需求点名；让下游质检/可视化/报告/终审无需区分记录型数据源。
        """
        cols = [str(c) for c in data["columns"]]
        rows = data["rows"]
        dim = self._pick_csv_dim(cols, rows, ctx.requirement)  # 找不到好维度 → None → 常量"全部"单组
        dim_header = str(dim) if dim else "全部"
        di = cols.index(dim) if dim in cols else None
        # 时间维度 → 按「月」粒度归一化聚合（趋势分析专用，避免明细时间出碎片图）
        time_dim = bool(dim) and SQLAgent._is_time_column(dim_header)
        # 数值列：优先命中需求点名的数值列（建筑面积/单价…），其次约定金额名，最后首个真实数值列
        num_cols = SQLAgent._numeric_csv_columns(cols, rows, exclude=(dim or ""))
        amount_col = SQLAgent._pick_csv_amount(cols, num_cols, ctx.requirement)
        ai = cols.index(amount_col) if amount_col else None
        # 聚合语义：需求要"平均/均价"时按组求均值（否则 SUM 会把单价、费率这类单位值错误相加）
        avg_mode = amount_col is not None and SQLAgent._wants_average(ctx.requirement)
        # 域语义标签：决定报告措辞（金额语义才说"销售额/订单量"，否则按其真实字段说）
        base_label = SQLAgent._col_display(amount_col) if amount_col else "记录数"
        value_label = f"平均{base_label}" if avg_mode else base_label
        money = SQLAgent._is_money_col(amount_col) if amount_col else False
        # 计数语义：只有"订单金额"类口径才叫订单量；单价/面积等记录型度量按记录数计
        count_label = "订单量" if money and not SQLAgent._is_unit_price(amount_col) else "记录数"

        agg: dict[str, list[Any]] = {}
        skipped = 0
        for r in rows:
            if di is not None:
                raw_dim = r[di] if di < len(r) else None
                if time_dim:
                    dim_val = SQLAgent._normalize_time_bucket(raw_dim)
                    if not dim_val:  # 时间值无法解析 → 跳过（不计入趋势桶）
                        skipped += 1
                        continue
                else:
                    dim_val = "" if raw_dim is None else str(raw_dim).strip()
                    if not dim_val:  # 空白维度值不入组（脏值不产生空白碎片）
                        skipped += 1
                        continue
            else:
                dim_val = "全部"
            amount = 0.0
            if ai is not None:
                parsed = SQLAgent._to_number(r[ai] if ai < len(r) else None)
                amount = parsed if parsed is not None else 0.0
            entry = agg.setdefault(dim_val, [0, 0.0])
            entry[0] += 1
            entry[1] += amount

        # 时间维度：按时间桶排序（保证折线图时间走向正确）
        ordered = sorted(agg.items(), key=lambda kv: str(kv[0])) if time_dim else list(agg.items())
        # 无金额列时只输出「维度 + 记录数」两列；不再造一列全 0 的 total_记录数
        # （否则报告会把记录数写成"合计 0.00"，与真实数据矛盾）
        if amount_col:
            value_col = f"{'avg' if avg_mode else 'total'}_{amount_col}"
            columns = [dim_header, "order_count", value_col]
            out_rows: list[list[Any]] = [
                [dv, cnt, round((amount / cnt if cnt else 0.0) if avg_mode else amount, 2)]
                for dv, (cnt, amount) in ordered
            ]
        else:
            columns = [dim_header, "order_count"]
            out_rows = [[dv, cnt] for dv, (cnt, _amount) in ordered]
        dataset = {
            "columns": columns,
            "rows": out_rows,
            "row_count": len(out_rows),
            "source_row_count": len(rows),
            "table": source_label,
            "kind": "money" if money else "numeric",
            "agg": "avg" if avg_mode else "sum",
            "value_label": value_label,
            "dim_label": SQLAgent._dim_display(dim_header),
            "count_label": count_label,
        }
        built: dict[str, Any] = {
            "sql": "",
            "table": source_label,
            "dataset": dataset,
            "acl_check_record": {
                "allowed": True,
                "note": f"{source_note}（{len(rows)} 行 → {len(out_rows)} 个维度单元）"
                + (f"，剔除空维度 {skipped} 行" if skipped else ""),
            },
            "title": title,
        }
        ms = self._extract_metric_spec(ctx)
        if ms:
            built["metric_spec"] = ms
        return built

    @staticmethod
    def _dim_display(col: str | None) -> str:
        """维度列的中文展示名（英文业务列如 region/channel 归一为中文，报告措辞更自然）。"""
        if not col:
            return "维度"
        raw = str(col).strip()
        return _DIM_DISPLAY.get(raw.lower(), raw)

    @staticmethod
    def _is_time_column(col: str) -> bool:
        """判定某列是否为时间列（英文约定名或中文时间语义词）。"""
        low = str(col).strip().lower()
        return low in SQLAgent._TIME_DIM_EN or any(tok in str(col) for tok in SQLAgent._TIME_DIM_CN)

    _AVG_WORDS = ("平均", "均值", "均价", "人均", "户均", "avg", "average", "mean")

    @staticmethod
    def _wants_average(requirement: str) -> bool:
        """需求是否要求"平均/均值"语义（决定按组 AVG 而非 SUM 聚合）。

        单位值（单价/均价/费率）直接 SUM 会得到无意义的"单价合计"，
        必须先按组求均值再比较；这是分析口径正确性的硬要求。
        """
        req = (requirement or "").lower()
        return any(word in req for word in SQLAgent._AVG_WORDS)

    @staticmethod
    def _normalize_time_bucket(value: Any) -> str | None:
        """把时间值归一化为「月」粒度桶（用于趋势聚合），无法解析返回 None。

        支持：`2025-11-24 03:01:47` / `2025-11-24` / `2025/11/24` / `2025-11` / `2025年11月` /
        `2025-11-24T03:01:47` / 时间戳数字。目标：把全唯一的明细时间收敛为可聚合的月桶。
        """
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        # 纯数字时间戳（10/13 位）
        if s.isdigit():
            if len(s) >= 10:
                try:
                    import datetime as _dt
                    ts = int(s[:10])
                    return _dt.datetime.fromtimestamp(ts, tz=_dt.UTC).strftime("%Y-%m")
                except (ValueError, OSError, OverflowError):
                    return None
            return None
        # 标准日期时间：2025-11-24 03:01:47 / 2025-11-24 / 2025/11/24 / 2025-11-24T03:01:47
        m = re.match(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        # 仅年月：2025-11 / 2025年11月
        m = re.match(r"^(\d{4})[-/年](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        return None

    @staticmethod
    def _to_number(value: Any) -> float | None:
        """宽松数值解析：容忍千分位与常见中文单位后缀。

        - 纯数字（3970.14 / 0.37）→ 直接数值；
        - 数字前缀 + 纯中文/符号单位后缀（133平米、13383元/平米、178万、1.5元/㎡·月）→ 取前缀数值；
        - 含内部数字的文字（3室2厅、2025-02、2021/3/21）→ None（避免把文本当数值）。
        """
        if value is None:
            return None
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        whole = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s)
        if whole:
            return float(s)
        match = re.match(r"^([+-]?\d+(?:\.\d+)?)(.*)$", s)
        if match:
            tail = match.group(2).strip()
            # 单位后缀须不含 ascii 数字且为中文/符号（元/平米·月/万/年等），防止 3室2厅/2025-02 误判
            if tail and not re.search(r"[0-9A-Za-z]", tail) and re.fullmatch(r"[一-龥·/%.‰]+", tail):
                return float(match.group(1))
        return None

    @staticmethod
    def _numeric_csv_columns(cols: list[str], rows: list[list[Any]], exclude: str = "") -> list[str]:
        """找出 CSV 中"多数值可解析（含单位后缀）"的列（排除维度列与空表头索引列）。"""
        numeric: set[str] = set()
        for c in cols:
            if c == exclude or not str(c).strip():
                continue
            if str(c).strip().lower().startswith(("unnamed", "index")):
                continue
            try:
                ci = cols.index(c)
            except ValueError:
                continue
            sample = [r[ci] for r in rows[:80] if ci < len(r) and str(r[ci] or "").strip()]
            if not sample:
                continue
            parsed = sum(1 for v in sample if SQLAgent._to_number(v) is not None)
            if parsed / len(sample) >= 0.8:
                numeric.add(c)
        return [c for c in cols if c in numeric]

    @staticmethod
    def _pick_csv_amount(cols: list[str], num_cols: list[str], requirement: str) -> str | None:
        """选 CSV 数值合计列：需求点名的数值列 > 合计语义金额列 > 首个真实数值列。

        金额列识别覆盖中文列名（实付金额/应付金额/成交额/销售额/总价/单价…）与英文约定名，
        且按「合计语义优先于单位价」排序——避免把单价汇总成"销售额"（口径错误）。
        """
        if not num_cols:
            return None
        req = requirement or ""
        # 1) 需求里显式点名某个数值列（如"建筑面积/单价/总价"）→ 精准口径
        for c in num_cols:
            if c and c in req:
                return c
        # 2) 中文/英文金额列（按合计语义优先级分组匹配）
        for tokens in SQLAgent._FILE_AMOUNT_CN:
            hit = next((c for c in num_cols if any(t in c.lower() for t in tokens)), None)
            if hit is not None:
                return hit
        # 3) 英文约定金额列（兼容历史规则）
        named = next((c for c in num_cols if c.lower() in SQLAgent._FILE_AMOUNT_COLS), None)
        return named or num_cols[0]

    @staticmethod
    def _pick_csv_dim(cols: list[str], rows: list[list[Any]], requirement: str) -> str | None:
        """选 CSV 聚合维度（v2.1 维度智能选取，行数基数感知；v3 意图感知时间列）。

        候选 = 非空表头 + 非"序号/ID" + 非纯数值列 + 值有重复（distinct<行数）的类别列；
        顺序：需求点名 > 常见业务维度列 > 低基数（重复最多）列；找不到返回 None（调用方用常量"全部"单组，
        避免把空表头/行号列当维度，出 96 行碎片图）。

        v3：趋势/预测意图时，若存在可解析的时间列（即使值全唯一），优先选它做维度，
        由聚合方按「月」粒度归一化（否则趋势分析会退化成按明细时间出碎片图/选错维度）。
        """
        n = len(rows)
        if not cols or n == 0:
            return None
        intent = classify_intent(requirement)
        # 时间列优先识别（值可能全唯一，不能走下方 distinct 过滤，须单独处理）
        time_col = SQLAgent._pick_time_column(cols, rows, requirement, intent)
        if time_col is not None:
            return time_col
        known: list[tuple[str, int]] = []  # (列名, distinct 基数)
        for idx, name in enumerate(cols):
            header = str(name).strip()
            if not header or header.lower().startswith(("unnamed", "index")):
                continue  # 空表头/索引列
            low = header.lower()
            if low in ("id", "no", "num", "序号", "编号") or "编号" in header or low.endswith("_id") or "订单号" in header:
                continue  # ID/序号列
            vals: list[str] = []
            for r in rows:
                cell = r[idx] if idx < len(r) else None
                s = "" if cell is None else str(cell).strip()
                if s:
                    vals.append(s)
            if not vals:
                continue
            distinct = len(set(vals))
            if distinct < 2 or distinct >= n:
                continue  # 常量列 / 全唯一（行号、ID 等无可重复）不做维度，避免一条记录一个碎片组
            # 纯数值列默认不是维度（可能是度量）；时间/月份类除外
            parsed = sum(1 for v in vals[:80] if SQLAgent._to_number(v) is not None)
            is_time_like = low in ("ym", "month", "date", "year") or any(tok in header for tok in ("月份", "日期", "年份", "年"))
            if parsed / len(vals[:80]) >= 0.6 and not is_time_like:
                continue
            known.append((header, distinct))
        if not known:
            return None
        req = requirement or ""
        # 1) 需求点名维度列（中文近义词 / 需求直接含列名）
        for keyword, aliases in SQLAgent._DIM_ALIASES:
            if keyword in req:
                hit = next((name for name, _ in known if name in aliases), None)
                if hit:
                    return hit
        for name, _ in known:
            if name in req:
                return name
        # 2) 常见业务维度列（英文约定名 / 中文类别词）→ 取基数最低（聚合度最高）者，图更干净
        biz = [
            (name, distinct) for name, distinct in known
            if name.lower() in SQLAgent._FILE_DIM_COLS or any(tok in name for tok in SQLAgent._FILE_DIM_CN)
        ]
        if biz:
            return min(biz, key=lambda item: item[1])[0]
        # 3) 其余低基数类别列兜底
        return min(known, key=lambda item: item[1])[0]

    @staticmethod
    def _pick_time_column(
        cols: list[str], rows: list[list[Any]], requirement: str, intent: Any
    ) -> str | None:
        """识别可作为趋势维度的时间列。

        优先级：需求点名的「时间/月份」列 > 意图为趋势/预测时的时间列 > 任何可解析的时间列
        （只要样本能归一化到月桶即视为有效时间列）。
        """
        req = requirement or ""
        # 找出所有时间列（按列名判定）
        time_cols: list[str] = []
        for idx, name in enumerate(cols):
            header = str(name).strip()
            if not header:
                continue
            if SQLAgent._is_time_column(header):
                time_cols.append(header)
        if not time_cols:
            return None
        # 1) 需求显式点名时间维度（"各月/走势/趋势/按时间/月份"等）
        time_keywords = ("月", "走势", "趋势", "时间", "日期", "年", "季度", "增长", "变化", "同比", "环比")
        if any(k in req for k in time_keywords):
            for tc in time_cols:
                # 需求含具体时间列名，或该列名与需求时间词匹配
                if tc in req:
                    return tc
            # 优先选值可归一化为月桶的列
            for tc in time_cols:
                idx = cols.index(tc)
                if SQLAgent._time_column_resolvable(rows, idx):
                    return tc
        # 2) 意图为趋势/预测 → 直接选可解析的时间列
        if getattr(intent, "time_series", False) or getattr(intent, "primary", "") in ("trend", "prediction"):
            for tc in time_cols:
                idx = cols.index(tc)
                if SQLAgent._time_column_resolvable(rows, idx):
                    return tc
        # 3) 兜底：任何可解析的时间列（仅在无其它维度时才启用，避免喧宾夺主）
        return None

    @staticmethod
    def _time_column_resolvable(rows: list[list[Any]], idx: int) -> bool:
        """样本中该列至少一半能归一化为月桶，则视为可用时间列。"""
        sample = [r[idx] for r in rows[:50] if idx < len(r) and str(r[idx] or "").strip()]
        if not sample:
            return False
        parsed = sum(1 for v in sample if SQLAgent._normalize_time_bucket(v) is not None)
        return parsed / len(sample) >= 0.5

    @staticmethod
    def _is_money_col(col: str | None) -> bool:
        """按列名判定是否为金额/销售语义。"""
        if not col:
            return False
        low = col.lower()
        money_words = ("amount", "sales", "revenue", "gmv", "price", "cost", "total", "sum", "成交", "营收", "收入", "利润", "成本")
        if low in money_words or low.startswith(("total_", "sum_", "avg_price")):
            return True
        return any(k in col for k in ("金额", "销售额", "销售", "收入", "营收", "价", "GMV"))

    @staticmethod
    def _is_unit_price(col: str | None) -> bool:
        """是否单位价/单位量列（单价、均价、单位面积价…）：这类列只应求均值或直接展示。

        用于报告措辞：单价类数据集的计数语义是"记录数"，不是"订单量"。
        """
        if not col:
            return False
        low = col.lower()
        return low in ("price", "unit_price", "avg_price") or any(k in col for k in ("单价", "均价", "单位价"))

    @staticmethod
    def _col_display(col: str | None) -> str:
        """数值列的中文展示名（销售语义归一为"销售额"，否则原列名）。"""
        if not col:
            return "指标"
        low = col.lower()
        mapping = {
            "amount": "销售额", "sales": "销售额", "revenue": "收入", "gmv": "成交额",
            "price": "成交额", "total_amount": "总额", "cost": "成本额",
        }
        return mapping.get(low, str(col))

    def _build_from_profile(self, ctx: AgentExecutionContext) -> dict[str, Any] | None:
        """从上游探查报告提取真实表 + 字段，生成可执行的聚合 SELECT。"""
        for content in ctx.inputs.values():
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            report = payload.get("report")
            if not isinstance(report, dict):
                continue
            table = report.get("table")
            columns = report.get("columns")
            if not table or not isinstance(columns, list) or not columns:
                continue

            safe_cols = [c["name"] for c in columns if isinstance(c, dict) and c.get("name")]
            if not safe_cols:
                continue
            # 单一分析维度聚合（保证数据集完整、可核对）：需求关键词优先，否则用业务维度列
            dim = self._pick_dim(safe_cols, ctx.requirement)
            amount_col = next((c for c in safe_cols if c.lower() in ("amount", "sales", "revenue", "price", "total", "cost")), None)
            ds_type = str((ctx.data_source or {}).get("type") or "mysql")
            q_dim = self._quote_ident(dim, ds_type)
            q_table = self._quote_ident(table, ds_type)
            avg_mode = self._wants_average(ctx.requirement) and amount_col is not None
            agg = ", COUNT(*) AS order_count"
            if amount_col:
                q_amount = self._quote_ident(amount_col, ds_type)
                func = "AVG" if avg_mode else "SUM"
                alias = self._quote_ident(f"{'avg' if avg_mode else 'total'}_{amount_col}", ds_type)
                agg += f", ROUND({func}({q_amount}), 2) AS {alias}"
            sql = f"SELECT {q_dim}{agg} FROM {q_table} GROUP BY {q_dim}"
            return {
                "sql": sql,
                "table": table,
                "dataset_object_key": "",
                "acl_check_record": {"allowed": True, "note": f"真实数据源接入（表 {table}）"},
                "title": f"{table} 取数SQL",
            }
        return None

    @staticmethod
    def _quote_ident(name: str, ds_type: str) -> str:
        """按方言引用标识符并去除引号字符（防注入 + MySQL/Postgres 兼容）。"""
        name = name.replace("`", "").replace('"', "").replace("'", "")
        if ds_type == "postgres":
            return f'"{name}"'
        return f"`{name}`"

    @staticmethod
    def _pick_dim(safe_cols: list[str], requirement: str) -> str:
        """选分析维度：意图感知（趋势→时间列优先）> 需求关键词 > 常用业务维度列 > 首列。"""
        intent = classify_intent(requirement)
        req = (requirement or "").lower()
        # 1) 趋势/预测意图 → 优先时间维度列（ym/month/date/year），让图表呈现时间序列
        if intent.time_series:
            for dim in ("ym", "month", "date", "year", "week"):
                if dim in safe_cols:
                    return dim
        # 2) 需求关键词点名维度
        for keyword, dim in (
            ("渠道", "channel"), ("区域", "region"), ("地区", "region"), ("城市", "city"),
            ("品类", "category"), ("状态", "status"), ("客户", "customer_id"), ("月份", "ym"),
        ):
            if keyword in req and dim in safe_cols:
                return dim
        # 3) 常用业务维度列
        for dim in ("region", "channel", "category", "city", "status", "ym", "month", "date"):
            if dim in safe_cols:
                return dim
        return safe_cols[0]

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        return {
            "sql": "",
            "dataset_object_key": "",
            "acl_check_record": {"allowed": False, "note": "未执行取数"},
            "error": "取数生成失败，无可用数据",
            "no_data": True,
            "title": "取数 SQL（失败）",
        }

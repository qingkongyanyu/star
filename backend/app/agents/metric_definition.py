"""MetricDefinitionAgent：指标定义与口径专家 → metric_spec。

上游含真实探查报告（profile）时，基于真实表/列**确定性**生成指标口径
（公式使用真实列名，杜绝 LLM 编造列）；并检索**指标知识库（kb_indicator）**，
命中时优先采用管理员维护的业务口径；无数据源时回退 LLM。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import AgentBase, AgentExecutionContext
from app.infra.db import to_thread
from app.repositories.kb_repo import KbRepo
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是【MetricDefinitionAgent 指标定义与口径专家】，全系统唯一指标口径来源。
只输出口径事实：不取数、不生成图表、不做业务分析。
请严格输出 JSON，格式：{"metric_spec": {"name": string, "formula": string, "description": string},
"constraints": [{"dimension": string, "rule": string}], "title": string}。"""

# 金额类列名 → 指标名称
_AMOUNT_NAMES: dict[str, str] = {
    "amount": "销售额",
    "sales": "销售额",
    "revenue": "收入",
    "price": "成交额",
    "total": "合计额",
    "cost": "成本额",
}
# 中文金额列关键词 → 指标名称（电商/业务 CSV 常见"实付金额/应付金额/总价"等）
# 顺序即优先级：合计语义（实付/应付/成交/销售/总金额）优先于单位价（单价/均价）
_AMOUNT_CN: tuple[tuple[str, str], ...] = (
    ("实付金额", "实付金额"), ("应付金额", "应付金额"), ("成交金额", "成交金额"),
    ("销售额", "销售额"), ("总金额", "总金额"), ("成交额", "成交额"),
    ("收入", "收入"), ("营收", "营收"), ("成本", "成本额"), ("利润", "利润"),
    ("总价", "总价"), ("金额", "金额"), ("单价", "单价"), ("均价", "均价"),
)
# 知识库匹配：真实列名 → 管理员口径的中文名（用于命中知识库条目）
_COL_ALIASES: dict[str, str] = {
    "amount": "销售额",
    "sales": "销售额",
    "revenue": "收入",
    "price": "成交额",
    "total": "合计额",
    "cost": "成本额",
    "order_id": "订单量",
}
# 计数类列名提示（优先作为 COUNT 口径）
_COUNT_HINTS = ("order_id", "order_no", "id", "customer_id", "transaction_id", "bill_no", "序号")


def _is_amount_column(col: str) -> str | None:
    """判定列是否为金额/度量列，返回其中文指标名（非金额返回 None）。

    兼容英文约定列名（amount/sales/...）与中文金额列名（实付金额/总价/单价/收入…），
    与 SQLAgent._is_money_col 语义对齐，避免中文 CSV 指标口径退化为「记录数」。
    """
    low = col.strip().lower()
    if low in _AMOUNT_NAMES:
        return _AMOUNT_NAMES[low]
    for token, name in _AMOUNT_CN:
        if token in col:
            return name
    return None


def _formula_columns(formula: str | None) -> list[str]:
    """抽取口径公式中引用的列名（SUM/COUNT/AVG/MAX/MIN 的参数）。

    用于校验「知识库口径」是否与真实表结构一致：引用了不存在的列时不能直接采用，
    否则报告会写出对真实数据不成立的公式（口径幻觉）。
    """
    if not formula:
        return []
    return [
        backtick or bare
        for backtick, bare in re.findall(
            r"(?:SUM|COUNT|AVG|MAX|MIN)\(\s*(?:`([^`]+)`|([^(),\s]+))\s*\)", formula, flags=re.IGNORECASE
        )
        if (backtick or bare)
    ]


class MetricDefinitionAgent(AgentBase):
    CAPABILITY = "metric_definition"
    ASSET_TYPE = "metric_spec"
    SYSTEM_PROMPT = _SYSTEM_PROMPT

    async def execute(self, ctx: AgentExecutionContext, deps: ToolDeps) -> dict[str, Any]:
        """有真实探查 schema 时确定性定义口径（列名对齐），并按知识库优先采用管理员口径。

        无真实探查时绝不调用 LLM 编造表/列名：只检索知识库（管理员口径），命不中就诚实标注 no_data。
        v3：优先消费 AnalysisPlan 的指标目录（可多个），产出 metric_specs。
        """
        plan_metrics = [m for m in (ctx.plan or {}).get("metrics") or [] if isinstance(m, dict)]
        if plan_metrics:
            return await self._plan_specs(ctx, deps, plan_metrics)
        profile = self._extract_profile(ctx)
        if profile is not None:
            spec = self._build_spec(profile, ctx.requirement)
            avg_requirement = any(word in (ctx.requirement or "") for word in ("平均", "均值", "均价", "人均", "户均"))
            kb = await self._kb_lookup(deps, ctx.requirement, spec)
            if kb is not None:
                column_names = {
                    str(c.get("name", "")) for c in profile.get("columns", []) if isinstance(c, dict)
                }
                refs = _formula_columns(kb.formula)
                missing = [ref for ref in refs if column_names and ref not in column_names]
                uses_avg = bool(kb.formula) and "avg(" in kb.formula.lower()
                formula_ok = bool(kb.formula) and not missing and (not avg_requirement or uses_avg)
                # 指标名只在"需求点名了该业务指标"时采用（否则会把无关指标名套到本任务上）
                if kb.metric_name and (kb.metric_name in (ctx.requirement or "") or not kb.formula):
                    spec["name"] = kb.metric_name
                    if kb.description:
                        spec["description"] = kb.description
                if formula_ok:
                    spec["formula"] = kb.formula
                elif kb.formula:
                    # 口径公式必须落在真实列上：引用的列不存在（或平均口径却给 SUM）时保留真实列口径，
                    # 并留下可审计说明（不让报告写出对真实数据不成立的公式）。
                    spec["kb_formula_ignored"] = True
                    spec["kb_formula_note"] = (
                        f"知识库口径引用的字段 {', '.join(missing)} 不在真实表结构中，已改用真实列口径"
                        if missing
                        else "知识库口径与本次「平均」分析语义不一致（SUM ≠ AVG），已改用真实列均值口径"
                    )
                spec["kb_indicator_key"] = kb.indicator_key
            return {
                "metric_spec": spec,
                "constraints": [],
                "title": "指标口径文档",
            }
        # 无真实探查：仅采用知识库中管理员维护的口径，否则诚实标注无数据源
        kb = await self._kb_lookup(deps, ctx.requirement, {})
        if kb is not None:
            spec = {
                "name": kb.metric_name or "",
                "formula": kb.formula or "",
                "description": kb.description or "",
                "kb_indicator_key": kb.indicator_key,
                "no_data": True,
            }
            return {"metric_spec": spec, "constraints": [], "title": "指标口径文档（无数据源，采用知识库口径）"}
        return {
            "metric_spec": {
                "name": "",
                "formula": "",
                "description": "未接入数据源，无法基于真实表结构定义口径",
                "no_data": True,
            },
            "constraints": [],
            "title": "指标口径文档（无数据源）",
        }

    async def _plan_specs(
        self, ctx: AgentExecutionContext, deps: ToolDeps, plan_metrics: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """把计划里的指标目录落成口径文档；主指标允许知识库覆盖（需过列存在性/聚合语义校验）。"""
        schema = (ctx.plan or {}).get("schema") or {}
        columns = {str(c.get("name")) for c in schema.get("columns") or [] if isinstance(c, dict)}
        specs: list[dict[str, Any]] = []
        for index, metric in enumerate(plan_metrics):
            spec: dict[str, Any] = {
                "id": metric.get("id"),
                "name": metric.get("name") or "",
                "formula": metric.get("formula") or "",
                "agg": metric.get("agg"),
                "column": metric.get("column"),
                "unit": metric.get("unit") or "",
                "kind": metric.get("kind") or "numeric",
                "feasible": bool(metric.get("feasible", True)),
                "reason": metric.get("reason") or "",
                "derived": bool(metric.get("derived", False)),
                "numerator": metric.get("numerator"),
                "denominator": metric.get("denominator"),
                "description": metric.get("description") or "",
                "table": schema.get("table") or "",
            }
            if index == 0:
                kb = await self._kb_lookup(deps, ctx.requirement, spec)
                if kb is not None:
                    spec = self._apply_kb(spec, kb, columns, ctx.requirement)
            specs.append(spec)
        return {
            "metric_specs": specs,
            "metric_spec": specs[0],
            "constraints": [],
            "unavailable": (ctx.plan or {}).get("unavailable") or [],
            "title": "指标口径文档",
        }

    @staticmethod
    def _apply_kb(
        spec: dict[str, Any], kb: Any, columns: set[str], requirement: str
    ) -> dict[str, Any]:
        """知识库口径覆盖（带列存在性与聚合语义校验，失败保留真实列口径）。"""
        avg_requirement = any(word in (requirement or "") for word in ("平均", "均值", "均价", "人均", "户均"))
        refs = _formula_columns(kb.formula)
        missing = [ref for ref in refs if columns and ref not in columns]
        uses_avg = bool(kb.formula) and "avg(" in kb.formula.lower()
        formula_ok = bool(kb.formula) and not missing and (not avg_requirement or uses_avg)
        if kb.metric_name and (formula_ok or not kb.formula) and (
            kb.metric_name in (requirement or "") or not kb.formula
        ):
            spec["name"] = kb.metric_name
            if kb.description:
                spec["description"] = kb.description
        if formula_ok:
            spec["formula"] = kb.formula
        elif kb.formula:
            spec["kb_formula_ignored"] = True
            spec["kb_formula_note"] = (
                f"知识库口径引用的字段 {', '.join(missing)} 不在真实表结构中，已改用真实列口径"
                if missing
                else "知识库口径与本次「平均」分析语义不一致（SUM ≠ AVG），已改用真实列均值口径"
            )
        spec["kb_indicator_key"] = kb.indicator_key
        return spec

    async def _kb_lookup(
        self, deps: ToolDeps, requirement: str, spec: dict[str, Any]
    ) -> Any | None:
        """检索指标知识库：需求关键词命中 / 金额列别名命中 → 返回管理员维护的指标口径。"""
        try:
            rows = await to_thread(KbRepo.list_all, deps.task_session, 200)
        except Exception:  # 知识库不可用不影响口径定义
            logger.warning("指标知识库检索失败，使用通用口径", exc_info=True)
            return None
        if not rows:
            return None
        req = requirement or ""
        # 口径公式现为方言中性写法（SUM(sales) 或保留历史 SUM(`sales`)）都能取到列名
        m = re.search(r"(?:SUM|COUNT)\(\s*(?:`([^`]+)`|([A-Za-z_]\w*))\s*\)", spec.get("formula", ""))
        amount_col = (m.group(1) or m.group(2)).lower() if m else ""
        amount_cn = _COL_ALIASES.get(amount_col)

        best = None
        best_score = 0
        for kb in rows:
            score = 0
            name = kb.metric_name or ""
            if name and name in req:
                score += 3
            if kb.description and kb.description in req:
                score += 2
            # 标签命中只加 1 分：标签常是"区域/销售"等泛词，单靠标签命中容易误配口径
            if any(tag and tag in req for tag in (kb.tags or [])):
                score += 1
            if amount_cn and (name == amount_cn or amount_cn in name):
                score += 2
            if score > best_score:
                best_score, best = score, kb
        # 阈值 3：必须"指标名被点名"或"口径与金额列对得上"，避免泛标签把无关指标套到本任务上
        return best if best_score >= 3 else None

    @staticmethod
    def _extract_profile(ctx: AgentExecutionContext) -> dict[str, Any] | None:
        """从上游输入解析探查报告（须含真实列名；空 columns/no_data 视为无真实探查）。"""
        for content in ctx.inputs.values():
            if not content:
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            report = payload.get("report")
            if (
                isinstance(report, dict)
                and isinstance(report.get("columns"), list)
                and report.get("columns")
                and not report.get("no_data")
            ):
                return report
        return None

    @staticmethod
    def _build_spec(report: dict[str, Any], requirement: str = "") -> dict[str, Any]:
        """基于真实列名生成指标口径（确定性，兼容中英文金额列）。

        - 需求点名的数值列优先（"平均单价/总价/实付金额"），与 SQLAgent 的取数列保持一致；
        - 需求含"平均/均值/均价" → 口径用 AVG（SUM 会把单价、费率这类单位值错误相加）。
        """
        table = str(report.get("table") or "数据表")
        columns = [str(c.get("name", "")) for c in report.get("columns", []) if isinstance(c, dict)]
        if not columns:
            return {"name": "记录数", "formula": "COUNT(*)", "description": f"{table} 记录数", "table": table}
        req = requirement or ""
        avg = any(word in req for word in ("平均", "均值", "均价", "人均", "户均"))

        # 0) 需求点名的"可度量"列（中文列名可直接命中，如"单价""总价""实付金额"）；
        #    维度列（区域/城市/类目）即使出现在需求里也不能当指标。
        col_types = {
            str(c.get("name", "")): str(c.get("type", "")).lower()
            for c in report.get("columns", [])
            if isinstance(c, dict)
        }
        numeric_types = ("int", "float", "integer", "decimal", "double", "bigint", "numeric")

        def _measure(col: str) -> bool:
            return col_types.get(col, "") in numeric_types or _is_amount_column(col) is not None

        named = next((c for c in columns if c and len(c) >= 2 and c in req and _measure(c)), None)

        # 金额列：英文约定名优先，其次中文金额列（按 _AMOUNT_CN 优先级选合计语义列，而非首个命中）
        amount_col = next((c for c in columns if c.lower() in _AMOUNT_NAMES), None)
        amount_name = _AMOUNT_NAMES.get(amount_col.lower()) if amount_col else None
        if amount_col is None:
            for token, name in _AMOUNT_CN:  # 按优先级顺序遍历，命中合计语义列即停
                hit = next((c for c in columns if token in c), None)
                if hit is not None:
                    amount_col, amount_name = hit, name
                    break
        if named is not None:
            amount_col = named
            amount_name = _is_amount_column(named) or named

        if amount_col and amount_name:
            if avg:
                return {
                    "name": f"平均{amount_name}",
                    "formula": f"AVG({amount_col})",
                    "description": f"{table} 的 {amount_col} 均值（AVG）",
                    "table": table,
                }
            return {
                "name": amount_name,
                # 口径公式用不加方言引号的写法（MySQL/Postgres 均成立，避免出现 SUM(`col`) 的 MySQL 引号）
                "formula": f"SUM({amount_col})",
                "description": f"{table} 的 {amount_col} 字段合计（SUM）",
                "table": table,
            }
        count_col = next((c for c in columns if c.lower() in _COUNT_HINTS), columns[0])
        if avg:
            return {
                "name": f"平均{count_col}",
                "formula": f"AVG({count_col})",
                "description": f"{table} 的 {count_col} 均值（AVG）",
                "table": table,
            }
        return {
            "name": "记录数",
            "formula": f"COUNT({count_col})",
            "description": f"{table} 按 {count_col} 统计记录数",
            "table": table,
        }

    def fallback(self, ctx: AgentExecutionContext) -> dict[str, Any]:
        return {
            "metric_spec": {
                "name": "",
                "formula": "",
                "description": f"指标定义失败（需求：{ctx.requirement}），无可用数据",
                "no_data": True,
            },
            "constraints": [],
            "title": "指标口径文档（失败）",
        }

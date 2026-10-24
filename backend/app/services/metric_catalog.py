"""指标目录（Metric Catalog）：从真实表结构生成「可计算、可解释」的指标候选。

设计目标（对应 v3「指标不能永远同一口径」）：
- **数据驱动**：指标只能来自真实字段（存在性 + 类型 + 语义），不凭空发明列；
- **多层指标**：基础聚合（SUM/COUNT/AVG/COUNT DISTINCT）+ 派生指标（客单价/毛利率/占比…）；
- **需求匹配**：需求点名的业务指标优先，其次按分析意图与数据默认；
- **可行性诚实**：算不出来的指标返回 `feasible=false + reason`，由报告显式说明。

本模块只做纯逻辑（无 DB / 无 IO），可被 Planner / MetricDefinition / SQLAgent / 报告复用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------- 字段语义 ----------

_TIME_HINTS = ("时间", "日期", "月份", "年份", "date", "time", "ym", "month", "year", "day", "日期时间")
_ID_HINTS = (
    "订单号", "订单id", "order_id", "order_no", "orderno", "编号", "id", "客户id", "customer_id",
    "用户id", "user_id", "手机号", "phone", "身份证", "id_card",
)
_MONEY_HINTS = (
    "销售额", "销售金额", "金额", "实付", "应付", "成交额", "成交金额", "收入", "营收", "利润", "成本",
    "单价", "均价", "总价", "价格", "amount", "sales", "revenue", "gmv", "price", "cost", "profit", "total",
)
_QUANTITY_HINTS = ("数量", "件数", "销量", "库存", "qty", "quantity", "count", "num")
_RATIO_HINTS = ("折扣", "税率", "利率", "占比", "率", "rate", "ratio", "discount", "percent")
_CATEGORY_HINTS = (
    "地区", "大区", "区域", "省份", "省", "城市", "战区", "品类", "类目", "类别", "渠道", "品牌",
    "状态", "方式", "会员", "等级", "类型", "region", "area", "province", "city", "category",
    "channel", "brand", "status", "type", "level", "segment",
)

_MONEY_DISPLAY = {
    "amount": "销售额",
    "sales": "销售额",
    "revenue": "收入",
    "gmv": "成交额",
    "price": "单价",
    "unit_price": "单价",
    "avg_price": "平均单价",
    "cost": "成本额",
    "profit": "利润",
    "total": "合计额",
}


def to_number(value: Any) -> float | None:
    """宽松数值解析（容忍千分位/中文单位后缀），与 SQLAgent 的口径保持一致。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return float(text)
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)(.*)$", text)
    if match:
        tail = match.group(2).strip()
        if tail and not re.search(r"[0-9A-Za-z]", tail) and re.fullmatch(r"[一-龥·/%.‰]+", tail):
            return float(match.group(1))
    return None


def _looks_like_time(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?", text))


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(hint in low for hint in hints)


@dataclass
class ColumnProfile:
    """字段画像：语义角色 + 基数 + 样本（供指标/图表/计划共同使用）。"""

    name: str
    type: str
    role: str  # time | id | money | quantity | ratio | numeric | category | text
    cardinality: int | None = None
    samples: list[str] = field(default_factory=list)

    @property
    def is_measure(self) -> bool:
        return self.role in ("money", "quantity", "ratio", "numeric")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "role": self.role,
            "cardinality": self.cardinality,
            "samples": self.samples[:8],
        }


def build_column_profiles(schema: dict[str, Any]) -> list[ColumnProfile]:
    """由探查 schema 生成字段画像（名称 + 类型 + 样本值）。"""
    columns = [c for c in schema.get("columns") or [] if isinstance(c, dict) and c.get("name")]
    raw_samples = schema.get("sample_rows") or []
    profiles: list[ColumnProfile] = []
    for idx, column in enumerate(columns):
        name = str(column.get("name"))
        type_name = str(column.get("type") or "").lower()
        samples = [str(row[idx]).strip() for row in raw_samples if isinstance(row, (list, tuple)) and idx < len(row)]
        samples = [s for s in samples if s][:60]
        non_empty = [s for s in samples if s]
        parsed = sum(1 for s in non_empty if to_number(s) is not None)
        numeric = type_name in ("int", "float", "integer", "decimal", "double", "bigint", "numeric") or (
            bool(non_empty) and parsed / len(non_empty) >= 0.8
        )
        time_like = _contains_any(name, _TIME_HINTS) or type_name in ("date", "datetime", "timestamp") or (
            bool(non_empty) and sum(1 for s in non_empty if _looks_like_time(s)) / len(non_empty) >= 0.6
        )
        if time_like:
            role = "time"
        elif _contains_any(name, _ID_HINTS):
            role = "id"
        elif _contains_any(name, _MONEY_HINTS):
            role = "money" if numeric else "text"
        elif _contains_any(name, _QUANTITY_HINTS):
            role = "quantity" if numeric else "text"
        elif _contains_any(name, _RATIO_HINTS):
            role = "ratio" if numeric else "text"
        elif numeric:
            role = "numeric"
        elif _contains_any(name, _CATEGORY_HINTS):
            role = "category"
        else:
            cardinality = len(set(non_empty)) if non_empty else None
            role = "category" if cardinality is not None and cardinality <= 50 else "text"
        cardinality = len(set(non_empty)) if non_empty else None
        profiles.append(
            ColumnProfile(name=name, type=type_name or "string", role=role, cardinality=cardinality, samples=samples[:12])
        )
    return profiles


# ---------- 指标候选 ----------


@dataclass
class MetricCandidate:
    """一个可执行/待说明的指标口径。"""

    id: str
    name: str
    formula: str
    agg: str  # sum | avg | count | count_distinct | ratio
    column: str | None = None
    unit: str = ""
    kind: str = "numeric"  # money | count | quantity | ratio | numeric
    aliases: tuple[str, ...] = ()
    feasible: bool = True
    reason: str = ""
    derived: bool = False
    numerator: dict[str, Any] | None = None
    denominator: dict[str, Any] | None = None
    description: str = ""
    rank: int = 0  # 同分候选的业务优先级微调（如单价类：AVG 优于 SUM）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "formula": self.formula,
            "agg": self.agg,
            "column": self.column,
            "unit": self.unit,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "feasible": self.feasible,
            "reason": self.reason,
            "derived": self.derived,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "description": self.description,
            "rank": self.rank,
        }


def _display_name(column: str) -> str:
    low = column.strip().lower()
    return _MONEY_DISPLAY.get(low, column.strip())


def _money_rank(column: str) -> int:
    """金额列的业务优先级（同分时决定谁作为「销售额」默认口径）。"""
    if any(token in column for token in ("实付", "actual", "paid")):
        return 1
    if any(token in column for token in ("应付", "order_amount")):
        return 2
    if any(token in column for token in ("销售额", "销售金额", "成交额", "成交金额", "gmv", "sales", "revenue", "amount")):
        return 3
    if any(token in column for token in ("总价", "总金额", "金额", "total")):
        return 4
    if any(token in column for token in ("收入", "营收")):
        return 5
    if any(token in column for token in ("成本", "利润", "cost", "profit")):
        return 6
    if any(token in column for token in ("单价", "均价", "price", "unit")):
        return 7
    return 8


def build_metric_catalog(columns: list[ColumnProfile]) -> list[MetricCandidate]:
    """从字段画像生成指标候选（基础聚合 + 派生指标）。"""
    catalog: list[MetricCandidate] = []
    money_columns = sorted((c for c in columns if c.role == "money"), key=lambda c: _money_rank(c.name))
    quantity_columns = [c for c in columns if c.role == "quantity"]
    id_columns = [c for c in columns if c.role == "id"]
    ratio_columns = [c for c in columns if c.role == "ratio"]

    # 记录数（永远可用）
    catalog.append(
        MetricCandidate(
            id="count",
            name="记录数",
            formula="COUNT(*)",
            agg="count",
            unit="条",
            kind="count",
            aliases=("记录数", "条数", "数据量", "行数", "总数"),
            description="按行计数（不区分字段）",
        )
    )

    # 金额类：SUM + AVG（单价类用 AVG 更有业务意义，也保留 SUM 供合计口径）
    for column in money_columns:
        name = _display_name(column.name)
        is_unit_price = any(token in column.name for token in ("单价", "均价", "单位价")) or column.name.lower() in (
            "price",
            "unit_price",
            "avg_price",
        )
        # 只有「合计语义」的销售额列才接受「销售额/金额/GMV」等泛化别名；
        # 单价、退款、成本、折扣等列挂这些别名会被误选成"销售额"（SUM(退款金额) 不是销售额）。
        non_sales = any(token in column.name for token in ("退款", "成本", "利润", "折扣", "运费", "税"))
        generic_aliases: tuple[str, ...] = () if (is_unit_price or non_sales) else ("销售额", "销售", "金额", "GMV", "gmv", "营收")
        catalog.append(
            MetricCandidate(
                id=f"sum:{column.name}",
                name=f"{name}" if not is_unit_price else f"{name}合计",
                formula=f"SUM({column.name})",
                agg="sum",
                column=column.name,
                unit="元",
                kind="money",
                aliases=tuple(dict.fromkeys((name, column.name, *generic_aliases))),
                description=f"{column.name} 字段合计",
                rank=-1 if is_unit_price else 0,
            )
        )
        if is_unit_price:
            catalog.append(
                MetricCandidate(
                    id=f"avg:{column.name}",
                    name=f"平均{name}",
                    formula=f"AVG({column.name})",
                    agg="avg",
                    column=column.name,
                    unit="元",
                    kind="money",
                    aliases=tuple(dict.fromkeys((f"平均{name}", name, column.name, "单价", "均价"))),
                    description=f"{column.name} 字段均值（单位价不可直接相加，用均值比较）",
                    rank=1,
                )
            )

    # 数量类：SUM
    for column in quantity_columns:
        catalog.append(
            MetricCandidate(
                id=f"sum:{column.name}",
                name=column.name if column.name not in ("数量", "qty", "quantity") else "销量",
                formula=f"SUM({column.name})",
                agg="sum",
                column=column.name,
                unit="件",
                kind="quantity",
                aliases=(column.name, "销量", "数量", "件数"),
                description=f"{column.name} 字段合计",
            )
        )

    # 比率类：AVG
    for column in ratio_columns:
        catalog.append(
            MetricCandidate(
                id=f"avg:{column.name}",
                name=f"平均{column.name}",
                formula=f"AVG({column.name})",
                agg="avg",
                column=column.name,
                unit="",
                kind="ratio",
                aliases=(column.name, f"平均{column.name}"),
                description=f"{column.name} 字段均值",
            )
        )

    # 标识类：去重计数（订单量/客户数）
    for column in id_columns:
        low = column.name.lower()
        id_name: str
        id_aliases: tuple[str, ...]
        id_unit: str
        if any(token in column.name for token in ("订单",)) or "order" in low:
            id_name, id_aliases, id_unit = "订单量", ("订单量", "订单数", "单量", "订单"), "单"
        elif any(token in column.name for token in ("客户", "用户", "会员")) or any(
            token in low for token in ("customer", "user", "member")
        ):
            id_name, id_aliases, id_unit = "客户数", ("客户数", "用户数", "人数", "买家数"), "人"
        else:
            id_name, id_aliases, id_unit = f"{column.name}去重数", (column.name, "去重数"), "个"
        catalog.append(
            MetricCandidate(
                id=f"count_distinct:{column.name}",
                name=id_name,
                formula=f"COUNT(DISTINCT {column.name})",
                agg="count_distinct",
                column=column.name,
                unit=id_unit,
                kind="count",
                aliases=id_aliases,
                description=f"{column.name} 的去重计数",
            )
        )

    # 派生：客单价 = 金额合计 / 订单数
    order_id = next((c for c in id_columns if ("订单" in c.name or "order" in c.name.lower())), None)
    if money_columns and order_id is not None:
        money = next((c for c in money_columns if "实付" in c.name), money_columns[0])
        catalog.append(
            MetricCandidate(
                id="ratio:客单价",
                name="客单价",
                formula=f"SUM({money.name})/COUNT(DISTINCT {order_id.name})",
                agg="ratio",
                unit="元/单",
                kind="money",
                aliases=("客单价", "平均订单金额", "单均金额"),
                derived=True,
                numerator={"op": "sum", "column": money.name},
                denominator={"op": "count_distinct", "column": order_id.name},
                description="销售额合计 ÷ 去重订单数",
            )
        )

    # 派生：毛利率 = (收入 - 成本) / 收入
    revenue = next(
        (c for c in money_columns if any(t in c.name for t in ("收入", "营收", "销售额", "实付")) or c.name.lower() in ("revenue", "sales", "amount")),
        None,
    )
    cost = next((c for c in money_columns if "成本" in c.name or c.name.lower() == "cost"), None)
    if revenue is not None and cost is not None:
        catalog.append(
            MetricCandidate(
                id="ratio:毛利率",
                name="毛利率",
                formula=f"(SUM({revenue.name})-SUM({cost.name}))/SUM({revenue.name})",
                agg="ratio",
                unit="%",
                kind="ratio",
                aliases=("毛利率", "利润率"),
                derived=True,
                numerator={"op": "diff", "left": {"op": "sum", "column": revenue.name}, "right": {"op": "sum", "column": cost.name}},
                denominator={"op": "sum", "column": revenue.name},
                description="(收入-成本) ÷ 收入",
            )
        )

    return catalog


# ---------- 需求解析 ----------


def _alias_hit(requirement: str, alias: str) -> bool:
    if not alias:
        return False
    return alias.lower() in requirement.lower()


def resolve_metrics(
    requirement: str, catalog: list[MetricCandidate], *, limit: int = 3
) -> tuple[list[MetricCandidate], list[dict[str, Any]]]:
    """按需求文本解析指标：返回 (可执行指标, 不可行说明)。

    规则：
    - 需求点名的别名优先（长别名优先，避免「销售额」被「销售」抢先）；
    - 同一别名命中多个指标时按业务优先级（金额合计 > 派生 > 计数/均值）；
    - 点名的派生指标若数据不足 → 返回 feasible=false 的说明，不硬算。
    """
    req = (requirement or "").strip()
    if not req:
        return [], []
    hits: list[tuple[int, int, int, int, MetricCandidate]] = []
    for candidate in catalog:
        best_len = 0
        for alias in (candidate.name, *candidate.aliases):
            if alias and _alias_hit(req, alias) and len(alias) > best_len:
                best_len = len(alias)
        if best_len:
            priority = {"ratio": 4, "sum": 3, "avg": 2, "count_distinct": 2, "count": 1}.get(candidate.agg, 0)
            # 指标展示名被原文点名 → 最高优先（避免"销售额"被同义别名抢走）
            exact = 1 if candidate.name and candidate.name in req else 0
            hits.append((exact, best_len, priority, candidate.rank, candidate))
    hits.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)

    selected: list[MetricCandidate] = []
    seen: set[str] = set()
    for _exact, _length, _priority, _rank, candidate in hits:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        selected.append(candidate)
        if len(selected) >= limit:
            break

    # 单位价（单价/均价）不能求和：若同一列已按 AVG 选中，去掉自动补进来的「单价合计」，
    # 除非需求明确要合计/总额。避免「各区域平均单价」报告里混入无业务意义的 SUM(单价)。
    explicit_total = any(word in req for word in ("合计", "总额", "总和", "累计", "汇总", "总价"))
    if not explicit_total:
        avg_columns = {item.column for item in selected if item.agg == "avg" and item.column}
        selected = [
            item
            for item in selected
            if not (item.agg == "sum" and item.column in avg_columns)
        ]

    # 需求点名但数据不支持的业务指标 → 显式说明（毛利率/转化率/同比/环比/占比）
    notes: list[dict[str, Any]] = []
    requested_unavailable = {
        "毛利率": ("毛利率", "需要同时存在「收入/销售额」与「成本」字段"),
        "利润率": ("毛利率", "需要同时存在「收入/销售额」与「成本」字段"),
        "转化率": ("转化率", "需要漏斗阶段/支付状态等可配对的阶段字段"),
        "同比": ("同比", "需要跨年可比的时间字段与至少两年数据"),
        "环比": ("环比", "需要连续周期（月/日）的时间字段"),
        "占比": ("占比", "属于分析算子（由洞察 Agent 计算），不是独立取数指标"),
    }
    for token, (name, reason) in requested_unavailable.items():
        if token in req and not any(c.name == name or name in c.aliases for c in selected):
            notes.append({"name": name, "feasible": False, "reason": reason})
    return selected, notes


def default_metrics(catalog: list[MetricCandidate], *, limit: int = 1) -> list[MetricCandidate]:
    """无指标语义时的默认指标：金额合计 > 订单量 > 记录数。"""
    preferred: list[MetricCandidate] = []
    for candidate in catalog:
        if candidate.agg == "sum" and candidate.kind == "money":
            preferred.append(candidate)
    for candidate in catalog:
        if candidate.id.startswith("count_distinct:") and candidate.name == "订单量":
            preferred.append(candidate)
    preferred.extend(c for c in catalog if c.id == "count")
    out: list[MetricCandidate] = []
    seen: set[str] = set()
    for candidate in preferred:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def find_metric(catalog: list[MetricCandidate], metric_id: str | None) -> MetricCandidate | None:
    """按 id 取指标候选（计划里只存 id，取数时回查目录）。"""
    if not metric_id:
        return None
    return next((c for c in catalog if c.id == metric_id), None)

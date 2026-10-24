"""task_repo 库 0007：指标知识库种子数据。

预置常用指标口径，供 MetricDefinitionAgent 检索引用（管理员可后续维护）。
幂等：INSERT IGNORE（indicator_key 唯一）。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_seed_kb_indicator"
down_revision = "0006_data_source"
branch_labels = None
depends_on = None

_SEED_ROWS = [
    # (indicator_key, metric_name, category, formula, description, tags, owner)
    ("sales_amount", "销售额", "ecommerce", "SUM(`amount`)", "销售总额：订单金额合计", ["销售额", "销售", "amount"], "admin"),
    ("gmv", "GMV 成交总额", "ecommerce", "SUM(`amount`)", "成交总额：含全部状态订单的金额合计", ["GMV", "成交", "总额"], "admin"),
    ("order_count", "订单量", "ecommerce", "COUNT(*)", "订单总数量", ["订单", "单量", "order"], "admin"),
    ("avg_order_value", "客单价", "ecommerce", "SUM(`amount`) / COUNT(*)", "平均每单成交金额", ["客单价", "客单", "均价"], "admin"),
    ("refund_rate", "退款率", "finance", "退款单数 / 总单数 * 100", "退款单量占比（%）", ["退款", "退货"], "admin"),
    ("conversion_rate", "转化率", "marketing", "成交单数 / 访问人数 * 100", "访问到成交的转化率（%）", ["转化", "conversion"], "admin"),
    ("city_sales", "分城市销售额", "ecommerce", "SUM(`amount`) GROUP BY `city`", "按城市维度汇总销售额", ["城市", "city", "区域"], "admin"),
]


def upgrade() -> None:
    for key, name, category, formula, desc, tags, owner in _SEED_ROWS:
        tag_json = ", ".join(f"'{t}'" for t in tags)
        op.execute(
            f"""
            INSERT IGNORE INTO kb_indicator
                (indicator_key, metric_name, category, formula, description, tags, owner)
            VALUES
                ('{key}', '{name}', '{category}', '{formula}', '{desc}', JSON_ARRAY({tag_json}), '{owner}')
            """
        )


def downgrade() -> None:
    keys = ", ".join(f"'{r[0]}'" for r in _SEED_ROWS)
    op.execute(f"DELETE FROM kb_indicator WHERE indicator_key IN ({keys})")

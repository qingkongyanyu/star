"""生成多维演示数据（含成本/退款/客户类型/活动），用于展示复杂指标与多图型。

用法：
    ./.venv/Scripts/python.exe scripts/generate_demo_csv.py
输出：Data/demo_multi_metric.csv（UTF-8 BOM，Excel 可直接打开）
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Data" / "demo_multi_metric.csv"

REGIONS = ["华东", "华南", "华北", "华中", "西南", "东北"]
REGION_WEIGHTS = [0.28, 0.24, 0.18, 0.12, 0.10, 0.08]
CHANNELS = ["线上", "线下", "小程序", "直播"]
CHANNEL_WEIGHTS = [0.42, 0.28, 0.18, 0.12]
CUSTOMER_TYPES = ["新客", "老客", "会员"]
CATEGORIES = {
    "数码家电": ["笔记本电脑", "智能手表", "蓝牙耳机", "扫地机器人", "平板电脑"],
    "食品生鲜": ["牛肉干", "坚果礼盒", "咖啡豆", "有机牛奶", "冷冻虾仁"],
    "美妆个护": ["精华液", "防晒霜", "口红", "面膜", "洗发水"],
    "服饰鞋包": ["运动鞋", "羽绒服", "双肩包", "牛仔裤", "连衣裙"],
    "家居日用": ["四件套", "收纳箱", "香薰机", "炒锅", "台灯"],
    "运动户外": ["瑜伽垫", "登山杖", "帐篷", "骑行头盔", "哑铃"],
}
MEMBER_LEVELS = ["普通", "银卡", "金卡", "黑金"]
ACTIVITIES = ["无", "满减", "秒杀", "直播专享"]
STATUSES = ["已完成", "已完成", "已完成", "已退款", "已取消"]


def _weighted(values: list[str], weights: list[float], rng: random.Random) -> str:
    return rng.choices(values, weights=weights, k=1)[0]


def main() -> None:
    rng = random.Random(42)
    rows: list[dict[str, object]] = []
    start = date(2025, 1, 1)
    for index in range(480):
        month = rng.choices(range(1, 13), weights=[7, 6, 8, 7, 8, 8, 8, 9, 9, 11, 14, 13], k=1)[0]
        day = rng.randint(1, 28)
        order_time = date(2025, month, day)
        if order_time < start:  # pragma: no cover - 防御
            order_time = start + timedelta(days=index % 300)
        region = _weighted(REGIONS, REGION_WEIGHTS, rng)
        channel = _weighted(CHANNELS, CHANNEL_WEIGHTS, rng)
        customer_type = rng.choices(CUSTOMER_TYPES, weights=[0.4, 0.35, 0.25], k=1)[0]
        category = rng.choice(list(CATEGORIES))
        product = rng.choice(CATEGORIES[category])
        unit_price = round(rng.uniform(39, 3999), 2)
        quantity = rng.choices([1, 2, 3, 4, 5], weights=[0.55, 0.24, 0.12, 0.06, 0.03], k=1)[0]
        discount = rng.choice([1.0, 0.95, 0.9, 0.85, 0.8, 0.75])
        sales = round(unit_price * quantity * discount, 2)
        cost_rate = rng.uniform(0.52, 0.78)
        cost = round(sales * cost_rate, 2)
        status = rng.choices(STATUSES, weights=[0.72, 0.12, 0.08, 0.05, 0.03], k=1)[0]
        refund = round(sales * rng.uniform(0.3, 1.0), 2) if status == "已退款" else 0.0
        member_level = rng.choices(MEMBER_LEVELS, weights=[0.5, 0.28, 0.17, 0.05], k=1)[0]
        activity = rng.choices(ACTIVITIES, weights=[0.55, 0.2, 0.15, 0.1], k=1)[0]
        rows.append(
            {
                "订单号": f"MM2025{month:02d}{index:04d}",
                "下单时间": f"{order_time.isoformat()} {rng.randint(8, 22):02d}:{rng.randint(0, 59):02d}:00",
                "客户ID": f"C{rng.randint(10000, 19999)}",
                "区域": region,
                "渠道": channel,
                "客户类型": customer_type,
                "会员等级": member_level,
                "商品类目": category,
                "商品名称": product,
                "单价": unit_price,
                "数量": quantity,
                "折扣": discount,
                "销售额": sales,
                "成本": cost,
                "退款金额": refund,
                "活动类型": activity,
                "订单状态": status,
            }
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    total_sales = sum(float(row["销售额"]) for row in rows)
    total_cost = sum(float(row["成本"]) for row in rows)
    print(f"已生成 {OUTPUT}：{len(rows)} 行，销售额合计 {total_sales:,.2f}，毛利率 {(total_sales - total_cost) / total_sales:.1%}")


if __name__ == "__main__":
    main()

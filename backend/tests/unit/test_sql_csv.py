"""CSV 维度智能选取 / 数值解析 / 出图 top-N（纯函数，无 DB）。

覆盖 v2.1：不把空表头/行号列当维度（避免 96 行碎片图）、需求点名维度、数值列含中文单位可解析、
图表 top-N 降序且剔除 0 值/空组。
"""
from __future__ import annotations

from app.agents.sql_agent import SQLAgent
from app.agents.visual_agent import VisualAgent

# ds8 湛江房源风格：首列 = 空表头 + 行号列；户型/区域为真实类别列；数值列带中文单位
_ZJ_HEADER = ["", "户型", "建筑面积", "单价", "朝向", "区域", "总价"]
_ZJ_ROWS = [
    ["0", "3室2厅", "133平米", "13383元/平米", "东南", "赤坎", "178万"],
    ["1", "3室2厅", "108平米", "7315元/平米", "东南", "麻章", "79万"],
    ["2", "4室2厅", "143平米", "9650元/平米", "东南", "赤坎", "138万"],
    ["3", "3室2厅", "105平米", "11664元/平米", "南", "霞山", "123万"],
    ["4", "4室2厅", "116平米", "6897元/平米", "东南", "开发区", "80万"],
    ["5", "2室1厅", "78平米", "13077元/平米", "东南", "霞山", "102万"],
    ["6", "3室2厅", "90平米", "10000元/平米", "西北", "赤坎", "90万"],
    ["7", "4室2厅", "200平米", "9000元/平米", "北", "开发区", "180万"],
    ["8", "3室2厅", "99平米", "11000元/平米", "东", "麻章", "109万"],
]


def test_pick_csv_dim_never_picks_blank_row_index_header() -> None:
    """空表头行号列（首列）绝不当维度。"""
    dim = SQLAgent._pick_csv_dim(_ZJ_HEADER, _ZJ_ROWS, "湛江房源按区域出图")
    assert dim == "区域"


def test_pick_csv_dim_honors_requirement_keyword() -> None:
    assert SQLAgent._pick_csv_dim(_ZJ_HEADER, _ZJ_ROWS, "湛江房源各户型出图") == "户型"


def test_pick_csv_dim_no_dim_returns_none_when_only_unique_text() -> None:
    assert SQLAgent._pick_csv_dim(["记录"], [["a"], ["b"], ["c"]], "统计") is None


def test_to_number_parses_numeric_with_cn_units_not_inner_digits() -> None:
    assert SQLAgent._to_number("3970.14") == 3970.14
    assert SQLAgent._to_number("133平米") == 133.0
    assert SQLAgent._to_number("13383元/平米") == 13383.0
    assert SQLAgent._to_number("178万") == 178.0
    # 内部含数字的文字不误判为数值（避免把户型/日期当金额）
    assert SQLAgent._to_number("3室2厅") is None
    assert SQLAgent._to_number("2025-02") is None
    assert SQLAgent._to_number("东南") is None
    assert SQLAgent._to_number("") is None


def test_numeric_columns_and_amount_respects_requirement() -> None:
    nc = SQLAgent._numeric_csv_columns(_ZJ_HEADER, _ZJ_ROWS, exclude="区域")
    assert "户型" not in nc  # 3室2厅 非数值
    assert "建筑面积" in nc
    assert SQLAgent._pick_csv_amount(_ZJ_HEADER, nc, "建筑面积与记录数分布") == "建筑面积"


def test_chart_points_top_n_desc_and_drop_zero_empty() -> None:
    dataset = {
        "columns": ["区域", "order_count", "total_amount"],
        "rows": [
            ["赤坎", 3, 300.0],
            ["霞山", 2, 0.0],        # 0 值 → 不出图
            ["", 5, 500.0],          # 空维度键 → 不出图
            ["麻章", 1, 900.0],
            ["开发区", 4, 700.0],
        ],
    }
    dim, points = VisualAgent._chart_points(dataset)
    assert dim == "区域"
    names = [p["name"] for p in points]
    assert names == ["麻章", "开发区", "赤坎"]  # 降序且剔除 0 值/空组


def test_pick_csv_dim_trend_intent_prefers_time_column() -> None:
    """趋势意图：即使时间列值全唯一（不可做类别维度），也应优先选它为时间维度。"""
    header = ["订单号", "下单时间", "商品类目", "实付金额"]
    rows = [
        ["1", "2025-01-05 10:00:00", "数码", "100"],
        ["2", "2025-02-08 11:00:00", "服饰", "200"],
        ["3", "2025-03-12 12:00:00", "数码", "150"],
        ["4", "2025-04-20 13:00:00", "美妆", "300"],
    ]
    dim = SQLAgent._pick_csv_dim(header, rows, "分析各月实付金额走势")
    assert dim == "下单时间"


def test_pick_csv_dim_comparison_intent_picks_category() -> None:
    """对比意图：无时间诉求时应选类别维度，不误选时间列。"""
    header = ["订单号", "下单时间", "商品类目", "实付金额"]
    rows = [
        ["1", "2025-01-05 10:00:00", "数码", "100"],
        ["2", "2025-02-08 11:00:00", "服饰", "200"],
        ["3", "2025-03-12 12:00:00", "数码", "150"],
    ]
    dim = SQLAgent._pick_csv_dim(header, rows, "对比各类目销售额")
    assert dim == "商品类目"


def test_normalize_time_bucket_various_formats() -> None:
    assert SQLAgent._normalize_time_bucket("2025-11-24 03:01:47") == "2025-11"
    assert SQLAgent._normalize_time_bucket("2025-11-24") == "2025-11"
    assert SQLAgent._normalize_time_bucket("2025/11/24") == "2025-11"
    assert SQLAgent._normalize_time_bucket("2025年11月24日") == "2025-11"
    assert SQLAgent._normalize_time_bucket("2025-11") == "2025-11"
    assert SQLAgent._normalize_time_bucket("无时间") is None
    assert SQLAgent._normalize_time_bucket("") is None
    assert SQLAgent._normalize_time_bucket(None) is None


def test_is_time_column() -> None:
    assert SQLAgent._is_time_column("下单时间")
    assert SQLAgent._is_time_column("date")
    assert SQLAgent._is_time_column("月份")
    assert not SQLAgent._is_time_column("商品类目")
    assert not SQLAgent._is_time_column("实付金额")


def test_chart_points_time_dimension_keeps_order() -> None:
    """趋势折线图：时间维度不按数值降序重排，保持时间走向。"""
    dataset = {
        "columns": ["下单时间", "order_count", "total_实付金额"],
        "rows": [
            ["2025-01", 175, 872720.97],
            ["2025-02", 144, 680681.92],  # 数值低于 3 月，但时间在前，顺序必须保持
            ["2025-03", 173, 863737.71],
        ],
    }
    _dim, points = VisualAgent._chart_points(dataset)
    assert [p["name"] for p in points] == ["2025-01", "2025-02", "2025-03"]

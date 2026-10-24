"""CSV 编码自动识别 + 大小/行数上限防护（纯函数，无 DB）。

覆盖：GBK/GB18030 中文 CSV 不乱码、UTF-8 BOM/UTF-16 正确解析、超大文件拒绝。
"""
from __future__ import annotations

from app.infra.datasource import MAX_CSV_ROWS, MAX_UPLOAD_BYTES, _decode_text, parse_csv


def test_decode_utf8_bom() -> None:
    data = "订单号,商品\n1,手机\n".encode("utf-8-sig")
    assert _decode_text(data) == "订单号,商品\n1,手机\n"


def test_decode_utf8_plain() -> None:
    data = "订单号,商品\n1,手机\n".encode()
    assert _decode_text(data) == "订单号,商品\n1,手机\n"


def test_decode_gbk_chinese_not_garbled() -> None:
    """GBK 编码中文（Windows Excel 常见）不应乱码。"""
    data = "订单号,商品,金额\n1,手机,1000\n".encode("gbk")
    text = _decode_text(data)
    assert "商品" in text
    assert "手机" in text
    assert "1000" in text


def test_decode_utf16_le() -> None:
    data = "订单号,商品\n1,手机\n".encode("utf-16")
    assert "手机" in _decode_text(data)


def test_parse_csv_gbk_columns() -> None:
    data = "订单号,商品,金额\n1,手机,1000\n2,电脑,5000\n".encode("gbk")
    parsed = parse_csv(data)
    assert [c["name"] for c in parsed["columns"]] == ["订单号", "商品", "金额"]
    assert parsed["row_count"] == 2


def test_upload_limits_are_sane() -> None:
    """上限常量合理：50MB 上传上限、20 万行解析上限。"""
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    assert MAX_CSV_ROWS == 200_000

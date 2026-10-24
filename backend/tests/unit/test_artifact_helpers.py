"""资产发布辅助函数与 JSON 抽取的单元测试（纯函数，不依赖数据库）。"""
from __future__ import annotations

from app.agents.base import _extract_json
from app.services.artifact_service import build_object_key, gen_asset_id


def test_build_object_key() -> None:
    assert build_object_key("report", "r1", 3) == "report/r1/v3.md"
    assert build_object_key("sql", "s1", 1) == "sql/s1/v1.sql"
    assert build_object_key("unknown_type", "x1", 2) == "unknown_type/x1/v2.json"


def test_build_object_key_dataset_and_feature_set_are_json() -> None:
    """回归：dataset/feature_set 内容实际是 JSON 文本，扩展名必须为 .json（曾错配为 .parquet）。"""
    assert build_object_key("dataset", "d1", 1) == "dataset/d1/v1.json"
    assert build_object_key("feature_set", "f1", 2) == "feature_set/f1/v2.json"


def test_gen_asset_id_prefix_and_len() -> None:
    assert gen_asset_id("metric_spec").startswith("m")
    assert gen_asset_id("report").startswith("r")
    assert len(gen_asset_id("dataset")) == 9  # 1 字母 + 8 hex


def test_extract_json_balanced() -> None:
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('prefix {"a": {"b": 2}} tail') == '{"a": {"b": 2}}'
    assert _extract_json("【MockLLM】没有 JSON") is None
    assert _extract_json("{}") == "{}"

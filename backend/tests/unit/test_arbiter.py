"""arbiter 分支仲裁纯函数测试（无 DB）。

覆盖：dq no_data/blocked、fe 复检、review verdict、以及 P0-3 修订回链核心
`latest_report_node`（第二轮修订必须选 rev1 修订稿而非 V1）。
"""
from __future__ import annotations

from app.models.task_repo import DagNode
from app.services import arbiter


def _report_node(task_id: str, node_id: str, output_asset_id: str | None) -> DagNode:
    return DagNode(
        node_id=node_id,
        task_id=task_id,
        agent_type="report_writing",
        status="SUCCESS" if output_asset_id else "RUNNING",
        output_asset_id=output_asset_id,
    )


# ---- DQ 判定 ----

def test_dq_no_data_flag() -> None:
    assert arbiter.dq_no_data({"no_data": True, "blocked": True}) is True
    assert arbiter.dq_no_data({"blocked": True}) is False
    assert arbiter.dq_no_data(None) is False
    assert arbiter.dq_no_data("not-dict") is False


def test_dq_blocked_flag() -> None:
    assert arbiter.dq_blocked({"blocked": True}) is True
    assert arbiter.dq_blocked({"no_data": True}) is False


def test_dq_state_priority_no_data_over_blocked() -> None:
    assert arbiter.dq_state({"no_data": True, "blocked": True}) == "no_data"
    assert arbiter.dq_state({"blocked": True}) == "blocked"
    assert arbiter.dq_state({"score": 100}) == "pass"
    assert arbiter.dq_state(None) == "unknown"


def test_dq_state_clean_on_non_empty_issues() -> None:
    """v2.1：通过但 issues 非空 → clean（触发前置特征清洗，不只是阻断才 FE）。"""
    assert arbiter.dq_state({"score": 85, "level": "warning", "blocked": False, "issues": [{"type": "missing", "severity": "warning"}]}) == "clean"
    assert arbiter.dq_state({"score": 95, "level": "pass", "issues": [{"type": "outlier", "severity": "minor"}]}) == "pass"
    assert arbiter.dq_state({"score": 95, "level": "pass", "issues": []}) == "pass"
    assert arbiter.dq_state({"blocked": True, "issues": [{"type": "duplicate"}]}) == "blocked"
    assert arbiter.dq_state({"no_data": True, "issues": []}) == "no_data"


def test_dq_needs_clean_flag() -> None:
    assert arbiter.dq_needs_clean({"issues": [{"type": "missing", "severity": "warning"}]}) is True
    assert arbiter.dq_needs_clean({"issues": [{"type": "outlier", "severity": "minor"}]}) is False
    assert arbiter.dq_needs_clean({"issues": []}) is False
    assert arbiter.dq_needs_clean({"blocked": True, "issues": [{"type": "x"}]}) is False
    assert arbiter.dq_needs_clean({"no_data": True, "issues": [{"type": "x"}]}) is False
    assert arbiter.dq_needs_clean(None) is False


# ---- FE 复检 ----

def test_fe_recheck_passes() -> None:
    assert arbiter.fe_recheck_passes({"quality_ok": True}) is True
    assert arbiter.fe_recheck_passes({"quality_ok": False}) is False
    assert arbiter.fe_recheck_passes({"blocked": True}) is False
    assert arbiter.fe_recheck_passes({"quality_issues": ["x"]}) is False
    assert arbiter.fe_recheck_passes({"quality_issues": []}) is True
    assert arbiter.fe_recheck_passes(None) is True  # 读取失败按放行


# ---- review verdict ----

def test_review_verdict() -> None:
    assert arbiter.review_verdict({"verdict": "rejected"}) == "rejected"
    assert arbiter.review_verdict({"verdict": "approved"}) == "approved"
    assert arbiter.review_verdict({"verdict": "weird"}) is None
    assert arbiter.review_verdict({}) is None
    assert arbiter.review_verdict(None) is None


# ---- 修订回链（P0-3）：latest_report_node 必须选"最新一轮 rev-report" ----

def test_latest_report_node_prefers_newest_revision() -> None:
    task = "t1"
    nodes = [
        _report_node(task, "t1-n5", "asset-v1"),          # 原始报告 V1
        _report_node(task, "t1-rev1-report", "asset-rev1"),  # 第 1 轮修订稿
        _report_node(task, "t1-rev1-review", None),       # 复评节点非 report，被忽略
    ]
    chosen = arbiter.latest_report_node(nodes, task)
    assert chosen is not None
    assert chosen.output_asset_id == "asset-rev1"  # 关键：第二轮输入是 rev1 而非 V1


def test_latest_report_node_no_revision_falls_back_to_v1() -> None:
    task = "t1"
    nodes = [_report_node(task, "t1-n5", "asset-v1")]
    chosen = arbiter.latest_report_node(nodes, task)
    assert chosen is not None and chosen.output_asset_id == "asset-v1"


def test_latest_report_node_ignores_unproduced() -> None:
    task = "t1"
    nodes = [
        _report_node(task, "t1-n5", None),                # 未产出 → 不可修订
        _report_node(task, "t1-rev2-report", "asset-rev2"),
        _report_node(task, "t1-rev1-report", "asset-rev1"),
    ]
    chosen = arbiter.latest_report_node(nodes, task)
    assert chosen is not None and chosen.output_asset_id == "asset-rev2"


def test_latest_report_node_none_when_nothing_produced() -> None:
    assert arbiter.latest_report_node([], "t1") is None
    assert arbiter.latest_report_node([_report_node("t1", "t1-n5", None)], "t1") is None

"""分支仲裁：把调度器的"读资产内容 → 决定下一步"判据抽成纯函数（无 DB/IO，可单测）。

设计动机：scheduler.py 的 while 循环里塞满了对 dq_report / feature_set / review 资产的
判定（no_data → 硬失败、blocked → 插 FE、quality_ok → 复检、verdict → 修订轮）。这些是
状态机最易出 bug 的地方，必须能被无 DB 的纯逻辑测试覆盖（P0-2）。本模块只做"对 payload
字典的判定"，读取资产由调度器完成后再调用。
"""
from __future__ import annotations

from typing import Any, cast

from app.models.task_repo import DagNode


def dq_no_data(payload: Any) -> bool:
    """质检产出是否 no_data（上游无数据集，硬阻断，不做特征工程清洗）。"""
    return bool(isinstance(payload, dict) and payload.get("no_data", False))


def dq_blocked(payload: Any) -> bool:
    """质检产出是否阻断（真实数据质量问题，触发 DQ→FE 分支）。"""
    return bool(isinstance(payload, dict) and payload.get("blocked", False))


def dq_state(payload: Any) -> str:
    """质检产出的统一判定：no_data / blocked / clean / pass / unknown。

    供调度器显式分支使用（顺序：no_data 先于 blocked 先于 clean）。
    clean = 通过但仍有告警级问题（issues 非空）→ v2.1 也前置特征清洗。
    """
    if not isinstance(payload, dict):
        return "unknown"
    if payload.get("no_data"):
        return "no_data"
    if payload.get("blocked"):
        return "blocked"
    issues = payload.get("issues")
    if isinstance(issues, list) and any(
        isinstance(issue, dict) and str(issue.get("severity")) in ("warning", "critical", "blocking")
        for issue in issues
    ):
        return "clean"
    return "pass"


def dq_needs_clean(payload: Any) -> bool:
    """质检通过但有告警级问题（issues 非空）→ 值得前置清洗（不只是阻断才 FE）。"""
    return dq_state(payload) == "clean"


def fe_recheck_passes(payload: Any) -> bool:
    """特征集复检：显式 quality_ok=false / blocked=true / quality_issues 非空 → 未通过。"""
    if not isinstance(payload, dict):
        return True  # 读取失败/非结构化按放行（v1 语义）
    if payload.get("quality_ok") is False or payload.get("blocked") is True:
        return False
    issues = payload.get("quality_issues")
    return not (isinstance(issues, list) and len(issues) > 0)


def review_verdict(payload: Any) -> str | None:
    """终审产出裁决：approved / rejected；无有效裁决返回 None。"""
    if isinstance(payload, dict) and payload.get("verdict") in ("approved", "rejected"):
        return cast(str, payload["verdict"])
    return None


def latest_report_node(nodes: list[DagNode], task_id: str) -> DagNode | None:
    """选最新可修订报告节点（P0-3 修订回链修复的核心纯函数）。

    在 report_writing 且已产出资产的节点中，优先取 `{task_id}-rev{N}-report` 中 N 最大者；
    无 rev 节点则取原始 report_writing 节点（V1）。返回 None 表示无可修订报告。
    """
    produced = [n for n in nodes if n.agent_type == "report_writing" and n.output_asset_id]

    def rev_no(node: DagNode) -> int:
        prefix = f"{task_id}-rev"
        if node.node_id.startswith(prefix):
            rest = node.node_id[len(prefix):]
            digits = "".join(ch for ch in rest.split("-", 1)[0] if ch.isdigit())
            return int(digits) if digits else 0
        return 0

    if not produced:
        return None
    return max(produced, key=lambda n: (rev_no(n), n.id or 0))

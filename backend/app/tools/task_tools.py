"""任务侧共享工具（schema 逐字对齐 Agent 契约书 §〇.1，impl 走 TaskRepo）。

v1 由 AgentBase 经 ToolRegistry 调用；LLM 自主选工具的循环留待下一阶段。
"""
from __future__ import annotations

import uuid
from typing import Any

from app.core.constants import NodeStatus
from app.infra.db import to_thread
from app.repositories.task_repo import TaskRepo
from app.tools.registry import ToolRegistry


def _claim_schema() -> dict[str, Any]:
    return {
        "name": "claim_task",
        "description": "按能力标签认领任务池中的节点（Redis 事务性锁 + 状态 CAS，保证唯一认领）。",
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "本 Agent 能力标签，如 sql_extract / feature_engineer"},
                "limit": {"type": "integer", "description": "最多认领节点数", "default": 1},
            },
            "required": ["capability"],
        },
    }


async def _claim_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    """认领一个 READY 且能力匹配的节点（DB CAS，v1 降级实现）。"""
    capability = args["capability"]
    nodes = await to_thread(TaskRepo.get_nodes, deps.task_session, ctx.task_id)
    target = next((n for n in nodes if n.agent_type == capability and n.status == NodeStatus.READY.value), None)
    if target is None:
        return {"claimed": False, "node_id": None}
    token = uuid.uuid4().hex
    ok = await to_thread(TaskRepo.claim_node, deps.task_session, target.node_id, token)
    return {"claimed": ok, "node_id": target.node_id if ok else None, "token": token if ok else None}


def _get_context_schema() -> dict[str, Any]:
    return {
        "name": "get_task_context",
        "description": "读取当前认领的 DAG 节点上下文：任务信息、依赖边、上游输入资产条件。",
        "parameters": {
            "type": "object",
            "properties": {"node_id": {"type": "string", "description": "当前 DAG 节点 ID"}},
            "required": ["node_id"],
        },
    }


async def _get_context_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    node = await to_thread(TaskRepo.get_node, deps.task_session, args["node_id"])
    if node is None:
        return {"found": False}
    return {"found": True, "node_id": node.node_id, "task_id": node.task_id, "agent_type": node.agent_type, "status": node.status}


def _report_status_schema() -> dict[str, Any]:
    return {
        "name": "report_status",
        "description": "上报节点执行进度/状态到 TaskRepo（RUNNING/SUCCESS/FAILED/BLOCKED）。",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "status": {"type": "string", "enum": ["RUNNING", "SUCCESS", "FAILED", "BLOCKED"]},
                "detail": {"type": "string"},
            },
            "required": ["node_id", "status"],
        },
    }


async def _report_status_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    node_id = args["node_id"]
    status = args["status"]
    if status == "SUCCESS":
        node = await to_thread(TaskRepo.get_node, deps.task_session, node_id)
        output = node.output_asset_id if node is not None else None
        if output is None:
            # SUCCESS 必须伴随资产，未发布则回退
            return {"ok": False, "reason": "SUCCESS without output_asset_id"}
        await to_thread(TaskRepo.set_node_success, deps.task_session, node_id, output)
    else:
        await to_thread(TaskRepo.update_node_status, deps.task_session, node_id, status)
    return {"ok": True, "node_id": node_id, "status": status}


def _release_schema() -> dict[str, Any]:
    return {
        "name": "release_task",
        "description": "主动释放节点（失败 / 超时 / 被仲裁收回时调用）。",
        "parameters": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["node_id", "reason"],
        },
    }


async def _release_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    await to_thread(TaskRepo.update_node_status, deps.task_session, args["node_id"], NodeStatus.READY.value)
    return {"ok": True, "node_id": args["node_id"]}


def register_task_tools(registry: ToolRegistry) -> None:
    """把任务侧共享工具注册进给定注册表。"""
    registry.register("claim_task", _claim_schema(), _claim_impl)
    registry.register("get_task_context", _get_context_schema(), _get_context_impl)
    registry.register("report_status", _report_status_schema(), _report_status_impl)
    registry.register("release_task", _release_schema(), _release_impl)

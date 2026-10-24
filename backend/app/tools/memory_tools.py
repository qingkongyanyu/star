"""记忆侧共享工具（schema 逐字对齐 Agent 契约书 §〇.3）。"""
from __future__ import annotations

from typing import Any

from app.infra.db import to_thread
from app.repositories.memory_repo import MemoryRepo
from app.tools.registry import ToolRegistry


def _submit_schema() -> dict[str, Any]:
    return {
        "name": "submit_experience_summary",
        "description": "复盘时提交结构化经验摘要给 MemoryKeeper（不直接写库，由 MemoryKeeper 去重合并后入库）。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "experience_type": {"type": "string", "enum": ["pitfall", "optimization", "template", "success_pattern"]},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id", "agent_id", "experience_type", "content"],
        },
    }


async def _submit_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    """写 memory_entry（MemoryKeeper 去重合并后入库）。"""
    await to_thread(
        MemoryRepo.create,
        deps.task_session,
        agent_id=args["agent_id"],
        scope="private",
        source_task=args.get("task_id"),
        content=args["content"],
        experience_type=args["experience_type"],
        tags=args.get("tags"),
    )
    return {"ok": True}


def register_memory_tools(registry: ToolRegistry) -> None:
    """把记忆侧共享工具注册进给定注册表。"""
    registry.register("submit_experience_summary", _submit_schema(), _submit_impl)

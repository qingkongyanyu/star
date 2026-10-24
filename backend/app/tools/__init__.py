"""Agent 共享工具层（Agent 契约书 §〇：三件套 = schema + 实现 + 权限钩子）。

v1：执行引擎由调度器驱动 Agent（非 LLM 自主选工具），工具实现供 AgentBase 经
ToolRegistry 调用（report_status / pull_artifact / publish_artifact），
LLM 函数调用式工具循环留待下一阶段。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass
class ToolDeps:
    """工具实现依赖（会话等），由 AgentBase 组装传入。"""

    task_session: Session
    artifact_session: Session

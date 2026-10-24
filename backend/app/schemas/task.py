"""任务与 DAG 相关 DTO。

契约来源：docs/02-后端/04-API接口契约.md §三（任务）+ §一（分页/时间）。
契约文档为意图级，仅 DAG 响应形状有明确定义，其余按数据模型防御性设计。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskCreateRequest(BaseModel):
    """POST /tasks 请求体。"""

    message: str = Field(min_length=1, max_length=4000, description="群聊需求原文")
    task_type: str = "analysis"
    priority: int = Field(default=5, ge=0, le=9)
    data_source_id: int | None = Field(
        default=None, description="数据源接入ID（分析任务必填：无数据源的任务无法真实取数，将被 422 拒绝）"
    )


class TaskOut(BaseModel):
    """任务摘要（列表项 / 详情）。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: str
    task_type: str
    status: str
    priority: int
    requester: str
    create_time: datetime


class DAGNodeOut(BaseModel):
    """DAG 节点。"""

    node_id: str
    agent_type: str
    status: str
    label: str
    output_asset_id: str | None = None
    instruction: str | None = None


class DAGEdgeOut(BaseModel):
    """DAG 边（契约字段名 from/to/type）。"""

    from_node: str = Field(serialization_alias="from")
    to: str
    edge_type: str = Field(serialization_alias="type")


class DAGOut(BaseModel):
    """DAG 图数据（GET /tasks/{id}/dag 契约形状）。"""

    task_id: str
    status: str
    nodes: list[DAGNodeOut]
    edges: list[DAGEdgeOut]


class TaskDetailOut(BaseModel):
    """任务详情 + DAG。"""

    task: TaskOut
    dag: DAGOut


class AuditLogOut(BaseModel):
    """审计日志（节点日志回放）。"""

    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    actor: str
    action: str
    detail: dict[str, Any] | None = None


class NodeDetailOut(BaseModel):
    """节点详情（含日志）。"""

    node_id: str
    task_id: str
    agent_type: str
    status: str
    instruction: str | None = None
    dep_ids: list[str] | None = None
    input_asset_ids: list[str] | None = None
    output_asset_id: str | None = None
    retry_count: int
    deadline: datetime | None = None
    create_time: datetime
    update_time: datetime
    logs: list[AuditLogOut] = Field(default_factory=list)


class OverrideRequest(BaseModel):
    """POST /tasks/{task_id}/override 请求体。"""

    node_id: str
    action: Literal["block", "allow", "rerun", "abort"]


class Page[T](BaseModel):
    """分页信封（docs/02-后端/04 §一）。"""

    items: list[T]
    total: int
    page: int

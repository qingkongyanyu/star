"""资产相关 DTO。

契约来源：docs/02-后端/04-API接口契约.md §四（资产）；血缘方向：asset 依赖 dep（dep 为上游）。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssetOut(BaseModel):
    """资产摘要（列表项）。"""

    model_config = ConfigDict(from_attributes=True)

    asset_id: str
    type: str
    title: str
    producer_agent: str
    status: str
    latest_version: int | None = None
    create_time: datetime


class VersionOut(BaseModel):
    """资产版本。"""

    model_config = ConfigDict(from_attributes=True)

    version: int
    object_key: str
    digest: str
    status: str
    reviewer: str | None = None
    review_status: str | None = None
    create_time: datetime


class AssetDetailOut(BaseModel):
    """资产详情（含最新版本信息）。"""

    asset_id: str
    type: str
    title: str
    producer_agent: str
    status: str
    latest_version: VersionOut | None = None
    create_time: datetime


class DownloadOut(BaseModel):
    """下载/预览响应：MinIO 预签名 URL（03-前端/05：直链 + 短时有效）。"""

    asset_id: str
    version: int
    url: str
    expires_in: int
    object_key: str


class LineageNodeOut(BaseModel):
    """血缘节点。"""

    asset_id: str
    type: str
    title: str
    version: int
    edge_type: str


class LineageEdgeOut(BaseModel):
    """血缘边（upstream → downstream）。"""

    from_node: str = Field(serialization_alias="from")
    to: str
    edge_type: str = Field(serialization_alias="type")


class LineageOut(BaseModel):
    """血缘链（从目标资产向上游递归）。"""

    asset_id: str
    version: int
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]

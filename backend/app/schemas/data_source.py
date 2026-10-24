"""数据源接入 schema（数据源接入功能）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# 数据源类型（前端选择器展示顺序）
DATA_SOURCE_TYPES: tuple[str, ...] = ("mysql", "postgres", "olap", "file", "stream", "api")


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="数据源名称")
    type: str = Field(description="mysql/postgres/olap/file/stream/api")
    config: dict[str, Any] = Field(default_factory=dict, description="类型相关连接配置")


class DataSourceRename(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="新的数据源名称")


class ColumnOut(BaseModel):
    name: str
    type: str


class DataSourceOut(BaseModel):
    id: int
    name: str
    type: str
    config: dict[str, Any]
    status: str
    last_tested_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DataSourceOptionOut(BaseModel):
    """任务下发用数据源选项（脱敏，不含 config/密码）。"""

    id: int
    name: str
    type: str
    status: str


class DataSourceTestOut(BaseModel):
    ok: bool = False
    message: str = ""
    tables: list[str] = Field(default_factory=list)
    columns: list[ColumnOut] = Field(default_factory=list)
    schema_preview: dict[str, Any] = Field(default_factory=dict)


class FileUploadOut(BaseModel):
    data_source: DataSourceOut
    columns: list[ColumnOut]
    sample_rows: list[list[str]]
    row_count: int

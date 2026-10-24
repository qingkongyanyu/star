"""系统管理 DTO（契约 docs/02-后端/04 §九；请求/响应体为自设计，字段以 Pydantic 为准）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RoleName = Literal["admin", "analyst", "readonly"]


class UserCreateRequest(BaseModel):
    """POST /admin/users 请求体。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    role: RoleName = "readonly"
    display_name: str | None = Field(default=None, max_length=64)


class UserUpdateRequest(BaseModel):
    """PUT /admin/users/{id} 请求体（改角色/停用）。"""

    role: RoleName | None = None
    is_active: bool | None = None


class UserAdminOut(BaseModel):
    """用户列表项 / 详情。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    display_name: str | None = None
    is_active: bool
    created_at: datetime


class ACLRuleInput(BaseModel):
    """ACL 规则入参（docs/02-后端/07：{role_or_user, table, action, allow}）。"""

    role_or_user: str = Field(min_length=1, max_length=64)
    table: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=16)
    allow: bool = True


class ACLRuleOut(BaseModel):
    """ACL 规则（含 id）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role_or_user: str
    table: str
    action: str
    allow: bool


class ACLUpdateRequest(BaseModel):
    """PUT /admin/acl 请求体：整体替换规则集（PUT 幂等语义）。"""

    rules: list[ACLRuleInput] = Field(default_factory=list)


class KBIndicatorCreate(BaseModel):
    """POST /admin/kb 请求体。"""

    indicator_key: str = Field(min_length=1, max_length=64)
    metric_name: str = Field(min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    formula: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class KBIndicatorUpdate(BaseModel):
    """PUT /admin/kb/{id} 请求体（全可选，只更新传入字段）。"""

    metric_name: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    formula: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class KBIndicatorOut(BaseModel):
    """知识库条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    indicator_key: str
    metric_name: str
    category: str | None = None
    formula: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    created_at: datetime


class AuditEntryOut(BaseModel):
    """审计日志条目（GET /admin/audit，含 checksum 便于完整性核验）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor: str
    action: str
    node_id: str | None = None
    asset_id: str | None = None
    detail: dict[str, Any] | None = None
    checksum: str

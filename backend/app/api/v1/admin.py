"""系统管理接口：/admin（用户 / ACL / 知识库 / 审计）。

契约见 docs/02-后端/04-API接口契约.md §九（管理），全部 管理员。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import require_role
from app.core.constants import Role
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.schemas.admin import (
    ACLRuleInput,
    ACLRuleOut,
    ACLUpdateRequest,
    AuditEntryOut,
    KBIndicatorCreate,
    KBIndicatorOut,
    KBIndicatorUpdate,
    UserAdminOut,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.schemas.task import Page
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])

_Admin = Annotated[User, Depends(require_role(Role.ADMIN.value))]
_Session = Annotated[Session, Depends(get_task_db)]


@router.get("/users", response_model=Page[UserAdminOut], summary="用户列表")
async def list_users(
    admin: _Admin,
    session: _Session,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[UserAdminOut]:
    """用户列表（分页）。"""
    return await AdminService(session).list_users(page, page_size)


@router.post("/users", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED, summary="新增用户")
async def create_user(
    payload: UserCreateRequest,
    admin: _Admin,
    session: _Session,
) -> UserAdminOut:
    """新增用户（用户名重复 → 409）。"""
    return await AdminService(session).create_user(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        display_name=payload.display_name,
        actor=admin.username,
    )


@router.put("/users/{user_id}", response_model=UserAdminOut, summary="改角色/停用")
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: _Admin,
    session: _Session,
) -> UserAdminOut:
    """改角色 / 停用（不可停用当前登录账号）。"""
    return await AdminService(session).update_user(
        user_id,
        role=payload.role,
        is_active=payload.is_active,
        actor=admin.username,
        operator_id=admin.id,
    )


@router.get("/acl", response_model=list[ACLRuleOut], summary="表级 ACL 列表")
async def get_acl(
    admin: _Admin,
    session: _Session,
) -> list[ACLRuleOut]:
    """表级 ACL 规则列表。"""
    return await AdminService(session).get_acl()


@router.post("/acl", response_model=ACLRuleOut, status_code=status.HTTP_201_CREATED, summary="新增 ACL 规则")
async def add_acl(
    payload: ACLRuleInput,
    admin: _Admin,
    session: _Session,
) -> ACLRuleOut:
    """新增一条表级 ACL 规则。"""
    return await AdminService(session).add_acl(payload, actor=admin.username)


@router.put("/acl/{rule_id}", response_model=ACLRuleOut, summary="更新 ACL 规则")
async def update_acl_rule(
    rule_id: int,
    payload: ACLRuleInput,
    admin: _Admin,
    session: _Session,
) -> ACLRuleOut:
    """更新一条 ACL 规则。"""
    return await AdminService(session).update_acl_rule(rule_id, payload, actor=admin.username)


@router.delete("/acl/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除 ACL 规则")
async def delete_acl(
    rule_id: int,
    admin: _Admin,
    session: _Session,
) -> None:
    """删除一条 ACL 规则。"""
    await AdminService(session).delete_acl(rule_id, actor=admin.username)


@router.put("/acl", response_model=list[ACLRuleOut], summary="更新 ACL")
async def update_acl(
    payload: ACLUpdateRequest,
    admin: _Admin,
    session: _Session,
) -> list[ACLRuleOut]:
    """整体替换 ACL 规则集（PUT 幂等语义）。"""
    return await AdminService(session).update_acl(payload.rules, actor=admin.username)


@router.get("/kb", response_model=Page[KBIndicatorOut], summary="指标知识库条目")
async def list_kb(
    admin: _Admin,
    session: _Session,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[KBIndicatorOut]:
    """指标知识库条目（关键字 + 分页）。"""
    return await AdminService(session).list_kb(keyword, page, page_size)


@router.post("/kb", response_model=KBIndicatorOut, status_code=status.HTTP_201_CREATED, summary="新增指标条目")
async def create_kb(
    payload: KBIndicatorCreate,
    admin: _Admin,
    session: _Session,
) -> KBIndicatorOut:
    """新增指标条目（indicator_key 唯一，重复 → 409）。"""
    return await AdminService(session).create_kb(payload, actor=admin.username)


@router.put("/kb/{kb_id}", response_model=KBIndicatorOut, summary="更新指标条目")
async def update_kb(
    kb_id: int,
    payload: KBIndicatorUpdate,
    admin: _Admin,
    session: _Session,
) -> KBIndicatorOut:
    """更新指标口径（全可选，只更新传入字段）。"""
    return await AdminService(session).update_kb(kb_id, payload, actor=admin.username)


@router.delete("/kb/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除指标条目")
async def delete_kb(
    kb_id: int,
    admin: _Admin,
    session: _Session,
) -> None:
    """删除指标条目。"""
    await AdminService(session).delete_kb(kb_id, actor=admin.username)


@router.get("/audit", response_model=Page[AuditEntryOut], summary="审计日志")
async def list_audit(
    admin: _Admin,
    session: _Session,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=32)] = None,
    asset_id: Annotated[str | None, Query(max_length=64)] = None,
    from_ts: Annotated[datetime | None, Query(alias="from")] = None,
    to_ts: Annotated[datetime | None, Query(alias="to")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AuditEntryOut]:
    """审计日志（分页 + 筛选，只读）。"""
    return await AdminService(session).list_audit(
        actor=actor,
        action=action,
        asset_id=asset_id,
        from_ts=from_ts,
        to_ts=to_ts,
        page=page,
        page_size=page_size,
    )

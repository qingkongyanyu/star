"""系统管理用例：用户 / ACL / 知识库 / 审计（仅管理员，所有写操作追审计）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security import hash_password, validate_password_strength
from app.infra.db import to_thread
from app.models.task_repo import AdminAcl
from app.repositories.acl_repo import AclRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.kb_repo import KbRepo
from app.repositories.user_repo import UserRepo
from app.schemas.admin import (
    ACLRuleInput,
    ACLRuleOut,
    AuditEntryOut,
    KBIndicatorCreate,
    KBIndicatorOut,
    KBIndicatorUpdate,
    UserAdminOut,
)
from app.schemas.task import Page


class AdminService:
    """系统管理服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- 用户 ----

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        display_name: str | None,
        actor: str,
    ) -> UserAdminOut:
        existing = await UserRepo.get_by_username(self._session, username)
        if existing is not None:
            raise ConflictError("用户名已存在")
        try:
            validate_password_strength(password)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        password_hash = await to_thread(hash_password, password)
        user = await UserRepo.create(
            self._session,
            username=username,
            password_hash=password_hash,
            role=role,
            display_name=display_name,
        )
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="user.created",
            detail={"user_id": user.id, "username": username, "role": role},
        )
        return UserAdminOut.model_validate(user)

    async def list_users(self, page: int, page_size: int) -> Page[UserAdminOut]:
        items, total = await UserRepo.list_users(self._session, page, page_size)
        return Page(items=[UserAdminOut.model_validate(u) for u in items], total=total, page=page)

    async def update_user(
        self,
        user_id: int,
        *,
        role: str | None,
        is_active: bool | None,
        actor: str,
        operator_id: int,
    ) -> UserAdminOut:
        """改角色 / 停用；不可停用当前登录账号。"""
        user = await UserRepo.get_by_id(self._session, user_id)
        if user is None:
            raise NotFoundError(f"用户不存在: {user_id}")
        if is_active is False and user.id == operator_id:
            raise ConflictError("不能停用当前登录账号")
        updated = await UserRepo.update_user(self._session, user_id, role=role, is_active=is_active)
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="user.updated",
            detail={"user_id": user_id, "role": role, "is_active": is_active},
        )
        return UserAdminOut.model_validate(updated)

    # ---- ACL ----

    async def get_acl(self) -> list[ACLRuleOut]:
        rules = await to_thread(AclRepo.list_all, self._session)
        return [ACLRuleOut.model_validate(r) for r in rules]

    async def add_acl(self, payload: ACLRuleInput, actor: str) -> ACLRuleOut:
        rule = await to_thread(
            AclRepo.create,
            self._session,
            role_or_user=payload.role_or_user,
            table=payload.table,
            action=payload.action,
            allow=payload.allow,
        )
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="acl.updated",
            detail={"add": {"role_or_user": payload.role_or_user, "table": payload.table, "action": payload.action, "allow": payload.allow}},
        )
        return ACLRuleOut.model_validate(rule)

    async def update_acl_rule(self, rule_id: int, payload: ACLRuleInput, actor: str) -> ACLRuleOut:
        rule = await to_thread(
            AclRepo.update,
            self._session,
            rule_id,
            role_or_user=payload.role_or_user,
            table=payload.table,
            action=payload.action,
            allow=payload.allow,
        )
        if rule is None:
            raise NotFoundError(f"ACL 规则不存在: {rule_id}")
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="acl.updated",
            detail={"update": rule_id, "role_or_user": payload.role_or_user, "table": payload.table},
        )
        return ACLRuleOut.model_validate(rule)

    async def delete_acl(self, rule_id: int, actor: str) -> None:
        ok = await to_thread(AclRepo.delete, self._session, rule_id)
        if not ok:
            raise NotFoundError(f"ACL 规则不存在: {rule_id}")
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="acl.updated",
            detail={"delete": rule_id},
        )

    async def update_acl(self, rules_input: list[ACLRuleInput], actor: str) -> list[ACLRuleOut]:
        """整体替换规则集（PUT 幂等语义）。"""
        rules = [
            AdminAcl(role_or_user=r.role_or_user, table=r.table, action=r.action, allow=r.allow)
            for r in rules_input
        ]
        count = await to_thread(AclRepo.replace_all, self._session, rules)
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="acl.updated",
            detail={"count": count},
        )
        return await self.get_acl()

    # ---- 知识库 ----

    async def list_kb(self, keyword: str | None, page: int, page_size: int) -> Page[KBIndicatorOut]:
        items, total = await to_thread(KbRepo.list_kb, self._session, keyword, page, page_size)
        return Page(items=[KBIndicatorOut.model_validate(k) for k in items], total=total, page=page)

    async def create_kb(self, payload: KBIndicatorCreate, actor: str) -> KBIndicatorOut:
        existing = await to_thread(KbRepo.get_by_key, self._session, payload.indicator_key)
        if existing is not None:
            raise ConflictError(f"指标编码已存在: {payload.indicator_key}")
        item = await to_thread(
            KbRepo.create,
            self._session,
            indicator_key=payload.indicator_key,
            metric_name=payload.metric_name,
            category=payload.category,
            formula=payload.formula,
            description=payload.description,
            tags=payload.tags,
            owner=actor,
        )
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="kb.created",
            detail={"indicator_key": payload.indicator_key, "metric_name": payload.metric_name},
        )
        return KBIndicatorOut.model_validate(item)

    async def update_kb(self, kb_id: int, payload: KBIndicatorUpdate, actor: str) -> KBIndicatorOut:
        item = await to_thread(
            KbRepo.update,
            self._session,
            kb_id,
            metric_name=payload.metric_name,
            category=payload.category,
            formula=payload.formula,
            description=payload.description,
            tags=payload.tags,
            owner=actor,
        )
        if item is None:
            raise NotFoundError(f"知识库条目不存在: {kb_id}")
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="kb.updated",
            detail={"kb_id": kb_id, "metric_name": item.metric_name},
        )
        return KBIndicatorOut.model_validate(item)

    async def delete_kb(self, kb_id: int, actor: str) -> None:
        ok = await to_thread(KbRepo.delete, self._session, kb_id)
        if not ok:
            raise NotFoundError(f"知识库条目不存在: {kb_id}")
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action="kb.deleted",
            detail={"kb_id": kb_id},
        )

    # ---- 审计 ----

    async def list_audit(
        self,
        *,
        actor: str | None,
        action: str | None,
        asset_id: str | None,
        from_ts: datetime | None,
        to_ts: datetime | None,
        page: int,
        page_size: int,
    ) -> Page[AuditEntryOut]:
        items, total = await to_thread(
            AuditRepo.list,
            self._session,
            actor=actor,
            action=action,
            asset_id=asset_id,
            from_ts=from_ts,
            to_ts=to_ts,
            page=page,
            page_size=page_size,
        )
        return Page(items=[AuditEntryOut.model_validate(a) for a in items], total=total, page=page)

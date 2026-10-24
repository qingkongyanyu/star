"""API 层公共依赖：会话注入、当前用户、角色守卫。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token, permissions_for
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.repositories.user_repo import UserRepo

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_task_db)],
) -> User:
    """解析 Bearer JWT 并加载用户；缺失/非法/停用一律 401。"""
    if credentials is None:
        raise UnauthorizedError("未登录")
    payload = decode_access_token(credentials.credentials)
    subject = payload.get("sub")
    try:
        user_id = int(subject)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise UnauthorizedError("无效的访问令牌") from None
    user = await UserRepo.get_by_id(session, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("登录已过期，请重新登录")
    return user


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """角色守卫：当前用户角色不在列表则 403。

    用法：``Depends(require_role(Role.ADMIN.value))``
    """

    async def _guard(user: Annotated[User, Depends(get_current_user)]) -> User:
        """角色守卫实现。"""
        if user.role not in roles:
            raise ForbiddenError(f"需要角色 {roles}，当前角色为 {user.role}")
        return user

    return _guard


def require_permission(permission: str) -> Callable[..., Awaitable[User]]:
    """权限点守卫：当前用户须具备指定权限点（admin 通配 `*`），否则 403。

    用法：``Depends(require_permission("asset:download"))``
    权限点定义见 core/security.py ROLE_PERMISSIONS；此守卫使细粒度 RBAC 真正落地。
    """

    async def _guard(user: Annotated[User, Depends(get_current_user)]) -> User:
        """权限点守卫实现。"""
        perms = permissions_for(user.role)
        if "*" not in perms and permission not in perms:
            raise ForbiddenError(f"缺少权限点 {permission}（当前角色 {user.role}）")
        return user

    return _guard

"""用户数据访问。

同步 SQLAlchemy 经 infra.db.to_thread 在线程池执行；本层只 import models + infra + core。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.db import to_thread
from app.models.task_repo import User


class UserRepo:
    """User 表 CRUD。"""

    @staticmethod
    async def get_by_username(session: Session, username: str) -> User | None:
        return await to_thread(_get_by_username, session, username)

    @staticmethod
    async def get_by_id(session: Session, user_id: int) -> User | None:
        return await to_thread(_get_by_id, session, user_id)

    @staticmethod
    async def create(
        session: Session,
        *,
        username: str,
        password_hash: str,
        role: str,
        display_name: str | None,
    ) -> User:
        return await to_thread(_create, session, username, password_hash, role, display_name)

    @staticmethod
    async def list_users(session: Session, page: int, page_size: int) -> tuple[list[User], int]:
        """用户列表（分页，按 id 正序）。"""
        return await to_thread(_list_users, session, page, page_size)

    @staticmethod
    async def update_user(
        session: Session,
        user_id: int,
        *,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> User | None:
        """改角色 / 停用；用户不存在返回 None。"""
        return await to_thread(_update_user, session, user_id, role, is_active)

    @staticmethod
    async def has_active_admin(session: Session) -> bool:
        """库中是否存在启用的管理员账号（商业版初始化的判定依据）。"""
        return await to_thread(_has_active_admin, session)

    @staticmethod
    async def update_password(session: Session, username: str, password_hash: str) -> User | None:
        """重置指定用户口令；用户不存在返回 None。"""
        return await to_thread(_update_password, session, username, password_hash)


def _get_by_username(session: Session, username: str) -> User | None:
    return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def _get_by_id(session: Session, user_id: int) -> User | None:
    return session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


def _create(session: Session, username: str, password_hash: str, role: str, display_name: str | None) -> User:
    user = User(username=username, password_hash=password_hash, role=role, display_name=display_name)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _list_users(session: Session, page: int, page_size: int) -> tuple[list[User], int]:
    total = session.execute(select(func.count()).select_from(User)).scalar_one()
    items = list(
        session.execute(select(User).order_by(User.id).offset((page - 1) * page_size).limit(page_size)).scalars()
    )
    return items, total


def _update_user(
    session: Session,
    user_id: int,
    role: str | None,
    is_active: bool | None,
) -> User | None:
    user = _get_by_id(session, user_id)
    if user is None:
        return None
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    session.commit()
    return user


def _has_active_admin(session: Session) -> bool:
    total = session.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == "admin", User.is_active.is_(True))
    ).scalar_one()
    return int(total) > 0


def _update_password(session: Session, username: str, password_hash: str) -> User | None:
    user = _get_by_username(session, username)
    if user is None:
        return None
    user.password_hash = password_hash
    session.commit()
    return user

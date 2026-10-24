"""SQLAlchemy 引擎与会话封装。

SQLAlchemy 为同步驱动（PyMySQL），按 docs 约定：同步阻塞调用一律经
``asyncio.to_thread`` 包裹（见下方 ``to_thread`` 桥）。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_BINDS: dict[str, str] = {
    "task_repo": settings.database_url,
    "artifact_repo": settings.artifact_database_url,
}


@lru_cache
def get_engine(bind: str = "task_repo") -> Engine:
    """按库名创建引擎（惰性，首次调用才建立连接池）。"""
    if bind not in _BINDS:
        raise ValueError(f"未知数据库绑定: {bind}")
    return create_engine(
        _BINDS[bind],
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=5,
        max_overflow=10,
    )


@lru_cache
def get_session_factory(bind: str = "task_repo") -> sessionmaker[Session]:
    """按库名创建会话工厂。"""
    return sessionmaker(bind=get_engine(bind), expire_on_commit=False, autoflush=False)


async def to_thread[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """同步阻塞调用（SQLAlchemy 同步驱动）跑在线程池。"""
    return await asyncio.to_thread(func, *args, **kwargs)


async def get_task_db() -> AsyncIterator[Session]:
    """FastAPI 依赖：task_repo 会话。"""
    session = get_session_factory("task_repo")()
    try:
        yield session
    finally:
        session.close()


async def get_artifact_db() -> AsyncIterator[Session]:
    """FastAPI 依赖：artifact_repo 会话。"""
    session = get_session_factory("artifact_repo")()
    try:
        yield session
    finally:
        session.close()

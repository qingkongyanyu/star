"""商业版启动初始化：按显式配置创建首个管理员，不内置任何默认账号。

设计原则：
- 不再自动种 ``admin/admin123`` 演示账号；库中无管理员时，只有显式配置
  ``BOOTSTRAP_ADMIN_USERNAME`` / ``BOOTSTRAP_ADMIN_PASSWORD`` 才会创建账号；
- 已存在启用管理员时不做任何修改（不会重置线上口令）；
- 生产环境若既无管理员又无初始化配置，启动失败并给出修复指引；
- 也可用 ``python -m app.cli create-admin`` / ``reset-password`` 完成初始化与口令重置。

数据库未就绪时仅 warning（不阻断开发环境启动），生产环境由健康检查兜底。
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.constants import Role
from app.core.security import hash_password, validate_password_strength
from app.infra.db import get_session_factory, to_thread
from app.repositories.user_repo import UserRepo

logger = logging.getLogger(__name__)

_INIT_HINT = (
    "请设置 BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD 后重启，"
    "或执行 `python -m app.cli create-admin --username <账号>` 初始化管理员。"
)


async def ensure_bootstrap_admin() -> None:
    """按需创建首个管理员（幂等；不覆盖任何既有账号）。"""
    session = get_session_factory("task_repo")()
    try:
        if await UserRepo.has_active_admin(session):
            return

        username = (settings.bootstrap_admin_username or "").strip()
        password = settings.bootstrap_admin_password or ""
        if not username or not password:
            message = f"未检测到启用的管理员账号。{_INIT_HINT}"
            if settings.env == "prod":
                raise RuntimeError(message)
            logger.warning(message)
            return

        if len(username) > 64:
            raise ValueError("BOOTSTRAP_ADMIN_USERNAME 长度不能超过 64")
        validate_password_strength(password)

        existing = await UserRepo.get_by_username(session, username)
        if existing is not None:
            message = (
                f"初始化账号 {username} 已存在但不是启用的管理员；为避免越权不会自动提权。{_INIT_HINT}"
            )
            if settings.env == "prod":
                raise RuntimeError(message)
            logger.warning(message)
            return

        await UserRepo.create(
            session,
            username=username,
            password_hash=await to_thread(hash_password, password),
            role=Role.ADMIN.value,
            display_name="系统管理员",
        )
        # 只记录账号名，绝不记录口令
        logger.info("已创建初始管理员账号 %s（请登录后妥善保管口令）", username)
    except ValueError as exc:
        message = f"初始管理员配置不合法：{exc}。{_INIT_HINT}"
        if settings.env == "prod":
            raise RuntimeError(message) from exc
        logger.warning(message)
    except RuntimeError:
        raise
    except Exception:
        logger.warning("初始管理员检查失败（数据库可能未就绪），已跳过", exc_info=True)
    finally:
        session.close()

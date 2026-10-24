"""认证用例：登录 / 注销 / 当前用户。

无状态 JWT：logout 由前端丢弃 token，服务端无会话可失效（黑名单留待二期接 Redis）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import RateLimitError, UnauthorizedError
from app.core.security import create_access_token, permissions_for, verify_password
from app.infra import login_throttle
from app.infra.captcha import verify_captcha
from app.infra.db import to_thread
from app.models.task_repo import User
from app.repositories.user_repo import UserRepo
from app.schemas.auth import LoginResponse, MeResponse, UserOut


class AuthService:
    """认证服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def login(
        self,
        username: str,
        password: str,
        captcha_id: str,
        captcha_code: str,
        *,
        client_ip: str = "-",
    ) -> LoginResponse:
        """账号密码 + 验证码登录，签发 access_token。

        先做失败次数限流（账号 + IP 双维度），再校验验证码与口令；成功后清零计数。
        错误提示保持统一，不泄露「账号不存在 / 密码错误」差异。
        """
        locked_seconds = await login_throttle.check_locked(username, client_ip)
        if locked_seconds is not None:
            raise RateLimitError(f"登录失败次数过多，请 {locked_seconds} 秒后再试")

        # 先校验验证码（一次性，错误/过期统一提示，避免暴露差异）
        if not await verify_captcha(captcha_id, captcha_code):
            await login_throttle.record_failure(username, client_ip)
            raise UnauthorizedError("验证码错误或已失效")
        user = await UserRepo.get_by_username(self._session, username)
        if user is None or not user.is_active:
            await login_throttle.record_failure(username, client_ip)
            raise UnauthorizedError("账号或密码错误")
        ok = await to_thread(verify_password, password, user.password_hash)
        if not ok:
            await login_throttle.record_failure(username, client_ip)
            raise UnauthorizedError("账号或密码错误")
        await login_throttle.reset(username, client_ip)
        token = create_access_token(str(user.id), user.role)
        return LoginResponse(access_token=token, user=UserOut.model_validate(user))

    async def logout(self) -> None:
        """无状态 JWT：no-op（如需 token 失效，后续在此接入 Redis denylist）。"""

    async def me(self, user: User) -> MeResponse:
        """当前用户 + 权限点。"""
        return MeResponse(user=UserOut.model_validate(user), permissions=permissions_for(user.role))

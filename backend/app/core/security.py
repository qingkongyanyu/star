"""密码哈希、JWT 签发/校验、角色与权限点。

- 密码：argon2（argon2-cffi，只存哈希）
- 令牌：JWT（HS256），载荷 { sub, role, exp }，TTL 见 settings.jwt_ttl_minutes
"""
from __future__ import annotations

import time
from typing import Any, cast

import argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings
from app.core.constants import Role
from app.core.exceptions import UnauthorizedError

_password_hasher = argon2.PasswordHasher()

# 常见弱口令（商业版禁止用于初始管理员/新建用户）
_WEAK_PASSWORDS = {
    "admin",
    "admin123",
    "password",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty",
    "qwerty123",
    "letmein",
    "welcome",
    "changeme",
    "change-me-in-prod",
    "starpivot",
}


def validate_password_strength(password: str, *, min_length: int | None = None) -> None:
    """校验口令强度；不满足抛 ValueError（中文提示直接展示给管理员）。

    规则：长度 ≥ ``PASSWORD_MIN_LENGTH``（默认 10）、非常见弱口令、至少包含
    大写/小写/数字/符号 四类中的三类。
    """
    minimum = min_length if min_length is not None else settings.password_min_length
    if len(password) < minimum:
        raise ValueError(f"密码长度不能少于 {minimum} 位")
    if password.strip().lower() in _WEAK_PASSWORDS:
        raise ValueError("密码过于常见，请更换为更复杂的口令")
    classes = sum(
        (
            any(ch.islower() for ch in password),
            any(ch.isupper() for ch in password),
            any(ch.isdigit() for ch in password),
            any(not ch.isalnum() for ch in password),
        )
    )
    if classes < 3:
        raise ValueError("密码需至少包含大写字母、小写字母、数字、符号中的三类")


def hash_password(password: str) -> str:
    """argon2 哈希口令。"""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验口令；哈希非法/不匹配一律返回 False（不向上抛异常）。"""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def create_access_token(subject: str, role: str) -> str:
    """签发 JWT（HS256）。payload { sub, role, exp }，exp 为 int。"""
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "exp": int(time.time()) + settings.jwt_ttl_minutes * 60,
    }
    return cast(str, jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def decode_access_token(token: str) -> dict[str, Any]:
    """校验并解码 JWT；过期/非法一律抛 UnauthorizedError。"""
    try:
        return cast(dict[str, Any], jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]))
    except ExpiredSignatureError:
        raise UnauthorizedError("登录已过期，请重新登录") from None
    except JWTError:
        raise UnauthorizedError("无效的访问令牌") from None


# 一期权限点（docs/03-前端/05-权限与安全.md；RBAC 细粒度二期再扩展）
ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.ADMIN.value: ["*"],
    Role.ANALYST.value: [
        "task:create",
        "task:view",
        "asset:view",
        "asset:download",
        "trace:view",
        "ask:create",
    ],
    Role.READONLY.value: ["asset:view", "dashboard:view"],
}


def permissions_for(role: str) -> list[str]:
    """按角色返回权限点列表。"""
    return ROLE_PERMISSIONS.get(role, [])

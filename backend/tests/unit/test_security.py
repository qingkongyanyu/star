"""core.security 单元测试（不依赖数据库）。

覆盖：argon2 哈希/校验、JWT 签发/解码、非法令牌拒绝、口令强度策略。
"""
from __future__ import annotations

import pytest
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    validate_password_strength,
    verify_password,
)


def test_password_hash_verify_roundtrip() -> None:
    secret = "Unit-Test#Pass2026"
    hashed = hash_password(secret)
    assert hashed != secret
    assert verify_password(secret, hashed) is True
    assert verify_password("wrong-pass", hashed) is False


def test_password_hash_never_plaintext() -> None:
    hashed = hash_password("secret")
    assert "secret" not in hashed


def test_jwt_roundtrip() -> None:
    token = create_access_token("1", "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["role"] == "admin"
    assert isinstance(payload["exp"], int)


def test_jwt_rejects_invalid_token() -> None:
    with pytest.raises(UnauthorizedError):
        decode_access_token("not-a-jwt")


def test_jwt_rejects_tampered_token() -> None:
    token = create_access_token("1", "admin")
    with pytest.raises(UnauthorizedError):
        decode_access_token(token[:-4] + "beef")


def test_password_strength_accepts_commercial_password() -> None:
    validate_password_strength("StarPivot#2026")


def test_password_strength_rejects_short_password() -> None:
    with pytest.raises(ValueError, match="长度"):
        validate_password_strength("Ab1!")


def test_password_strength_rejects_common_password() -> None:
    with pytest.raises(ValueError, match="常见"):
        validate_password_strength("Password123")


def test_password_strength_rejects_low_complexity() -> None:
    with pytest.raises(ValueError, match="三类"):
        validate_password_strength("onlylowercase")

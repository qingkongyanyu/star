"""认证相关 DTO（契约见 docs/02-后端/04-API接口契约.md 第二节）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """POST /auth/login 请求体（含登录验证码）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    captcha_id: str = Field(min_length=1, description="验证码 ID（GET /auth/captcha 返回）")
    captcha_code: str = Field(min_length=1, max_length=8, description="用户输入的验证码")


class CaptchaOut(BaseModel):
    """GET /auth/captcha 响应：验证码 ID + SVG 图形。"""

    captcha_id: str
    svg: str
    expires_in: int


class UserOut(BaseModel):
    """用户信息（登录/me 响应均含）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    display_name: str | None = None


class LoginResponse(BaseModel):
    """POST /auth/login 响应：access_token + 用户信息。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class MeResponse(BaseModel):
    """GET /auth/me 响应：当前用户 + 权限点。"""

    user: UserOut
    permissions: list[str]

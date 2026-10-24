"""认证接口：/auth/login、/auth/logout、/auth/me。

契约见 docs/02-后端/04-API接口契约.md 第二节。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.infra.captcha import create_captcha
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.schemas.auth import CaptchaOut, LoginRequest, LoginResponse, MeResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/captcha", response_model=CaptchaOut, summary="获取登录验证码")
async def captcha() -> CaptchaOut:
    """生成登录验证码（SVG 图形），返回 captcha_id 供登录时校验。"""
    return CaptchaOut(**(await create_captcha()))


@router.post("/login", response_model=LoginResponse, summary="账号密码 + 验证码登录")
async def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[Session, Depends(get_task_db)],
) -> LoginResponse:
    """账号密码 + 验证码登录，返回 access_token + 用户信息。

    失败次数按「账号 + 来源 IP」双维度限流，达到阈值返回 429。
    """
    client_ip = request.client.host if request.client else "-"
    return await AuthService(session).login(
        payload.username,
        payload.password,
        payload.captcha_id,
        payload.captcha_code,
        client_ip=client_ip,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="注销")
async def logout(_user: Annotated[User, Depends(get_current_user)]) -> None:
    """注销（无状态 JWT：前端丢弃 token 即可）。"""


@router.get("/me", response_model=MeResponse, summary="当前用户 + 权限点")
async def me(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> MeResponse:
    """当前用户 + 角色 + 权限点。"""
    return await AuthService(session).me(user)

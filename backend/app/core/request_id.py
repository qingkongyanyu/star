"""request_id 贯穿 HTTP→Agent→工具（docs/05-DevOps/01 第六节）。

中间件读取 ``X-Request-Id``（无则生成 ``req-<hex12>``），注入 ContextVar 供
日志/错误信封使用，并回写响应头。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def generate_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def get_request_id() -> str:
    """当前请求的 request_id（未进入请求上下文时为空串）。"""
    return _request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 request_id。"""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-Id") or generate_request_id()
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers.setdefault("X-Request-Id", request_id)
        return response

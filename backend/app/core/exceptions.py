"""统一业务异常与错误码。

契约（docs/02-后端/04-API接口契约.md）：
    错误响应 { code, message, request_id }

    HTTP  业务码          含义
    401   E_UNAUTHORIZED  未登录/令牌过期
    403   E_FORBIDDEN     已登录但无权限
    404   E_NOT_FOUND     资源不存在
    422   E_VALIDATION    参数校验失败
    409   E_CONFLICT      状态冲突
    429   E_RATELIMIT     限流
    500   E_INTERNAL      内部错误

开发规范（docs/04-开发指南/02）：业务异常抛 AppError，禁止裸抛 Exception。
"""
from __future__ import annotations


class AppError(Exception):
    """业务异常基类：携带业务码与 HTTP 状态码。"""

    status_code: int = 500
    code: str = "E_INTERNAL"

    def __init__(self, message: str = "", code: str | None = None, status_code: int | None = None) -> None:
        self.message = message or self.code
        super().__init__(self.message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class UnauthorizedError(AppError):
    """未登录 / 令牌过期。"""

    status_code = 401
    code = "E_UNAUTHORIZED"


class ForbiddenError(AppError):
    """已登录但无权限。"""

    status_code = 403
    code = "E_FORBIDDEN"


class NotFoundError(AppError):
    """资源不存在。"""

    status_code = 404
    code = "E_NOT_FOUND"


class ValidationError(AppError):
    """参数校验失败。"""

    status_code = 422
    code = "E_VALIDATION"


class ConflictError(AppError):
    """状态冲突（如节点已被认领）。"""

    status_code = 409
    code = "E_CONFLICT"


class RateLimitError(AppError):
    """限流。"""

    status_code = 429
    code = "E_RATELIMIT"


class InternalError(AppError):
    """内部错误。"""

    status_code = 500
    code = "E_INTERNAL"


def error_envelope(code: str, message: str, request_id: str) -> dict[str, str]:
    """错误响应信封。"""
    return {"code": code, "message": message, "request_id": request_id}

"""FastAPI 应用入口。

启动：uvicorn app.main:app --reload --port 8070
路由按 docs/02-后端/04-API接口契约.md 挂载。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import admin, agents, assets, auth, dashboard, data_sources, memory, tasks, trace, ws
from app.bus.event_hub import hub
from app.core.config import settings
from app.core.exceptions import AppError, error_envelope
from app.core.request_id import RequestIdMiddleware, get_request_id
from app.infra.db import get_engine, to_thread
from app.infra.minio import get_minio
from app.infra.redis import get_redis
from app.services.bootstrap import ensure_bootstrap_admin
from app.services.scheduler_worker import worker as scheduler_worker

logger = logging.getLogger(__name__)

_DEFAULT_JWT_SECRETS = {"change-me-in-prod", "7dba7a4a6578a410c7f229c828971c2cd627b1820852411320baaee0f2c812fc"}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动期：生产环境强密钥校验 → 按显式配置初始化管理员 → 启动调度 Worker。"""
    if settings.env == "prod" and (
        settings.jwt_secret in _DEFAULT_JWT_SECRETS or len(settings.jwt_secret) < 32
    ):
        raise RuntimeError("生产环境必须配置独立的强 JWT_SECRET（至少 32 位随机字符串）")
    await ensure_bootstrap_admin()
    await scheduler_worker.start()
    await hub.start_redis_bridge()  # 多副本事件共享（EVENT_BUS_REDIS=true 时启用；本地默认 no-op）
    try:
        yield
    finally:
        await hub.stop_redis_bridge()
        await scheduler_worker.stop()


app = FastAPI(
    title=settings.app_name,
    version="3.2.0",
    description="星枢 StarPivot · 企业级 12-Agent 智能数据分析平台 · 后端 API",
    debug=settings.debug,
    lifespan=lifespan,
    # 商业版生产环境不暴露交互式 API 文档，降低攻击面（开发/测试环境保留）
    docs_url="/docs" if settings.env != "prod" else None,
    redoc_url="/redoc" if settings.env != "prod" else None,
    openapi_url="/openapi.json" if settings.env != "prod" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """商业版安全响应头；生产环境追加 HSTS 与严格 CSP（API 响应不承载页面）。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if settings.env == "prod":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
    return response


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """业务异常 → {code, message, request_id}。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, get_request_id() or "-"),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """参数校验失败 → 422 E_VALIDATION。"""
    message = str(exc.errors()[0]["msg"]) if exc.errors() else "参数校验失败"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_envelope("E_VALIDATION", message, get_request_id() or "-"),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Starlette 自带 HTTP 异常（404/405/429 等）→ 统一信封（按状态码映射业务码）。"""
    code = {
        400: "E_VALIDATION",
        401: "E_UNAUTHORIZED",
        403: "E_FORBIDDEN",
        404: "E_NOT_FOUND",
        405: "E_VALIDATION",
        409: "E_CONFLICT",
        429: "E_RATELIMIT",
    }.get(exc.status_code, "E_INTERNAL")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(code, str(exc.detail), get_request_id() or "-"),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    """兜底：未捕获异常 → 500 E_INTERNAL。"""
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope("E_INTERNAL", "服务器内部错误", get_request_id() or "-"),
    )


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """存活探针：仅表示进程在运行，不探测依赖。"""
    return {"status": "ok", "app": settings.app_name, "env": settings.env, "version": app.version}


def _ping_database() -> None:
    """同步数据库探测（在线程池执行，避免阻塞事件循环）。"""
    with get_engine("task_repo").connect() as conn:
        conn.execute(text("SELECT 1"))


async def _check_database() -> None:
    """数据库连通性（必需依赖）。"""
    await to_thread(_ping_database)


async def _check_redis() -> None:
    """Redis 连通性（验证码/事件桥/限流，可降级）。"""
    await get_redis().ping()


async def _check_minio() -> None:
    """MinIO 连通性（资产存储，可降级本地）。"""
    await asyncio.to_thread(get_minio().bucket_exists, settings.minio_bucket)


@app.get("/readyz", tags=["system"])
async def readyz() -> JSONResponse:
    """就绪探针：并发探测 DB / Redis / MinIO；数据库不可用返回 503。

    Redis/MinIO 不可用时平台仍有内存/本地降级，返回 200 但标记 degraded，
    便于编排系统区分「不可服务」与「降级运行」。
    """
    database, redis_ok, minio_ok = await asyncio.gather(
        _check_database(), _check_redis(), _check_minio(), return_exceptions=True
    )
    checks = {
        "database": "ok" if not isinstance(database, BaseException) else "error",
        "redis": "ok" if not isinstance(redis_ok, BaseException) else "error",
        "minio": "ok" if not isinstance(minio_ok, BaseException) else "error",
    }
    if checks["database"] != "ok":
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "not_ready", "checks": checks})
    overall = "ready" if all(value == "ok" for value in checks.values()) else "degraded"
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": overall, "checks": checks})


# ---- 模块路由在此挂载（按 API 契约逐模块接入）----
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(tasks.nodes_router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(trace.router, prefix="/api/v1")
app.include_router(memory.router, prefix="/api/v1")
app.include_router(data_sources.router, prefix="/api/v1")
# WebSocket（契约路径 /ws/chat，无 /api/v1 前缀）
app.include_router(ws.router)

"""登录验证码：随机码 + SVG 图形 + 一次性校验（TTL 过期）。

存储（商业版）：优先异步 Redis（多 worker/多实例共享校验）；Redis 不可用或调用失败时
自动降级为进程内字典。所有 I/O 均为 await，不会阻塞事件循环。key 约定 ``captcha:{id}``。
"""
from __future__ import annotations

import hashlib
import logging
import random
import secrets
import threading
import time
import uuid
from typing import TypedDict

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.infra.redis import get_redis

logger = logging.getLogger(__name__)


class CaptchaPayload(TypedDict):
    """验证码下发结构（/auth/captcha 响应体）。"""

    captcha_id: str
    svg: str
    expires_in: int

# 排除易混淆字符 0/O/1/I/l，长度 4
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"
_CAPTCHA_TTL_SECONDS = 300
_MAX_ENTRIES = 1000
_CAPTCHA_KEY_PREFIX = "captcha:"

_lock = threading.Lock()
_store: dict[str, tuple[str, float]] = {}  # captcha_id -> (code, expires_at)

# Redis 可用性探测（避免不可用时每次请求都等连接超时）
_redis_available: bool | None = None  # None=未探测
_next_probe_at = 0.0
_PROBE_INTERVAL_SECONDS = 30.0


def _mark_unavailable() -> None:
    global _redis_available, _next_probe_at
    _redis_available = False
    _next_probe_at = time.monotonic() + _PROBE_INTERVAL_SECONDS


async def _redis() -> Redis | None:
    """返回可用异步 Redis 客户端；未配/不可用返回 None（走内存兜底）。"""
    global _redis_available
    now = time.monotonic()
    if _redis_available is False and now < _next_probe_at:
        return None
    if not (settings.redis_url or "").strip().startswith(("redis://", "rediss://")):
        _redis_available = False
        return None
    client = get_redis()
    if _redis_available is True:
        return client
    try:
        await client.ping()
        _redis_available = True
        return client
    except (RedisError, OSError):
        _mark_unavailable()
        return None


def _generate_code(length: int = 4) -> str:
    """随机 4 位验证码（字母+数字，剔除易混淆字符）。

    使用 secrets（密码学安全随机源）而非 random：验证码是登录凭据的一部分，
    MT19937 的状态可被预测，不能用于安全场景。
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _render_svg(code: str) -> str:
    """把验证码渲染成带干扰线/点的 SVG（同一 code 图形可复现，便于测试）。"""
    rng = random.Random(int(hashlib.md5(code.encode("utf-8")).hexdigest(), 16) % (2**32))
    width, height = 120, 44
    bg = "#f6efe2"
    chars: list[str] = []
    for i, ch in enumerate(code):
        x = 20 + i * 22
        y = 31
        angle = rng.randint(-20, 20)
        color = f"#{rng.randint(60, 110):02x}{rng.randint(80, 120):02x}{rng.randint(100, 150):02x}"
        size = rng.randint(24, 30)
        chars.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'transform="rotate({angle} {x} {y})" font-family="Arial, sans-serif" '
            f'font-weight="bold" text-anchor="middle">{ch}</text>'
        )
    lines = [
        f'<line x1="{rng.randint(0, width)}" y1="{rng.randint(0, height)}" '
        f'x2="{rng.randint(0, width)}" y2="{rng.randint(0, height)}" '
        f'stroke="rgba({rng.randint(120, 170)},{rng.randint(120, 170)},{rng.randint(120, 170)},0.5)" stroke-width="1"/>'
        for _ in range(4)
    ]
    dots = "".join(
        f'<circle cx="{rng.randint(0, width)}" cy="{rng.randint(0, height)}" r="1" fill="rgba(130,95,60,0.35)"/>'
        for _ in range(50)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="{bg}"/>' + dots + "".join(lines) + "".join(chars) + "</svg>"
    )


def _store_memory(captcha_id: str, code: str) -> None:
    """内存兜底存储（Redis 不可用时）。"""
    now = time.time()
    with _lock:
        for key in [k for k, (_, exp) in _store.items() if exp <= now]:
            _store.pop(key, None)
        if len(_store) >= _MAX_ENTRIES:
            _store.clear()
        _store[captcha_id] = (code, now + _CAPTCHA_TTL_SECONDS)


async def create_captcha() -> CaptchaPayload:
    """生成验证码：返回 {captcha_id, svg, expires_in}。优先异步 Redis，不可用回落内存。"""
    code = _generate_code()
    captcha_id = uuid.uuid4().hex
    client = await _redis()
    if client is not None:
        try:
            await client.set(_CAPTCHA_KEY_PREFIX + captcha_id, code, ex=_CAPTCHA_TTL_SECONDS)
        except (RedisError, OSError):
            _mark_unavailable()
            logger.warning("验证码写入 Redis 失败，降级内存存储")
            _store_memory(captcha_id, code)
    else:
        _store_memory(captcha_id, code)
    return {"captcha_id": captcha_id, "svg": _render_svg(code), "expires_in": _CAPTCHA_TTL_SECONDS}


async def verify_captcha(captcha_id: str, code: str) -> bool:
    """校验验证码：一次性使用（校验后即删除），大小写不敏感，过期视为无效。"""
    if not captcha_id or not code:
        return False
    expected = code.strip().lower()
    client = await _redis()
    if client is not None:
        try:
            key = _CAPTCHA_KEY_PREFIX + captcha_id
            stored = await client.get(key)
            if stored is not None:
                await client.delete(key)
                return bool(str(stored).lower() == expected)
        except (RedisError, OSError):
            _mark_unavailable()
            logger.warning("Redis 校验验证码失败，回落内存校验")
        # Redis 未命中：可能是创建时 Redis 短暂不可用而落到了内存兜底，继续尝试内存
    with _lock:
        entry = _store.pop(captcha_id, None)
    if entry is None:
        return False
    stored, exp = entry
    if time.time() > exp:
        return False
    return stored.lower() == expected

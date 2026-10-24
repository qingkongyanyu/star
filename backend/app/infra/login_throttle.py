"""登录失败防爆破：按账号与来源 IP 双维度计数。

存储策略：
- 配好 Redis 时用 Redis 计数（多 worker/多副本共享），键 ``login_fail:user:{name}``、
  ``login_fail:ip:{ip}``，首次失败写入 TTL；
- Redis 不可用时自动降级为进程内计数（单实例仍可防爆破），恢复后每 30 秒探测一次。

所有函数均为 async，避免在登录链路里用同步 Redis 客户端阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.infra.redis import get_redis

logger = logging.getLogger(__name__)

_USER_PREFIX = "login_fail:user:"
_IP_PREFIX = "login_fail:ip:"

# Redis 可用性探测状态（避免 Redis 故障时每次登录都等待连接超时）
_UNKNOWN, _AVAILABLE, _UNAVAILABLE = 0, 1, 2
_redis_state = _UNKNOWN
_next_probe_at = 0.0
_PROBE_INTERVAL_SECONDS = 30.0

# 进程内兜底：key -> (失败次数, 过期时间 monotonic)
_memory: dict[str, tuple[int, float]] = {}
_memory_lock = asyncio.Lock()


async def _client() -> Redis | None:
    """返回可用 Redis 客户端；不可用返回 None（降级内存）。"""
    global _redis_state, _next_probe_at
    now = time.monotonic()
    client = get_redis()
    if _redis_state == _AVAILABLE:
        return client
    if _redis_state == _UNAVAILABLE and now < _next_probe_at:
        return None
    try:
        await client.ping()
        _redis_state = _AVAILABLE
        return client
    except (RedisError, OSError):
        _redis_state = _UNAVAILABLE
        _next_probe_at = now + _PROBE_INTERVAL_SECONDS
        return None


def _mark_unavailable() -> None:
    """Redis 操作失败后进入冷却期，避免后续每次登录都等待连接超时。"""
    global _redis_state, _next_probe_at
    _redis_state = _UNAVAILABLE
    _next_probe_at = time.monotonic() + _PROBE_INTERVAL_SECONDS


def _user_key(username: str) -> str:
    return f"{_USER_PREFIX}{username.strip().lower()}"


def _ip_key(client_ip: str) -> str:
    return f"{_IP_PREFIX}{client_ip or '-'}"


def _memory_count(key: str) -> int:
    entry = _memory.get(key)
    if entry is None:
        return 0
    count, expires_at = entry
    if expires_at <= time.monotonic():
        _memory.pop(key, None)
        return 0
    return count


async def _incr(client: Redis | None, key: str) -> int:
    """计数 +1（首次写入过期时间）。"""
    if client is not None:
        try:
            count = int(await client.incr(key))
            if count == 1:
                await client.expire(key, settings.login_lockout_seconds)
            return count
        except (RedisError, OSError):
            _mark_unavailable()
            logger.warning("登录限流 Redis 写入失败，降级内存计数")
    async with _memory_lock:
        now = time.monotonic()
        count, expires_at = _memory.get(key, (0, now + settings.login_lockout_seconds))
        count += 1
        _memory[key] = (count, expires_at)
        return count


async def _get(client: Redis | None, key: str) -> tuple[int, int]:
    """返回 (失败次数, 剩余锁定秒数)。"""
    if client is not None:
        try:
            value = await client.get(key)
            count = int(value) if value is not None else 0
            ttl = int(await client.ttl(key)) if count else 0
            return count, max(ttl, 0)
        except (RedisError, OSError):
            _mark_unavailable()
            logger.warning("登录限流 Redis 读取失败，降级内存计数")
    async with _memory_lock:
        count = _memory_count(key)
        entry = _memory.get(key)
        remaining = int(entry[1] - time.monotonic()) if entry else 0
        return count, max(remaining, 0)


async def check_locked(username: str, client_ip: str) -> int | None:
    """若账号或 IP 已被锁定，返回剩余秒数；否则返回 None。"""
    client = await _client()
    for key, limit in (
        (_user_key(username), settings.login_max_attempts),
        (_ip_key(client_ip), settings.login_ip_max_attempts),
    ):
        count, ttl = await _get(client, key)
        if count >= limit:
            return max(ttl, 1)
    return None


async def record_failure(username: str, client_ip: str) -> None:
    """记录一次登录失败（账号 + IP 双计数）。"""
    client = await _client()
    await _incr(client, _user_key(username))
    await _incr(client, _ip_key(client_ip))


async def reset(username: str, client_ip: str) -> None:
    """登录成功后清除失败计数。"""
    client = await _client()
    keys = [_user_key(username), _ip_key(client_ip)]
    if client is not None:
        try:
            await client.delete(*keys)
            return
        except (RedisError, OSError):
            _mark_unavailable()
            logger.warning("登录限流 Redis 清理失败，降级内存清理")
    async with _memory_lock:
        for key in keys:
            _memory.pop(key, None)

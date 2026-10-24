"""Redis 封装：锁（SETNX）+ 令牌桶限流。

降级策略：Redis 不可用时**放行**并打 warning，开发/演练环境不阻塞业务。
Redis key 约定见 docs/02-后端/03-数据模型与迁移.md 第六节：
    lock:node:{node_id}  /  pool:{capability}  /  limiter:{scope}  /  ws:{user_id}
"""
from __future__ import annotations

import logging
import time
import uuid
from functools import lru_cache

from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Lua：token 一致才删除，防误删他人锁
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua：令牌桶（capacity 容量，refill_per_sec 每秒补充，key 分 tokens/last 两个键）
_TOKEN_BUCKET_LUA = """
local key, capacity, refill, now = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local tokens = tonumber(redis.call("get", key .. ":tokens") or capacity)
local last = tonumber(redis.call("get", key .. ":last") or now)
tokens = math.min(capacity, tokens + (now - last) * refill)
redis.call("set", key .. ":last", now)
if tokens >= 1 then
    redis.call("set", key .. ":tokens", tokens - 1)
    return 1
else
    redis.call("set", key .. ":tokens", tokens)
    return 0
end
"""


@lru_cache
def _client() -> Redis:
    # 商业版：显式连接/读写超时，Redis 故障时快速降级而非拖住请求
    return from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=2.0,
    )


def get_redis() -> Redis:
    """获取全局 Redis 客户端（惰性连接）。"""
    return _client()


async def acquire_lock(key: str, token: str | None = None, ttl_seconds: int = 30) -> str | None:
    """SET NX EX 取锁；成功返回 token（释放时比对），失败返回 None。"""
    token = token or uuid.uuid4().hex
    try:
        ok = await _client().set(key, token, nx=True, ex=ttl_seconds)
    except RedisError:
        logger.warning("Redis 不可用，锁 acquire_lock(%s) 降级放行", key)
        return token
    return token if ok else None


async def release_lock(key: str, token: str) -> bool:
    """释放锁（token 比对通过才删除）。"""
    try:
        ok = await _client().eval(_RELEASE_LUA, 1, key, token)
    except RedisError:
        return False
    return bool(ok)


async def token_bucket_allow(scope: str, capacity: int = 10, refill_per_sec: float = 1.0) -> bool:
    """令牌桶放行判定。scope 示例：``task:{user_id}`` / ``agent:{agent_id}``。"""
    try:
        key = f"limiter:{scope}"
        ok = await _client().eval(_TOKEN_BUCKET_LUA, 1, key, capacity, refill_per_sec, time.time())
    except RedisError:
        return True  # 降级放行
    return bool(ok)

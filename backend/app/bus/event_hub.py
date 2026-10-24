"""事件中枢：默认进程内广播；可选 Redis Pub/Sub 桥接（多副本共享事件）。

事件信封：{ "type": <event>, "data": {...} }（对齐 docs/02-后端/04 §十 下行格式）。
本地/单 worker 默认不启用 Redis（EVENT_BUS_REDIS=false）→ 行为、单测、联调与原来一致；
多副本部署开启后：本进程直接投递本地订阅者 + 写 Redis 通道；各进程订阅该通道把远端事件喂回
本地（用自己的 instance_id 去重，避免收到自己发布的消息重复）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class EventHub:
    """发布/订阅事件中枢（进程内队列 + 可选 Redis 桥）。"""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._instance_id = uuid.uuid4().hex[:8]
        self._redis: Any = None  # redis.asyncio 客户端（仅 EVENT_BUS_REDIS=true 且可达时）
        self._redis_task: asyncio.Task[None] | None = None

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """订阅事件流，返回队列（消费方循环 queue.get）。"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """广播事件给所有本地订阅者；开启 Redis 桥时同时写通道供其他副本消费。"""
        message = {"type": event_type, "data": data}
        self._deliver_local(message)
        if self._redis is not None:
            try:
                envelope = {"instance_id": self._instance_id, **message}
                await self._redis.publish(settings.event_bus_redis_channel, json.dumps(envelope, ensure_ascii=False))
            except Exception:  # noqa: BLE001  Redis 桥瞬时故障不影响本地投递
                logger.warning("事件发布到 Redis 失败（仅影响跨副本），事件 %s", event_type)

    def _deliver_local(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("WS 订阅队列满，丢弃事件 %s", message.get("type"))

    async def start_redis_bridge(self) -> None:
        """开启 Redis Pub/Sub 桥（settings.event_bus_redis=true 且 Redis 可达）；否则 no-op。"""
        if not settings.event_bus_redis:
            return
        try:
            from redis import asyncio as aioredis

            client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2.0)
            await client.ping()
            self._redis = client
            self._redis_task = asyncio.create_task(self._redis_loop(), name="event-bus-redis")
            logger.info("事件总线 Redis 桥已开启（channel=%s）", settings.event_bus_redis_channel)
        except Exception:
            logger.warning("Redis 事件桥不可用，回退进程内广播", exc_info=True)

    async def _redis_loop(self) -> None:
        """订阅远端事件并喂回本地订阅者（按 instance_id 去重）。"""
        if self._redis is None:
            return
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(settings.event_bus_redis_channel)
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(raw.get("data") or "{}")
                except (ValueError, TypeError):
                    continue
                if envelope.get("instance_id") == self._instance_id:
                    continue  # 自己发布的消息已本地投递，跳过
                self._deliver_local({"type": envelope.get("type"), "data": envelope.get("data", {})})
        except asyncio.CancelledError:
            raise
        except Exception:  # 订阅循环异常则退出（下轮由运维/重启恢复）
            logger.exception("Redis 事件订阅循环异常退出")
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(settings.event_bus_redis_channel)

    async def stop_redis_bridge(self) -> None:
        """关闭 Redis 桥（取消订阅任务并释放连接）。"""
        if self._redis_task is not None:
            self._redis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._redis_task
            self._redis_task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None


# 全局单例
hub = EventHub()

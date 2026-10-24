"""常驻调度 Worker（v2：进程内后台驱动，MySQL 租约/CAS 保证多 worker 安全）。

职责（每个 uvicorn 进程一个实例，由 app.main 的 lifespan 启停）：
- 每个扫描周期：`reclaim_stale_tasks`（崩溃/重启遗留的 PLANNING/RUNNING 卡死任务复位）
  → 扫描 PENDING 任务 → 对空余并发槽发起 `_drive_task`。
- `_drive_task`：`TaskRepo.try_acquire_task_lease` CAS 抢占（PENDING→RUNNING 或接管过期 RUNNING）
  → 成功后调用 `SchedulerService.execute_task(task_id, token, lease_ttl)` 驱动 DAG 到终态。

正确性：事件/唤醒仅是本地加速，**跨进程与崩溃恢复全部落在 DB**（CAS rowcount + 心跳租约），
不依赖 Redis/Kafka。启动即扫描，故新任务无需外部信号也会在下一周期被拾起。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from app.core.config import settings
from app.infra.db import get_session_factory, to_thread
from app.repositories.task_repo import TaskRepo
from app.services.scheduler import SchedulerService

logger = logging.getLogger(__name__)


class SchedulerWorker:
    """常驻调度 Worker。"""

    def __init__(
        self,
        *,
        lease_ttl: int | None = None,
        scan_interval: float | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        self._lease_ttl = lease_ttl or settings.scheduler_lease_ttl
        self._scan_interval = scan_interval or settings.scheduler_scan_interval
        self._max_concurrent = max_concurrent or settings.scheduler_max_concurrent
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._stop = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()
        self._runner: asyncio.Task[None] | None = None

    # ---- 生命周期 ----

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._stop.clear()
            self._runner = asyncio.create_task(self._run(), name="scheduler-worker")
            logger.info("调度 Worker 启动（lease_ttl=%ss, scan=%ss, concurrent=%s）", self._lease_ttl, self._scan_interval, self._max_concurrent)

    async def stop(self) -> None:
        self._stop.set()
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None
        # 取消在飞驱动（execute_task 内部会 catch 异常并把状态交还回收；心跳由 finally 停止）
        pending = list(self._active)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._active.clear()
        logger.info("调度 Worker 已停止")

    async def pump_once(self) -> None:
        """扫描一轮并派发可驱动任务（供测试与手动触发）。"""
        reclaim_session = get_session_factory("task_repo")()
        try:
            reclaimed = await to_thread(TaskRepo.reclaim_stale_tasks, reclaim_session, self._lease_ttl)
            if reclaimed:
                logger.info("自愈：复位卡死任务 %s", reclaimed)
        finally:
            reclaim_session.close()

        scan_session = get_session_factory("task_repo")()
        try:
            pending = await to_thread(TaskRepo.pending_task_ids, scan_session, 100)
        finally:
            scan_session.close()

        slots = self._max_concurrent - len(self._active)
        for task_id in pending[: max(slots, 0)]:
            if self._stop.is_set():
                return
            # 每个候选用独立协程抢占租约，避免本函数被长任务阻塞
            coro = asyncio.create_task(self._drive_task(task_id), name=f"drive-{task_id}")
            self._active.add(coro)
            coro.add_done_callback(self._active.discard)

    # ---- 内部 ----

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.pump_once()
            except Exception:
                logger.exception("调度扫描异常，下轮重试")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._scan_interval)
            except TimeoutError:
                pass

    async def _drive_task(self, task_id: str) -> None:
        async with self._sem:
            token = uuid.uuid4().hex
            session = get_session_factory("task_repo")()
            try:
                outcome = await to_thread(TaskRepo.try_acquire_task_lease, session, task_id, token, self._lease_ttl)
            finally:
                session.close()
            if outcome != "acquired":
                return
            logger.info("接管并驱动任务 %s", task_id)
            await SchedulerService.execute_task(task_id, token=token, lease_ttl=self._lease_ttl)


# 全局单例（与 hub 一致）
worker = SchedulerWorker()

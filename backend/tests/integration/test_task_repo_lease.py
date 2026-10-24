"""仓库层调度租约/CAS/自愈 集成测试（需 MySQL，连不上自动 skip）。

覆盖：P0-1 的核心原语——任务驱动态租约独占、陈旧租约接管并复位在飞节点、
心跳续租、清理租约、reclaim_stale_tasks 自愈。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from app.infra.db import get_session_factory
from app.models.task_repo import DagEdge, DagNode, Task
from app.repositories.task_repo import TaskRepo
from sqlalchemy import delete, update

pytestmark = pytest.mark.integration


def _new_id() -> str:
    return f"t{uuid.uuid4().hex[:12]}"


def _insert_task(status: str = "PENDING") -> Task:
    session = get_session_factory("task_repo")()
    try:
        task = Task(
            task_id=_new_id(),
            req_id=_new_id(),
            requester="pytest",
            task_type="analysis",
            requirement="租约测试",
            status=status,
        )
        session.add(task)
        session.commit()
        return task
    finally:
        session.close()


def _delete_task(task_id: str) -> None:
    session = get_session_factory("task_repo")()
    try:
        session.execute(delete(DagEdge).where(DagEdge.task_id == task_id))
        session.execute(delete(DagNode).where(DagNode.task_id == task_id))
        session.execute(delete(Task).where(Task.task_id == task_id))
        session.commit()
    finally:
        session.close()


def test_lease_exclusive_and_stale_takeover(mysql_backend: None) -> None:
    task = _insert_task("PENDING")
    try:
        ttl = 60
        token_a, token_b = "tok-a", "tok-b"
        session = get_session_factory("task_repo")()
        try:
            # 1) 新任务：A 抢占成功，B 同一时刻抢占失败（busy）
            assert TaskRepo.try_acquire_task_lease(session, task.task_id, token_a, ttl) == "acquired"
            assert TaskRepo.try_acquire_task_lease(session, task.task_id, token_b, ttl) == "busy"

            # 2) 心跳续租只认当前 token（旧 token 不续 A 的租约，租约仍属 A）
            TaskRepo.touch_task_heartbeat(session, task.task_id, "tok-old")
            after = TaskRepo.get_by_task_id(session, task.task_id)
            assert after is not None and after.scheduler_token == token_a

            # 3) 心跳过期 → 可被 B 接管
            session.execute(
                update(Task)
                .where(Task.task_id == task.task_id)
                .values(scheduler_heartbeat_at=datetime.now() - timedelta(seconds=ttl + 10))  # noqa: DTZ005
            )
            session.commit()
            assert TaskRepo.try_acquire_task_lease(session, task.task_id, token_b, ttl) == "acquired"
            assert TaskRepo.get_by_task_id(session, task.task_id).scheduler_token == token_b
        finally:
            session.close()
    finally:
        _delete_task(task.task_id)


def test_takeover_resets_inflight_nodes(mysql_backend: None) -> None:
    # 崩溃遗留：任务 RUNNING、心跳过期、在飞节点 RUNNING → 新 worker 接管后复位在飞节点
    task = _insert_task("RUNNING")
    try:
        session = get_session_factory("task_repo")()
        try:
            node = DagNode(
                node_id=f"{task.task_id}-n0",
                task_id=task.task_id,
                agent_type="metadata_profiling",
                status="RUNNING",
                claim_token="stale-token",
            )
            # READY 孤儿节点：调度器置 READY 后、CAS 认领前崩溃（必须一并复位，否则永久卡住）
            ready_node = DagNode(
                node_id=f"{task.task_id}-n1",
                task_id=task.task_id,
                agent_type="sql_extract",
                status="READY",
            )
            session.add_all([node, ready_node])
            session.commit()
            # 把任务心跳置过期（模拟崩溃 worker 不再续租）
            session.execute(
                update(Task)
                .where(Task.task_id == task.task_id)
                .values(
                    scheduler_token="tok-dead",
                    scheduler_heartbeat_at=datetime.now() - timedelta(seconds=200),  # noqa: DTZ005
                )
            )
            session.commit()
            assert TaskRepo.try_acquire_task_lease(session, task.task_id, "tok-c", 60) == "acquired"
            # 在飞节点被复位 PENDING 且清 token
            reset = TaskRepo.get_node(session, f"{task.task_id}-n0")
            assert reset is not None
            assert reset.status == "PENDING"
            assert reset.claim_token is None
            ready_reset = TaskRepo.get_node(session, f"{task.task_id}-n1")
            assert ready_reset is not None and ready_reset.status == "PENDING"
        finally:
            session.close()
    finally:
        _delete_task(task.task_id)


def test_reclaim_stale_tasks_requeues(mysql_backend: None) -> None:
    task = _insert_task("RUNNING")
    try:
        session = get_session_factory("task_repo")()
        try:
            session.execute(
                update(Task)
                .where(Task.task_id == task.task_id)
                .values(
                    scheduler_token="tok-d",
                    scheduler_heartbeat_at=datetime.now() - timedelta(seconds=999),  # noqa: DTZ005
                )
            )
            session.commit()
            reclaimed = TaskRepo.reclaim_stale_tasks(session, ttl=60)
            assert task.task_id in reclaimed
            after = TaskRepo.get_by_task_id(session, task.task_id)
            assert after is not None and after.status == "PENDING"
            assert after.scheduler_token is None
        finally:
            session.close()
    finally:
        _delete_task(task.task_id)


def test_pending_task_ids_and_clear_lease(mysql_backend: None) -> None:
    task = _insert_task("PENDING")
    try:
        session = get_session_factory("task_repo")()
        try:
            assert task.task_id in TaskRepo.pending_task_ids(session, limit=100)
            TaskRepo.try_acquire_task_lease(session, task.task_id, "tok-e", 60)
            TaskRepo.clear_task_lease(session, task.task_id, token="tok-e")
            after = TaskRepo.get_by_task_id(session, task.task_id)
            assert after is not None and after.scheduler_token is None
        finally:
            session.close()
    finally:
        _delete_task(task.task_id)

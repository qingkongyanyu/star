"""任务与 DAG 数据访问（task / dag_node / dag_edge）。

同步函数，服务层统一经 infra.db.to_thread 调用；多对象写入用单函数一次提交，
避免同一 Session 跨线程使用。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.constants import NodeStatus, TaskStatus
from app.models.task_repo import DagEdge, DagNode, Task

_TERMINAL_NODE_STATUS = {NodeStatus.SUCCESS.value, NodeStatus.FAILED.value, NodeStatus.BLOCKED.value}


class TaskRepo:
    """Task / DagNode / DagEdge 数据访问。"""

    @staticmethod
    def get_by_task_id(session: Session, task_id: str) -> Task | None:
        return session.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()

    @staticmethod
    def get_by_req_id(session: Session, req_id: str) -> Task | None:
        """按需求 ID（幂等键）查已提交任务。"""
        return session.execute(select(Task).where(Task.req_id == req_id)).scalar_one_or_none()

    @staticmethod
    def list_tasks(session: Session, status: str | None, page: int, page_size: int) -> tuple[list[Task], int]:
        """任务列表（状态筛选 + 分页，按 id 倒序）。"""
        stmt = select(Task)
        count_stmt = select(func.count()).select_from(Task)
        if status is not None:
            stmt = stmt.where(Task.status == status)
            count_stmt = count_stmt.where(Task.status == status)
        total = session.execute(count_stmt).scalar_one()
        items = list(
            session.execute(stmt.order_by(Task.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars()
        )
        return items, total

    @staticmethod
    def get_nodes(session: Session, task_id: str) -> list[DagNode]:
        return list(
            session.execute(select(DagNode).where(DagNode.task_id == task_id).order_by(DagNode.id)).scalars()
        )

    @staticmethod
    def get_edges(session: Session, task_id: str) -> list[DagEdge]:
        return list(
            session.execute(select(DagEdge).where(DagEdge.task_id == task_id).order_by(DagEdge.id)).scalars()
        )

    @staticmethod
    def get_node(session: Session, node_id: str) -> DagNode | None:
        return session.execute(select(DagNode).where(DagNode.node_id == node_id)).scalar_one_or_none()

    @staticmethod
    def get_node_by_output_asset_id(session: Session, asset_id: str) -> DagNode | None:
        """按产出资产定位节点（资产 → 产出节点，溯源用）。"""
        return session.execute(select(DagNode).where(DagNode.output_asset_id == asset_id)).scalar_one_or_none()

    @staticmethod
    def create_task_with_dag(session: Session, task: Task, nodes: list[DagNode], edges: list[DagEdge]) -> Task:
        """整棵 DAG 单事务落库并返回 task。"""
        session.add_all([task, *nodes, *edges])
        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def update_task_status(session: Session, task_id: str, status: str) -> Task | None:
        task = TaskRepo.get_by_task_id(session, task_id)
        if task is None:
            return None
        task.status = status
        session.commit()
        return task

    @staticmethod
    def update_node_status(session: Session, node_id: str, status: str) -> DagNode | None:
        node = TaskRepo.get_node(session, node_id)
        if node is None:
            return None
        node.status = status
        session.commit()
        return node

    @staticmethod
    def clear_node_for_rerun(session: Session, node_id: str) -> DagNode | None:
        """重跑：节点回 PENDING，清 claim_token/output_asset_id/retry_count。"""
        node = TaskRepo.get_node(session, node_id)
        if node is None:
            return None
        node.status = NodeStatus.PENDING.value
        node.claim_token = None
        node.output_asset_id = None
        node.retry_count = 0
        session.commit()
        return node

    @staticmethod
    def increment_retry_count(session: Session, node_id: str) -> None:
        """节点失败重试时递增 retry_count（供节点详情展示真实重试次数）。"""
        session.execute(
            update(DagNode).where(DagNode.node_id == node_id).values(retry_count=DagNode.retry_count + 1)
        )
        session.commit()

    @staticmethod
    def mark_task_aborted(session: Session, task_id: str) -> Task | None:
        """任务级中止：task → ABORTED，非终态节点 → BLOCKED。"""
        task = TaskRepo.get_by_task_id(session, task_id)
        if task is None:
            return None
        task.status = TaskStatus.ABORTED.value
        for node in TaskRepo.get_nodes(session, task_id):
            if node.status not in _TERMINAL_NODE_STATUS:
                node.status = NodeStatus.BLOCKED.value
        session.commit()
        return task

    # ---- 执行引擎辅助 ----

    @staticmethod
    def get_ready_nodes(session: Session, task_id: str) -> list[DagNode]:
        """返回 PENDING 且全部依赖已 SUCCESS 的节点（按 id 正序）。

        长活会话 expire_on_commit=False 会缓存身份映射，必须先 expire_all 读到
        其它会话已提交的最新节点状态。
        """
        session.expire_all()
        nodes = TaskRepo.get_nodes(session, task_id)
        edges = TaskRepo.get_edges(session, task_id)
        deps: dict[str, set[str]] = {node.node_id: set() for node in nodes}
        for edge in edges:
            deps[edge.to_node].add(edge.from_node)
        status_by_id = {node.node_id: node.status for node in nodes}
        return [
            node
            for node in nodes
            if node.status == NodeStatus.PENDING.value
            and all(status_by_id.get(dep) == NodeStatus.SUCCESS.value for dep in deps[node.node_id])
        ]

    @staticmethod
    def claim_node(session: Session, node_id: str, token: str) -> bool:
        """DB CAS 认领：READY → CLAIMED（受影响行数=1 判定唯一认领）。"""
        result = session.execute(
            update(DagNode)
            .where(DagNode.node_id == node_id, DagNode.status == NodeStatus.READY.value)
            .values(status=NodeStatus.CLAIMED.value, claim_token=token)
        )
        session.commit()
        return cast(CursorResult[Any], result).rowcount == 1

    @staticmethod
    def set_node_success(session: Session, node_id: str, output_asset_id: str) -> DagNode | None:
        """节点 → SUCCESS 并登记产出资产。"""
        node = TaskRepo.get_node(session, node_id)
        if node is None:
            return None
        node.status = NodeStatus.SUCCESS.value
        node.output_asset_id = output_asset_id
        session.commit()
        return node

    @staticmethod
    def propagate_output(session: Session, task_id: str, node_id: str, dep_ref: str) -> None:
        """把产出 asset_id@version 追加到下游节点的 input_asset_ids。"""
        downstream = [edge.to_node for edge in TaskRepo.get_edges(session, task_id) if edge.from_node == node_id]
        for target in downstream:
            node = TaskRepo.get_node(session, target)
            if node is None:
                continue
            refs = list(node.input_asset_ids or [])
            if dep_ref not in refs:
                refs.append(dep_ref)
                node.input_asset_ids = refs
        session.commit()

    @staticmethod
    def all_nodes_terminal(session: Session, task_id: str) -> bool:
        """所有节点均已进入终态（SUCCESS/FAILED/BLOCKED）。"""
        nodes = TaskRepo.get_nodes(session, task_id)
        return bool(nodes) and all(node.status in _TERMINAL_NODE_STATUS for node in nodes)

    # ---- 调度租约 / 心跳 / 崩溃自愈（v2：MySQL 为真相源，多 worker CAS）----

    @staticmethod
    def _inflight_statuses() -> tuple[str, ...]:
        """在飞节点状态（归驱动态所有，崩溃后应复位）。

        必须包含 READY：调度器会把待执行节点置 READY 再 CAS 认领，若进程在
        「置 READY」与「认领」之间崩溃，READY 会变成没人再驱动的孤儿状态
        （get_ready_nodes 只消费 PENDING），任务会永久卡住。
        """
        return (
            NodeStatus.READY.value,
            NodeStatus.CLAIMED.value,
            NodeStatus.RUNNING.value,
        )

    @staticmethod
    def _reset_inflight_nodes(session: Session, task_id: str) -> int:
        """把任务的 CLAIMED/RUNNING 在飞节点复位为 PENDING 并清 claim_token（崩溃接管）。"""
        result = session.execute(
            update(DagNode)
            .where(DagNode.task_id == task_id, DagNode.status.in_(TaskRepo._inflight_statuses()))
            .values(status=NodeStatus.PENDING.value, claim_token=None)
        )
        session.commit()
        return int(cast(CursorResult[Any], result).rowcount)

    @staticmethod
    def try_acquire_task_lease(session: Session, task_id: str, token: str, ttl: int) -> str:
        """CAS 抢占任务驱动态（RUNNING）租约，返回 acquired / busy / terminal。

        顺序：①终态 → terminal；②PENDING/PLANNING → RUNNING（新驱动）；③RUNNING 且心跳过期
        （或历史遗留无心跳）→ 接管 RUNNING 并复位其 CLAIMED/RUNNING 在飞节点；④其余 → busy。
        并发正确性依赖单语句 UPDATE rowcount（多个 worker 竞争同一任务只一个胜出）。
        """
        task = TaskRepo.get_by_task_id(session, task_id)
        if task is None or task.status in (TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.ABORTED.value):
            return "terminal"
        now = datetime.now()  # noqa: DTZ005  与库内 DATETIME 无时区统一用本地朴素时间

        fresh = session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status.in_((TaskStatus.PENDING.value, TaskStatus.PLANNING.value)))
            .values(
                status=TaskStatus.RUNNING.value,
                scheduler_token=token,
                scheduler_heartbeat_at=now,
            )
        )
        session.commit()
        if cast(CursorResult[Any], fresh).rowcount == 1:
            return "acquired"

        cutoff = now - timedelta(seconds=ttl)
        stale = session.execute(
            update(Task)
            .where(
                Task.task_id == task_id,
                Task.status == TaskStatus.RUNNING.value,
                or_(Task.scheduler_heartbeat_at.is_(None), Task.scheduler_heartbeat_at < cutoff),
            )
            .values(status=TaskStatus.RUNNING.value, scheduler_token=token, scheduler_heartbeat_at=now)
        )
        session.commit()
        if cast(CursorResult[Any], stale).rowcount == 1:
            TaskRepo._reset_inflight_nodes(session, task_id)  # 接管后复位崩溃遗留的在飞节点
            return "acquired"
        return "busy"

    @staticmethod
    def touch_task_heartbeat(session: Session, task_id: str, token: str) -> None:
        """续租任务心跳（token 校验：旧驱动态不再续租）。"""
        session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.scheduler_token == token)
            .values(scheduler_heartbeat_at=datetime.now())  # noqa: DTZ005
        )
        session.commit()

    @staticmethod
    def touch_node_heartbeat(session: Session, node_id: str, token: str) -> None:
        """续租节点心跳（token 校验）。"""
        session.execute(
            update(DagNode)
            .where(DagNode.node_id == node_id, DagNode.claim_token == token)
            .values(heartbeat_at=datetime.now())  # noqa: DTZ005
        )
        session.commit()

    @staticmethod
    def clear_task_lease(session: Session, task_id: str, token: str | None = None) -> None:
        """任务写入终态后清理租约字段（token/heartbeat 置空）。

        token 非空时加 token 校验，避免崩溃后仍在跑的旧驱动态清掉接任者的租约。
        """
        stmt = update(Task).where(Task.task_id == task_id)
        if token is not None:
            stmt = stmt.where(Task.scheduler_token == token)
        session.execute(stmt.values(scheduler_token=None, scheduler_heartbeat_at=None))
        session.commit()

    @staticmethod
    def reclaim_stale_tasks(session: Session, ttl: int) -> list[str]:
        """扫描 PLANNING/RUNNING 且心跳过期的任务 → 复位 PENDING（清租约 + 复位在飞节点）。

        供启动自愈与空闲扫描兜底：把崩溃遗留的"卡死任务"交还给可驱动集合。返回被复位任务 id。
        """
        cutoff = datetime.now() - timedelta(seconds=ttl)  # noqa: DTZ005
        rows = session.execute(
            select(Task.task_id).where(
                Task.status.in_((TaskStatus.PLANNING.value, TaskStatus.RUNNING.value)),
                or_(Task.scheduler_heartbeat_at.is_(None), Task.scheduler_heartbeat_at < cutoff),
            )
        ).scalars().all()
        for task_id in rows:
            TaskRepo._reset_inflight_nodes(session, task_id)
            session.execute(
                update(Task)
                .where(Task.task_id == task_id)
                .values(
                    status=TaskStatus.PENDING.value,
                    scheduler_token=None,
                    scheduler_heartbeat_at=None,
                )
            )
            session.commit()
        return list(rows)

    @staticmethod
    def pending_task_ids(session: Session, limit: int = 50) -> list[str]:
        """待驱动任务（PENDING），按 id 正序取前 limit 个。"""
        return list(
            session.execute(
                select(Task.task_id)
                .where(Task.status == TaskStatus.PENDING.value)
                .order_by(Task.id)
                .limit(limit)
            ).scalars()
        )

    @staticmethod
    def reset_task_for_rerun(session: Session, task_id: str) -> int:
        """失败任务重跑：task → PENDING（清租约），所有非 SUCCESS 节点复位 PENDING 清 token。

        SUCCESS 节点保留产出（血缘/引用不断）；worker 下一轮扫描到 PENDING 任务会重新驱动，
        重跑失败的节点及其下游。返回复位的节点数。
        """
        session.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status=TaskStatus.PENDING.value,
                scheduler_token=None,
                scheduler_heartbeat_at=None,
            )
        )
        result = session.execute(
            update(DagNode)
            .where(
                DagNode.task_id == task_id,
                DagNode.status != NodeStatus.SUCCESS.value,
            )
            .values(status=NodeStatus.PENDING.value, claim_token=None, output_asset_id=None)
        )
        session.commit()
        return int(cast(CursorResult[Any], result).rowcount)

    # ---- DQ→FE 分支运行时改写（on_dq_blocked）----

    @staticmethod
    def insert_node(session: Session, node: DagNode) -> DagNode:
        session.add(node)
        session.commit()
        return node

    @staticmethod
    def insert_edge(session: Session, edge: DagEdge) -> DagEdge:
        session.add(edge)
        session.commit()
        return edge

    @staticmethod
    def delete_edge(session: Session, task_id: str, from_node: str, to_node: str) -> None:
        session.execute(
            delete(DagEdge).where(
                DagEdge.task_id == task_id, DagEdge.from_node == from_node, DagEdge.to_node == to_node
            )
        )
        session.commit()

    @staticmethod
    def get_downstream_nodes(session: Session, task_id: str, node_id: str) -> list[DagNode]:
        """依赖给定节点的下游节点列表（按 id 正序）。"""
        targets = {edge.to_node for edge in TaskRepo.get_edges(session, task_id) if edge.from_node == node_id}
        return [node for node in TaskRepo.get_nodes(session, task_id) if node.node_id in targets]

    @staticmethod
    def set_node_inputs(session: Session, node_id: str, refs: list[str]) -> DagNode | None:
        """覆写节点 input_asset_ids（重接下游时重置）。"""
        node = TaskRepo.get_node(session, node_id)
        if node is None:
            return None
        node.input_asset_ids = refs
        session.commit()
        return node

    @staticmethod
    def drop_node_input_asset(session: Session, node_id: str, asset_id: str) -> DagNode | None:
        """从节点 input_asset_ids 中移除指定资产的引用，保留其它上游输入。

        DQ→FE 分支重接下游时只应摘掉「原始（未清洗）数据集」这条引用；
        整体清空会把指标口径等其它上游输入一并丢掉，导致报告丢章节。
        """
        node = TaskRepo.get_node(session, node_id)
        if node is None:
            return None
        refs = list(node.input_asset_ids or [])
        kept = [ref for ref in refs if ref.split("@", 1)[0] != asset_id]
        if len(kept) != len(refs):
            node.input_asset_ids = kept
            session.commit()
        return node

    # ---- 仪表盘聚合（只读）----

    @staticmethod
    def count_tasks_by_status(session: Session) -> dict[str, int]:
        """任务按状态计数。"""
        rows = session.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        return {status: count for status, count in rows}

    @staticmethod
    def task_trend(session: Session, days: int) -> list[tuple[str, int]]:
        """近 N 日任务创建趋势（补零，含今天）。"""
        from datetime import datetime, timedelta

        # MySQL DATETIME(3) 无时区、统一本地时间（docker TZ=Asia/Shanghai），用朴素本地时间比较
        start = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)  # noqa: DTZ005
        rows = session.execute(
            select(func.date(Task.create_time).label("d"), func.count())
            .where(Task.create_time >= start)
            .group_by(func.date(Task.create_time))
        ).all()
        counts = {d.isoformat(): count for d, count in rows}
        return [
            ((start + timedelta(days=offset)).date().isoformat(), counts.get((start + timedelta(days=offset)).date().isoformat(), 0))
            for offset in range(days)
        ]

    @staticmethod
    def count_edges_by_type(session: Session) -> dict[str, int]:
        """DAG 边按类型计数（quality_block 为 DQ 阻断代理指标）。"""
        rows = session.execute(select(DagEdge.edge_type, func.count()).group_by(DagEdge.edge_type)).all()
        return {edge_type: count for edge_type, count in rows}

    @staticmethod
    def count_nodes_by_agent_type(session: Session) -> dict[str, dict[str, int]]:
        """节点按 agent_type 聚合 {success, failed, blocked, total}。"""
        rows = session.execute(
            select(DagNode.agent_type, DagNode.status, func.count()).group_by(DagNode.agent_type, DagNode.status)
        ).all()
        out: dict[str, dict[str, int]] = {}
        for agent_type, status, count in rows:
            entry = out.setdefault(agent_type, {"success": 0, "failed": 0, "blocked": 0, "total": 0})
            entry["total"] += count
            if status == NodeStatus.SUCCESS.value:
                entry["success"] += count
            elif status == NodeStatus.FAILED.value:
                entry["failed"] += count
            elif status == NodeStatus.BLOCKED.value:
                entry["blocked"] += count
        return out

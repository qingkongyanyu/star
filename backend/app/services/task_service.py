"""任务用例：下发 / 列表 / 详情 / DAG 查询 / 节点详情 / 人工干预。

创建任务后触发后台执行（进程内调度器 v1 降级，无 Redis/Kafka）。
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.agents.planner import PlannerAgent
from app.bus.event_hub import hub
from app.core.constants import NodeStatus, Role, TaskStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.infra.db import get_session_factory, to_thread
from app.models.task_repo import Task
from app.repositories.artifact_repo import ArtifactRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.data_source_repo import DataSourceRepo
from app.repositories.task_repo import TaskRepo
from app.schemas.asset import AssetOut
from app.schemas.memory import MemoryHitOut
from app.schemas.task import (
    AuditLogOut,
    DAGEdgeOut,
    DAGNodeOut,
    DAGOut,
    NodeDetailOut,
    Page,
    TaskDetailOut,
    TaskOut,
)
from app.services.dag_service import AGENT_LABELS
from app.services.memory_service import MemoryService

_TERMINAL_TASK_STATUS = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.ABORTED.value}


def _gen_task_id() -> str:
    """业务 task_id：t + hex8（VARCHAR64 全局唯一）。"""
    return f"t{uuid.uuid4().hex[:8]}"


class TaskService:
    """任务服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create_task(
        self,
        *,
        message: str,
        task_type: str,
        priority: int,
        requester: str,
        req_id: str,
        data_source_id: int | None = None,
    ) -> DAGOut:
        """下发任务；req_id（Idempotency-Key）重复提交返回原任务 DAG。

        分析任务必须绑定真实数据源：无数据源的任务要么胡编、要么失败，
        这里在源头拦截并给出清晰引导，保证下发的任务都有机会真正成功。
        """
        if data_source_id is None:
            raise ValidationError("分析任务必须选择数据源，请先在「数据源接入」连接并选择真实数据源")
        ds = await to_thread(DataSourceRepo.get_by_id, self._session, data_source_id)
        if ds is None:
            raise NotFoundError(f"数据源不存在: {data_source_id}")
        # v2.2：SQL 类（mysql/postgres/olap）与记录型（file/api/stream）均支持真实取数出报告；
        # 不可读的数据源在执行期由取数 Agent 诚实 no_data，不再在源头拦截。
        existing = await to_thread(TaskRepo.get_by_req_id, self._session, req_id)
        if existing is not None:
            return await self._build_dag(existing.task_id)

        task = Task(
            task_id=_gen_task_id(),
            req_id=req_id,
            requester=requester,
            task_type=task_type,
            requirement=message,
            status=TaskStatus.PENDING.value,
            priority=priority,
            data_source_id=data_source_id,
        )
        # Planner 按需求+真实结构+知识库+记忆拆解（规则为主，LLM 仅补槽位；不可用 → 确定性模板兜底）
        plan = await PlannerAgent.plan(
            self._session,
            task=task,
            data_source={"id": ds.id, "type": ds.type, "name": ds.name, "config": ds.config},
        )
        # 结构化计划随任务落库：指标定义/取数/可视化/洞察/报告/终审全链消费
        task.plan_json = plan.get("analysis_plan")
        nodes, edges = plan["nodes"], plan["edges"]
        await to_thread(TaskRepo.create_task_with_dag, self._session, task, nodes, edges)
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=requester,
            action="task.created",
            detail={
                "task_id": task.task_id,
                "req_id": req_id,
                "requester": requester,
                "task_type": task_type,
                "message": message,
                "plan_source": plan["plan_source"],
                "plan_summary": plan.get("plan_summary", ""),
            },
        )
        dag = await self._build_dag(task.task_id)
        # 广播任务创建事件（WS 聊天流；node_count 供 Planner 在群里汇报）
        await hub.publish(
            "task.created",
            {
                "task_id": task.task_id,
                "requester": requester,
                "message": message,
                "node_count": len(dag.nodes),
            },
        )
        # Planner 真实拆解结果汇报（plan_source 诚实标注：hybrid / llm / template 兜底）
        await hub.publish(
            "planner.ready",
            {
                "task_id": task.task_id,
                "plan_source": plan["plan_source"],
                "plan_summary": plan.get("plan_summary", ""),
                "node_count": len(nodes),
            },
        )
        # 任务保持 PENDING，由常驻 SchedulerWorker（main lifespan）凭 DB 租约接管驱动。
        # 不再用 asyncio.create_task：进程内 fire-and-forget 在重启/多 worker 下会丢任务。
        return dag

    async def list_tasks(self, status: str | None, page: int, page_size: int) -> Page[TaskOut]:
        items, total = await to_thread(TaskRepo.list_tasks, self._session, status, page, page_size)
        return Page(items=[TaskOut.model_validate(task) for task in items], total=total, page=page)

    async def get_task_detail(self, task_id: str) -> TaskDetailOut:
        task = await self._get_task(task_id)
        dag = await self._build_dag(task_id)
        return TaskDetailOut(task=TaskOut.model_validate(task), dag=dag)

    async def get_dag(self, task_id: str) -> DAGOut:
        return await self._build_dag(task_id)

    async def get_node_detail(self, node_id: str) -> NodeDetailOut:
        node = await to_thread(TaskRepo.get_node, self._session, node_id)
        if node is None:
            raise NotFoundError(f"节点不存在: {node_id}")
        logs = await to_thread(AuditRepo.list_by_node, self._session, node_id)
        return NodeDetailOut(
            node_id=node.node_id,
            task_id=node.task_id,
            agent_type=node.agent_type,
            status=node.status,
            instruction=node.instruction,
            dep_ids=node.dep_ids,
            input_asset_ids=node.input_asset_ids,
            output_asset_id=node.output_asset_id,
            retry_count=node.retry_count,
            deadline=node.deadline,
            create_time=node.create_time,
            update_time=node.update_time,
            logs=[AuditLogOut.model_validate(log) for log in logs],
        )

    async def get_task_assets(self, task_id: str) -> list[AssetOut]:
        """任务工作台：按 DAG 节点产出顺序返回该任务的全部资产。"""
        await self._get_task(task_id)
        nodes = await to_thread(TaskRepo.get_nodes, self._session, task_id)
        asset_ids = [n.output_asset_id for n in nodes if n.output_asset_id]
        if not asset_ids:
            return []
        artifact_session = get_session_factory("artifact_repo")()
        try:
            assets = await to_thread(ArtifactRepo.get_by_ids, artifact_session, asset_ids)
            latest = await to_thread(ArtifactRepo.get_latest_version_numbers, artifact_session, asset_ids)
            out: list[AssetOut] = []
            for a in assets:
                item = AssetOut.model_validate(a)
                item.latest_version = latest.get(a.asset_id)
                out.append(item)
            return out
        finally:
            artifact_session.close()

    async def get_task_memory(self, task_id: str, role: str = "admin") -> list[MemoryHitOut]:
        """任务工作台：该任务（source_task）产生的记忆；非管理员只见 collective，避免越权读私有记忆。"""
        await self._get_task(task_id)
        artifact_session = get_session_factory("artifact_repo")()
        try:
            scope = "all" if role == Role.ADMIN.value else "collective"
            return await MemoryService(self._session, artifact_session).search(
                query="", scope=scope, agent_id=None, top_k=200, task_id=task_id
            )
        finally:
            artifact_session.close()

    async def override(self, task_id: str, node_id: str, action: str, actor: str) -> None:
        """人工干预：block/allow/rerun/abort（abort 为任务级）。"""
        node = await to_thread(TaskRepo.get_node, self._session, node_id)
        if node is None or node.task_id != task_id:
            raise NotFoundError("节点不存在或不属于该任务")
        task = await self._get_task(task_id)

        if action == "abort":
            if task.status == TaskStatus.ABORTED.value:
                raise ConflictError("任务已中止")
            await to_thread(TaskRepo.mark_task_aborted, self._session, task_id)
            await to_thread(
                AuditRepo.append,
                self._session,
                actor=actor,
                action="task.aborted",
                node_id=node_id,
                detail={"task_id": task_id},
            )
            return

        if action == "rerun":
            # v2：失败任务整链重跑（复位失败/阻断节点 + task 回 PENDING），由 Worker 重新驱动；
            # 运行中任务的重跑会与在飞驱动竞态，禁用引导用户等待终态。
            if task.status != TaskStatus.FAILED.value:
                raise ConflictError(f"仅失败任务支持重跑（当前 {task.status}）；请等待任务终态后再重跑")
            await to_thread(TaskRepo.reset_task_for_rerun, self._session, task_id)
            await to_thread(
                AuditRepo.append,
                self._session,
                actor=actor,
                action="task.rerun",
                node_id=node_id,
                detail={"task_id": task_id, "reason": "人工重跑失败任务"},
            )
            return

        if task.status in _TERMINAL_TASK_STATUS:
            raise ConflictError(f"任务已终态（{task.status}），不可干预")

        if action == "block":
            await to_thread(TaskRepo.update_node_status, self._session, node_id, NodeStatus.BLOCKED.value)
            audit_action = "node.blocked"
        elif action == "allow":
            # 置回 PENDING：调度器 get_ready_nodes 只消费 PENDING，置 READY 会成孤儿节点永不被执行
            await to_thread(TaskRepo.update_node_status, self._session, node_id, NodeStatus.PENDING.value)
            audit_action = "node.allowed"
        else:
            raise ConflictError(f"未知干预动作: {action}")
        await to_thread(
            AuditRepo.append,
            self._session,
            actor=actor,
            action=audit_action,
            node_id=node_id,
            detail={"task_id": task_id},
        )

    async def _get_task(self, task_id: str) -> Task:
        task = await to_thread(TaskRepo.get_by_task_id, self._session, task_id)
        if task is None:
            raise NotFoundError(f"任务不存在: {task_id}")
        return task

    async def _build_dag(self, task_id: str) -> DAGOut:
        task = await self._get_task(task_id)
        nodes = await to_thread(TaskRepo.get_nodes, self._session, task_id)
        edges = await to_thread(TaskRepo.get_edges, self._session, task_id)
        return DAGOut(
            task_id=task.task_id,
            status=task.status,
            nodes=[
                DAGNodeOut(
                    node_id=node.node_id,
                    agent_type=node.agent_type,
                    status=node.status,
                    label=AGENT_LABELS.get(node.agent_type, node.agent_type),
                    output_asset_id=node.output_asset_id,
                    instruction=node.instruction,
                )
                for node in nodes
            ],
            edges=[
                DAGEdgeOut(from_node=edge.from_node, to=edge.to_node, edge_type=edge.edge_type)
                for edge in edges
            ],
        )

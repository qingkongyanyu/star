"""执行调度器：任务级租约驱动的 DAG 推进（v2：MySQL 为真相源，多 worker CAS）。

v1 是每任务一个 in-process while（`asyncio.create_task(execute_task)`），重启即丢。
v2 改为：常驻 `SchedulerWorker`（scheduler_worker.py）持**任务级租约**调用
`execute_task(task_id, token, lease_ttl)`：
- 租约 token/heartbeat 存 task.scheduler_token / scheduler_heartbeat_at；
- 执行全程后台心跳续租，崩溃后由其它 worker 的 `reclaim_stale_tasks` / 空闲扫描接管
  （`try_acquire_task_lease` CAS：PENDING/PLANNING→RUNNING；RUNNING 心跳过期→接管并复位在飞节点）；
- 单节点执行仍走 `AgentBase.run_node`（DB CAS 认领），事件经进程内 EventHub 广播给 WS。

DQ→FE 分支：quality_check 产出 dq_report 判 no_data（硬阻断失败）/ blocked（数据质量问题）/
clean（通过但 issues 非空，v2.1 告警也前置清洗）→ 后两者插 feature_engineer 节点并重接下游
（下游只消费清洗后数据集）；FE 复检未通过 → 失败。终审 review verdict=rejected
→ 自动修订报告并复评（最多 _MAX_REVIEW_REVISIONS 轮）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from sqlalchemy.orm import Session

from app.agents import get_agent_for
from app.agents.memory_keeper import MemoryKeeperAgent
from app.bus.event_hub import hub
from app.core.constants import NodeStatus, TaskStatus
from app.infra.db import get_session_factory, to_thread
from app.models.task_repo import DagEdge, DagNode
from app.repositories.artifact_repo import ArtifactRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.task_repo import TaskRepo
from app.services import arbiter
from app.services.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

# 节点终态：失败/失败/阻断后不再流转
_TERMINAL_NODE_STATUS = {NodeStatus.SUCCESS.value, NodeStatus.FAILED.value, NodeStatus.BLOCKED.value}


class SchedulerService:
    # 终审否决后的自动修订轮数上限（修订报告→复评）
    _MAX_REVIEW_REVISIONS = 2

    @staticmethod
    async def execute_task(task_id: str, *, token: str, lease_ttl: int) -> None:
        """把任务 DAG 逐节点推进到终态。

        前置：调用方须已通过 `TaskRepo.try_acquire_task_lease` 持有该任务租约（token）。
        执行期间后台心跳续租；离开时若任务仍归我方租约则清理租约字段。异常全部捕获，
        避免 unhandled 协程。崩溃语义为 at-least-once（心跳过期被接管可能重跑在飞节点）。
        """
        session = get_session_factory("task_repo")()
        stop = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        try:
            task = await to_thread(TaskRepo.get_by_task_id, session, task_id)
            if task is None or task.scheduler_token != token:
                return  # 租约非我方（已被接管/已终态），放弃
            if task.status in {TaskStatus.SUCCESS.value, TaskStatus.ABORTED.value}:
                return
            await to_thread(TaskRepo.update_task_status, session, task_id, TaskStatus.RUNNING.value)
            heartbeat = asyncio.create_task(
                SchedulerService._heartbeat_loop(task_id, token, lease_ttl, stop)
            )

            while True:
                ready = await to_thread(TaskRepo.get_ready_nodes, session, task_id)
                if not ready:
                    break
                for node in ready:
                    await to_thread(TaskRepo.update_node_status, session, node.node_id, NodeStatus.READY.value)
                    agent_cls = get_agent_for(node.agent_type)
                    if agent_cls is None:
                        logger.warning("未实现的能力标签 %s，节点 %s 标记失败", node.agent_type, node.node_id)
                        await to_thread(TaskRepo.update_node_status, session, node.node_id, NodeStatus.FAILED.value)
                        continue
                    # 节点开始执行（WS 聊天流展示各 Agent 登场；诚实按真实开始广播）
                    await hub.publish(
                        "node.started",
                        {"task_id": task_id, "node_id": node.node_id, "agent_type": node.agent_type},
                    )
                    asset_id = await agent_cls().run_node(node.node_id, task)

                    # 节点执行失败（无产出资产）→ 自动重试一次（临时性失败自愈），仍失败才广播 node.failed
                    if not asset_id:
                        logger.warning("节点 %s（%s）执行失败，自动重试一次", node.node_id, node.agent_type)
                        await to_thread(TaskRepo.increment_retry_count, session, node.node_id)
                        await to_thread(TaskRepo.update_node_status, session, node.node_id, NodeStatus.READY.value)
                        asset_id = await agent_cls().run_node(node.node_id, task)
                        if not asset_id:
                            await hub.publish(
                                "node.failed",
                                {"task_id": task_id, "node_id": node.node_id, "agent_type": node.agent_type},
                            )
                            continue

                    # DQ：先判定「无数据」（硬阻断，不做特征工程清洗，直接失败并带原因）
                    if node.agent_type == "quality_check":
                        dq_state = await SchedulerService._dq_state(asset_id)
                        if dq_state == "no_data":
                            await SchedulerService._fail_task(
                                session, task_id,
                                audit_action="dq.no_data",
                                reason="上游无数据集，无法进行数据分析",
                                extra={"node_id": node.node_id, "asset_id": asset_id},
                            )
                            await hub.publish(
                                "dq.no_data",
                                {"task_id": task_id, "node_id": node.node_id, "asset_id": asset_id, "reason": "上游无数据集"},
                            )
                            return
                        # DQ 阻断（真实数据质量问题）或告警级问题非空 → 特征工程分支（插 FE + 重接下游）。
                        # v2.1：不再只在 blocked 才清洗——只要 issues 非空（clean）也前置清洗再出图/报告。
                        if dq_state in ("blocked", "clean"):
                            await SchedulerService._on_dq_fe(
                                session, task_id, node, dq_asset_id=asset_id, clean=(dq_state == "clean")
                            )
                            continue

                    # FE 产出后复检；未通过 → 升级人工（v1 直接 FAILED）
                    if node.agent_type == "feature_engineer" and not await SchedulerService._recheck(asset_id):
                        await SchedulerService._fail_task(
                            session, task_id,
                            audit_action="dq.recheck_failed",
                            reason="特征集复检未通过",
                            extra={"asset_id": asset_id},
                        )
                        return

                    # 终审：verdict=rejected → 进入自动修订循环（修订报告→复评），不再直接放弃
                    if node.agent_type == "review":
                        verdict = await SchedulerService._review_verdict(asset_id)
                        if verdict == "rejected":
                            await SchedulerService._on_review_rejected(
                                session, task_id, node, review_asset_id=asset_id
                            )
                            continue

                    dep_ref = await SchedulerService._latest_ref(asset_id)
                    await to_thread(TaskRepo.propagate_output, session, task_id, node.node_id, dep_ref)
                    title = await SchedulerService._asset_title(asset_id)
                    await hub.publish(
                        "artifact.published",
                        {
                            "task_id": task_id,
                            "node_id": node.node_id,
                            "asset_id": asset_id,
                            "version": int(dep_ref.split("@")[1]),
                            "agent_type": node.agent_type,
                            "title": title or "",
                        },
                    )
                    # 每推进一个节点续一次任务租约（额外兜底，心跳后台也在续）
                    await to_thread(TaskRepo.touch_task_heartbeat, session, task_id, token)

            # 循环结束后：若任务已被人工中止（ABORTED）或 _fail_task 已判定失败（FAILED），
            # 保留已写入的终态，不被 _final_status 覆盖；租约归我方则清理。
            current = await to_thread(TaskRepo.get_by_task_id, session, task_id)
            if current is not None and current.status in {TaskStatus.ABORTED.value, TaskStatus.FAILED.value}:
                logger.info("任务 %s 已终态（%s），跳过终态写入", task_id, current.status)
                await to_thread(TaskRepo.clear_task_lease, session, task_id, token)
                return
            final = await SchedulerService._final_status(session, task_id)
            await to_thread(TaskRepo.update_task_status, session, task_id, final)
            await to_thread(TaskRepo.clear_task_lease, session, task_id, token)
            reason = "任务存在失败节点，详情见任务工作台" if final == TaskStatus.FAILED.value else None
            detail: dict[str, object] = {"task_id": task_id, "status": final}
            if reason:
                detail["reason"] = reason
            await to_thread(AuditRepo.append, session, actor="scheduler", action="task.completed", detail=detail)
            logger.info("任务 %s 执行完成: %s%s", task_id, final, f"（{reason}）" if reason else "")
            completed_data: dict[str, object] = {"task_id": task_id, "status": final}
            if reason:
                completed_data["reason"] = reason
            await hub.publish("task.completed", completed_data)
            # 任务成功 → 触发 MemoryKeeper 复盘（失败不影响任务状态，run_retrospective 内部兜底）
            if final == TaskStatus.SUCCESS.value:
                await MemoryKeeperAgent().run_retrospective(task_id)
        except Exception:
            logger.exception("任务 %s 执行异常，标记 FAILED", task_id)
            await to_thread(TaskRepo.update_task_status, session, task_id, TaskStatus.FAILED.value)
            await to_thread(TaskRepo.clear_task_lease, session, task_id, token)
        finally:
            stop.set()
            if heartbeat is not None:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            session.close()

    @staticmethod
    async def _heartbeat_loop(task_id: str, token: str, lease_ttl: int, stop: asyncio.Event) -> None:
        """后台续租任务心跳（独立 session，token 校验；随 execute_task 结束被取消）。"""
        interval = max(5.0, lease_ttl / 4)
        while not stop.is_set():
            try:
                await asyncio.sleep(interval)
                if stop.is_set():
                    break
                hs = get_session_factory("task_repo")()
                try:
                    await to_thread(TaskRepo.touch_task_heartbeat, hs, task_id, token)
                finally:
                    hs.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("任务 %s 心跳续租失败（可忽略）", task_id, exc_info=True)

    @staticmethod
    async def _asset_title(asset_id: str) -> str:
        """取资产标题（供聊天事件展示各 Agent 产出摘要）。"""
        artifact_session = get_session_factory("artifact_repo")()
        try:
            asset = await to_thread(ArtifactRepo.get_by_asset_id, artifact_session, asset_id)
            return asset.title if asset is not None else ""
        finally:
            artifact_session.close()

    @staticmethod
    async def _latest_ref(asset_id: str) -> str:
        """取资产最新版本引用 asset_id@version（供下游 input_asset_ids 与血缘）。"""
        artifact_session = get_session_factory("artifact_repo")()
        try:
            latest = await to_thread(ArtifactRepo.get_latest_version, artifact_session, asset_id)
            version = latest.version if latest is not None else 1
            return f"{asset_id}@{version}"
        finally:
            artifact_session.close()

    @staticmethod
    async def _final_status(session: Session, task_id: str) -> str:
        session.expire_all()  # 读最新节点状态（可能被 Agent 会话更新过）
        nodes = await to_thread(TaskRepo.get_nodes, session, task_id)
        if nodes and all(node.status == NodeStatus.SUCCESS.value for node in nodes):
            return TaskStatus.SUCCESS.value
        return TaskStatus.FAILED.value

    # ---- 问题上报与终态 ----

    @staticmethod
    async def _fail_task(
        session: Session, task_id: str, *, audit_action: str, reason: str, extra: dict[str, object] | None = None
    ) -> None:
        """把任务标记 FAILED（带审计原因），剩余非终态节点置 BLOCKED，并广播 task.completed(FAILED, reason)。"""
        # 长活会话可能持有旧事务快照（读到 Agent 会话已提交前的旧状态），先 rollback + expire 强制用新快照
        session.rollback()
        session.expire_all()
        for node in await to_thread(TaskRepo.get_nodes, session, task_id):
            if node.status not in _TERMINAL_NODE_STATUS:
                await to_thread(TaskRepo.update_node_status, session, node.node_id, NodeStatus.BLOCKED.value)
        await to_thread(TaskRepo.update_task_status, session, task_id, TaskStatus.FAILED.value)
        detail: dict[str, object] = {"task_id": task_id, "reason": reason}
        if extra:
            detail.update(extra)
        await to_thread(AuditRepo.append, session, actor="scheduler", action=audit_action, detail=detail)
        await hub.publish("task.completed", {"task_id": task_id, "status": TaskStatus.FAILED.value, "reason": reason})
        logger.info("任务 %s 失败（%s）：%s", task_id, audit_action, reason)

    @staticmethod
    async def _dq_state(asset_id: str) -> str:
        """读 dq_report 统一判定 no_data / blocked / clean / pass。判据收敛到 arbiter.dq_state。"""
        artifact_session = get_session_factory("artifact_repo")()
        try:
            content = await ArtifactService(artifact_session).fetch_content(asset_id)
            return arbiter.dq_state(json.loads(content))
        except Exception:
            logger.warning("读取 dq_report %s 失败，按 pass 处理", asset_id, exc_info=True)
            return "pass"
        finally:
            artifact_session.close()

    @staticmethod
    async def _review_verdict(asset_id: str) -> str | None:
        """读取终审资产 verdict（approved/rejected）。判据收敛到 arbiter.review_verdict。"""
        artifact_session = get_session_factory("artifact_repo")()
        try:
            content = await ArtifactService(artifact_session).fetch_content(asset_id)
            return arbiter.review_verdict(json.loads(content))
        except Exception:
            logger.warning("读取 review %s 失败，按无裁决处理", asset_id, exc_info=True)
            return None
        finally:
            artifact_session.close()

    @staticmethod
    async def _on_review_rejected(
        session: Session, task_id: str, review_node: DagNode, *, review_asset_id: str
    ) -> None:
        """终审否决 → 自动修订报告并复评（最多 _MAX_REVIEW_REVISIONS 轮），修订耗尽才失败。

        每轮插入 `{task_id}-rev{rev_no}-report` 与 `{task_id}-rev{rev_no}-review`；修订报告
        的输入 = **上一轮修订报告**（rev1 输入 V1，rev2 输入 rev1 而非 V1），保证修订逐轮累积。
        """
        session.expire_all()  # 读最新节点状态
        nodes = await to_thread(TaskRepo.get_nodes, session, task_id)
        revisions = [n for n in nodes if n.node_id.startswith(f"{task_id}-rev") and n.agent_type == "report_writing"]
        rev_no = len(revisions) + 1
        if rev_no > SchedulerService._MAX_REVIEW_REVISIONS:
            await SchedulerService._fail_task(
                session, task_id,
                audit_action="review.rejected",
                reason=f"终审连续否决（已修订 {rev_no - 1} 轮仍未通过），报告不可交付",
                extra={"review_asset_id": review_asset_id},
            )
            await hub.publish(
                "review.rejected",
                {"task_id": task_id, "node_id": review_node.node_id, "asset_id": review_asset_id, "reason": "多次修订后仍被终审否决"},
            )
            return

        # 取"最新一轮可修订报告"：本轮新否决前，最新报告 = 原始 V1 或最近一轮 rev-report
        latest_report_node = SchedulerService._latest_report_node(nodes, task_id)
        if latest_report_node is None or latest_report_node.output_asset_id is None:
            await SchedulerService._fail_task(
                session, task_id,
                audit_action="review.rejected",
                reason="终审否决报告，且无已产出的报告可修订",
                extra={"review_asset_id": review_asset_id},
            )
            return

        report_ref = await SchedulerService._latest_ref(latest_report_node.output_asset_id)
        review_ref = await SchedulerService._latest_ref(review_asset_id)

        rev_report_id = f"{task_id}-rev{rev_no}-report"
        rev_review_id = f"{task_id}-rev{rev_no}-review"
        # 修订报告节点：输入 = 最新报告（V1 或上一轮修订稿）+ 最新评审意见 → report_scribe 据此修订
        await to_thread(
            TaskRepo.insert_node, session,
            DagNode(
                node_id=rev_report_id, task_id=task_id, agent_type="report_writing",
                status=NodeStatus.PENDING.value, input_asset_ids=[report_ref, review_ref],
            ),
        )
        # 复评节点：消费修订后报告
        await to_thread(
            TaskRepo.insert_node, session,
            DagNode(node_id=rev_review_id, task_id=task_id, agent_type="review",
                    status=NodeStatus.PENDING.value, input_asset_ids=[]),
        )
        await to_thread(
            TaskRepo.insert_edge, session,
            DagEdge(task_id=task_id, from_node=latest_report_node.node_id, to_node=rev_report_id, edge_type="data_dep"),
        )
        await to_thread(
            TaskRepo.insert_edge, session,
            DagEdge(task_id=task_id, from_node=review_node.node_id, to_node=rev_report_id, edge_type="review_reject"),
        )
        await to_thread(
            TaskRepo.insert_edge, session,
            DagEdge(task_id=task_id, from_node=rev_report_id, to_node=rev_review_id, edge_type="data_dep"),
        )

        await to_thread(
            AuditRepo.append,
            session,
            actor="scheduler",
            action="review.revision",
            detail={"task_id": task_id, "round": rev_no, "reason": "终审否决，进入自动修订"},
        )
        await hub.publish(
            "review.rejected",
            {"task_id": task_id, "node_id": review_node.node_id, "asset_id": review_asset_id, "reason": f"终审否决，进入第 {rev_no} 轮自动修订"},
        )
        logger.info("任务 %s 终审否决，进入第 %d 轮自动修订", task_id, rev_no)

    @staticmethod
    def _latest_report_node(nodes: list[DagNode], task_id: str) -> DagNode | None:
        """取最新可修订报告节点（P0-3 修订回链修复）。薄包装 arbiter.latest_report_node。"""
        return arbiter.latest_report_node(nodes, task_id)

    # ---- DQ→FE 分支 ----

    @staticmethod
    async def _on_dq_fe(
        session: Session, task_id: str, quality_node: DagNode, *, dq_asset_id: str, clean: bool = False
    ) -> None:
        """新增 feature_engineer 节点 + 重接下游（DQ 阻断或告警级问题前置清洗，spec spawn_fe_task）。"""
        session.expire_all()  # 读最新节点状态
        nodes = await to_thread(TaskRepo.get_nodes, session, task_id)
        extract = next((n for n in nodes if n.agent_type == "sql_extract" and n.output_asset_id), None)
        if extract is None:
            logger.warning("任务 %s DQ 阻断但找不到 sql_extract 上游，跳过 FE 分支", task_id)
            return

        dataset_asset_id = extract.output_asset_id
        if dataset_asset_id is None:
            return
        dataset_ref = await SchedulerService._latest_ref(dataset_asset_id)
        dq_ref = await SchedulerService._latest_ref(dq_asset_id)
        downstream = await to_thread(TaskRepo.get_downstream_nodes, session, task_id, quality_node.node_id)

        fe_node_id = f"{task_id}-fe"
        fe_node = DagNode(
            node_id=fe_node_id,
            task_id=task_id,
            agent_type="feature_engineer",
            status=NodeStatus.PENDING.value,
            input_asset_ids=[dataset_ref, dq_ref],
        )
        await to_thread(TaskRepo.insert_node, session, fe_node)
        await to_thread(
            TaskRepo.insert_edge, session,
            DagEdge(task_id=task_id, from_node=extract.node_id, to_node=fe_node_id, edge_type="data_dep"),
        )
        await to_thread(
            TaskRepo.insert_edge, session,
            DagEdge(task_id=task_id, from_node=quality_node.node_id, to_node=fe_node_id, edge_type="quality_block"),
        )
        # P2.a：阻断时摘除 sql→(visualization/report_writing) 直达边，让下游只消费清洗后特征集
        for n in nodes:
            if n.agent_type in {"visualization", "report_writing"}:
                await to_thread(TaskRepo.delete_edge, session, task_id, extract.node_id, n.node_id)
        for target in downstream:
            await to_thread(TaskRepo.delete_edge, session, task_id, quality_node.node_id, target.node_id)
            await to_thread(
                TaskRepo.insert_edge, session,
                DagEdge(task_id=task_id, from_node=fe_node_id, to_node=target.node_id, edge_type="data_dep"),
            )
            # 只摘除「原始未清洗数据集」这条上游引用；整体清空会连指标口径等合法输入一起丢掉
            await to_thread(TaskRepo.drop_node_input_asset, session, target.node_id, dataset_asset_id)

        action = "dq.fe" if clean else "dq.blocked"
        reason = "存在数据质量告警，已前置特征清洗" if clean else "数据质量问题"
        await to_thread(
            AuditRepo.append,
            session,
            actor="scheduler",
            action=action,
            detail={"task_id": task_id, "node_id": quality_node.node_id, "asset_id": dq_asset_id, "clean": clean},
        )
        await hub.publish(
            action,
            {"task_id": task_id, "node_id": quality_node.node_id, "asset_id": dq_asset_id, "reason": reason},
        )
        logger.info("任务 %s %s，插入特征工程节点 %s", task_id, "告警级问题" if clean else "DQ 阻断", fe_node_id)

    @staticmethod
    async def _recheck(asset_id: str) -> bool:
        """复检特征集：未通过则失败。判据收敛到 arbiter.fe_recheck_passes。"""
        artifact_session = get_session_factory("artifact_repo")()
        try:
            content = await ArtifactService(artifact_session).fetch_content(asset_id)
            return arbiter.fe_recheck_passes(json.loads(content))
        except Exception:  # noqa: BLE001  读取失败按放行
            return True
        finally:
            artifact_session.close()

"""WebSocket 端点：/ws/chat（docs/02-后端/04 §十 协同）。

- 握手：?token=<JWT>（decode_access_token，失败 close 4401）
- 下行：{ type, data } 事件（镜像 task.events，进程内 EventHub 广播）
- 上行：task.submit（→ POST /tasks 同逻辑）/ agent.inquiry（→ 回执）
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.bus.event_hub import hub
from app.core.exceptions import AppError, UnauthorizedError
from app.core.request_id import generate_request_id
from app.core.security import decode_access_token
from app.infra.db import get_session_factory
from app.repositories.user_repo import UserRepo
from app.services.agent_service import AgentService
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """协同群聊 WebSocket：首帧鉴权（首条消息携带 token），订阅事件流，处理上行消息。

    鉴权优先从首帧 {type:"auth", token} 取；兼容旧版 ?token=<JWT> 查询参数（不推荐，会进日志）。
    """
    await websocket.accept()

    # 首帧鉴权：优先消息内 token，其次 query 参数（向后兼容）
    token = websocket.query_params.get("token", "")
    first_message: str | None = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            frame = {}
        if isinstance(frame, dict) and frame.get("type") == "auth" and frame.get("token"):
            token = str(frame["token"])
            first_message = None  # auth 帧不参与业务处理
        else:
            first_message = raw  # 非 auth 帧视为业务消息，鉴权后处理
    except TimeoutError:
        await websocket.close(code=4401)
        return
    except WebSocketDisconnect:
        return

    try:
        payload = decode_access_token(token)
        user_id = int(str(payload.get("sub") or ""))
    except (UnauthorizedError, ValueError, TypeError):
        await websocket.close(code=4401)
        return

    queue = await hub.subscribe()
    sender = asyncio.create_task(_send_loop(websocket, queue))
    try:
        if first_message is not None:
            await _handle_client_message(websocket, user_id, first_message)
        while True:
            text = await websocket.receive_text()
            await _handle_client_message(websocket, user_id, text)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, Exception):  # noqa: BLE001  发送循环随连接关闭终止
            logger.debug("WS 发送循环随连接关闭终止")
        hub.unsubscribe(queue)


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        message = await queue.get()
        await websocket.send_json(message)


async def _handle_client_message(websocket: WebSocket, user_id: int, text: str) -> None:
    """处理上行：task.submit / agent.inquiry。

    单条消息的异常就地捕获并回错误消息，不向上抛出（否则整条 WS 连接会被断开）。
    """
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return
    msg_type = msg.get("type")
    session = get_session_factory("task_repo")()
    try:
        user = await UserRepo.get_by_id(session, user_id)
        requester = user.username if user is not None else f"user-{user_id}"
        try:
            if msg_type == "task.submit":
                message = str(msg.get("message") or "")
                ds_id = msg.get("data_source_id")
                if message:
                    await TaskService(session).create_task(
                        message=message,
                        task_type="analysis",
                        priority=5,
                        requester=requester,
                        req_id=generate_request_id(),
                        data_source_id=int(ds_id) if isinstance(ds_id, int) or (isinstance(ds_id, str) and ds_id.isdigit()) else None,
                    )
            elif msg_type == "agent.inquiry":
                agent_id = str(msg.get("target") or "")
                inquiry = str(msg.get("message") or "")
                if agent_id:
                    resp = await AgentService(session).inquiry(agent_id, inquiry, actor=requester)
                    await websocket.send_json(
                        {"type": "agent.inquiry.reply", "data": {"agent_id": agent_id, "reply": resp.reply}}
                    )
        except AppError as exc:
            logger.warning("WS 上行 %s 业务异常: %s", msg_type, exc.message)
            await websocket.send_json(
                {"type": "error", "data": {"code": exc.code, "message": exc.message, "message_type": msg_type}}
            )
        except Exception:
            logger.exception("WS 上行 %s 处理异常", msg_type)
            await websocket.send_json(
                {"type": "error", "data": {"code": "E_INTERNAL", "message": "处理失败，请稍后再试", "message_type": msg_type}}
            )
    finally:
        session.close()

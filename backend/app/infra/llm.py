"""LLM 抽象（OpenAI 兼容），含 MockLLM 开发兜底。

所有 Agent 只经此层调用模型；``get_llm()`` 在未配置有效 key 或 debug 模式下
返回 MockLLM，保证本地/CI 不依赖外部服务。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, cast

from app.core.config import settings

logger = logging.getLogger(__name__)

_Message = dict[str, Any]


@dataclass
class ToolCall:
    """一次 LLM 发起的工具调用（OpenAI tool_calls 解析结果）。"""

    name: str
    arguments: dict[str, Any]
    tool_call_id: str = ""


@dataclass
class LLMResponse:
    """带工具调用的补全结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant: dict[str, Any] | None = None  # 可回放的 assistant 消息（含 tool_calls），供工具循环追加


class LLMClient(ABC):
    """统一 LLM 接口。"""

    @abstractmethod
    async def complete(self, messages: list[_Message], temperature: float = 0.2) -> str:
        """非流式补全。messages: [{"role": "system"|"user"|"assistant", "content": str}]"""

    @abstractmethod
    async def complete_with_tools(
        self, messages: list[_Message], tools: list[dict[str, Any]], temperature: float = 0.2
    ) -> LLMResponse:
        """工具调用补全：tools 为 [{type:'function', function: schema}]；解析 OpenAI 标准 tool_calls。

        模型/格式不支持时返回 tool_calls=[]（调用方落确定性兜底，绝不让决策失败）。
        """

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """文本向量化。"""


class OpenAILLM(LLMClient):
    """OpenAI 兼容客户端（DeepSeek/Qwen/Claude 等均走此抽象）。

    v2.2b：provider 级韧性——单次请求 asyncio 超时 + openai 内置重试（连接/5xx/429 指数退避）。
    与 Agent 级超时/重试叠加：wait_for 在总量上兜底，防止挂起拖死调度 worker。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str,
        timeout_seconds: float = settings.llm_timeout_seconds,
        max_retries: int = settings.llm_max_retries,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)
        self._timeout = timeout_seconds
        self._model = model
        self._embedding_model = embedding_model

    async def complete(self, messages: list[_Message], temperature: float = 0.2) -> str:
        resp = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model, messages=cast(Any, messages), temperature=temperature
            ),
            timeout=self._timeout,
        )
        return resp.choices[0].message.content or ""

    async def complete_with_tools(
        self, messages: list[_Message], tools: list[dict[str, Any]], temperature: float = 0.2
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": cast(Any, messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        resp = await asyncio.wait_for(self._client.chat.completions.create(**payload), timeout=self._timeout)
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=tc.function.name or "", arguments=args, tool_call_id=tc.id or ""))
        assistant: dict[str, Any] | None = None
        if calls:
            assistant = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.tool_call_id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in calls
                ],
            }
        return LLMResponse(content=msg.content or "", tool_calls=calls, assistant=assistant)

    async def embed(self, text: str) -> list[float]:
        resp = await asyncio.wait_for(
            self._client.embeddings.create(model=self._embedding_model, input=text), timeout=self._timeout
        )
        return resp.data[0].embedding


class MockLLM(LLMClient):
    """开发兜底：返回固定结果，避免依赖外部服务。"""

    async def complete(self, messages: list[_Message], temperature: float = 0.2) -> str:
        last = messages[-1]["content"] if messages else ""
        return f"【MockLLM】{last}"

    async def complete_with_tools(
        self, messages: list[_Message], tools: list[dict[str, Any]], temperature: float = 0.2
    ) -> LLMResponse:
        # MockLLM 不发起工具调用 → 决策侧落到确定性兜底，保证本地/CI 可测
        return LLMResponse(content=f"【MockLLM】{messages[-1]['content'] if messages else ''}", tool_calls=[])

    async def embed(self, text: str) -> list[float]:
        # 确定性伪向量（MD5 种子），便于本地联调与测试
        digest = hashlib.md5(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(8)]


@lru_cache
def get_llm(model: str | None = None) -> LLMClient:
    """返回全局 LLM 客户端；配置了有效 key 走真模型（OpenAI 兼容），否则 MockLLM 兜底。

    model 可选：按 Agent 配置覆盖会话级模型（per-agent config.model 生效，P0-4）。
    未配置有效 key 时忽略 model 参数统一返回 MockLLM（本地/CI 不依赖外部服务）。
    """
    key = settings.llm_api_key
    if not key or key == "sk-xxx":
        logger.warning("未配置有效 LLM_API_KEY，使用 MockLLM 开发兜底")
        return MockLLM()
    return OpenAILLM(
        base_url=settings.llm_base_url,
        api_key=key,
        model=model or settings.llm_model,
        embedding_model=settings.embedding_model,
    )

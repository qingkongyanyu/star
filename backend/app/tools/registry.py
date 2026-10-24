"""工具注册表：register / get_all（OpenAI function 列表）/ call（含权限钩子）。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolImpl = Callable[..., Awaitable[dict[str, Any]]]
ToolGuard = Callable[[Any, dict[str, Any]], None]


@dataclass
class ToolDef:
    name: str
    schema: dict[str, Any]
    impl: ToolImpl
    guard: ToolGuard | None = None


class ToolRegistry:
    """工具注册与调用。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        impl: ToolImpl,
        guard: ToolGuard | None = None,
    ) -> None:
        """注册工具：schema 供 LLM 声明，impl 为实现，guard 为权限钩子。"""
        self._tools[name] = ToolDef(name=name, schema=schema, impl=impl, guard=guard)

    def get_all(self) -> list[dict[str, Any]]:
        """返回 OpenAI function-calling 声明列表（供 LLM tools 参数）。"""
        return [{"type": "function", "function": tool.schema} for tool in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    async def call(self, name: str, ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
        """调用工具：先过权限钩子（guard），再执行 impl。"""
        tool = self._tools[name]
        if tool.guard is not None:
            tool.guard(ctx, args)
        return await tool.impl(ctx, args, deps)

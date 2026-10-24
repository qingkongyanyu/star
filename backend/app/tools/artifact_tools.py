"""资产侧共享工具（schema 逐字对齐 Agent 契约书 §〇.2，impl 走 ArtifactService）。"""
from __future__ import annotations

from typing import Any

from app.core.constants import AssetType
from app.services.artifact_service import ArtifactService
from app.tools.registry import ToolRegistry

_ASSET_TYPES = [item.value for item in AssetType]


def _pull_schema() -> dict[str, Any]:
    return {
        "name": "pull_artifact",
        "description": "从 ArtifactRepo 按资产 ID + 版本拉取上游真实产出；缺省取最新版。",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "version": {"type": "string", "description": "可选，缺省为最新版"},
            },
            "required": ["asset_id"],
        },
    }


async def _pull_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    version: int | None = None
    if args.get("version"):
        try:
            version = int(args["version"])
        except ValueError:
            version = None
    content = await ArtifactService(deps.artifact_session).fetch_content(args["asset_id"], version)
    return {"asset_id": args["asset_id"], "version": version, "content": content}


def _publish_schema() -> dict[str, Any]:
    return {
        "name": "publish_artifact",
        "description": "将产出写入 ArtifactRepo：文件写 MinIO、索引写 MySQL、自动登记血缘（dep_asset_ids）。",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "type": {"type": "string", "enum": _ASSET_TYPES},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "文本内容或对象 key"},
                "file_path": {"type": "string", "description": "可选，文件型资产路径"},
                "dep_asset_ids": {"type": "array", "items": {"type": "string"}, "description": "血缘依赖（上游 asset_id@version）"},
            },
            "required": ["node_id", "type", "title", "content"],
        },
    }


async def _publish_impl(ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
    version = await ArtifactService(deps.artifact_session).publish(
        asset_type=args["type"],
        title=args["title"],
        content=args["content"],
        dep_ids=args.get("dep_asset_ids", []),
        producer_agent=ctx.capability,
    )
    return {"asset_id": version.asset_id, "version": version.version, "object_key": version.object_key}


def register_artifact_tools(registry: ToolRegistry) -> None:
    """把资产侧共享工具注册进给定注册表。"""
    registry.register("pull_artifact", _pull_schema(), _pull_impl)
    registry.register("publish_artifact", _publish_schema(), _publish_impl)

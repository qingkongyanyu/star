"""资产接口：/assets（列表/详情/版本/下载/血缘）。

契约见 docs/02-后端/04-API接口契约.md §四（资产）。全部只读，权限 登录。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_permission
from app.core.constants import AssetType
from app.infra.db import get_artifact_db
from app.models.task_repo import User
from app.schemas.asset import AssetDetailOut, AssetOut, DownloadOut, LineageOut, VersionOut
from app.schemas.task import Page
from app.services.artifact_service import ArtifactService

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=Page[AssetOut], summary="资产列表")
async def list_assets(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_artifact_db)],
    asset_type: Annotated[AssetType | None, Query(alias="type")] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AssetOut]:
    """资产列表（类型 / 关键字 / 分页）。"""
    return await ArtifactService(session).list_assets(
        asset_type.value if asset_type is not None else None,
        keyword,
        page,
        page_size,
    )


@router.get("/{asset_id}", response_model=AssetDetailOut, summary="资产详情")
async def get_asset(
    asset_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_artifact_db)],
) -> AssetDetailOut:
    """资产详情（含最新版本信息）。"""
    return await ArtifactService(session).get_asset(asset_id)


@router.get("/{asset_id}/versions", response_model=Page[VersionOut], summary="版本列表")
async def list_versions(
    asset_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_artifact_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[VersionOut]:
    """资产版本列表（version 倒序）。"""
    return await ArtifactService(session).list_versions(asset_id, page, page_size)


@router.get("/{asset_id}/versions/{v}", response_model=DownloadOut, summary="下载/预览")
async def download(
    asset_id: str,
    v: int,
    _user: Annotated[User, Depends(require_permission("asset:download"))],
    session: Annotated[Session, Depends(get_artifact_db)],
) -> DownloadOut:
    """下载/预览：返回 MinIO 预签名 URL（直链 + 短时有效；需 asset:download 权限点）。"""
    return await ArtifactService(session).download_url(asset_id, v)


@router.get("/{asset_id}/lineage", response_model=LineageOut, summary="血缘链")
async def lineage(
    asset_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_artifact_db)],
    depth: Annotated[int, Query(ge=1, le=10)] = 5,
) -> LineageOut:
    """血缘链（沿上游递归，depth 控制层数）。"""
    return await ArtifactService(session).lineage(asset_id, depth)

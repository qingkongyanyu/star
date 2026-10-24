"""资产用例：列表 / 详情 / 版本 / 下载预签名 / 血缘 / 发布 / 内容读取。

会话使用 artifact_repo 库。publish 为 Agent 产出入口：内容写 MinIO（不可用时降级
本地 `backend/data/artifacts/`），元数据与血缘写 MySQL。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from collections import deque
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.infra.db import to_thread
from app.infra.minio import _client, ensure_bucket, get_minio
from app.models.artifact_repo import Artifact, ArtifactDependency, ArtifactVersion
from app.repositories.artifact_repo import ArtifactRepo
from app.schemas.asset import (
    AssetDetailOut,
    AssetOut,
    DownloadOut,
    LineageEdgeOut,
    LineageNodeOut,
    LineageOut,
    VersionOut,
)
from app.schemas.task import Page

logger = logging.getLogger(__name__)

_DOWNLOAD_EXPIRES_SECONDS = 900
_LOCAL_ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "data" / "artifacts"

# 资产类型 → asset_id 前缀字母 / 对象扩展名
_TYPE_LETTERS: dict[str, str] = {
    "metric_spec": "m",
    "profile": "p",
    "sql": "s",
    "dataset": "d",
    "dq_report": "q",
    "feature_set": "f",
    "chart": "c",
    "insight": "i",
    "report": "r",
    "review": "v",
    "retrospective": "t",
    "trace": "x",
}
_TYPE_EXT: dict[str, str] = {
    "metric_spec": "json",
    "profile": "json",
    "sql": "sql",
    "dataset": "json",
    "dq_report": "json",
    "feature_set": "json",
    "chart": "json",
    "insight": "json",
    "report": "md",
    "review": "json",
    "retrospective": "md",
    "trace": "json",
}


def gen_asset_id(asset_type: str) -> str:
    """生成业务 asset_id：{类型前缀}{hex8}（VARCHAR64 全局唯一）。"""
    letter = _TYPE_LETTERS.get(asset_type, "a")
    return f"{letter}{uuid.uuid4().hex[:8]}"


def build_object_key(asset_type: str, asset_id: str, version: int) -> str:
    """MinIO 对象 key：{type}/{asset_id}/v{version}.{ext}。"""
    return f"{asset_type}/{asset_id}/v{version}.{_TYPE_EXT.get(asset_type, 'json')}"


def _store_content(object_key: str, data: bytes) -> str:
    """写资产内容：优先 MinIO，失败降级本地文件；返回存储位置描述。"""
    try:
        ensure_bucket()
        _client().put_object(settings.minio_bucket, object_key, BytesIO(data), length=len(data))
        return f"minio://{settings.minio_bucket}/{object_key}"
    except Exception:  # noqa: BLE001  对象存储降级：写本地
        path = _LOCAL_ARTIFACT_DIR / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.warning("MinIO 不可用，资产内容降级写本地: %s", path)
        return str(path)


def _read_content(object_key: str) -> bytes:
    """读资产内容：优先 MinIO，失败降级本地文件。"""
    try:
        resp = _client().get_object(settings.minio_bucket, object_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except Exception:  # noqa: BLE001  对象存储降级：读本地
        path = _LOCAL_ARTIFACT_DIR / object_key
        if not path.exists():
            raise FileNotFoundError(f"资产对象不存在: {object_key}")
        return path.read_bytes()


def _publish_with_deps(
    session: Session,
    artifact: Artifact,
    version: ArtifactVersion,
    dep_refs: list[str],
) -> ArtifactVersion:
    """解析血缘引用（asset_id@version，缺省取最新）并单事务落库。"""
    dependencies: list[ArtifactDependency] = []
    for ref in dep_refs:
        dep_asset, _, ver = ref.partition("@")
        dep_version = int(ver) if ver else None
        if dep_version is None:
            latest = ArtifactRepo.get_latest_version(session, dep_asset)
            dep_version = latest.version if latest is not None else 1
        dependencies.append(
            ArtifactDependency(
                asset_id=artifact.asset_id,
                version=version.version,
                dep_asset_id=dep_asset,
                dep_version=dep_version,
                edge_type="data_dep",
            )
        )
    return ArtifactRepo.publish(session, artifact, version, dependencies)


class ArtifactService:
    """资产服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_assets(
        self,
        asset_type: str | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> Page[AssetOut]:
        items, total = await to_thread(
            ArtifactRepo.list_assets, self._session, asset_type, keyword, page, page_size
        )
        latest = await to_thread(
            ArtifactRepo.get_latest_version_numbers,
            self._session,
            [asset.asset_id for asset in items],
        )
        result: list[AssetOut] = []
        for asset in items:
            item = AssetOut.model_validate(asset)
            item.latest_version = latest.get(asset.asset_id)
            result.append(item)
        return Page(items=result, total=total, page=page)

    async def get_asset(self, asset_id: str) -> AssetDetailOut:
        asset = await self._get_asset(asset_id)
        latest = await to_thread(ArtifactRepo.get_latest_version, self._session, asset_id)
        return AssetDetailOut(
            asset_id=asset.asset_id,
            type=asset.type,
            title=asset.title,
            producer_agent=asset.producer_agent,
            status=asset.status,
            latest_version=VersionOut.model_validate(latest) if latest is not None else None,
            create_time=asset.create_time,
        )

    async def list_versions(self, asset_id: str, page: int, page_size: int) -> Page[VersionOut]:
        await self._get_asset(asset_id)
        versions = await to_thread(ArtifactRepo.get_versions, self._session, asset_id)
        total = len(versions)
        start = (page - 1) * page_size
        items = [VersionOut.model_validate(v) for v in versions[start : start + page_size]]
        return Page(items=items, total=total, page=page)

    async def download_url(self, asset_id: str, version: int) -> DownloadOut:
        """下载/预览：返回 MinIO 预签名 URL（本地签名，短时有效）。

        MinIO 不可用时降级返回 data URL（读本地降级内容），与 publish 的降级写本地保持一致。
        """
        ver = await to_thread(ArtifactRepo.get_version, self._session, asset_id, version)
        if ver is None:
            raise NotFoundError(f"资产版本不存在: {asset_id}@{version}")
        try:
            url = await to_thread(
                get_minio().presigned_get_object,
                settings.minio_bucket,
                ver.object_key,
                expires=timedelta(seconds=_DOWNLOAD_EXPIRES_SECONDS),
            )
        except Exception:
            logger.warning("MinIO 预签名失败，降级返回 data URL: %s", ver.object_key, exc_info=True)
            try:
                data = await to_thread(_read_content, ver.object_key)
            except Exception:  # noqa: BLE001  内容也不可读 → 抛出可读错误
                raise NotFoundError(f"资产内容不可读: {asset_id}@{version}（对象存储与本地均不可用）") from None
            url = f"data:application/octet-stream;base64,{base64.b64encode(data).decode('ascii')}"
        return DownloadOut(
            asset_id=asset_id,
            version=version,
            url=url,
            expires_in=_DOWNLOAD_EXPIRES_SECONDS,
            object_key=ver.object_key,
        )

    async def publish(
        self,
        *,
        asset_type: str,
        title: str,
        content: str,
        dep_ids: list[str],
        producer_agent: str,
    ) -> ArtifactVersion:
        """发布资产：写内容（MinIO→本地降级）+ 元数据/血缘落库，返回版本。

        v1 简化：每次发布生成新 asset_id（version=1）；版本叠加留待重跑/迭代。
        """
        asset_id = gen_asset_id(asset_type)
        version_no = await to_thread(ArtifactRepo.next_version, self._session, asset_id)
        object_key = build_object_key(asset_type, asset_id, version_no)
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        await to_thread(_store_content, object_key, data)
        artifact = Artifact(
            asset_id=asset_id,
            type=asset_type,
            title=title,
            producer_agent=producer_agent,
            status="published",
        )
        version = ArtifactVersion(
            asset_id=asset_id,
            version=version_no,
            object_key=object_key,
            digest=digest,
            status="active",
        )
        return await to_thread(_publish_with_deps, self._session, artifact, version, list(dep_ids))

    async def fetch_content(self, asset_id: str, version: int | None = None) -> str:
        """读取资产对象内容（供上游 Agent 消费）；版本缺省取最新。"""
        if version is not None:
            ver = await to_thread(ArtifactRepo.get_version, self._session, asset_id, version)
        else:
            ver = await to_thread(ArtifactRepo.get_latest_version, self._session, asset_id)
        if ver is None:
            raise NotFoundError(f"资产版本不存在: {asset_id}@{version or 'latest'}")
        data = await to_thread(_read_content, ver.object_key)
        return data.decode("utf-8")

    async def lineage(self, asset_id: str, depth: int) -> LineageOut:
        """血缘链：从目标资产沿 dep 上游 BFS 递归（含环保护与去重）。"""
        asset = await self._get_asset(asset_id)
        start_version = await to_thread(ArtifactRepo.get_latest_version, self._session, asset_id)
        if start_version is None:
            return LineageOut(asset_id=asset_id, version=0, nodes=[], edges=[])

        version = start_version.version
        nodes: list[LineageNodeOut] = [
            LineageNodeOut(asset_id=asset_id, type=asset.type, title=asset.title, version=version, edge_type="data_dep")
        ]
        edges: list[LineageEdgeOut] = []
        visited: set[tuple[str, int]] = {(asset_id, version)}
        queue: deque[tuple[str, int]] = deque([(asset_id, version)])

        for _ in range(depth):
            if not queue:
                break
            for _ in range(len(queue)):
                cur_id, cur_version = queue.popleft()
                deps = await to_thread(ArtifactRepo.get_dependencies, self._session, cur_id, cur_version)
                for dep in deps:
                    key = (dep.dep_asset_id, dep.dep_version)
                    if key in visited:
                        continue
                    visited.add(key)
                    dep_asset = await to_thread(ArtifactRepo.get_by_asset_id, self._session, dep.dep_asset_id)
                    nodes.append(
                        LineageNodeOut(
                            asset_id=dep.dep_asset_id,
                            type=dep_asset.type if dep_asset is not None else "unknown",
                            title=dep_asset.title if dep_asset is not None else dep.dep_asset_id,
                            version=dep.dep_version,
                            edge_type=dep.edge_type,
                        )
                    )
                    edges.append(
                        LineageEdgeOut(from_node=dep.dep_asset_id, to=cur_id, edge_type=dep.edge_type)
                    )
                    queue.append(key)

        return LineageOut(asset_id=asset_id, version=version, nodes=nodes, edges=edges)

    async def _get_asset(self, asset_id: str) -> Artifact:
        asset = await to_thread(ArtifactRepo.get_by_asset_id, self._session, asset_id)
        if asset is None:
            raise NotFoundError(f"资产不存在: {asset_id}")
        return asset

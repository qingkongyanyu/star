"""数据源接入用例：CRUD / 连接测试 / 结构读取 / CSV 上传注册。

连接器实现见 app/infra/datasource.py；文件上传落 MinIO。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.infra import datasource
from app.infra.db import to_thread
from app.infra.minio import ensure_bucket, put_object
from app.models.task_repo import DataSource
from app.repositories.data_source_repo import DataSourceRepo
from app.schemas.data_source import ColumnOut, DataSourceTestOut

_SQL_TYPES = ("mysql", "postgres", "olap")
_LOCAL_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "data-source"

logger = logging.getLogger(__name__)


class DataSourceService:
    """数据源服务。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_all(self) -> list[DataSource]:
        return await to_thread(DataSourceRepo.list_all, self._session)

    async def get(self, ds_id: int) -> DataSource:
        ds = await to_thread(DataSourceRepo.get_by_id, self._session, ds_id)
        if ds is None:
            raise NotFoundError(f"数据源不存在: {ds_id}")
        return ds

    async def create(self, *, name: str, ds_type: str, config: dict[str, Any], creator: str) -> DataSource:
        return await to_thread(
            DataSourceRepo.create,
            self._session,
            name=name,
            ds_type=ds_type,
            config=config,
            created_by=creator,
        )

    async def delete(self, ds_id: int) -> None:
        await self.get(ds_id)
        await to_thread(DataSourceRepo.delete, self._session, ds_id)

    async def rename(self, ds_id: int, name: str) -> DataSource:
        ds = await self.get(ds_id)
        ds.name = name
        await to_thread(self._session.commit)
        await to_thread(self._session.refresh, ds)
        return ds

    async def test(self, ds_id: int) -> DataSourceTestOut:
        ds = await self.get(ds_id)
        out = DataSourceTestOut()

        if ds.type in _SQL_TYPES:
            url = datasource.build_sqlalchemy_url(ds.config)
            ok, msg = await to_thread(datasource.sql_test, url)
            out.ok, out.message = ok, msg
            if ok:
                out.tables = await to_thread(datasource.sql_list_tables, url)
        elif ds.type == "api":
            ok, msg, inferred = await datasource.api_test(ds.config)
            out.ok, out.message = ok, msg
            if ok and inferred and inferred.get("columns"):
                ds.config = {**ds.config, **inferred}
                out.columns = [ColumnOut(**c) for c in inferred["columns"]]
                await self._flush_status(ds, True)
                return out
        elif ds.type == "stream":
            out.ok, out.message = await to_thread(datasource.kafka_test, ds.config)
        elif ds.type == "file":
            out.ok, out.message = bool(ds.config.get("object_key")), "文件已就绪" if ds.config.get("object_key") else "缺少文件"
            out.columns = [ColumnOut(**c) for c in ds.config.get("columns") or []]
            out.schema_preview = {"columns": ds.config.get("columns") or [], "sample_rows": ds.config.get("sample_rows") or []}
        else:
            out.ok, out.message = False, f"不支持的数据源类型: {ds.type}"

        await self._flush_status(ds, out.ok)
        return out

    async def tables(self, ds_id: int) -> list[str]:
        ds = await self.get(ds_id)
        if ds.type not in _SQL_TYPES:
            raise NotFoundError("仅 SQL 型数据源支持列表演")
        return await to_thread(datasource.sql_list_tables, datasource.build_sqlalchemy_url(ds.config))

    async def schema(self, ds_id: int, table: str | None) -> dict[str, Any]:
        ds = await self.get(ds_id)
        info = await to_thread(datasource.schema_for, ds.type, ds.config, table)
        return info

    async def create_from_csv(
        self, *, name: str, filename: str, data: bytes, creator: str
    ) -> tuple[DataSource, list[dict[str, Any]], int]:
        """解析 CSV → 落 MinIO（不可用则降级写本地）→ 注册为 file 型数据源。"""
        parsed = datasource.parse_csv(data)
        object_key = f"uploads/{uuid.uuid4().hex}_{filename}"
        try:
            bucket = ensure_bucket("data-source")
            await put_object(bucket, object_key, data, content_type="text/csv")
            stored_ref = f"{bucket}/{object_key}"
        except Exception:
            logger.warning("MinIO 不可用，CSV 降级写本地", exc_info=True)
            local_path = _LOCAL_UPLOAD_DIR / object_key
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            stored_ref = str(local_path)
        config = {
            "file_name": filename,
            "object_key": stored_ref,
            "columns": parsed["columns"],
            "sample_rows": parsed["sample_rows"],
            "row_count": parsed["row_count"],
        }
        ds = await to_thread(
            DataSourceRepo.create,
            self._session,
            name=name,
            ds_type="file",
            config=config,
            created_by=creator,
            status="connected",
        )
        return ds, parsed["columns"], parsed["row_count"]

    async def _flush_status(self, ds: DataSource, ok: bool) -> None:
        ds.status = "connected" if ok else "error"
        ds.last_tested_at = datetime.now()  # noqa: DTZ005  与 task_repo.py 时间处理一致
        await to_thread(self._session.commit)

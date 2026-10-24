"""数据源接入接口：/data-sources（管理员）。

契约：CRUD + 连接测试 + 表/结构读取 + CSV 上传注册。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_role
from app.core.constants import Role
from app.core.exceptions import ValidationError
from app.infra.db import get_task_db
from app.models.task_repo import User
from app.schemas.data_source import (
    ColumnOut,
    DataSourceCreate,
    DataSourceOptionOut,
    DataSourceOut,
    DataSourceRename,
    DataSourceTestOut,
    FileUploadOut,
)

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


@router.get("", response_model=list[DataSourceOut], summary="数据源列表")
async def list_data_sources(
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[DataSourceOut]:
    from app.services.data_source_service import DataSourceService

    return [DataSourceOut.model_validate(ds) for ds in await DataSourceService(session).list_all()]


@router.get("/options", response_model=list[DataSourceOptionOut], summary="数据源选项（任务下发用，脱敏，所有登录用户）")
async def list_data_source_options(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[DataSourceOptionOut]:
    """任务表单可选的数据源（不含 config/密码）。分析师等所有登录用户可用。"""
    from app.services.data_source_service import DataSourceService

    return [
        DataSourceOptionOut(id=ds.id, name=ds.name, type=ds.type, status=ds.status)
        for ds in await DataSourceService(session).list_all()
    ]


@router.post("", response_model=DataSourceOut, status_code=status.HTTP_201_CREATED, summary="新增数据源")
async def create_data_source(
    payload: DataSourceCreate,
    user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> DataSourceOut:
    from app.services.data_source_service import DataSourceService

    if payload.type not in ("mysql", "postgres", "olap", "file", "stream", "api"):
        raise ValidationError("不支持的 data source type")
    ds = await DataSourceService(session).create(
        name=payload.name, ds_type=payload.type, config=payload.config, creator=user.username
    )
    return DataSourceOut.model_validate(ds)


@router.post("/upload", response_model=FileUploadOut, status_code=status.HTTP_201_CREATED, summary="上传 CSV 注册数据源")
async def upload_csv(
    user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
    file: Annotated[UploadFile, File(description="CSV 文件")],
    name: Annotated[str, Form(description="数据源名称")],
) -> FileUploadOut:
    from app.services.data_source_service import DataSourceService

    data = await file.read()
    if not data:
        raise ValidationError("文件内容为空")
    # 文件大小上限（防超大文件 OOM）
    from app.infra.datasource import MAX_UPLOAD_BYTES

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"文件过大（{len(data) / 1024 / 1024:.1f} MB），超过上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，请拆分后上传"
        )
    ds, columns, row_count = await DataSourceService(session).create_from_csv(
        name=name, filename=file.filename or "upload.csv", data=data, creator=user.username
    )
    # 返回完整数据源（含 id）：客户端无需再回查列表即可直接选中新上传的文件数据源
    return FileUploadOut(
        data_source=DataSourceOut.model_validate(ds),
        columns=[ColumnOut(**c) for c in columns],
        sample_rows=ds.config.get("sample_rows") or [],
        row_count=row_count,
    )


@router.put("/{ds_id}", response_model=DataSourceOut, summary="重命名数据源")
async def rename_data_source(
    ds_id: int,
    payload: DataSourceRename,
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> DataSourceOut:
    from app.services.data_source_service import DataSourceService

    return DataSourceOut.model_validate(await DataSourceService(session).rename(ds_id, payload.name))


@router.post("/{ds_id}/test", response_model=DataSourceTestOut, summary="测试连接")
async def test_data_source(
    ds_id: int,
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> DataSourceTestOut:
    from app.services.data_source_service import DataSourceService

    return await DataSourceService(session).test(ds_id)


@router.get("/{ds_id}/tables", response_model=list[str], summary="表清单（SQL 型）")
async def list_tables(
    ds_id: int,
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> list[str]:
    from app.services.data_source_service import DataSourceService

    return await DataSourceService(session).tables(ds_id)


@router.get("/{ds_id}/schema", response_model=dict, summary="表结构")
async def get_schema(
    ds_id: int,
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
    table: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    from app.services.data_source_service import DataSourceService

    return await DataSourceService(session).schema(ds_id, table)


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除数据源")
async def delete_data_source(
    ds_id: int,
    _user: Annotated[User, Depends(require_role(Role.ADMIN.value))],
    session: Annotated[Session, Depends(get_task_db)],
) -> None:
    from app.services.data_source_service import DataSourceService

    await DataSourceService(session).delete(ds_id)

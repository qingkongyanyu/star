"""MinIO 对象存储封装（惰性客户端）。"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from io import BytesIO

from minio import Minio

from app.core.config import settings


@lru_cache
def _client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
        region=settings.minio_region,
    )


def get_minio() -> Minio:
    """获取全局 MinIO 客户端（惰性连接）。"""
    return _client()


def ensure_bucket(bucket: str | None = None) -> str:
    """确保 bucket 存在并返回 bucket 名。"""
    bucket = bucket or settings.minio_bucket
    client = _client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket


async def put_object(bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """上传对象，返回 object_name。"""
    await asyncio.to_thread(
        _client().put_object,
        bucket,
        object_name,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_name


async def get_object(bucket: str, object_name: str) -> bytes:
    """下载对象。"""
    resp = await asyncio.to_thread(_client().get_object, bucket, object_name)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()

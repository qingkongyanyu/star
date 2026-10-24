"""ORM 模型包。

import 本包即把全部表注册到对应 Base.metadata（alembic env.py 依赖此注册）。
"""
from app.models import (
    artifact_repo,  # noqa: F401
    task_repo,  # noqa: F401
)
from app.models.base import ArtifactRepoBase, TaskRepoBase

__all__ = ["ArtifactRepoBase", "TaskRepoBase"]

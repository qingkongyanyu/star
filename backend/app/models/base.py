"""SQLAlchemy 声明式基类。

三库隔离架构下，业务库各持一个独立的 DeclarativeBase：

- ``TaskRepoBase``     → 元数据落在 ``task_repo`` 库（对应 alembic/task_repo 环境）
- ``ArtifactRepoBase`` → 元数据落在 ``artifact_repo`` 库（对应 alembic/artifact_repo 环境）

``__bind_key__`` 仅为标识，供 alembic env.py 选择对应的元数据；SQLAlchemy 本身
不参与多库绑定（连接串由 infra/db.py 按库名显式选择）。
"""
from sqlalchemy.orm import DeclarativeBase


class TaskRepoBase(DeclarativeBase):
    """task_repo 库声明式基类。"""

    __bind_key__ = "task_repo"


class ArtifactRepoBase(DeclarativeBase):
    """artifact_repo 库声明式基类。"""

    __bind_key__ = "artifact_repo"

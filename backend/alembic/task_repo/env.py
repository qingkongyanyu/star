"""Alembic 迁移环境：task_repo 库。

一条 `alembic upgrade head` 同时迁移两个库：
    1) 本环境跑 task_repo（version_table=task_repo_version）
    2) 迁移完成后编排 artifact_repo 环境（alembic/artifact_repo）

仅当实际执行 `upgrade` 命令时才编排，避免 revision/current/stamp 等误触发。
"""
import subprocess
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# 保证 `alembic` 在任意 cwd 下都能 import app 包
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from app.models import task_repo as _task_models  # noqa: F401  注册表到 metadata
from app.models.base import TaskRepoBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = TaskRepoBase.metadata
DATABASE_URL = settings.database_url


def _is_upgrade_command() -> bool:
    """判断本次 CLI 是否 `upgrade`（alembic 将命令函数放进 config.cmd_opts.cmd[0]）。"""
    cmd_opts = getattr(config, "cmd_opts", None)
    cmd = getattr(cmd_opts, "cmd", None) if cmd_opts else None
    if not cmd:
        return False
    fn = cmd[0]
    return callable(fn) and fn.__name__ == "upgrade"


def _run_artifact_repo() -> None:
    """在子进程中运行 artifact_repo 环境，使一条 `alembic upgrade head` 覆盖两库。

    用子进程而非编程式 command.upgrade：两套 env.py 的 `config` 全局代理互不污染
    （嵌套 EnvironmentContext 会相互 del 掉同名全局，触发 KeyError）。
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-n", "artifact_repo", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"artifact_repo 迁移失败（退出码 {result.returncode}）")


def run_migrations_offline() -> None:
    """离线模式（--sql）：只输出 task_repo 的 SQL，不编排 artifact_repo。"""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        version_table="task_repo_version",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：跑 task_repo，随后编排 artifact_repo（仅 upgrade 命令）。"""
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="task_repo_version",
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    if _is_upgrade_command():
        _run_artifact_repo()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

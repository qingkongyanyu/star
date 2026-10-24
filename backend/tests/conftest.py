"""pytest 公共夹具（v2 测试地基）。

- 全自动强制 MockLLM：把 settings.llm_api_key 置为无效，保证 get_llm() 返回 MockLLM，
  本地/CI 测试不依赖外部 LLM 服务。
- `mysql_backend`（session，marker integration）：尝试连配置的 MySQL 双库并 `alembic upgrade head`
  建/升 schema；连不上或迁移失败则整体 skip。集成测试依赖它（仓库层 CAS/租约、驱动器行为）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def force_mockllm(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有测试强制 MockLLM（无论 backend/.env 是否配了真 key）。"""
    from app.infra import llm

    monkeypatch.setattr(llm.settings, "llm_api_key", "sk-xxx")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.invalid/v1")
    llm.get_llm.cache_clear()


@pytest.fixture(scope="session")
def mysql_backend() -> None:
    """MySQL 双库就绪 + schema 已迁移到 head；不可达/失败 → skip 整批集成测试。

    迁移为增量加列（0009 向后兼容），在同一 MySQL 上幂等。若你不希望测试触碰本地库，
    可用 `pytest -m "not integration"` 只跑纯逻辑单测。
    """
    from app.core.config import settings

    for url in (settings.database_url, settings.artifact_database_url):
        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            engine.dispose()
            pytest.skip(f"MySQL 不可达（{url.split('@')[-1]}）：{exc}")
        finally:
            engine.dispose()

    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head 失败：{result.stderr[-500:]}")
    logger.info("MySQL 后端就绪，schema 已升级到 head")

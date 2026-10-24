"""Agent 配置数据访问（agent_config）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task_repo import AgentConfig


class AgentConfigRepo:
    """agent_config 数据访问。"""

    @staticmethod
    def get_by_agent_id(session: Session, agent_id: str) -> AgentConfig | None:
        return session.execute(select(AgentConfig).where(AgentConfig.agent_id == agent_id)).scalar_one_or_none()

    @staticmethod
    def upsert(
        session: Session,
        *,
        agent_id: str,
        capability: str,
        config: dict[str, Any],
        updated_by: str | None,
    ) -> AgentConfig:
        """插入或覆盖配置（幂等）。"""
        row = AgentConfigRepo.get_by_agent_id(session, agent_id)
        if row is None:
            row = AgentConfig(agent_id=agent_id, capability=capability, config=config, updated_by=updated_by)
            session.add(row)
        else:
            row.config = config
            row.updated_by = updated_by
        session.commit()
        session.refresh(row)
        return row

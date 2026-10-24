"""记忆相关 DTO（契约 docs/02-后端/04 §六）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MemoryHitOut(BaseModel):
    """记忆检索命中。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: str
    scope: str
    source_task: str | None = None
    task_type: str | None = None
    content: str
    experience_type: str
    tags: list[str] | None = None
    created_ts: datetime
    ttl_ts: datetime | None = None
    access_count: int

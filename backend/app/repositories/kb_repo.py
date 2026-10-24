"""指标知识库数据访问（kb_indicator）。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.task_repo import KBIndicator


class KbRepo:
    """kb_indicator 数据访问。"""

    @staticmethod
    def list_kb(
        session: Session,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[KBIndicator], int]:
        """知识库条目（关键字 + 分页，按 id 倒序）。"""
        stmt = select(KBIndicator)
        count_stmt = select(func.count()).select_from(KBIndicator)
        if keyword is not None:
            pattern = f"%{keyword}%"
            cond = or_(
                KBIndicator.metric_name.like(pattern),
                KBIndicator.indicator_key.like(pattern),
                KBIndicator.category.like(pattern),
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = session.execute(count_stmt).scalar_one()
        items = list(
            session.execute(
                stmt.order_by(KBIndicator.id.desc()).offset((page - 1) * page_size).limit(page_size)
            ).scalars()
        )
        return items, total

    @staticmethod
    def get_by_key(session: Session, indicator_key: str) -> KBIndicator | None:
        return session.execute(
            select(KBIndicator).where(KBIndicator.indicator_key == indicator_key)
        ).scalar_one_or_none()

    @staticmethod
    def list_all(session: Session, limit: int = 200) -> list[KBIndicator]:
        """全量条目（Agent 检索用，按 id 顺序）。"""
        return list(session.execute(select(KBIndicator).order_by(KBIndicator.id).limit(limit)).scalars())

    @staticmethod
    def update(
        session: Session,
        kb_id: int,
        *,
        metric_name: str | None,
        category: str | None,
        formula: str | None,
        description: str | None,
        tags: list[str] | None,
        owner: str | None,
    ) -> KBIndicator | None:
        item = session.execute(select(KBIndicator).where(KBIndicator.id == kb_id)).scalar_one_or_none()
        if item is None:
            return None
        if metric_name is not None:
            item.metric_name = metric_name
        if category is not None:
            item.category = category
        if formula is not None:
            item.formula = formula
        if description is not None:
            item.description = description
        if tags is not None:
            item.tags = tags
        if owner is not None:
            item.owner = owner
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def delete(session: Session, kb_id: int) -> bool:
        item = session.execute(select(KBIndicator).where(KBIndicator.id == kb_id)).scalar_one_or_none()
        if item is None:
            return False
        session.delete(item)
        session.commit()
        return True

    @staticmethod
    def create(
        session: Session,
        *,
        indicator_key: str,
        metric_name: str,
        category: str | None,
        formula: str | None,
        description: str | None,
        tags: list[str] | None,
        owner: str | None,
    ) -> KBIndicator:
        item = KBIndicator(
            indicator_key=indicator_key,
            metric_name=metric_name,
            category=category,
            formula=formula,
            description=description,
            tags=tags,
            owner=owner,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

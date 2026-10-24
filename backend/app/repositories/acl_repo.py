"""表级 ACL 数据访问（admin_acl）。"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.task_repo import AdminAcl


class AclRepo:
    """admin_acl 数据访问。"""

    @staticmethod
    def list_all(session: Session) -> list[AdminAcl]:
        return list(session.execute(select(AdminAcl).order_by(AdminAcl.id)).scalars())

    @staticmethod
    def replace_all(session: Session, rules: list[AdminAcl]) -> int:
        """整体替换规则集（单事务 delete + insert），返回新规则数。"""
        session.execute(delete(AdminAcl))
        session.add_all(rules)
        session.commit()
        return len(rules)

    @staticmethod
    def create(
        session: Session,
        *,
        role_or_user: str,
        table: str,
        action: str,
        allow: bool,
    ) -> AdminAcl:
        rule = AdminAcl(role_or_user=role_or_user, table=table, action=action, allow=allow)
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule

    @staticmethod
    def delete(session: Session, rule_id: int) -> bool:
        rule = session.execute(select(AdminAcl).where(AdminAcl.id == rule_id)).scalar_one_or_none()
        if rule is None:
            return False
        session.delete(rule)
        session.commit()
        return True

    @staticmethod
    def update(
        session: Session,
        rule_id: int,
        *,
        role_or_user: str,
        table: str,
        action: str,
        allow: bool,
    ) -> AdminAcl | None:
        rule = session.execute(select(AdminAcl).where(AdminAcl.id == rule_id)).scalar_one_or_none()
        if rule is None:
            return None
        rule.role_or_user = role_or_user
        rule.table = table
        rule.action = action
        rule.allow = allow
        session.commit()
        session.refresh(rule)
        return rule

    @staticmethod
    def check(
        session: Session,
        *,
        role: str,
        user: str,
        table: str,
        action: str,
    ) -> tuple[bool, str]:
        """表级 ACL 判定（docs/02-后端/07）。

        规则匹配：role_or_user ∈ {role, user, "*"} 且 table ∈ {table, "*"} 且 action 相同。
        策略：显式「拒绝」优先；其次显式「允许」；无匹配规则 → 默认允许（只读连接）。
        """
        if not table:
            return True, "无目标表，跳过 ACL"
        denied: list[AdminAcl] = []
        allowed: list[AdminAcl] = []
        for rule in AclRepo.list_all(session):
            if rule.action != action:
                continue
            if rule.role_or_user not in (role, user, "*"):
                continue
            if rule.table not in (table, "*"):
                continue
            (allowed if rule.allow else denied).append(rule)
        if denied:
            return False, f"ACL 拒绝：{role}/{user} 对表 {table} 的 {action} 被禁止"
        if allowed:
            return True, f"ACL 允许：{role}/{user} 对表 {table} 的 {action}"
        return True, "无 ACL 规则，默认允许（只读连接）"

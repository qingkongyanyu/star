"""task_repo 库 0011：task.plan_json（Planner 结构化分析计划）。

v3 起 Planner 不再只输出 per-node instruction 文本，而是产出结构化 AnalysisPlan
（意图槽位 / 指标 / 维度 / 时间 / 过滤 / 子任务），供指标定义、取数、可视化、洞察、
报告、终审各 Agent 消费。计划随任务落库，重跑与审计都可复现。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_task_plan"
down_revision = "0010_agent_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task", sa.Column("plan_json", sa.JSON(), nullable=True, comment="Planner 结构化分析计划"))


def downgrade() -> None:
    op.drop_column("task", "plan_json")

"""task_repo 库 0003：task 增加 requirement 列（原始需求，供 Agent 消费）。"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_task_requirement"
down_revision = "0002_kb_indicator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("requirement", sa.Text(), nullable=True, comment="原始需求（群聊消息原文，供 Agent 消费）"),
    )


def downgrade() -> None:
    op.drop_column("task", "requirement")

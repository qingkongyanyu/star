"""task_repo 库 0009：调度自愈字段 + 节点指令。

- task：加 scheduler_token / scheduler_heartbeat_at（PLANNING 与收尾仲裁的租约，多 worker 竞争 + 崩溃自愈）
- dag_node：加 instruction（Planner 拆解定向指令）、heartbeat_at（节点执行心跳，超时复位）

对应 models/task_repo.py 的 Task / DagNode。加列向后兼容。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0009_scheduler_lease_instruction"
down_revision = "0008_task_req_id_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task", sa.Column("scheduler_token", sa.String(length=64), nullable=True))
    op.add_column("task", sa.Column("scheduler_heartbeat_at", mysql.DATETIME(fsp=3), nullable=True))
    op.add_column("dag_node", sa.Column("instruction", sa.Text(), nullable=True))
    op.add_column("dag_node", sa.Column("heartbeat_at", mysql.DATETIME(fsp=3), nullable=True))


def downgrade() -> None:
    op.drop_column("dag_node", "heartbeat_at")
    op.drop_column("dag_node", "instruction")
    op.drop_column("task", "scheduler_heartbeat_at")
    op.drop_column("task", "scheduler_token")

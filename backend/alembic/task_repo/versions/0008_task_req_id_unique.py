"""task_repo 库 0008：task.req_id 唯一约束（幂等键并发兜底）。

先清理历史重复 req_id（保留最早一条），再删冗余普通索引、加唯一约束，
防止并发同 req_id 提交产生两条任务 / get_by_req_id 抛 MultipleResultsFound。
"""
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0008_task_req_id_unique"
down_revision = "0007_seed_kb_indicator"
branch_labels = None
depends_on = None


def _index_names(table: str) -> set[str]:
    """当前库中指定表的索引名集合（用 inspector 判定，避免 try/except 静默吞错）。"""
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # 1) 清理重复 req_id：每组仅保留 id 最小的一条
    op.execute(
        """
        DELETE t1 FROM task t1
        INNER JOIN task t2 ON t1.req_id = t2.req_id AND t1.id > t2.id
        """
    )
    # 2) 去掉冗余的普通索引（唯一索引已足够）
    if "idx_req" in _index_names("task"):
        op.drop_index("idx_req", table_name="task")
    # 3) 加唯一约束（MySQL 下等价唯一索引）
    op.create_unique_constraint("uq_task_req_id", "task", ["req_id"])


def downgrade() -> None:
    op.drop_constraint("uq_task_req_id", "task", type_="unique")
    if "idx_req" not in _index_names("task"):
        op.create_index("idx_req", "task", ["req_id"])

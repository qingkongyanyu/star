"""DAG 拆解与校验。

本期用**确定性模板拓扑**（LLM 拆解留给 agents 阶段）。标准链路依据
docs/02-后端/06-Agent实现指南.md §六（LangGraph 拓扑）与 12-Agent 能力契约
（consumes→produces）：
    metric_definition ∥ metadata_profiling → sql_extract → quality_check
      → visualization → report_writing → review
并含 registry 隐含的 metric_definition → review（review 消费 metric_spec）。
"""
from __future__ import annotations

from collections import defaultdict, deque

from app.core.constants import AGENT_CAPABILITY, AGENT_DISPLAY_NAMES, NodeStatus
from app.models.task_repo import DagEdge, DagNode
from app.services.intent import Intent, classify_intent

# 能力标签(capability) → 前端展示标签（拟人化名称_职称）。单一来源：constants.AGENT_DISPLAY_NAMES
# （键为 agent_id），按 AGENT_CAPABILITY 反查能力标签导出，避免两处清单漂移（P2.b）。
AGENT_LABELS: dict[str, str] = {
    AGENT_CAPABILITY[aid]: name for aid, name in AGENT_DISPLAY_NAMES.items() if aid != "planner"
}

# 标准模板：节点 agent_type（拓扑序）
DEFAULT_DAG_NODES: list[str] = [
    "metric_definition",
    "metadata_profiling",
    "sql_extract",
    "quality_check",
    "visualization",
    "insight_analysis",
    "report_writing",
    "review",
]

# 溯源意图关键词：命中则在模板末尾追加 trace 节点（TraceNavigator 按需运行）
TRACE_KEYWORDS = ("溯源", "追责", "根因", "为什么")

# ---- v2.1 调度剪枝：按数据源类型与需求意图裁剪节点集（纯逻辑，单测可覆盖）----

# 分析任务允许的数据源分组：SQL 类走真实 schema/取数；记录型（file/api/stream）走真实拉取→聚合链路
SQL_SOURCE_TYPES = {"mysql", "postgres", "olap"}
FILE_SOURCE_TYPES = {"file"}
RECORD_SOURCE_TYPES = FILE_SOURCE_TYPES | {"api", "stream"}


def has_metric_intent(requirement: str) -> bool:
    """需求是否含金额/销售/指标口径语义（决定是否保留指标定义链）。

    单一来源：委托 app.services.intent.has_metric_intent（本函数仅为向后兼容旧调用方保留）。
    """
    from app.services.intent import has_metric_intent as _impl

    return _impl(requirement)


def plan_agent_types(
    requirement: str, data_source_type: str | None, intent: Intent | None = None
) -> list[str]:
    """返回标准分析链（v3：不再按数据源裁剪）。

    指标定义/探查在 v3 是「指标目录 + 真实结构」的必要输入；分析差异化由
    AnalysisPlan 的 metrics/tasks 与图表/报告结构决定，而不是删节点。
    保留该函数签名以兼容旧调用方与测试。
    """
    if intent is None:
        intent = classify_intent(requirement)
    return list(DEFAULT_DAG_NODES)

# 标准模板依赖边（data_dep）
# metadata_profiling → metric_definition：让指标口径基于真实探查 schema 定义（列名对齐）
DEFAULT_DAG_EDGES: list[tuple[str, str]] = [
    ("metadata_profiling", "metric_definition"),
    ("metric_definition", "sql_extract"),
    ("metadata_profiling", "sql_extract"),
    ("sql_extract", "quality_check"),
    ("quality_check", "visualization"),
    # 洞察层：基于可视化产出的 datasets/charts 做分析，再交给报告
    ("visualization", "insight_analysis"),
    ("insight_analysis", "report_writing"),
    ("visualization", "report_writing"),
    ("report_writing", "review"),
    ("metric_definition", "review"),
    # v2（P2.a 数据去重复放大）：dataset 唯一载体为 sql 资产，下游可视化/报告/终审直接读 sql，
    # 不再依赖 quality 把整份 dataset 透传复制（FE 分支时这些直达边会被调度器摘除改走清洗后数据）。
    ("sql_extract", "visualization"),
    ("sql_extract", "report_writing"),
    # v2：终审可直达真实数据集（数字校验），不依赖上游 passthrough 是否完整
    ("sql_extract", "review"),
    # v3：终审需要图表清单/洞察发现核对引用是否真实存在（防止"图表缺失"被误判为幻觉）
    ("visualization", "review"),
    ("insight_analysis", "review"),
]


def build_dag(
    task_id: str,
    node_specs: list[tuple[str, str | None]],
) -> tuple[list[DagNode], list[DagEdge]]:
    """按节点序列构建 DAG：节点带 per-node instruction，依赖边沿用 DEFAULT_DAG_EDGES。

    node_specs: [(agent_type, instruction), ...]，按拓扑/标准顺序给出；未知能力标签会被跳过。
    依赖边仅保留两端都在序列中的边，保证链路合法、无新增能力。
    """
    node_ids: dict[str, str] = {}
    nodes: list[DagNode] = []
    for index, (agent_type, instruction) in enumerate(node_specs):
        node_id = f"{task_id}-n{index}"
        node_ids[agent_type] = node_id
        nodes.append(
            DagNode(
                node_id=node_id,
                task_id=task_id,
                agent_type=agent_type,
                instruction=instruction,
                status=NodeStatus.PENDING.value,
            )
        )

    edges: list[DagEdge] = []
    for from_agent, to_agent in DEFAULT_DAG_EDGES:
        if from_agent in node_ids and to_agent in node_ids:
            edges.append(
                DagEdge(
                    task_id=task_id,
                    from_node=node_ids[from_agent],
                    to_node=node_ids[to_agent],
                    edge_type="data_dep",
                )
            )
    # trace 节点（溯源意图追加）：溯源应在终审之后按需运行。
    # 同时接「报告 → 溯源」边：溯源以最终报告为锚点（否则只能拿到终审意见书，归因会错锚到终审 Agent）。
    if "review" in node_ids and "trace" in node_ids:
        edges.append(
            DagEdge(task_id=task_id, from_node=node_ids["review"], to_node=node_ids["trace"], edge_type="data_dep")
        )
    if "report_writing" in node_ids and "trace" in node_ids:
        edges.append(
            DagEdge(task_id=task_id, from_node=node_ids["report_writing"], to_node=node_ids["trace"], edge_type="data_dep")
        )

    deps: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        deps[edge.to_node].append(edge.from_node)
    for node in nodes:
        node.dep_ids = deps.get(node.node_id, [])
    return nodes, edges


def decompose(
    requirement: str, task_id: str, data_source_type: str | None = None
) -> tuple[list[DagNode], list[DagEdge]]:
    """按标准模板把需求拆成 DAG（确定性兜底路径）。

    requirement 记录在审计 detail，不直接改拓扑；data_source_type 供调度剪枝
    （file(CSV) 无金额意图时裁掉 metric_definition/metadata_profiling）。
    溯源意图（intent.has_trace）自动在末尾追加 trace 节点。
    """
    intent = classify_intent(requirement)
    node_types = plan_agent_types(requirement, data_source_type, intent)
    if intent.has_trace:
        node_types = [*node_types, "trace"]
    specs: list[tuple[str, str | None]] = [(t, None) for t in node_types]
    return build_dag(task_id, specs)


def detect_cycle(edges: list[tuple[str, str]]) -> list[list[str]]:
    """Kahn 拓扑排序检测环；返回留在环中的节点组（无环返回空列表）。

    检测到环时调用方应阻断发布并写审计（docs/02-后端/02 DagService.detect_cycle）。
    """
    adj: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = defaultdict(int)
    all_nodes: set[str] = set()
    for from_node, to_node in edges:
        adj[from_node].append(to_node)
        indegree[to_node] += 1
        all_nodes.update((from_node, to_node))
    for node in all_nodes:
        indegree.setdefault(node, 0)

    queue: deque[str] = deque(node for node in all_nodes if indegree[node] == 0)
    while queue:
        node = queue.popleft()
        for neighbor in adj[node]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    in_cycle = [node for node in all_nodes if indegree[node] > 0]
    return [in_cycle] if in_cycle else []

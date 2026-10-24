"""dag_service 单元测试（纯逻辑，不依赖数据库）。

v3：标准链路固定包含指标/探查/取数/质检/可视化/洞察/报告/终审；
差异化由 AnalysisPlan 的任务/图表/报告结构承担，不再按数据源删节点。
"""
from __future__ import annotations

from app.core.constants import NodeStatus
from app.services.dag_service import (
    DEFAULT_DAG_EDGES,
    DEFAULT_DAG_NODES,
    decompose,
    detect_cycle,
    plan_agent_types,
)


def test_plan_agent_types_always_full_chain() -> None:
    # v3：无论数据源类型/意图，完整链路都保留（指标目录与洞察层需要）
    assert plan_agent_types("按区域统计各房源记录数并出图", "file") == DEFAULT_DAG_NODES
    assert plan_agent_types("看下各渠道记录数分布并出图", "api") == DEFAULT_DAG_NODES
    assert plan_agent_types("看下近30天GMV与销售", None) == DEFAULT_DAG_NODES
    assert "insight_analysis" in DEFAULT_DAG_NODES


def test_decompose_full_chain_for_file() -> None:
    nodes, edges = decompose("按区域统计记录数并出图", "t9", "file")
    assert [n.agent_type for n in nodes] == DEFAULT_DAG_NODES
    node_by_id = {n.agent_type: n for n in nodes}
    assert (node_by_id["sql_extract"].node_id, node_by_id["quality_check"].node_id) in {
        (e.from_node, e.to_node) for e in edges
    }
    # 指标/探查是入口（无上游依赖）
    assert (node_by_id["sql_extract"].dep_ids or [])
    assert (node_by_id["metadata_profiling"].dep_ids or []) == []
    # 可视化依赖 quality + sql（直达真实数据集）
    assert set(node_by_id["visualization"].dep_ids or []) == {
        node_by_id["quality_check"].node_id,
        node_by_id["sql_extract"].node_id,
    }


def test_decompose_produces_expected_topology() -> None:
    nodes, edges = decompose("分析近30天GMV", "t00000001")

    assert [node.agent_type for node in nodes] == DEFAULT_DAG_NODES
    assert all(node.task_id == "t00000001" for node in nodes)
    assert all(node.status == NodeStatus.PENDING.value for node in nodes)
    assert all(node.node_id == f"t00000001-n{i}" for i, node in enumerate(nodes))

    node_by_type = {node.agent_type: node.node_id for node in nodes}
    edge_pairs = {(edge.from_node, edge.to_node) for edge in edges}
    assert len(edges) == len(DEFAULT_DAG_EDGES)
    assert (node_by_type["metadata_profiling"], node_by_type["metric_definition"]) in edge_pairs
    assert (node_by_type["metric_definition"], node_by_type["sql_extract"]) in edge_pairs
    assert (node_by_type["metadata_profiling"], node_by_type["sql_extract"]) in edge_pairs
    assert (node_by_type["metric_definition"], node_by_type["review"]) in edge_pairs
    assert (node_by_type["sql_extract"], node_by_type["review"]) in edge_pairs
    assert (node_by_type["sql_extract"], node_by_type["visualization"]) in edge_pairs
    assert (node_by_type["sql_extract"], node_by_type["report_writing"]) in edge_pairs
    # 洞察层：可视化 → 洞察 → 报告
    assert (node_by_type["visualization"], node_by_type["insight_analysis"]) in edge_pairs
    assert (node_by_type["insight_analysis"], node_by_type["report_writing"]) in edge_pairs


def test_decompose_fills_dep_ids() -> None:
    nodes, _ = decompose("x", "t1")
    node_by_type = {node.agent_type: node for node in nodes}
    extract = node_by_type["sql_extract"]
    assert set(extract.dep_ids or []) == {
        node_by_type["metric_definition"].node_id,
        node_by_type["metadata_profiling"].node_id,
    }
    assert node_by_type["metric_definition"].dep_ids == [node_by_type["metadata_profiling"].node_id]
    assert node_by_type["metadata_profiling"].dep_ids == []
    # 洞察只依赖可视化（datasets/charts 由可视化透传）
    assert node_by_type["insight_analysis"].dep_ids == [node_by_type["visualization"].node_id]
    # 报告吃 洞察 + 可视化 + 取数（数字来源）
    assert set(node_by_type["report_writing"].dep_ids or []) == {
        node_by_type["insight_analysis"].node_id,
        node_by_type["visualization"].node_id,
        node_by_type["sql_extract"].node_id,
    }


def test_detect_cycle_on_acyclic_graph() -> None:
    assert detect_cycle([("a", "b"), ("b", "c")]) == []
    assert detect_cycle([(a, b) for a, b in DEFAULT_DAG_EDGES]) == []


def test_detect_cycle_finds_cycle() -> None:
    cycles = detect_cycle([("a", "b"), ("b", "a")])
    assert len(cycles) == 1
    assert "a" in cycles[0] and "b" in cycles[0]

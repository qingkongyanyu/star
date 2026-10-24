# Agent 实现指南

> 本文件对应 v3.2 的 **12-Agent** 实现：Planner + 11 个执行 Agent。每个 Agent = 一个 Python 类（系统提示词）+ 一组工具（Function 实现）。

## 一、整体架构

```
┌───────────────────────────────────────────────┐
│  tools/registry.py                             │
│    register("claim_task", claim_impl, schema)  │
│    get_all_for(agent_id) → tools[]             │
└──────────────┬────────────────────────────────┘
               ▼
┌───────────────────────────────────────────────┐
│  agents/base.py → AgentBase.run()             │
│    1 claim_node → 2 get_context               │
│    3 pull_inputs → 4 execute(system_prompt,   │
│        tools, inputs) → 5 publish + notify    │
└──────────────┬────────────────────────────────┘
               ▼
┌───────────────────────────────────────────────┐
│  infra/llm.py                                  │
│    chat(system, messages, tools) → completion │
│    （OpenAI 兼容；工具调用循环）                │
└───────────────────────────────────────────────┘
```

## 二、工具注册与实现

**每个工具三件套**：schema（给 LLM 声明，与能力契约一致）+ 实现函数 + 权限校验钩子。

```python
# tools/artifact_tools.py
import hashlib
from openai.types.chat import ChatCompletionToolParam

def publish_artifact_schema() -> dict:
    return {
        "name": "publish_artifact",
        "description": "将产出写入 ArtifactRepo 并生成新版本号；自动登记血缘。",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "type": {"type": "string", "enum": ["profile","metric_spec","sql","dataset",
                    "dq_report","feature_set","chart","report","review","retrospective","trace"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "file_path": {"type": "string"},
                "dep_asset_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["node_id", "type", "title", "content"],
        },
    }

async def publish_artifact_impl(ctx, args: dict, deps) -> dict:
    """ctx: AgentExecutionContext（当前节点/用户/角色）。"""
    return await deps.artifact_service.publish(
        node_id=args["node_id"], type=args["type"], title=args["title"],
        content=args["content"], file_path=args.get("file_path"),
        dep_ids=args.get("dep_asset_ids", []),
    )
```

```python
# tools/registry.py
from openai.types.chat import ChatCompletionToolParam

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
    def register(self, name, schema, impl, guard=None):
        self._tools[name] = ToolDef(name, schema, impl, guard)
    def get_all(self) -> list[dict]:
        return [{"type": "function", "function": t.schema} for t in self._tools.values()]
    def call(self, name, ctx, args, deps):
        t = self._tools[name]
        if t.guard: t.guard(ctx, args)          # 权限钩子（如 ACL）
        return t.impl(ctx, args, deps)
```

## 三、Agent 基类与 12 个实现

```python
# agents/base.py
class AgentBase:
    CAPABILITY: str                # 认领能力标签（AGENT_CAPABILITY 注册表）
    SYSTEM_PROMPT: str             # 该能力的系统提示词
    @classmethod
    def tools(cls) -> list[dict]:
        return get_tools_for(cls.CAPABILITY)   # 该能力专属工具 + 共享工具

    async def run_node(self, node_id: str, task) -> str:   # 主循环见 02-后端模块设计·三
        ...
```

**12 个文件 ↔ 能力契约对照**：

| 文件 | CAPABILITY | 专属工具 |
| --- | --- | --- |
| `planner.py` | `orchestrator` | decompose_task / spawn_fe_task / arbitrate_claim / reroute_node / human_override / trigger_retrospective |
| `metric_definition.py` | `metric_definition` | search_metric_kb / resolve_definition / detect_conflict / publish_metric_spec |
| `data_profiler.py` | `metadata_profiling` | inspect_table_schema / inspect_relations / build_asset_map / publish_profile_report |
| `sql_agent.py` | `sql_extract` | generate_sql / check_table_acl / execute_read_only / mask_result / publish_dataset |
| `data_quality.py` | `quality_check` | run_quality_checks / classify_severity / score_dataset / recheck_dataset / notify_blocked |
| `feature_engineer.py` | `feature_engineer` | clean_dataset / preprocess_dataset / derive_features / select_features / build_feature_dict / publish_feature_set |
| `visual_agent.py` | `visualization` | select_chart_type / render_chart / summarize_features / publish_visuals |
| `insight_analyst.py` | `insight_analysis` | 基于数据集/图表产出带证据的洞察发现并发布 insight 资产（v3 新增） |
| `report_scribe.py` | `report_writing` | assemble_report / generate_pdf / publish_report |
| `analyst_review.py` | `review` | check_metric_consistency / check_processing_logic / detect_hallucination / issue_verdict |
| `memory_keeper.py` | `memory_management` | start_retrospective / write_private_memory / write_collective_memory / dedupe_memory / age_out_memory / serve_memory_search / compose_retrospective_doc |
| `trace_navigator.py` | `trace` | trace_lineage / causal_attribution / build_responsibility_list / send_inquiry / compose_trace_report |

**共享工具**（`tools/task_tools.py`、`artifact_tools.py`、`memory_tools.py`、`bus_tools.py`）：claim_task / get_task_context / report_status / release_task / pull_artifact / publish_artifact / recall_memory / submit_experience_summary / notify_bus —— 所有 Agent 均注入。

**执行上下文要点**：单个节点执行时，上游资产由 `_pull_inputs` **并发拉取**（每个引用独立
artifact 会话，受 `AGENT_INPUT_FETCH_CONCURRENCY` 限制）；同步 SQLAlchemy 调用全部经
`infra.db.to_thread` 线程池桥接，不阻塞事件循环。

## 四、系统提示词存放

- 系统提示词以常量形式放入对应 Agent 文件顶部（`SYSTEM_PROMPT`）。
- 支持模板变量：`{task_type}`、`{user_role}`、`{recalled_memories}`（渲染后进 messages）。
- 提示词版本号随 Agent 代码版本；管理员后台可覆盖（存 Agent 配置表，二次评审）。

## 五、LLM 调用与工具（infra/llm.py）

`LLMClient` 提供 `complete` / `complete_with_tools` / `embed`；当前多数 Agent 采用
「LLM 直出 JSON（`complete`）+ 解析失败回退确定性逻辑」的模式，工具注册表主要用于
资产发布/记忆/任务状态等确定性操作。下图为需要多轮工具调用时的通用循环设计：

```python
async def chat_with_tools(system: str, user: str, tools: list[dict], registry, ctx, deps, max_rounds=8):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(max_rounds):
        resp = await llm_client.chat(messages=messages, tools=tools, tool_choice="auto")
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            result = await registry.call(tc.function.name, ctx, json.loads(tc.function.arguments), deps)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    raise AgentError("工具调用轮次超限")
```

## 六、确定性 DAG 编排（DQ→FE 分支 + 自愈重跑）

编排由自研 `dag_service.build_dag`（确定性模板拓扑）+ `scheduler_worker`（MySQL 租约/CAS 抢占 + 心跳续租）驱动，不依赖 LangGraph：

```python
# services/dag_service.py
def build_dag(agent_types, ...):
    # 按确定性模板组装节点与边；DQ→FE 分支在调度时按 dq.blocked 事件重接线
    ...

# services/scheduler_worker.py
# 常驻 Worker 凭 DB 租约/CAS 多 worker 安全推进 DAG；崩溃自愈、心跳续租
```

- **崩溃自愈**：节点租约过期后由其他 worker 抢占续跑，从最后成功节点继续（状态持久化在 MySQL dag_node）。
- **branch_on_dq**：`dq.blocked` 事件驱动 DQ→FE 前置清洗分支；终审驳回（`review.rejected`）触发重调度。

## 七、Agent 执行上下文

```python
@dataclass
class AgentExecutionContext:
    node_id: str
    task_id: str
    requester: str
    role: str
    capability: str
    token: str
    requirement: str
    instruction: str = ""
    plan: dict | None = None
    inputs: dict[str, str] = field(default_factory=dict)   # asset_ref -> 内容文本
    data_source: dict | None = None
    model: str | None = None
    timeout_seconds: int | None = None
    max_retries: int = 2
```

每个工具实现第一个参数为 `ctx`，据此做权限/审计/日志绑定。

## 八、安全钩子（工具层）

| 工具 | 钩子 | 说明 |
| --- | --- | --- |
| `execute_read_only` | guard=acl_guard | 表级 ACL：越权抛 `E_FORBIDDEN`，且 SQL 写入审计 |
| `mask_result` | 必须调用 | 含手机号/身份证/邮箱字段未脱敏则拒绝输出 |
| `recall_memory` | 属主校验 | private scope 非属主 → 拒绝 |
| `notify_bus` | 事件白名单 | 仅允许本 Agent 契约允许的事件类型 |
